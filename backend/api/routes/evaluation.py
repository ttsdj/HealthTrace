from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from backend.env import PROJECT_ROOT
from backend.infra.auth import require_admin
from backend.db.models import User


class EvaluationSummary(BaseModel):
    has_results: bool
    result_file: str
    case_count: int = 0
    avg_recall_at_k: float | None = None
    avg_hit_rate: float | None = None
    avg_mrr: float | None = None
    metrics: list[str]
    message: str = ""


router = APIRouter(tags=["evaluation"])


def _avg(values: list[float | None]) -> float | None:
    valid = [float(item) for item in values if item is not None]
    return round(sum(valid) / len(valid), 4) if valid else None


@router.get("/evaluation/summary", response_model=EvaluationSummary)
async def evaluation_summary(_: User = Depends(require_admin)):
    result_path = PROJECT_ROOT / "data" / "eval_results.json"
    metrics = ["recall_at_k", "hit_rate", "mrr", "groundedness_proxy"]
    if not result_path.is_file():
        return EvaluationSummary(
            has_results=False,
            result_file=str(result_path),
            metrics=metrics,
            message="尚未运行评估脚本：python scripts/evaluate_rag.py --cases data/eval_cases.jsonl --top-k 5",
        )

    try:
        rows = json.loads(result_path.read_text(encoding="utf-8"))
        if not isinstance(rows, list):
            rows = []
    except (OSError, json.JSONDecodeError):
        rows = []

    return EvaluationSummary(
        has_results=bool(rows),
        result_file=str(result_path),
        case_count=len(rows),
        avg_recall_at_k=_avg([row.get("recall_at_k") for row in rows if isinstance(row, dict)]),
        avg_hit_rate=_avg([row.get("hit_rate") for row in rows if isinstance(row, dict)]),
        avg_mrr=_avg([row.get("mrr") for row in rows if isinstance(row, dict)]),
        metrics=metrics,
        message="评估结果来自 data/eval_results.json",
    )
