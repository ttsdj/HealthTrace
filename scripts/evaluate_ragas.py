"""Run checkpointed RAGAS metrics over a persisted RAGCare experiment."""
from __future__ import annotations

import argparse
import asyncio
import importlib.metadata
import json
import os
import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.evaluation.ragas_evaluator import (  # noqa: E402
    RAGAS_METRIC_NAMES,
    RagasMetricSuite,
    RagasSettings,
)
from backend.evaluation.ragas_runner import (  # noqa: E402
    case_key,
    read_jsonl,
    select_records,
    write_ragas_outputs,
)


def _csv_list(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


async def _run(
    records: list[dict],
    *,
    suite: RagasMetricSuite,
    checkpoint_path: Path,
    resume: bool,
    concurrency: int,
) -> list[dict]:
    completed = read_jsonl(checkpoint_path) if resume else []
    completed_by_key = {case_key(record): record for record in completed}
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    mode = "a" if resume and checkpoint_path.exists() else "w"

    pending = [record for record in records if case_key(record) not in completed_by_key]
    print(
        f"RAGAS selected={len(records)} completed={len(completed_by_key)} "
        f"pending={len(pending)}",
        flush=True,
    )
    semaphore = asyncio.Semaphore(max(concurrency, 1))

    async def evaluate(record: dict) -> tuple[dict, dict]:
        async with semaphore:
            try:
                ragas_result = await suite.evaluate_case(record)
            except Exception as exc:
                ragas_result = {
                    "scores": {},
                    "ragas_quality_score": None,
                    "reasons": {},
                    "errors": {"case": f"{type(exc).__name__}: {exc}"[:1000]},
                    "durations": {},
                }
            return record, ragas_result

    tasks = [asyncio.create_task(evaluate(record)) for record in pending]
    with checkpoint_path.open(mode, encoding="utf-8", newline="\n") as handle:
        for index, task in enumerate(asyncio.as_completed(tasks), start=1):
            record, ragas_result = await task
            output = {
                "query_id": record.get("query_id"),
                "row_idx": record.get("row_idx"),
                "subset": record.get("subset"),
                "baseline": record.get("baseline"),
                "ragas": ragas_result,
            }
            handle.write(json.dumps(output, ensure_ascii=False) + "\n")
            handle.flush()
            completed_by_key[case_key(output)] = output
            print(
                f"RAGAS {index}/{len(pending)} "
                f"{output['baseline']} {output['query_id']} "
                f"score={ragas_result.get('ragas_quality_score')} "
                f"errors={len(ragas_result.get('errors') or {})}",
                flush=True,
            )
    return [completed_by_key[case_key(record)] for record in records]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-experiment",
        default="pilot-100-bge-m3-3baseline-v2",
    )
    parser.add_argument(
        "--baselines",
        type=_csv_list,
        default=["bm25", "dense", "hybrid"],
    )
    parser.add_argument(
        "--metrics",
        type=_csv_list,
        default=list(RAGAS_METRIC_NAMES),
    )
    parser.add_argument("--max-cases-per-baseline", type=int, default=0)
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--resume", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-id", default="")
    args = parser.parse_args()

    unknown = set(args.metrics) - set(RAGAS_METRIC_NAMES)
    if unknown:
        parser.error(f"Unsupported metrics: {sorted(unknown)}")
    if args.max_cases_per_baseline < 0:
        parser.error("--max-cases-per-baseline must be >= 0")
    if args.concurrency < 1:
        parser.error("--concurrency must be >= 1")

    source_dir = (
        PROJECT_ROOT / "data" / "ragcare" / "evaluations" / args.source_experiment
    )
    source_path = source_dir / "per_case.jsonl"
    if not source_path.exists():
        raise FileNotFoundError(f"Source experiment not found: {source_path}")
    records = select_records(
        read_jsonl(source_path),
        baselines=args.baselines,
        max_cases_per_baseline=args.max_cases_per_baseline,
    )
    if not records:
        raise RuntimeError("No generated-answer records matched the requested filters")

    output_id = args.output_id or datetime.now().strftime("ragas-%Y%m%d-%H%M%S")
    output_dir = source_dir / "ragas" / output_id
    settings = RagasSettings.from_env(cache_dir=source_dir / "ragas" / ".cache")
    checkpoint_path = output_dir / "per_case.jsonl"
    existing = read_jsonl(checkpoint_path) if args.resume else []
    existing_by_key = {case_key(record): record for record in existing}
    pending_count = sum(case_key(record) not in existing_by_key for record in records)

    if pending_count:
        suite = RagasMetricSuite(settings, metric_names=args.metrics)

        async def run_and_close() -> list[dict]:
            try:
                return await _run(
                    records,
                    suite=suite,
                    checkpoint_path=checkpoint_path,
                    resume=args.resume,
                    concurrency=args.concurrency,
                )
            finally:
                await suite.aclose()

        scored = asyncio.run(run_and_close())
        ragas_version = suite.ragas_version
    else:
        print(
            f"RAGAS selected={len(records)} completed={len(existing_by_key)} "
            "pending=0",
            flush=True,
        )
        scored = [existing_by_key[case_key(record)] for record in records]
        ragas_version = importlib.metadata.version("ragas")

    config = {
        "source_experiment": args.source_experiment,
        "output_id": output_id,
        "cases": len(scored),
        "baselines": args.baselines,
        "metrics": args.metrics,
        "ragas_version": ragas_version,
        "judge_model": settings.judge_model,
        "embedding_model": settings.embedding_model,
        "embedding_device": settings.embedding_device,
        "resume": args.resume,
        "concurrency": args.concurrency,
    }
    summary = write_ragas_outputs(
        output_dir,
        config=config,
        records=scored,
        metric_names=tuple(args.metrics),
    )
    print(f"Wrote RAGAS outputs to {output_dir}", flush=True)
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)


if __name__ == "__main__":
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")
    main()
