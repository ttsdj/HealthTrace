"""Deterministic retrieval and answer metrics for RAGCare-QA."""
from __future__ import annotations

import math
import re
from collections import Counter
from typing import Iterable


def _normalize_answer(value: str) -> str:
    value = (value or "").lower().strip()
    value = re.sub(r"\s+", " ", value)
    return re.sub(r"[^\w\s]", "", value, flags=re.UNICODE).strip()


def _answer_tokens(value: str) -> list[str]:
    normalized = _normalize_answer(value)
    return re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", normalized)


def _unique_ranked_document_ids(retrieved_docs: Iterable[dict]) -> list[str]:
    seen: set[str] = set()
    ranked: list[str] = []
    for doc in retrieved_docs:
        document_id = str(doc.get("document_id") or "").strip()
        if not document_id or document_id in seen:
            continue
        seen.add(document_id)
        ranked.append(document_id)
    return ranked


def retrieval_metrics_at_k(
    retrieved_docs: Iterable[dict],
    gold_document_ids: Iterable[str],
    *,
    k: int = 5,
) -> dict:
    ranked = _unique_ranked_document_ids(retrieved_docs)
    gold = {str(item).strip() for item in gold_document_ids if str(item).strip()}
    top_k = ranked[:k]
    hits = [document_id for document_id in top_k if document_id in gold]

    recall = len(set(hits)) / len(gold) if gold else 0.0
    precision = len(hits) / k if k > 0 else 0.0
    f1 = (
        2 * precision * recall / (precision + recall)
        if precision + recall > 0
        else 0.0
    )
    first_rank = next(
        (rank for rank, document_id in enumerate(ranked, 1) if document_id in gold),
        None,
    )
    dcg = sum(
        1.0 / math.log2(rank + 1)
        for rank, document_id in enumerate(top_k, 1)
        if document_id in gold
    )
    ideal_hits = min(len(gold), k)
    idcg = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))

    result = {
        f"recall_at_{k}": round(recall, 6),
        f"precision_at_{k}": round(precision, 6),
        f"f1_at_{k}": round(f1, 6),
        "mrr": round(1.0 / first_rank, 6) if first_rank else 0.0,
        f"ndcg_at_{k}": round(dcg / idcg, 6) if idcg else 0.0,
        "first_relevant_rank": first_rank,
        "matched_document_ids": hits,
        "ranked_document_ids": ranked,
    }
    for cutoff in (1, 3, 5, 10):
        cutoff_hits = set(ranked[:cutoff]) & gold
        result[f"recall_at_{cutoff}"] = round(
            len(cutoff_hits) / len(gold) if gold else 0.0,
            6,
        )
    return result


def answer_token_f1(prediction: str, reference: str) -> float:
    predicted = _answer_tokens(prediction)
    expected = _answer_tokens(reference)
    if not predicted or not expected:
        return 1.0 if predicted == expected else 0.0
    overlap = Counter(predicted) & Counter(expected)
    common = sum(overlap.values())
    if not common:
        return 0.0
    precision = common / len(predicted)
    recall = common / len(expected)
    return round(2 * precision * recall / (precision + recall), 6)


def choice_accuracy(prediction: str, gold_answer: str) -> float | None:
    gold = _normalize_answer(gold_answer)
    if not gold:
        return None
    gold_choice = re.fullmatch(r"([a-z])", gold)
    if not gold_choice:
        return None
    predicted = _normalize_answer(prediction)
    match = re.search(r"(?:^|\s)([a-z])(?:\s|$)", predicted)
    if not match:
        match = re.search(r"\b(?:answer|option)\s*(?:is|:)?\s*([a-z])\b", predicted)
    return 1.0 if match and match.group(1) == gold_choice.group(1) else 0.0


def mean_metric(rows: Iterable[dict], key: str) -> float | None:
    values = [float(row[key]) for row in rows if row.get(key) is not None]
    return round(sum(values) / len(values), 6) if values else None
