"""Run a small offline retrieval evaluation from a JSONL file.

Each JSONL row can contain:
{"question": "...", "expected_sources": ["filename or chunk text"], "answer": "..."}
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from backend.env import load_env
from backend.evaluation import evaluate_retrieval_case
from backend.indexing.milvus_client import get_milvus_store
from backend.rag.utils import retrieve_documents

load_env()


def _ensure_eval_collections() -> None:
    """Create empty retrieval collections so first-run evaluation is warning-free."""
    for kind in ("medical_qa", "episodic_memory", "semantic_memory"):
        get_milvus_store(kind).init_collection()


def _iter_cases(path: Path):
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if not line.strip():
            continue
        item = json.loads(line)
        if item.get("question"):
            yield item


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True, help="JSONL evaluation cases")
    parser.add_argument("--top-k", type=int, default=5)
    parser.add_argument("--output", default="data/eval_results.json")
    args = parser.parse_args()

    _ensure_eval_collections()

    results = []
    for case in _iter_cases(Path(args.cases)):
        retrieved = retrieve_documents(case["question"], top_k=args.top_k)
        docs = retrieved.get("docs", [])
        metrics = evaluate_retrieval_case(
            question=case["question"],
            retrieved_docs=docs,
            expected_sources=case.get("expected_sources", []),
            answer=case.get("answer", ""),
        )
        metrics["retrieval_meta"] = retrieved.get("meta", {})
        results.append(metrics)
        print(json.dumps(metrics, ensure_ascii=False))

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} results to {out_path}")


if __name__ == "__main__":
    main()
