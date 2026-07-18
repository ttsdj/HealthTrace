"""Run MIRAGE benchmark for MedRetrieveV2.0."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.env import load_env

load_env()

from backend.evaluation.mirage_dataset import (  # noqa: E402
    load_mirage_cases,
    resolve_benchmark_path,
    select_cases,
)
from backend.evaluation.mirage_runner import (  # noqa: E402
    MIRAGE_PROMPT_VERSION,
    SUPPORTED_MODES,
    ChoiceLLMClient,
    OpenAICompatibleChoiceConfig,
    run_benchmark,
    write_outputs,
)

DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "mirage" / "evaluations"


def _parse_datasets(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _record_key(record: dict) -> tuple[str, str]:
    return str(record.get("query_id") or ""), str(record.get("mode") or "")


def _read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                rows.append(payload)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Evaluate MedRetrieveV2.0 on the MIRAGE medical QA benchmark."
    )
    parser.add_argument("--data-path", default="", help="Path to MIRAGE benchmark.json")
    parser.add_argument("--download", action="store_true", help="Download benchmark.json if missing")
    parser.add_argument("--force-download", action="store_true")
    parser.add_argument("--mode", choices=SUPPORTED_MODES, default="llm_only")
    parser.add_argument("--datasets", type=_parse_datasets, default=[])
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--max-cases-per-dataset", type=int, default=0)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument(
        "--progress-every",
        type=int,
        default=50,
        help="Print progress every N completed cases; set 0 to disable.",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from per_case.partial.jsonl in the same experiment directory.",
    )
    parser.add_argument(
        "--rerun-failed",
        action="store_true",
        help="When resuming, rerun records that previously failed or produced invalid answers.",
    )
    parser.add_argument("--experiment-id", default="")
    args = parser.parse_args()

    benchmark_path = resolve_benchmark_path(
        PROJECT_ROOT,
        args.data_path or None,
        download=args.download or args.force_download,
        force_download=args.force_download,
    )
    all_cases = load_mirage_cases(benchmark_path)
    cases = select_cases(
        all_cases,
        datasets=args.datasets,
        max_cases=args.max_cases,
        max_cases_per_dataset=args.max_cases_per_dataset,
    )
    if not cases:
        parser.error("No cases selected")

    config = OpenAICompatibleChoiceConfig.from_env()
    experiment_id = args.experiment_id or datetime.now().strftime(
        f"%Y%m%d-%H%M%S-mirage-{args.mode}"
    )
    output_dir = DEFAULT_OUTPUT_ROOT / experiment_id
    output_dir.mkdir(parents=True, exist_ok=True)
    partial_path = output_dir / "per_case.partial.jsonl"
    existing_records = _read_jsonl(partial_path) if args.resume else []
    existing_by_key = {
        _record_key(record): record
        for record in existing_records
        if _record_key(record)[0] and _record_key(record)[1] == args.mode
        and not (
            args.rerun_failed
            and (record.get("error") or not record.get("is_valid"))
        )
    }
    cases_to_run = [
        case
        for case in cases
        if (str(case.get("query_id") or ""), args.mode) not in existing_by_key
    ]
    raw_sha256 = hashlib.sha256(benchmark_path.read_bytes()).hexdigest()
    run_config = {
        "experiment_id": experiment_id,
        "benchmark": "MIRAGE",
        "benchmark_source": "https://github.com/gzxiong/MIRAGE",
        "benchmark_path": str(benchmark_path),
        "benchmark_sha256": raw_sha256,
        "prompt_version": MIRAGE_PROMPT_VERSION,
        "mode": args.mode,
        "case_count": len(cases),
        "resumed_records": len(existing_by_key),
        "remaining_cases_at_start": len(cases_to_run),
        "rerun_failed": args.rerun_failed,
        "total_cases_available": len(all_cases),
        "datasets": args.datasets or "all",
        "max_cases": args.max_cases,
        "max_cases_per_dataset": args.max_cases_per_dataset,
        "workers": args.workers,
        "top_k": args.top_k,
        "progress_every": args.progress_every,
        "model": config.model,
        "base_url": config.base_url,
        "qor_policy": "Retrieval receives Question only; options are used only for answer generation.",
    }

    print(
        f"Loaded MIRAGE: {len(all_cases)} cases from {benchmark_path}. "
        f"Selected {len(cases)} cases, running {len(cases_to_run)} remaining, "
        f"mode={args.mode}, model={config.model}."
    )

    start_completed = len(existing_by_key)
    start_correct = sum(1 for record in existing_by_key.values() if record.get("is_correct"))
    start_invalid = sum(1 for record in existing_by_key.values() if not record.get("is_valid"))
    start_failures = sum(1 for record in existing_by_key.values() if record.get("error"))

    partial_handle = partial_path.open("a", encoding="utf-8", newline="\n")

    def _progress(completed: int, total: int, totals: dict, record: dict) -> None:
        partial_handle.write(json.dumps(record, ensure_ascii=False) + "\n")
        partial_handle.flush()
        every = max(args.progress_every, 0)
        overall_completed = start_completed + completed
        overall_total = start_completed + total
        correct = start_correct + totals["correct"]
        invalid = start_invalid + totals["invalid"]
        failures = start_failures + totals["failures"]
        if every == 0 and overall_completed < overall_total:
            return
        if overall_completed != overall_total and overall_completed % every != 0:
            return
        accuracy = correct / overall_completed if overall_completed else 0.0
        invalid_rate = invalid / overall_completed if overall_completed else 0.0
        failure_rate = failures / overall_completed if overall_completed else 0.0
        print(
            "Progress: "
            f"{overall_completed}/{overall_total} "
            f"accuracy={accuracy:.4f} "
            f"invalid={invalid_rate:.4f} "
            f"failure={failure_rate:.4f}",
            flush=True,
        )

    try:
        new_records = (
            run_benchmark(
                cases_to_run,
                mode=args.mode,
                llm_client=ChoiceLLMClient(config),
                top_k=args.top_k,
                workers=args.workers,
                progress_callback=_progress,
            )
            if cases_to_run
            else []
        )
    finally:
        partial_handle.close()

    records_by_key = dict(existing_by_key)
    for record in new_records:
        records_by_key[_record_key(record)] = record
    records = [
        records_by_key[(str(case.get("query_id") or ""), args.mode)]
        for case in cases
        if (str(case.get("query_id") or ""), args.mode) in records_by_key
    ]
    summary = write_outputs(output_dir, config=run_config, records=records)
    print(f"Wrote MIRAGE outputs to {output_dir}")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
