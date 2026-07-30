"""Tool bulkheads and retry policy used by the Planner Executor runtime."""

from __future__ import annotations

import random
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from backend.agent.tool_registry import spec_for


@dataclass(frozen=True)
class RetryEvent:
    attempt: int
    retry_delay_ms: int
    error_type: str = ""


class ToolBulkheadRejected(RuntimeError):
    pass


def _limit(tool_name: str) -> int:
    spec = spec_for(tool_name)
    return spec.bulkhead_limit if spec else 2


_BULKHEADS: dict[str, threading.BoundedSemaphore] = {}
_BULKHEAD_LOCK = threading.Lock()


def _bulkhead(tool_name: str) -> threading.BoundedSemaphore:
    with _BULKHEAD_LOCK:
        return _BULKHEADS.setdefault(tool_name, threading.BoundedSemaphore(_limit(tool_name)))


def is_transient_error(error: Exception) -> bool:
    message = str(error).lower()
    return any(item in message for item in ("timeout", "timed out", "connection", "429", "502", "503", "504"))


def invoke_with_resilience(
    tool_name: str,
    callback: Callable[[], str],
    *,
    on_event: Callable[[str, RetryEvent], None] | None = None,
) -> str:
    """Invoke one tool with a per-tool bulkhead and full-jitter read retries."""
    spec = spec_for(tool_name)
    read_only = spec.read_only if spec else True
    max_attempts = spec.max_attempts if spec else 2
    if not read_only:
        max_attempts = 1  # writes must use their own idempotency contract
    queue_timeout = max(0.0, float(__import__("os").getenv("HEALTHTRACE_TOOL_BULKHEAD_WAIT_SECONDS", "0.05")))
    semaphore = _bulkhead(tool_name)
    acquired = semaphore.acquire(timeout=queue_timeout)
    if not acquired:
        raise ToolBulkheadRejected(f"tool bulkhead exhausted: {tool_name}")
    try:
        for attempt in range(1, max_attempts + 1):
            try:
                result = callback()
                if on_event:
                    on_event("ok", RetryEvent(attempt=attempt, retry_delay_ms=0))
                return result
            except Exception as error:
                transient = is_transient_error(error)
                if attempt >= max_attempts or not transient:
                    if on_event:
                        on_event("failed", RetryEvent(attempt, 0, type(error).__name__))
                    raise
                base = float(__import__("os").getenv("HEALTHTRACE_TOOL_RETRY_BASE_SECONDS", "0.05"))
                cap = float(__import__("os").getenv("HEALTHTRACE_TOOL_RETRY_CAP_SECONDS", "0.5"))
                delay = random.uniform(0, min(cap, base * (2 ** (attempt - 1))))
                if on_event:
                    on_event("retryable_error", RetryEvent(attempt, round(delay * 1000), type(error).__name__))
                time.sleep(delay)
    finally:
        semaphore.release()
    raise RuntimeError("unreachable")
