from __future__ import annotations

import math
from collections import Counter
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy.orm import Session

from backend.db.models import (
    AuditEvent,
    BackgroundJob,
    ChatMessage,
    HealthNotification,
    HealthNotificationDelivery,
    HealthTaskRun,
)


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
    jobs = db.query(BackgroundJob).filter(BackgroundJob.created_at >= window_start).all()
    job_statuses = Counter(item.status for item in jobs)
    job_types = Counter(item.job_type for item in jobs)
    audits = db.query(AuditEvent).filter(AuditEvent.occurred_at >= window_start).all()
    audit_outcomes = Counter(item.outcome for item in audits)
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
        "background_jobs": {
            "count": len(jobs),
            "status_counts": dict(sorted(job_statuses.items())),
            "type_counts": dict(sorted(job_types.items())),
            "retry_count": sum(item.attempt_count > 1 for item in jobs),
        },
        "audit": {
            "event_count": len(audits),
            "outcome_counts": dict(sorted(audit_outcomes.items())),
        },
        "privacy": {
            "contains_prompt_text": False,
            "contains_patient_identifiers": False,
        },
    }


def build_operational_alerts(summary: dict) -> dict:
    import os

    alerts: list[dict] = []

    def add(code: str, severity: str, value, threshold, message: str) -> None:
        alerts.append(
            {
                "code": code,
                "severity": severity,
                "value": value,
                "threshold": threshold,
                "message": message,
            }
        )

    fallback_rate = summary["chat"].get("fallback_rate")
    fallback_threshold = float(os.getenv("HEALTHTRACE_ALERT_FALLBACK_RATE", "0.25"))
    if fallback_rate is not None and fallback_rate > fallback_threshold:
        add(
            "rag_fallback_rate_high",
            "warning",
            fallback_rate,
            fallback_threshold,
            "RAG fallback rate is above the configured threshold.",
        )

    tool_success = summary["tools"].get("success_rate")
    tool_threshold = float(os.getenv("HEALTHTRACE_ALERT_TOOL_SUCCESS_RATE", "0.9"))
    if tool_success is not None and tool_success < tool_threshold:
        add(
            "tool_success_rate_low",
            "warning",
            tool_success,
            tool_threshold,
            "Tool success rate is below the configured threshold.",
        )

    p95 = summary["tools"].get("latency_p95_ms")
    p95_threshold = float(os.getenv("HEALTHTRACE_ALERT_TOOL_P95_MS", "5000"))
    if p95 is not None and p95 > p95_threshold:
        add(
            "tool_latency_p95_high",
            "warning",
            p95,
            p95_threshold,
            "Tool P95 latency is above the configured threshold.",
        )

    failed_jobs = int(summary["background_jobs"]["status_counts"].get("failed", 0))
    if failed_jobs:
        add(
            "background_jobs_failed",
            "critical",
            failed_jobs,
            0,
            "One or more durable background jobs exhausted retries.",
        )
    failed_deliveries = int(
        summary["notifications"]["delivery_status_counts"].get("failed", 0)
    )
    if failed_deliveries:
        add(
            "notification_deliveries_failed",
            "warning",
            failed_deliveries,
            0,
            "One or more external notification deliveries exhausted retries.",
        )
    audit_errors = int(summary["audit"]["outcome_counts"].get("error", 0))
    if audit_errors:
        add(
            "audited_requests_error",
            "warning",
            audit_errors,
            0,
            "Audited API requests returned server errors.",
        )
    return {
        "status": "ok" if not alerts else "critical" if any(item["severity"] == "critical" for item in alerts) else "warning",
        "alert_count": len(alerts),
        "alerts": alerts,
        "window": summary["window"],
    }


def render_summary_prometheus(summary: dict) -> str:
    def metric(name: str, value) -> str:
        return f"{name} {0 if value is None else value}"

    lines = [
        "# HELP healthtrace_chat_trace_turns RAG traced chat turns in the selected window.",
        "# TYPE healthtrace_chat_trace_turns gauge",
        metric("healthtrace_chat_trace_turns", summary["chat"]["trace_turns"]),
        "# HELP healthtrace_rag_fallback_rate Fraction of traced turns using fallback.",
        "# TYPE healthtrace_rag_fallback_rate gauge",
        metric("healthtrace_rag_fallback_rate", summary["chat"]["fallback_rate"]),
        "# HELP healthtrace_tool_success_rate Fraction of successful tool calls.",
        "# TYPE healthtrace_tool_success_rate gauge",
        metric("healthtrace_tool_success_rate", summary["tools"]["success_rate"]),
        "# HELP healthtrace_tool_latency_p95_ms Tool latency P95 in milliseconds.",
        "# TYPE healthtrace_tool_latency_p95_ms gauge",
        metric("healthtrace_tool_latency_p95_ms", summary["tools"]["latency_p95_ms"]),
        "# HELP healthtrace_background_jobs Number of durable jobs by status.",
        "# TYPE healthtrace_background_jobs gauge",
    ]
    for status, value in sorted(summary["background_jobs"]["status_counts"].items()):
        lines.append(f'healthtrace_background_jobs{{status="{status}"}} {value}')
    lines.extend(
        [
            "# HELP healthtrace_notification_deliveries Number of external deliveries by status.",
            "# TYPE healthtrace_notification_deliveries gauge",
        ]
    )
    for status, value in sorted(
        summary["notifications"]["delivery_status_counts"].items()
    ):
        lines.append(
            f'healthtrace_notification_deliveries{{status="{status}"}} {value}'
        )
    return "\n".join(lines) + "\n"
