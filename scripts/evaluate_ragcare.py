"""Prepare, index, and evaluate the four RAGCare-QA retrieval baselines."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from backend.env import load_env

load_env()

from backend.evaluation.ragcare_dataset import (
    DEFAULT_SEED,
    prepare_ragcare_dataset,
    read_jsonl,
)
from backend.evaluation.ragcare_judges import (
    DeepSeekEvaluator,
    OllamaJudge,
)
from backend.evaluation.ragcare_retrieval import (
    BASELINE_NAMES,
    EvalMilvusSettings,
    LocalBgeReranker,
    RagcareEvalStore,
)
from backend.evaluation.ragcare_runner import (
    base_config,
    run_experiment,
    write_experiment_outputs,
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data" / "ragcare"


def _parse_baselines(value: str) -> list[str]:
    selected = [item.strip() for item in value.split(",") if item.strip()]
    unknown = [item for item in selected if item not in BASELINE_NAMES]
    if unknown:
        raise argparse.ArgumentTypeError(f"Unsupported baselines: {unknown}")
    return selected


def _load_cases(processed_dir: Path, stage: str) -> list[dict]:
    filename = {
        "pilot": "pilot.jsonl",
        "heldout": "heldout.jsonl",
        "full": "all.jsonl",
    }[stage]
    cases = read_jsonl(processed_dir / filename)
    pilot_ids = {
        item["query_id"] for item in read_jsonl(processed_dir / "pilot.jsonl")
    }
    for case in cases:
        case["subset"] = (
            "pilot" if case["query_id"] in pilot_ids else "heldout"
        )
    return cases


def _validate_no_label_leakage(chunks: list[dict]) -> None:
    forbidden = {"question", "answer", "text_answer", "gold_answer", "gold_text_answer"}
    for chunk in chunks:
        leaked = forbidden & set(chunk)
        if leaked:
            raise RuntimeError(
                f"Label leakage detected in corpus chunk {chunk.get('chunk_id')}: {leaked}"
            )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=["pilot", "heldout", "full"],
        default="pilot",
    )
    parser.add_argument(
        "--baselines",
        type=_parse_baselines,
        default=list(BASELINE_NAMES),
        help="Comma-separated: bm25,dense,hybrid,hybrid_rerank",
    )
    parser.add_argument("--judge", choices=["deepseek", "none"], default="deepseek")
    parser.add_argument(
        "--audit-judge",
        choices=["ollama", "none"],
        default="none",
    )
    parser.add_argument("--audit-ratio", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--pilot-size", type=int, default=100)
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--candidate-k", type=int, default=20)
    parser.add_argument("--rrf-k", type=int, default=60)
    parser.add_argument("--chunk-size", type=int, default=800)
    parser.add_argument("--chunk-overlap", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=8)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument("--force-prepare", action="store_true")
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--index-only", action="store_true")
    parser.add_argument("--allow-hash-smoke", action="store_true")
    parser.add_argument(
        "--collection",
        default=os.getenv("RAGCARE_EVAL_COLLECTION", "med_ragcare_eval_v1"),
    )
    parser.add_argument(
        "--reranker-model",
        default=os.getenv("EVAL_RERANK_MODEL", "BAAI/bge-reranker-v2-m3"),
    )
    parser.add_argument(
        "--experiment-id",
        default="",
    )
    args = parser.parse_args()

    if not 0.0 <= args.audit_ratio <= 1.0:
        parser.error("--audit-ratio must be between 0 and 1")
    if args.top_k != 5:
        parser.error("This benchmark contract requires --top-k 5")
    if args.candidate_k < args.top_k:
        parser.error("--candidate-k must be >= --top-k")

    embedding_backend = os.getenv("EMBEDDING_BACKEND", "bge").strip().lower()
    if embedding_backend == "hash" and not args.allow_hash_smoke:
        parser.error(
            "Formal RAGCare evaluation requires BGE-M3. "
            "Unset EMBEDDING_BACKEND or use --allow-hash-smoke only for development."
        )

    raw_path = DEFAULT_DATA_ROOT / "raw" / "ragcare_qa_420.jsonl"
    processed_dir = DEFAULT_DATA_ROOT / "processed"
    manifest_path = processed_dir / "manifest.json"
    if args.force_prepare or not manifest_path.exists():
        manifest = prepare_ragcare_dataset(
            raw_path,
            processed_dir,
            force_download=args.force_prepare,
            seed=args.seed,
            pilot_size=args.pilot_size,
            chunk_size=args.chunk_size,
            chunk_overlap=args.chunk_overlap,
        )
    else:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    print(
        "Prepared dataset: "
        f"rows={manifest['rows']} documents={manifest['corpus_documents']} "
        f"chunks={manifest['leaf_chunks']} pilot={manifest['pilot_cases']} "
        f"heldout={manifest['heldout_cases']}"
    )
    if args.prepare_only:
        return

    chunks = read_jsonl(processed_dir / "chunks.jsonl")
    _validate_no_label_leakage(chunks)
    settings = EvalMilvusSettings.from_env(args.collection)
    store = RagcareEvalStore(settings=settings)
    expected_rows = len(chunks)
    current_rows = store.row_count()
    if current_rows not in (0, expected_rows) and not args.rebuild_index:
        raise RuntimeError(
            f"Collection {args.collection} contains {current_rows} rows, expected "
            f"{expected_rows}. Re-run with --rebuild-index."
        )
    if current_rows == 0 or args.rebuild_index:
        embedding_label = "hash smoke" if embedding_backend == "hash" else "BGE-M3"
        chunks_by_length = sorted(
            chunks,
            key=lambda item: len(str(item.get("text") or "")),
        )
        inserted = store.index_chunks(
            chunks_by_length,
            dense_dim=int(os.getenv("DENSE_EMBEDDING_DIM", "1024")),
            batch_size=args.batch_size,
            rebuild=args.rebuild_index,
            progress_callback=lambda completed, total: print(
                f"Indexing {embedding_label} chunks: {completed}/{total}",
                flush=True,
            ),
        )
        print(f"Indexed {inserted} chunks into {args.collection}")
    else:
        print(f"Reusing {current_rows} indexed chunks in {args.collection}")
    if args.index_only:
        return

    cases = _load_cases(processed_dir, args.stage)
    if args.max_cases > 0:
        cases = cases[: args.max_cases]
    if not cases:
        raise RuntimeError("No evaluation cases selected")

    reranker = (
        LocalBgeReranker(model_name=args.reranker_model)
        if "hybrid_rerank" in args.baselines
        else None
    )
    evaluator = DeepSeekEvaluator() if args.judge == "deepseek" else None
    audit_judge = OllamaJudge() if args.audit_judge == "ollama" else None
    if evaluator is None and args.audit_judge != "none":
        parser.error("--audit-judge requires --judge deepseek")

    experiment_id = args.experiment_id or datetime.now().strftime(
        f"%Y%m%d-%H%M%S-{args.stage}"
    )
    output_dir = DEFAULT_DATA_ROOT / "evaluations" / experiment_id
    config = {
        **base_config(),
        "experiment_id": experiment_id,
        "stage": args.stage,
        "case_count": len(cases),
        "baselines": args.baselines,
        "collection": args.collection,
        "dataset_manifest": manifest,
        "seed": args.seed,
        "top_k": args.top_k,
        "metric_max_k": max(args.top_k, 10),
        "candidate_k": args.candidate_k,
        "rrf_k": args.rrf_k,
        "embedding_backend": embedding_backend,
        "embedding_model": os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
        "reranker_model": args.reranker_model,
        "judge": args.judge,
        "judge_model": os.getenv("EVAL_JUDGE_MODEL")
        or os.getenv("MODEL", "deepseek-v4-flash"),
        "audit_judge": args.audit_judge,
        "audit_model": os.getenv("OLLAMA_JUDGE_MODEL", "qwen2.5:7b"),
        "audit_ratio": args.audit_ratio,
        "workers": args.workers,
    }
    records = run_experiment(
        cases=cases,
        baselines=args.baselines,
        store=store,
        reranker=reranker,
        evaluator=evaluator,
        audit_judge=audit_judge,
        audit_ratio=args.audit_ratio,
        audit_seed=args.seed,
        top_k=args.top_k,
        candidate_k=args.candidate_k,
        rrf_k=args.rrf_k,
        workers=args.workers,
        generate_answers=evaluator is not None,
    )
    outputs = write_experiment_outputs(
        output_dir,
        config=config,
        records=records,
        baselines=args.baselines,
    )
    print(f"Wrote evaluation outputs to {output_dir}")
    print(json.dumps(outputs["summary"].get("all", {}), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
