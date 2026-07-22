import argparse
import json
from datetime import datetime
from pathlib import Path

from backend.env import PROJECT_ROOT, load_env


def main() -> int:
    parser = argparse.ArgumentParser(description="Evaluate HealthTrace Agent policy or runtime traces")
    parser.add_argument("--cases", default="evaluation/healthtrace_agent_v1.jsonl")
    parser.add_argument("--trace-results", default="")
    parser.add_argument("--output-id", default="")
    args = parser.parse_args()
    load_env()

    from backend.evaluation.healthtrace_agent import (
        evaluate_policy_case,
        load_agent_cases,
        summarize_policy_results,
        summarize_runtime_traces,
        write_agent_report,
    )
    from backend.patient.planner import plan_patient_tool_calls

    output_id = args.output_id or datetime.now().strftime("%Y%m%d-%H%M%S")
    output_dir = PROJECT_ROOT / "data" / "evaluations" / "healthtrace_agent" / output_id
    if args.trace_results:
        trace_path = Path(args.trace_results)
        rows = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        summary = summarize_runtime_traces(rows)
        config = {"mode": "runtime_trace", "source": str(trace_path)}
    else:
        case_path = PROJECT_ROOT / args.cases
        cases = load_agent_cases(case_path)
        rows = [
            evaluate_policy_case(case, tool_planner=plan_patient_tool_calls)
            for case in cases
        ]
        summary = summarize_policy_results(rows)
        config = {"mode": "deterministic_policy", "dataset": str(case_path), "dataset_version": "v1"}
    report = write_agent_report(output_dir, rows, summary, config)
    print(json.dumps({"summary": summary, "report": str(report)}, ensure_ascii=False, indent=2))
    return 0 if not summary.get("failed_case_ids") else 2


if __name__ == "__main__":
    raise SystemExit(main())
