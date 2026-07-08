"""Compare retrieval settings for a small evaluation JSONL."""

from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path

from backend.env import load_env
from backend.evaluation.metrics import retrieval_metrics
from backend.rag.utils import retrieve_documents

load_env()


def _iter_cases(path: Path):
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        if line.strip():
            yield json.loads(line)


def _avg(values):
    values = [value for value in values if value is not None]
    return sum(values) / len(values) if values else None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", required=True)
    parser.add_argument("--top-k", default="3,5,8")
    parser.add_argument("--candidate-k", default="0,10,20")
    parser.add_argument("--output", default="data/rerank_param_results.json")
    args = parser.parse_args()

    cases = list(_iter_cases(Path(args.cases)))
    top_ks = [int(item) for item in args.top_k.split(",") if item.strip()]
    candidate_ks = [int(item) for item in args.candidate_k.split(",") if item.strip()]
    results = []

    original_candidate_k = os.getenv("RETRIEVAL_CANDIDATE_K", "")
    try:
        for top_k in top_ks:
            for candidate_k in candidate_ks:
                if candidate_k > 0:
                    os.environ["RETRIEVAL_CANDIDATE_K"] = str(candidate_k)
                else:
                    os.environ.pop("RETRIEVAL_CANDIDATE_K", None)

                recalls = []
                hit_rates = []
                mrrs = []
                started = time.perf_counter()
                for case in cases:
                    retrieved = retrieve_documents(case["question"], top_k=top_k)
                    metrics = retrieval_metrics(
                        retrieved.get("docs", []),
                        case.get("expected_sources", []),
                    )
                    recalls.append(metrics["recall_at_k"])
                    hit_rates.append(metrics["hit_rate"])
                    mrrs.append(metrics["mrr"])

                elapsed = time.perf_counter() - started
                row = {
                    "top_k": top_k,
                    "candidate_k": candidate_k or "multiplier",
                    "case_count": len(cases),
                    "avg_recall_at_k": _avg(recalls),
                    "avg_hit_rate": _avg(hit_rates),
                    "avg_mrr": _avg(mrrs),
                    "elapsed_seconds": round(elapsed, 3),
                }
                results.append(row)
                print(json.dumps(row, ensure_ascii=False))
    finally:
        if original_candidate_k:
            os.environ["RETRIEVAL_CANDIDATE_K"] = original_candidate_k
        else:
            os.environ.pop("RETRIEVAL_CANDIDATE_K", None)

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Wrote {len(results)} rows to {out_path}")


if __name__ == "__main__":
    main()
