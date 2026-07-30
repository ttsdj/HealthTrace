"""Run the frozen 100-case intent-router acceptance suite."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from backend.evaluation.intent_router import (
    acceptance_passed,
    evaluate_intent_router,
    load_frozen_intent_cases,
    sha256_file,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dataset", default="evaluation/intent_router_v1.jsonl")
    parser.add_argument("--manifest", default="evaluation/intent_router_v1.manifest.json")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--verbose", action="store_true")
    parser.add_argument(
        "--router-mode",
        choices=("rules", "fastmodel", "auto"),
        help="Override HEALTHTRACE_INTENT_ROUTER_MODE for this isolated evaluation run.",
    )
    args = parser.parse_args()
    if args.router_mode:
        os.environ["HEALTHTRACE_INTENT_ROUTER_MODE"] = args.router_mode
    cases = load_frozen_intent_cases(args.dataset, args.manifest)
    report = evaluate_intent_router(cases)
    report["dataset_sha256"] = sha256_file(args.dataset)
    report["router_mode"] = os.getenv("HEALTHTRACE_INTENT_ROUTER_MODE", "rules")
    report["acceptance_passed"] = acceptance_passed(report)
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    console_report = report if args.verbose else {key: value for key, value in report.items() if key != "rows"}
    print(json.dumps(console_report, ensure_ascii=False, indent=2))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if report["acceptance_passed"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
