import json

from backend.evaluation.healthtrace_agent import (
    evaluate_policy_case,
    load_agent_cases,
    summarize_policy_results,
    summarize_runtime_traces,
)


def test_agent_dataset_is_versioned_unique_and_has_required_categories():
    cases = load_agent_cases("evaluation/healthtrace_agent_v1.jsonl")
    categories = {item["category"] for item in cases}
    assert len(cases) == 42
    assert categories >= {"high_risk", "missing_information", "public_medical", "patient_tool_routing", "privacy", "boundary"}


def test_policy_metric_summary_is_deterministic():
    case = {
        "case_id": "synthetic",
        "category": "public_medical",
        "query": "肺炎有哪些症状",
        "expected_action": "ANSWER",
        "expected_evidence_state": "PARTIAL",
        "expected_sources": ["public_rag", "medical_kg"],
    }
    result = evaluate_policy_case(case)
    summary = summarize_policy_results([result])
    assert result["passed"] is True
    assert summary["action_accuracy"] == 1.0
    assert summary["pass_rate"] == 1.0


def test_runtime_trace_summary_reports_fallback_tools_and_latency():
    rows = [
        {"rag_trace": {"evidence_state": "SUFFICIENT", "action": "ANSWER", "retrieval_mode": "hybrid", "tool_calls": [{"status": "ok", "latency_ms": 10}]}},
        {"rag_trace": {"evidence_state": "NO_EVIDENCE", "action": "REFUSE", "retrieval_mode": "no_results", "retrieval_failure_reason": "empty", "tool_calls": [{"status": "failed", "latency_ms": 80}]}},
    ]
    summary = summarize_runtime_traces(rows)
    assert summary["fallback_rate"] == 0.5
    assert summary["tool_success_rate"] == 0.5
    assert summary["tool_latency_p95_ms"] == 80.0
