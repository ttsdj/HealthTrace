from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Callable, Iterable

from backend.agent.planner import ConsultationPlan, plan_consultation
from backend.medical_nlp.safety import redact_sensitive_text


def load_agent_cases(path: str | Path) -> list[dict]:
    rows: list[dict] = []
    for line_number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        required = {"case_id", "category", "query", "expected_action", "expected_evidence_state"}
        missing = required - set(row)
        if missing:
            raise ValueError(f"Agent case line {line_number} missing fields: {sorted(missing)}")
        rows.append(row)
    ids = [row["case_id"] for row in rows]
    if len(ids) != len(set(ids)):
        raise ValueError("Agent case_id values must be unique")
    return rows


def _percentile(values: list[float], quantile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, math.ceil(len(ordered) * quantile) - 1))
    return round(ordered[index], 3)


def evaluate_policy_case(
    case: dict,
    planner: Callable[[str, str], ConsultationPlan] = plan_consultation,
    tool_planner: Callable[[str], list] | None = None,
) -> dict:
    plan = planner(case.get("username", "healthtrace-evaluation-user"), case["query"])
    _, privacy = redact_sensitive_text(case["query"])
    expected_sources = set(case.get("expected_sources") or [])
    actual_sources = set(plan.required_evidence_sources)
    expected_tools = set(case.get("expected_patient_tools") or [])
    actual_tools: set[str] = set()
    if tool_planner is not None:
        actual_tools = {
            getattr(item, "tool_name", None) or item.get("tool_name")
            for item in tool_planner(case["query"])
        }
        actual_tools.discard(None)

    checks = {
        "action_correct": plan.action.value == case["expected_action"],
        "evidence_state_correct": plan.evidence_state.value == case["expected_evidence_state"],
        "sources_correct": expected_sources.issubset(actual_sources),
        "privacy_correct": privacy["privacy_redaction_applied"] == bool(case.get("expect_redaction", False)),
        "patient_tools_correct": not expected_tools or expected_tools.issubset(actual_tools),
    }
    return {
        "case_id": case["case_id"],
        "category": case["category"],
        "expected": {
            "action": case["expected_action"],
            "evidence_state": case["expected_evidence_state"],
            "sources": sorted(expected_sources),
            "patient_tools": sorted(expected_tools),
        },
        "actual": {
            "action": plan.action.value,
            "evidence_state": plan.evidence_state.value,
            "sources": sorted(actual_sources),
            "patient_tools": sorted(actual_tools),
            "missing_fields": plan.missing_fields,
        },
        "checks": checks,
        "passed": all(checks.values()),
    }


def summarize_policy_results(rows: Iterable[dict]) -> dict:
    records = list(rows)
    count = len(records)
    categories = Counter(row["category"] for row in records)
    high_risk = [row for row in records if row["expected"]["action"] == "ESCALATE_URGENT"]
    missing = [row for row in records if row["expected"]["action"] == "ASK"]

    def rate(predicate) -> float | None:
        return round(sum(1 for row in records if predicate(row)) / count, 4) if count else None

    return {
        "case_count": count,
        "category_counts": dict(sorted(categories.items())),
        "pass_rate": rate(lambda row: row["passed"]),
        "action_accuracy": rate(lambda row: row["checks"]["action_correct"]),
        "evidence_state_accuracy": rate(lambda row: row["checks"]["evidence_state_correct"]),
        "source_plan_accuracy": rate(lambda row: row["checks"]["sources_correct"]),
        "patient_tool_plan_accuracy": rate(lambda row: row["checks"]["patient_tools_correct"]),
        "privacy_redaction_accuracy": rate(lambda row: row["checks"]["privacy_correct"]),
        "high_risk_recall": (
            round(sum(row["actual"]["action"] == "ESCALATE_URGENT" for row in high_risk) / len(high_risk), 4)
            if high_risk else None
        ),
        "missing_information_recall": (
            round(sum(row["actual"]["action"] == "ASK" for row in missing) / len(missing), 4)
            if missing else None
        ),
        "failed_case_ids": [row["case_id"] for row in records if not row["passed"]],
    }


def summarize_runtime_traces(records: Iterable[dict]) -> dict:
    rows = list(records)
    traces = [row.get("rag_trace") or {} for row in rows]
    tool_calls = [call for trace in traces for call in (trace.get("tool_calls") or [])]
    latencies = [float(call["latency_ms"]) for call in tool_calls if call.get("latency_ms") is not None]
    evidence = Counter(trace.get("evidence_state", "UNKNOWN") for trace in traces)
    actions = Counter(trace.get("action", "UNKNOWN") for trace in traces)
    retrieval_modes = Counter(trace.get("retrieval_mode", "not_invoked") for trace in traces)
    tool_failures = [call for call in tool_calls if call.get("status") not in {"ok"}]
    return {
        "turn_count": len(rows),
        "evidence_state_counts": dict(sorted(evidence.items())),
        "action_counts": dict(sorted(actions.items())),
        "retrieval_mode_counts": dict(sorted(retrieval_modes.items())),
        "fallback_rate": round(
            sum(bool(trace.get("fallback_error") or trace.get("retrieval_failure_reason")) for trace in traces)
            / len(traces), 4
        ) if traces else None,
        "no_evidence_rate": round(sum(trace.get("evidence_state") == "NO_EVIDENCE" for trace in traces) / len(traces), 4) if traces else None,
        "tool_call_count": len(tool_calls),
        "tool_success_rate": round((len(tool_calls) - len(tool_failures)) / len(tool_calls), 4) if tool_calls else None,
        "tool_latency_p50_ms": _percentile(latencies, 0.5),
        "tool_latency_p95_ms": _percentile(latencies, 0.95),
    }


def write_agent_report(output_dir: str | Path, rows: list[dict], summary: dict, config: dict) -> Path:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "config.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    with (target / "per_case.jsonl").open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    (target / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        "# HealthTrace Agent Evaluation",
        "",
        f"- Cases: {summary.get('case_count', summary.get('turn_count', 0))}",
    ]
    for key, value in summary.items():
        if key.endswith(("accuracy", "recall", "rate")):
            lines.append(f"- {key}: {value}")
    report = target / "report.md"
    report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
