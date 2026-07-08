"""Small, dependency-free RAG evaluation metrics."""

from __future__ import annotations

import re
from typing import Iterable


def _norm(value: str) -> str:
    return re.sub(r"\s+", "", (value or "").lower())


def _source_key(doc: dict) -> str:
    return "|".join(
        _norm(str(doc.get(key, "")))
        for key in ("filename", "page_number", "chunk_id")
        if doc.get(key) not in (None, "")
    )


def _matches_expected(doc: dict, expected: str) -> bool:
    needle = _norm(expected)
    if not needle:
        return False
    haystacks = [
        _source_key(doc),
        _norm(doc.get("filename", "")),
        _norm(doc.get("chunk_id", "")),
        _norm(doc.get("text", "")),
    ]
    return any(needle in item or item in needle for item in haystacks if item)


def retrieval_metrics(retrieved_docs: Iterable[dict], expected_sources: Iterable[str]) -> dict:
    docs = list(retrieved_docs)
    expected = [item for item in expected_sources if item]
    if not expected:
        return {
            "recall_at_k": None,
            "hit_rate": None,
            "mrr": None,
            "matched_expected": [],
        }

    matched_expected: list[str] = []
    first_rank: int | None = None
    for rank, doc in enumerate(docs, 1):
        for item in expected:
            if item in matched_expected:
                continue
            if _matches_expected(doc, item):
                matched_expected.append(item)
                if first_rank is None:
                    first_rank = rank

    return {
        "recall_at_k": len(matched_expected) / len(expected),
        "hit_rate": 1.0 if matched_expected else 0.0,
        "mrr": (1.0 / first_rank) if first_rank else 0.0,
        "matched_expected": matched_expected,
    }


def groundedness_proxy(answer: str, retrieved_docs: Iterable[dict]) -> float:
    answer_tokens = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", answer or ""))
    if not answer_tokens:
        return 0.0
    context = "\n".join(doc.get("text", "") for doc in retrieved_docs)
    context_tokens = set(re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", context))
    if not context_tokens:
        return 0.0
    overlap = answer_tokens & context_tokens
    return round(len(overlap) / len(answer_tokens), 4)


def evaluate_retrieval_case(
    question: str,
    retrieved_docs: Iterable[dict],
    expected_sources: Iterable[str] | None = None,
    answer: str = "",
) -> dict:
    docs = list(retrieved_docs)
    return {
        "question": question,
        "retrieved_count": len(docs),
        **retrieval_metrics(docs, expected_sources or []),
        "groundedness_proxy": groundedness_proxy(answer, docs) if answer else None,
    }
