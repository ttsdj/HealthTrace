from __future__ import annotations

from collections import defaultdict
from threading import Lock

_BUCKETS = (0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)
_lock = Lock()
_request_counts: dict[tuple[str, str, str], int] = defaultdict(int)
_request_duration_counts: dict[tuple[str, str, float], int] = defaultdict(int)
_request_duration_sum: dict[tuple[str, str], float] = defaultdict(float)


def record_http_request(
    *,
    method: str,
    route: str,
    status_code: int,
    duration_seconds: float,
) -> None:
    safe_route = route if route.startswith("/") else "/unknown"
    status_class = f"{max(1, min(status_code // 100, 5))}xx"
    key = (method.upper(), safe_route, status_class)
    with _lock:
        _request_counts[key] += 1
        _request_duration_sum[(method.upper(), safe_route)] += max(0.0, duration_seconds)
        for bucket in _BUCKETS:
            if duration_seconds <= bucket:
                _request_duration_counts[(method.upper(), safe_route, bucket)] += 1


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

