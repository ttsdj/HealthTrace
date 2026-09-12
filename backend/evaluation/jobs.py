from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from backend.env import PROJECT_ROOT
from backend.evaluation.healthtrace_agent import (
    evaluate_policy_case,
    load_agent_cases,
    summarize_policy_results,
    write_agent_report,
)
from backend.jobs.queue import JobReporter
from backend.patient.planner import plan_patient_tool_calls

# output_id becomes a filesystem path segment; keep it to a safe charset so a
# crafted id cannot traverse out of the evaluation report directory.
_SAFE_OUTPUT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$")


def run_agent_evaluation_job(job_id: str, payload: dict) -> dict:
    reporter = JobReporter(job_id)
    case_path = PROJECT_ROOT / str(
        payload.get("cases") or "evaluation/healthtrace_agent_v1.jsonl"
    )
    try:
        case_path.resolve().relative_to((PROJECT_ROOT / "evaluation").resolve())
    except ValueError as exc:
        raise ValueError("Evaluation cases must be inside the evaluation directory") from exc
    cases = load_agent_cases(case_path)
    output_id = str(payload.get("output_id") or datetime.now().strftime("%Y%m%d-%H%M%S"))
    if not _SAFE_OUTPUT_ID.fullmatch(output_id):
        raise ValueError(
            "output_id may only contain letters, digits, dot, dash and underscore"
        )
    output_dir = PROJECT_ROOT / "data" / "evaluations" / "healthtrace_agent" / output_id
    resolved_output = output_dir.resolve()
    try:
        resolved_output.relative_to((PROJECT_ROOT / "data" / "evaluations").resolve())
    except ValueError as exc:
        raise ValueError("Evaluation output directory escapes the evaluations directory") from exc
    rows = []
    total = len(cases)
    for index, case in enumerate(cases, 1):
        rows.append(
            evaluate_policy_case(case, tool_planner=plan_patient_tool_calls)
        )
        reporter.update_step(
            job_id,
            "evaluate",
            round(index * 100 / total) if total else 100,
            "running",
            f"Evaluated {index} / {total} cases",
            total_chunks=total,
            processed_chunks=index,
        )
    summary = summarize_policy_results(rows)
    report = write_agent_report(
        output_dir,
        rows,
        summary,
        {
            "mode": "deterministic_policy",
            "dataset": str(case_path),
            "dataset_version": "v1",
            "background_job_id": job_id,
            "golden_review_required": bool(payload.get("require_approved_golden")),
            "golden_dataset": payload.get("dataset_name"),
            "golden_dataset_version": payload.get("dataset_version"),
        },
    )
    reporter.complete_job(job_id, "Agent evaluation completed")
    return {
        "summary": summary,
        "report": str(Path(report).relative_to(PROJECT_ROOT)),
    }
