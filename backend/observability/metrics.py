from __future__ import annotations

import os
from collections import defaultdict
from threading import Lock

_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

# Label values have to come from a bounded set.  A request that matches no route
# -- a scanner walking random URLs -- has no route template, and using its raw
# path as the label would add one time series per probe to the dicts below until
# the process runs out of memory.  backend.security.middleware passes
# UNMATCHED_ROUTE for those; the cap here is the second line of defence for any
# caller that interpolates something unbounded.
UNMATCHED_ROUTE = "/unmatched"
_OVERFLOW_ROUTE = "/other"


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


_MAX_ROUTE_SERIES = _positive_int_env("HEALTHTRACE_METRICS_MAX_ROUTES", 200)

_lock = Lock()
_request_counts: dict[tuple[str, str, str], int] = defaultdict(int)
_request_duration_counts: dict[tuple[str, str, float], int] = defaultdict(int)
_request_duration_sum: dict[tuple[str, str], float] = defaultdict(float)
_known_routes: set[str] = set()


def _bounded_route(route: str) -> str:
    """Map a route onto a label from a bounded set. Caller must hold _lock."""
    if not route.startswith("/"):
        return UNMATCHED_ROUTE
    if route in _known_routes:
        return route
    if len(_known_routes) >= _MAX_ROUTE_SERIES:
        return _OVERFLOW_ROUTE
    _known_routes.add(route)
    return route


def record_http_request(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    status_class = f"{max(1, min(status_code // 100, 5))}xx"
    normalized_method = method.upper()
    duration = max(0.0, duration_seconds)
    with _lock:
        safe_route = _bounded_route(route)
        _request_counts[(normalized_method, safe_route, status_class)] += 1
        _request_duration_sum[(normalized_method, safe_route)] += duration
        for bucket in _BUCKETS:
            if duration <= bucket:
                _request_duration_counts[(normalized_method, safe_route, bucket)] += 1


def reset_process_metrics() -> None:
    """Drop every series. Intended for tests that assert on metric output."""
    with _lock:
        _request_counts.clear()
        _request_duration_counts.clear()
        _request_duration_sum.clear()
        _known_routes.clear()


def _labels(**values: str) -> str:
    escaped = {
        key: str(value).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        for key, value in values.items()
    }
    return "{" + ",".join(f'{key}="{value}"' for key, value in escaped.items()) + "}"


def render_process_metrics() -> str:
    lines = [
        "# HELP healthtrace_http_requests_total Total HTTP requests by route template.",
        "# TYPE healthtrace_http_requests_total counter",
    ]
    with _lock:
        counts = dict(_request_counts)
        duration_counts = dict(_request_duration_counts)
        duration_sums = dict(_request_duration_sum)
    for (method, route, status_class), value in sorted(counts.items()):
        lines.append(
            f"healthtrace_http_requests_total{_labels(method=method, route=route, status_class=status_class)} {value}"
        )
    lines.extend(
        [
            "# HELP healthtrace_http_request_duration_seconds HTTP request latency.",
            "# TYPE healthtrace_http_request_duration_seconds histogram",
        ]
    )
    for method, route in sorted(duration_sums):
        total = sum(
            value
            for (item_method, item_route, _), value in counts.items()
            if item_method == method and item_route == route
        )
        for bucket in _BUCKETS:
            value = duration_counts.get((method, route, bucket), 0)
            lines.append(
                f"healthtrace_http_request_duration_seconds_bucket{_labels(method=method, route=route, le=str(bucket))} {value}"
            )
        lines.append(
            f"healthtrace_http_request_duration_seconds_bucket{_labels(method=method, route=route, le='+Inf')} {total}"
        )
        lines.append(
            f"healthtrace_http_request_duration_seconds_sum{_labels(method=method, route=route)} {duration_sums[(method, route)]:.6f}"
        )
        lines.append(
            f"healthtrace_http_request_duration_seconds_count{_labels(method=method, route=route)} {total}"
        )
    return "\n".join(lines) + "\n"

