"""Lightweight conflict detection for retrieved medical evidence."""

from __future__ import annotations

from collections import defaultdict
import re
from typing import Iterable


_NEGATIVE_PATTERNS = (
    "不推荐",
    "不建议",
    "不能",
    "不宜",
    "禁忌",
    "避免",
    "慎用",
    "无效",
    "无证据",
    "增加风险",
    "升高风险",
    "not recommended",
    "contraindicated",
    "avoid",
    "ineffective",
)

_POSITIVE_PATTERNS = (
    "推荐",
    "建议",
    "可以",
    "可用于",
    "适合",
    "应当",
    "有效",
    "降低风险",
    "改善",
    "recommended",
    "effective",
    "benefit",
)


def _compact(text: str, max_len: int = 90) -> str:
    compact = re.sub(r"\s+", " ", text or "").strip()
    return compact[:max_len] + ("..." if len(compact) > max_len else "")


def _polarity(text: str) -> str:
    lowered = (text or "").lower()
    has_negative = any(pattern in lowered for pattern in _NEGATIVE_PATTERNS)
    has_positive = any(pattern in lowered for pattern in _POSITIVE_PATTERNS)
    if has_negative and has_positive:
        return "mixed"
    if has_negative:
        return "negative"
    if has_positive:
        return "positive"
    return "neutral"


def _topic(doc: dict) -> str:
    section = (doc.get("section_path") or "").strip()
    if section:
        return f"{doc.get('filename', 'Unknown')}::{section}"
    root = (doc.get("root_chunk_id") or "").strip()
    if root:
        return root
    return doc.get("filename", "Unknown")


def detect_conflicts(docs: Iterable[dict]) -> dict:
    groups: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for doc in docs:
        polarity = _polarity(doc.get("text", ""))
        if polarity == "neutral":
            continue
        groups[_topic(doc)][polarity].append(doc)

    conflict_groups = []
    for topic, buckets in groups.items():
        positive = buckets.get("positive", []) + buckets.get("mixed", [])
        negative = buckets.get("negative", []) + buckets.get("mixed", [])
        if not positive or not negative:
            continue
        conflict_groups.append(
            {
                "topic": topic,
                "positive_count": len(positive),
                "negative_count": len(negative),
                "positive_example": _compact(positive[0].get("text", "")),
                "negative_example": _compact(negative[0].get("text", "")),
            }
        )

    conflict_groups = conflict_groups[:5]
    summary = ""
    if conflict_groups:
        summary = "召回证据中存在可能相反的医学建议，请在回答中说明证据差异并避免给出绝对化结论。"

    return {
        "conflict_detected": bool(conflict_groups),
        "conflict_count": len(conflict_groups),
        "conflict_summary": summary,
        "conflict_groups": conflict_groups,
    }


def detect_kg_vector_conflict(kg_trace: dict | None, vector_trace: dict | None) -> dict:
    """Flag KG/vector tension such as KG suitability plus document contraindication."""
    if not kg_trace or not vector_trace:
        return {
            "kg_vector_conflict_detected": False,
            "kg_vector_conflict_summary": "",
        }

    docs = vector_trace.get("retrieved_chunks") or []
    restricted = []
    for doc in docs:
        text = doc.get("text", "")
        if _polarity(text) in {"negative", "mixed"}:
            restricted.append({
                "filename": doc.get("filename", ""),
                "chunk_id": doc.get("chunk_id", ""),
                "example": _compact(text),
            })

    has_kg_hits = int(kg_trace.get("kg_hit_count") or kg_trace.get("hit_count") or 0) > 0
    if not has_kg_hits or not restricted:
        return {
            "kg_vector_conflict_detected": False,
            "kg_vector_conflict_summary": "",
        }

    return {
        "kg_vector_conflict_detected": True,
        "kg_vector_conflict_summary": (
            "知识图谱命中结构化医学事实，但向量证据包含禁忌、慎用或不推荐信息，"
            "回答时必须说明适用条件和限制人群。"
        ),
        "kg_vector_conflict_examples": restricted[:3],
    }
