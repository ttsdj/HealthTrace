"""Fixed-window rate limiting for the unauthenticated credential endpoints.

Deliberately dependency-free.  Redis is optional in this project
(``backend.infra.cache`` degrades to a no-op when it is absent), so a limiter
built on it would silently disable itself in the default deployment -- which is
exactly the configuration where an unbounded login flood does the most damage.

Two consequences follow from keeping the counter in process memory, and both
matter in production:

* The window lives in one worker.  With N uvicorn workers the effective limit is
  N times the configured value.
* Client identity comes from the peer address unless
  ``HEALTHTRACE_TRUST_PROXY_HEADERS`` is enabled.  Behind a reverse proxy that
  means every user shares the proxy's address and therefore one bucket, so
  either enable that flag (only when a proxy you control sets the header) or
  rate limit at the proxy instead.

Neither limitation is a reason to skip a per-process limiter: it caps the
trivial flood and makes credential brute force cost the attacker something.
"""

from __future__ import annotations

import os
import threading
import time
from typing import NamedTuple

from fastapi import HTTPException, Request


class RateLimitDecision(NamedTuple):
    allowed: bool
    retry_after_seconds: int


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _flag_env(name: str, default: bool = False) -> bool:
    return os.getenv(name, "true" if default else "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# Read at module level so operators can tune them, but the dependency functions
# below read the globals at call time so tests can monkeypatch them.
_LOGIN_LIMIT = _positive_int_env("HEALTHTRACE_LOGIN_RATE_LIMIT", 20)
_LOGIN_WINDOW_SECONDS = _positive_int_env("HEALTHTRACE_LOGIN_RATE_WINDOW_SECONDS", 60)
_REGISTER_LIMIT = _positive_int_env("HEALTHTRACE_REGISTER_RATE_LIMIT", 5)
_REGISTER_WINDOW_SECONDS = _positive_int_env("HEALTHTRACE_REGISTER_RATE_WINDOW_SECONDS", 300)

_TRUST_PROXY_HEADERS = _flag_env("HEALTHTRACE_TRUST_PROXY_HEADERS")
_MAX_CLIENT_KEYS = _positive_int_env("HEALTHTRACE_RATE_LIMIT_MAX_CLIENTS", 4096)


class FixedWindowLimiter:
    """Counts requests per (bucket, client) pair in fixed windows.

    Memory is bounded twice over: entries from an expired window are dropped
    once the table is under pressure, and a table that is still full after that
    denies rather than growing.  A full table means a flood is in progress, so
    failing closed is the point -- it is the one case where the limiter exists.
    """

    def __init__(self, *, max_keys: int = _MAX_CLIENT_KEYS) -> None:
        self._max_keys = max(1, max_keys)
        self._lock = threading.Lock()
        self._windows: dict[tuple[str, str], tuple[int, int]] = {}

    def reset(self) -> None:
        with self._lock:
            self._windows.clear()

    def _drop_stale(self, current_window: int) -> None:
        stale = [key for key, window in self._windows.items() if window[0] != current_window]
        for key in stale:
            del self._windows[key]

    def check(
        self,
        *,
        bucket: str,
        key: str,
        limit: int,
        window_seconds: int,
        now: float | None = None,
    ) -> RateLimitDecision:
        if limit <= 0 or window_seconds <= 0:
            return RateLimitDecision(True, 0)
        moment = time.monotonic() if now is None else now
        window = int(moment // window_seconds)
        remaining = max(1, int(window_seconds - (moment - window * window_seconds)) + 1)
        composite = (bucket, key)

        with self._lock:
            if len(self._windows) >= self._max_keys:
                self._drop_stale(window)
                if len(self._windows) >= self._max_keys:
                    return RateLimitDecision(False, remaining)

            stored = self._windows.get(composite)
            if stored is None or stored[0] != window:
                self._windows[composite] = (window, 1)
                return RateLimitDecision(True, 0)

            count = stored[1] + 1
            self._windows[composite] = (window, count)
            if count > limit:
                return RateLimitDecision(False, remaining)
            return RateLimitDecision(True, 0)


limiter = FixedWindowLimiter()


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting purposes."""
    if _TRUST_PROXY_HEADERS:
        forwarded = request.headers.get("X-Forwarded-For", "")
        hops = [hop.strip() for hop in forwarded.split(",") if hop.strip()]
        if hops:
            # The rightmost entry is the one appended by the proxy nearest to
            # us; anything further left is attacker-controlled.
            return hops[-1][:64]
    client = getattr(request, "client", None)
    return (getattr(client, "host", None) or "unknown")[:64]


def _enforce(request: Request, *, bucket: str, limit: int, window_seconds: int) -> None:
    decision = limiter.check(
        bucket=bucket,
        key=client_key(request),
        limit=limit,
        window_seconds=window_seconds,
    )
    if not decision.allowed:
        raise HTTPException(
            status_code=429,
            detail="请求过于频繁，请稍后重试",
            headers={"Retry-After": str(decision.retry_after_seconds)},
        )


def enforce_login_rate_limit(request: Request) -> None:
    """Dependency for POST /auth/login."""
    _enforce(
        request,
        bucket="auth_login",
        limit=_LOGIN_LIMIT,
        window_seconds=_LOGIN_WINDOW_SECONDS,
    )


def enforce_register_rate_limit(request: Request) -> None:
    """Dependency for POST /auth/register.

    Tighter than login: registering provisions a User, a Tenant, a
    PatientProfile and two access rows, so it is the cheaper request to abuse.
    """
    _enforce(
        request,
        bucket="auth_register",
        limit=_REGISTER_LIMIT,
        window_seconds=_REGISTER_WINDOW_SECONDS,
    )
