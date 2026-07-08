"""Checkpointed execution and reporting for RAGAS experiment scores."""
from __future__ import annotations

import csv
import json
import math
from collections import defaultdict
from pathlib import Path
from statistics import mean

from backend.evaluation.ragas_evaluator import RAGAS_METRIC_NAMES


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def case_key(record: dict) -> str:
    return f"{record.get('query_id')}::{record.get('baseline')}"


def select_records(
    records: list[dict],
    *,
    baselines: list[str],
    max_cases_per_baseline: int = 0,
) -> list[dict]:
    selected: list[dict] = []
    counts: dict[str, int] = defaultdict(int)
    for record in sorted(
        records,
        key=lambda item: (int(item.get("row_idx", 0)), str(item.get("baseline"))),
    ):
        baseline = str(record.get("baseline") or "")
        if baseline not in baselines:
            continue
        if not record.get("generated_answer"):
            continue
        if max_cases_per_baseline and counts[baseline] >= max_cases_per_baseline:
            continue
        selected.append(record)
        counts[baseline] += 1
    return selected


def _mean(values: list[float]) -> float | None:
    finite = [float(value) for value in values if math.isfinite(float(value))]
    return round(mean(finite), 6) if finite else None


def summarize_ragas(records: list[dict], metric_names=RAGAS_METRIC_NAMES) -> dict:
    grouped: dict[str, list[dict]] = defaultdict(list)
    for record in records:
        grouped[str(record.get("baseline") or "unknown")].append(record)

    summary: dict[str, dict] = {}
    for baseline, rows in sorted(grouped.items()):
        result = {
            "cases": len(rows),
            "complete_cases": sum(not row.get("ragas", {}).get("errors") for row in rows),
            "cases_with_errors": sum(bool(row.get("ragas", {}).get("errors")) for row in rows),
        }
        for metric in metric_names:
            values = [
                row["ragas"]["scores"][metric]
                for row in rows
                if metric in row.get("ragas", {}).get("scores", {})
            ]
            result[metric] = _mean(values)
            result[f"{metric}_valid_cases"] = len(values)
        result["ragas_quality_score"] = _mean(
            [
                row["ragas"]["ragas_quality_score"]
                for row in rows
                if row.get("ragas", {}).get("ragas_quality_score") is not None
            ]
        )
        summary[baseline] = result
    return summary

def write_ragas_outputs(
    output_dir: Path,
    *,
    config: dict,
    records: list[dict],
    metric_names: tuple[str, ...],
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_ragas(records, metric_names)
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    fields = [
        "baseline",
        "cases",
        "complete_cases",
        "cases_with_errors",
        *metric_names,
        "ragas_quality_score",
    ]
    with (output_dir / "summary.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for baseline, values in summary.items():
            writer.writerow(
                {
                    "baseline": baseline,
                    **{field: values.get(field) for field in fields[1:]},
                }
            )

    lines = [
        "# RAGAS Evaluation",
        "",
        f"- Source experiment: `{config['source_experiment']}`",
        f"- RAGAS: `{config['ragas_version']}`",
        f"- Judge: `{config['judge_model']}`",
        f"- Embedding: `{config['embedding_model']}`",
        "",
        "| Baseline | Cases | Context precision | Context recall | Faithfulness | Answer relevancy | Factual correctness | Overall | Errors |",
        "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for baseline, values in summary.items():
        lines.append(
            "| {baseline} | {cases} | {context_precision} | {context_recall} | "
            "{faithfulness} | {answer_relevancy} | {factual_correctness} | "
            "{overall} | {errors} |".format(
                baseline=baseline,
                cases=values["cases"],
                context_precision=values.get("context_precision"),
                context_recall=values.get("context_recall"),
                faithfulness=values.get("faithfulness"),
                answer_relevancy=values.get("answer_relevancy"),
                factual_correctness=values.get("factual_correctness"),
                overall=values.get("ragas_quality_score"),
                errors=values["cases_with_errors"],
            )
        )
    lines.extend(
        [
            "",
            "All metrics are macro averages over valid cases and range from 0 to 1.",
            "The overall score is an equal-weight mean of the available five metrics.",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
