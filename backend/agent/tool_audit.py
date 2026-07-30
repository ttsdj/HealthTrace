from __future__ import annotations

import hashlib
import json
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from backend.medical_nlp.safety import redact_sensitive_text

_AUDIT: ContextVar[list[dict] | None] = ContextVar("healthtrace_tool_audit", default=None)
_SECRET_KEYS = {"api_key", "token", "password", "latitude", "longitude", "lat", "lng"}


def reset_tool_audit() -> None:
    _AUDIT.set([])


def _sanitize(value: Any, key: str = "") -> Any:
    if key.lower() in _SECRET_KEYS:
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(k): _sanitize(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:20]]
    if isinstance(value, str):
        redacted, _ = redact_sensitive_text(value)
        return redacted[:500]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return str(value)[:200]


def audit_arguments(arguments: Any) -> tuple[Any, str]:
    sanitized = _sanitize(arguments)
    canonical = json.dumps(sanitized, ensure_ascii=False, sort_keys=True, default=str)
    return sanitized, hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def record_tool_call(
    *,
    tool_name: str,
    arguments: Any,
    attempt: int,
    status: str,
    latency_ms: int,
    error_type: str = "",
    retry_delay_ms: int = 0,
) -> None:
    audit = list(_AUDIT.get() or [])
    sanitized, digest = audit_arguments(arguments)
    audit.append(
        {
            "tool_name": tool_name,
            "arguments": sanitized,
            "arguments_sha256": digest,
            "attempt": attempt,
            "status": status,
            "latency_ms": latency_ms,
            "error_type": error_type,
            "retry_delay_ms": retry_delay_ms,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
    )
    _AUDIT.set(audit)


def get_tool_audit(clear: bool = True) -> list[dict]:
    audit = list(_AUDIT.get() or [])
    if clear:
        _AUDIT.set([])
    return audit
