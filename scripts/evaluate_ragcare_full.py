"""Reproducible RAGCare-QA retrieval experiment runner.

Runs a single retrieval baseline (dense / bm25 / hybrid / hybrid_rerank) against a
prepared RAGCare-QA dataset, producing per-question ranks and gold-document recall
metrics, and emitting a uniform, reproducible ``MANIFEST.json`` artifact.

The artifact records everything required to trust the numbers: data version + hash,
experiment config (model / temperature / top_k / mode), per-question ranks & scores,
statistical definitions, the exact reproduce command, and the git commit.

Examples
--------
    .venv\\Scripts\\python.exe scripts\\evaluate_ragcare_full.py --mode dense --top-k 5
    .venv\\Scripts\\python.exe scripts\\evaluate_ragcare_full.py --mode hybrid_rerank --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Make the project importable when run as a script.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.env import load_env  # noqa: E402

load_env()

from backend.evaluation.eval_artifact import (  # noqa: E402
    build_artifact,
    capture_command,
    capture_git_commit,
    hash_dataset_manifest,
    standard_statistical_definitions,
    write_artifact,
)
from backend.evaluation.ragcare_dataset import (  # noqa: E402
    DEFAULT_SEED,
    read_jsonl,
)
from backend.evaluation.ragcare_retrieval import (  # noqa: E402
    BASELINE_NAMES,
    EvalMilvusSettings,
    LocalBgeReranker,
    RagcareEvalStore,
)
from backend.evaluation.ragcare_runner import (  # noqa: E402
    base_config,
    run_experiment,
    write_experiment_outputs,
)

DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "ragcare"
DEFAULT_OUTPUT_ROOT = PROJECT_ROOT / "data" / "evaluations" / "ragcare"

SUPPORTED_DATASETS = ("pilot", "heldout", "full")

# Metrics surfaced in the artifact summary (retrieval-only baseline).
_METRIC_KEYS = (
    "recall_at_1",
    "recall_at_5",
    "recall_at_10",
    "precision_at_5",
    "f1_at_5",
    "mrr",
    "ndcg_at_5",
)


def _load_cases(processed_dir: Path, dataset: str) -> list[dict]:
    filename = {"pilot": "pilot.jsonl", "heldout": "heldout.jsonl", "full": "all.jsonl"}[dataset]
    cases = read_jsonl(processed_dir / filename)
    pilot_ids = {
        item["query_id"] for item in read_jsonl(processed_dir / "pilot.jsonl")
    }
    for case in cases:
        case["subset"] = "pilot" if case["query_id"] in pilot_ids else "heldout"
    return cases


def _build_per_question(records: list[dict], mode: str) -> list[dict]:
    """Slim per-question records: ranks, gold ids, and retrieval metrics."""
    per_question: list[dict] = []
    for record in records:
        if record.get("baseline") != mode:
            continue
        ranked_docs = record.get("retrieval_ranking") or record.get("retrieved_docs") or []
        per_question.append(
            {
                "query_id": record.get("query_id"),
                "row_idx": record.get("row_idx"),
                "subset": record.get("subset"),
                "baseline": mode,
                "question": record.get("question"),
                "ranked_document_ids": [
                    str(doc.get("document_id") or "").strip()
                    for doc in ranked_docs
                    if str(doc.get("document_id") or "").strip()
                ],
                "gold_document_ids": list(record.get("gold_document_ids") or []),
                "retrieval_metrics": dict(record.get("retrieval_metrics") or {}),
                "retrieval_seconds": record.get("retrieval_seconds"),
            }
        )
    return per_question


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run a reproducible RAGCare-QA retrieval baseline and emit a MANIFEST artifact."
    )
    parser.add_argument(
        "--dataset",
        choices=SUPPORTED_DATASETS,
        default="full",
        help="Which prepared RAGCare-QA split to evaluate (default: full).",
    )
    parser.add_argument(
        "--mode",
        choices=BASELINE_NAMES,
        required=True,
        help="Retrieval baseline to run: dense, bm25, hybrid, or hybrid_rerank.",
    )
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument(
        "--collection",
        default=os.getenv("RAGCARE_EVAL_COLLECTION", "med_ragcare_eval_v1"),
    )
    parser.add_argument(
        "--reranker-model",
        default=os.getenv("EVAL_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
    )
    parser.add_argument(
        "--model",
        default=os.getenv("EVAL_JUDGE_MODEL") or os.getenv("MODEL", "deepseek-v4-flash"),
        help="Model used for generation/judging (recorded for reproducibility).",
    )
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument(
        "--base-url",
        default=os.getenv("BASE_URL", "https://api.deepseek.com"),
        help="Model base URL (recorded for reproducibility).",
    )
    parser.add_argument(
        "--out-dir",
        default="",
        help="Output directory. Default: data/evaluations/ragcare/<experiment-id>/.",
    )
    parser.add_argument("--experiment-id", default="")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Emit a schema-only MANIFEST.json with NO retrieval/LLM run and NO metric numbers.",
    )
    args = parser.parse_args()

    if args.top_k <= 0:
        parser.error("--top-k must be positive")
    if args.candidate_k < args.top_k:
        parser.error("--candidate-k must be >= --top-k")
    if not -1.0 <= args.temperature <= 2.0:
        parser.error("--temperature must be in [-1, 2]")

    processed_dir = DEFAULT_DATA_ROOT / "processed"
    manifest_path = processed_dir / "manifest.json"
    if not manifest_path.exists():
        parser.error(
            f"RAGCare-QA dataset not prepared: {manifest_path}. "
            "Run scripts/evaluate_ragcare.py first."
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    dataset_hash = hash_dataset_manifest(manifest)

    experiment_id = args.experiment_id or datetime.now().strftime(
        f"%Y%m%d-%H%M%S-ragcare-{args.mode}"
    )
    out_dir = Path(args.out_dir) if args.out_dir else DEFAULT_OUTPUT_ROOT / experiment_id

    config = {
        **base_config(),
        "experiment_id": experiment_id,
        "dataset": args.dataset,
        "dataset_version": manifest.get("dataset_id", "ragcare"),
        "dataset_hash": dataset_hash,
        "baseline": args.mode,
        "collection": args.collection,
        "case_count": None,  # filled before reporting
        "seed": args.seed,
        "top_k": args.top_k,
        "metric_max_k": max(args.top_k, 10),
        "candidate_k": args.candidate_k,
        "rrf_k": args.rrf_k,
        "workers": args.workers,
        "model": args.model,
        "temperature": args.temperature,
        "base_url": args.base_url,
        "embedding_backend": os.getenv("EMBEDDING_BACKEND", "bge"),
        "embedding_model": os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
        "reranker_model": args.reranker_model,
        "retrieval_only": True,
    }

    command = capture_command()
    commit = capture_git_commit(PROJECT_ROOT)

    if args.dry_run:
        # Schema-only artifact: NO retrieval, NO LLM, NO metric numbers.
        artifact = build_artifact(
            dataset_hash=dataset_hash,
            config=config,
            per_question=[],
            summary={"cases": 0, **{key: None for key in _METRIC_KEYS}},
            statistical_definitions=standard_statistical_definitions(),
            command=command,
            commit=commit,
            notes=(
                "DRY_RUN: no retrieval or LLM was performed. This is an empty "
                "artifact validating the schema/contract only. No metric numbers "
                "are present."
            ),
        )
        path = write_artifact(artifact, out_dir)
        print(f"Dry run wrote schema-only artifact to {path}")
        print("No retrieval/LLM was performed; no metric numbers were produced.")
        return

    cases = _load_cases(processed_dir, args.dataset)
    if args.max_cases > 0:
        cases = cases[: args.max_cases]
    if not cases:
        parser.error("No evaluation cases selected")
    config["case_count"] = len(cases)

    settings = EvalMilvusSettings.from_env(args.collection)
    store = RagcareEvalStore(settings=settings)
    reranker = (
        LocalBgeReranker(model_name=args.reranker_model)
        if args.mode == "hybrid_rerank"
        else None
    )

    records = run_experiment(
        cases=cases,
        baselines=[args.mode],
        store=store,
        reranker=reranker,
        evaluator=None,
        audit_judge=None,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        rrf_k=args.rrf_k,
        workers=args.workers,
        generate_answers=False,
    )

    # Reuse the existing output writers for config/per_case/baseline_summary/report.
    write_experiment_outputs(
        out_dir,
        config=config,
        records=records,
        baselines=[args.mode],
    )

    per_question = _build_per_question(records, args.mode)
    artifact = build_artifact(
        dataset_hash=dataset_hash,
        config=config,
        per_question=per_question,
        metric_keys=_METRIC_KEYS,
        statistical_definitions=standard_statistical_definitions(),
        command=command,
        commit=commit,
    )
    manifest_path = write_artifact(artifact, out_dir)

    print(f"Wrote RAGCare evaluation outputs to {out_dir}")
    print(f"Wrote reproducibility artifact to {manifest_path}")
    print(json.dumps(artifact["summary"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
