from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from backend.db.models import ChatMessage, HealthNotification, HealthNotificationDelivery, HealthTaskRun


def _rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _average(values: Iterable[float]) -> float | None:
    rows = list(values)
    return round(sum(rows) / len(rows), 4) if rows else None


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return round(ordered[index], 2)


def build_observability_summary(
    db: Session,
    *,
    hours: int = 24,
    now: datetime | None = None,
) -> dict:
    """Aggregate runtime metrics without exposing prompts, answers, or patient identifiers."""
    window_end = now or datetime.utcnow()
    window_start = window_end - timedelta(hours=max(1, min(hours, 24 * 30)))
    messages = (
        db.query(ChatMessage)
        .filter(ChatMessage.timestamp >= window_start, ChatMessage.rag_trace.is_not(None))
        .all()
    )
    traces = [dict(row.rag_trace or {}) for row in messages]
    evidence_states = Counter(str(item.get("evidence_state") or "UNKNOWN") for item in traces)
    actions = Counter(str(item.get("action") or "UNKNOWN") for item in traces)
    retrieval_modes = Counter(str(item.get("retrieval_mode") or "not_invoked") for item in traces)
    fallback_count = sum(
        bool(
            item.get("retrieval_degraded")
            or item.get("retrieval_failure_reason")
            or item.get("fallback_error")
        )
        for item in traces
    )
    tool_calls = [call for trace in traces for call in (trace.get("tool_calls") or []) if isinstance(call, dict)]
    tool_failures = [call for call in tool_calls if call.get("status") != "ok"]
    latencies = [
        float(call["latency_ms"])
        for call in tool_calls
        if isinstance(call.get("latency_ms"), (int, float))
    ]
    quality_keys = (
        "ragas_context_relevance",
        "ragas_faithfulness",
        "ragas_answer_relevance",
        "ragas_quality_score",
    )
    quality = {
        key: _average(
            float(trace[key])
            for trace in traces
            if isinstance(trace.get(key), (int, float))
        )
        for key in quality_keys
    }

    runs = db.query(HealthTaskRun).filter(HealthTaskRun.created_at >= window_start).all()
    run_statuses = Counter(item.status for item in runs)
    retried_runs = sum(item.attempt_count > 1 for item in runs)
    notifications = (
        db.query(HealthNotification)
        .filter(HealthNotification.created_at >= window_start)
        .all()
    )
    notification_statuses = Counter(item.status for item in notifications)
    deliveries = (
        db.query(HealthNotificationDelivery)
        .filter(HealthNotificationDelivery.created_at >= window_start)
        .all()
    )
    delivery_statuses = Counter(item.status for item in deliveries)
    delivery_channels = Counter(item.channel for item in deliveries)
    return {
        "window": {
            "hours": hours,
            "start_at": window_start.isoformat(),
            "end_at": window_end.isoformat(),
        },
        "chat": {
            "trace_turns": len(traces),
            "evidence_states": dict(sorted(evidence_states.items())),
            "actions": dict(sorted(actions.items())),
            "retrieval_modes": dict(sorted(retrieval_modes.items())),
            "fallback_count": fallback_count,
            "fallback_rate": _rate(fallback_count, len(traces)),
            "high_risk_count": sum(bool(item.get("high_risk_medical")) for item in traces),
            "no_evidence_count": evidence_states.get("NO_EVIDENCE", 0),
        },
        "tools": {
            "call_count": len(tool_calls),
            "success_count": len(tool_calls) - len(tool_failures),
            "failure_count": len(tool_failures),
            "success_rate": _rate(len(tool_calls) - len(tool_failures), len(tool_calls)),
            "latency_p50_ms": _percentile(latencies, 0.5),
            "latency_p95_ms": _percentile(latencies, 0.95),
        },
        "quality": {
            **quality,
            "mode": "ragas_lite_runtime_no_reference",
            "is_official_ragas": False,
        },
        "tasks": {
            "run_count": len(runs),
            "status_counts": dict(sorted(run_statuses.items())),
            "retried_run_count": retried_runs,
            "retry_rate": _rate(retried_runs, len(runs)),
        },
        "notifications": {
            "count": len(notifications),
            "status_counts": dict(sorted(notification_statuses.items())),
            "external_delivery_count": len(deliveries),
            "delivery_status_counts": dict(sorted(delivery_statuses.items())),
            "delivery_channel_counts": dict(sorted(delivery_channels.items())),
        },
        "privacy": {
            "contains_prompt_text": False,
            "contains_patient_identifiers": False,
        },
    }
