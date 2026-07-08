"""RAGCare-QA experiment orchestration, aggregation, and reports."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Iterable

from backend.evaluation.ragcare_judges import (
    GENERATION_PROMPT_VERSION,
    JUDGE_PROMPT_VERSION,
)
from backend.evaluation.ragcare_metrics import (
    answer_token_f1,
    choice_accuracy,
    mean_metric,
    retrieval_metrics_at_k,
)
from backend.evaluation.ragcare_retrieval import BASELINE_NAMES


def _write_json(path: Path, payload: object) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _audit_selected(query_id: str, *, seed: int, ratio: float) -> bool:
    value = hashlib.sha256(f"{seed}:{query_id}".encode("utf-8")).digest()
    fraction = int.from_bytes(value[:8], "big") / (2**64 - 1)
    return fraction < ratio


def _pearson(xs: list[float], ys: list[float]) -> float | None:
    if len(xs) < 2 or len(xs) != len(ys):
        return None
    mean_x = sum(xs) / len(xs)
    mean_y = sum(ys) / len(ys)
    numerator = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    denominator = math.sqrt(
        sum((x - mean_x) ** 2 for x in xs)
        * sum((y - mean_y) ** 2 for y in ys)
    )
    return round(numerator / denominator, 6) if denominator else None


def _run_generation_and_judging(
    record: dict,
    *,
    evaluator,
    audit_judge,
    audit_ratio: float,
    audit_seed: int,
) -> dict:
    started = time.perf_counter()
    try:
        answer = evaluator.generate_answer(record["question"], record["retrieved_docs"])
        judge = evaluator.score(
            record["question"],
            record["retrieved_docs"],
            answer,
        )
        record["generated_answer"] = answer
        record["answer_token_f1"] = answer_token_f1(
            answer,
            record.get("gold_text_answer") or record.get("gold_answer") or "",
        )
        record["choice_accuracy"] = choice_accuracy(
            answer,
            record.get("gold_answer") or "",
        )
        record["llm_judge"] = judge
        if (
            audit_judge is not None
            and record.get("subset") == "heldout"
            and _audit_selected(
                record["query_id"],
                seed=audit_seed,
                ratio=audit_ratio,
            )
        ):
            record["audit_judge"] = audit_judge.score(
                record["question"],
                record["retrieved_docs"],
                answer,
            )
    except Exception as exc:
        record["generation_judge_error"] = str(exc)[:1000]
    record["generation_judge_seconds"] = round(time.perf_counter() - started, 4)
    return record


def run_experiment(
    *,
    cases: list[dict],
    baselines: list[str],
    store,
    reranker,
    evaluator,
    audit_judge=None,
    audit_ratio: float = 0.2,
    audit_seed: int = 20260627,
    top_k: int = 5,
    candidate_k: int = 20,
    rrf_k: int = 60,
    workers: int = 4,
    generate_answers: bool = True,
) -> list[dict]:
    unknown = [name for name in baselines if name not in BASELINE_NAMES]
    if unknown:
        raise ValueError(f"Unsupported baselines: {unknown}")

    records: list[dict] = []
    needs_dense = any(name != "bm25" for name in baselines)
    metric_max_k = max(top_k, 10)
    for case in cases:
        dense_embedding = (
            store.embed_query(case["question"]) if needs_dense else None
        )
        for baseline in baselines:
            started = time.perf_counter()
            result = store.retrieve(
                case["question"],
                mode=baseline,
                top_k=metric_max_k,
                candidate_k=candidate_k,
                dense_embedding=dense_embedding,
                reranker=reranker if baseline == "hybrid_rerank" else None,
                rrf_k=rrf_k,
            )
            ranked_docs = result["docs"]
            metrics = retrieval_metrics_at_k(
                ranked_docs,
                case.get("gold_document_ids") or [],
                k=top_k,
            )
            retrieval_meta = {
                **result["meta"],
                "generation_top_k": top_k,
                "metric_max_k": metric_max_k,
            }
            records.append(
                {
                    **case,
                    "baseline": baseline,
                    "retrieved_docs": ranked_docs[:top_k],
                    "retrieval_ranking": ranked_docs,
                    "retrieval_meta": retrieval_meta,
                    "retrieval_metrics": metrics,
                    "retrieval_seconds": round(time.perf_counter() - started, 4),
                }
            )

    if not generate_answers:
        return records

    completed: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as executor:
        futures = [
            executor.submit(
                _run_generation_and_judging,
                record,
                evaluator=evaluator,
                audit_judge=audit_judge,
                audit_ratio=audit_ratio,
                audit_seed=audit_seed,
            )
            for record in records
        ]
        for future in as_completed(futures):
            completed.append(future.result())
    completed.sort(key=lambda item: (int(item.get("row_idx", 0)), item["baseline"]))
    return completed


def _flatten_metric(record: dict, key: str):
    if key in record:
        return record.get(key)
    if key in record.get("retrieval_metrics", {}):
        return record["retrieval_metrics"].get(key)
    if key in record.get("llm_judge", {}):
        return record["llm_judge"].get(key)
    return None


def summarize_records(records: list[dict], baselines: Iterable[str]) -> dict:
    metric_names = [
        "recall_at_1",
        "recall_at_3",
        "recall_at_5",
        "recall_at_10",
        "precision_at_5",
        "f1_at_5",
        "mrr",
        "ndcg_at_5",
        "answer_token_f1",
        "choice_accuracy",
        "context_relevance",
        "faithfulness",
        "answer_relevance",
        "rag_quality_score",
        "retrieval_seconds",
        "generation_judge_seconds",
    ]
    subsets = sorted({str(record.get("subset") or "all") for record in records})
    if "all" not in subsets:
        subsets.append("all")

    output: dict[str, dict] = {}
    for subset in subsets:
        output[subset] = {}
        for baseline in baselines:
            selected = [
                record
                for record in records
                if record["baseline"] == baseline
                and (subset == "all" or record.get("subset") == subset)
            ]
            summary = {
                "cases": len(selected),
                "successful_llm_judgements": sum(
                    1 for record in selected if record.get("llm_judge")
                ),
                "errors": sum(
                    1 for record in selected if record.get("generation_judge_error")
                ),
            }
            for metric in metric_names:
                values = [
                    {"value": _flatten_metric(record, metric)}
                    for record in selected
                ]
                summary[metric] = mean_metric(values, "value")
            output[subset][baseline] = summary
    return output


def judge_agreement(records: list[dict]) -> dict:
    audited = [
        record
        for record in records
        if record.get("llm_judge") and record.get("audit_judge")
    ]
    metric_names = ("context_relevance", "faithfulness", "answer_relevance")
    output = {"audited_records": len(audited), "metrics": {}}
    for metric in metric_names:
        primary = [float(record["llm_judge"][metric]) for record in audited]
        audit = [float(record["audit_judge"][metric]) for record in audited]
        differences = [abs(left - right) for left, right in zip(primary, audit)]
        output["metrics"][metric] = {
            "mean_absolute_error": (
                round(sum(differences) / len(differences), 6)
                if differences
                else None
            ),
            "pearson_correlation": _pearson(primary, audit),
            "within_0_2_rate": (
                round(
                    sum(1 for value in differences if value <= 0.2)
                    / len(differences),
                    6,
                )
                if differences
                else None
            ),
        }
    return output


def write_experiment_outputs(
    output_dir: Path,
    *,
    config: dict,
    records: list[dict],
    baselines: list[str],
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_json(output_dir / "config.json", config)

    with (output_dir / "per_case.jsonl").open(
        "w", encoding="utf-8", newline="\n"
    ) as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    summary = summarize_records(records, baselines)
    agreement = judge_agreement(records)
    _write_json(output_dir / "baseline_summary.json", summary)
    _write_json(output_dir / "judge_agreement.json", agreement)

    comparison_path = output_dir / "baseline_comparison.csv"
    fields = [
        "subset",
        "baseline",
        "cases",
        "recall_at_5",
        "precision_at_5",
        "f1_at_5",
        "mrr",
        "ndcg_at_5",
        "context_relevance",
        "faithfulness",
        "answer_relevance",
        "rag_quality_score",
        "answer_token_f1",
        "choice_accuracy",
        "retrieval_seconds",
        "generation_judge_seconds",
        "errors",
    ]
    with comparison_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for subset, baseline_rows in summary.items():
            for baseline, values in baseline_rows.items():
                writer.writerow(
                    {
                        "subset": subset,
                        "baseline": baseline,
                        **{key: values.get(key) for key in fields[2:]},
                    }
                )

    report_lines = [
        "# RAGCare-QA Baseline Evaluation",
        "",
        f"- Experiment: `{config.get('experiment_id')}`",
        f"- Stage: `{config.get('stage')}`",
        f"- Cases: `{config.get('case_count')}`",
        f"- Collection: `{config.get('collection')}`",
        f"- Embedding backend: `{config.get('embedding_backend')}`",
        f"- Embedding model: `{config.get('embedding_model')}`",
        f"- Judge: `{config.get('judge_model')}`",
        "",
        "## Baseline Comparison",
        "",
        "| Subset | Baseline | Recall@5 | Precision@5 | F1@5 | MRR | Context relevance | Faithfulness | Answer relevance | RAG quality |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |",
    ]
    for subset, baseline_rows in summary.items():
        for baseline, values in baseline_rows.items():
            report_lines.append(
                "| {subset} | {baseline} | {recall} | {precision} | {f1} | {mrr} | "
                "{context} | {faith} | {answer} | {quality} |".format(
                    subset=subset,
                    baseline=baseline,
                    recall=values.get("recall_at_5"),
                    precision=values.get("precision_at_5"),
                    f1=values.get("f1_at_5"),
                    mrr=values.get("mrr"),
                    context=values.get("context_relevance"),
                    faith=values.get("faithfulness"),
                    answer=values.get("answer_relevance"),
                    quality=values.get("rag_quality_score"),
                )
            )
    report_lines.extend(
        [
            "",
            "## Judge Agreement",
            "",
            f"- Audited records: `{agreement['audited_records']}`",
            f"- Details: `judge_agreement.json`",
            "",
            "## Limitations",
            "",
            "- DeepSeek is both generator and primary judge; deterministic gold metrics and Qwen audit reduce but do not remove self-evaluation bias.",
            "- RAGCare-QA is mainly English and does not replace a clinician-reviewed Chinese medical golden set.",
        ]
    )
    (output_dir / "report.md").write_text(
        "\n".join(report_lines) + "\n",
        encoding="utf-8",
    )
    return {"summary": summary, "agreement": agreement}


def base_config() -> dict:
    return {
        "generation_prompt_version": GENERATION_PROMPT_VERSION,
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
    }
