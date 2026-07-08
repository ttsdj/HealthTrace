"""Context compression helpers for medical RAG.

The first version is intentionally deterministic: it trims evidence and chat
history by character budget and keeps source metadata untouched for trace.
"""

from __future__ import annotations

import os
import re
from typing import Iterable


def _read_positive_int(name: str, default: int) -> int:
    try:
        return max(int(os.getenv(name, str(default))), 1)
    except ValueError:
        return default


EVIDENCE_MAX_CHARS = _read_positive_int("EVIDENCE_COMPRESS_MAX_CHARS", 900)
EVIDENCE_SENTENCE_MAX = _read_positive_int("EVIDENCE_COMPRESS_SENTENCES", 4)
HISTORY_MAX_MESSAGES = _read_positive_int("HISTORY_COMPRESS_MAX_MESSAGES", 6)


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[。！？!?；;])\s*|\n+", text or "")
    return [part.strip() for part in parts if part and part.strip()]


def compress_evidence_text(text: str, query: str = "") -> tuple[str, dict]:
    """Extract the most query-relevant sentences from one evidence block."""
    raw = re.sub(r"\s+", " ", text or "").strip()
    if len(raw) <= EVIDENCE_MAX_CHARS:
        return raw, {"compressed": False, "original_chars": len(raw), "compressed_chars": len(raw)}

    terms = {item for item in re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,}", query or "")}
    ranked = []
    for idx, sentence in enumerate(_sentences(raw)):
        score = sum(1 for term in terms if term.lower() in sentence.lower())
        ranked.append((score, -idx, sentence))

    ranked.sort(reverse=True)
    selected = [item[2] for item in ranked[:EVIDENCE_SENTENCE_MAX]]
    if not selected:
        selected = [raw[:EVIDENCE_MAX_CHARS]]

    compressed = " ".join(selected)
    if len(compressed) > EVIDENCE_MAX_CHARS:
        compressed = compressed[:EVIDENCE_MAX_CHARS].rstrip() + "..."
    return compressed, {
        "compressed": True,
        "original_chars": len(raw),
        "compressed_chars": len(compressed),
    }


def compress_documents_for_context(docs: Iterable[dict], query: str = "") -> tuple[list[dict], dict]:
    compressed_docs: list[dict] = []
    original_chars = 0
    compressed_chars = 0
    compressed_count = 0

    for doc in docs:
        item = dict(doc)
        text = item.get("text", "")
        compressed_text, meta = compress_evidence_text(text, query=query)
        item["text"] = compressed_text
        if meta["compressed"]:
            item["compressed_from_chars"] = meta["original_chars"]
            compressed_count += 1
        original_chars += meta["original_chars"]
        compressed_chars += meta["compressed_chars"]
        compressed_docs.append(item)

    return compressed_docs, {
        "context_compression_enabled": True,
        "evidence_compression_applied": compressed_count > 0,
        "evidence_compressed_chunks": compressed_count,
        "evidence_original_chars": original_chars,
        "evidence_compressed_chars": compressed_chars,
        "evidence_compression_ratio": round(compressed_chars / original_chars, 4) if original_chars else 1.0,
        "evidence_max_chars_per_chunk": EVIDENCE_MAX_CHARS,
    }


def summarize_history_window(messages: list) -> tuple[list, dict]:
    """Keep a bounded recent window; persistent note already stores old turns."""
    total = len(messages)
    kept = messages[-HISTORY_MAX_MESSAGES:] if total > HISTORY_MAX_MESSAGES else messages
    return kept, {
        "history_compression_enabled": True,
        "history_total_messages": total,
        "history_kept_messages": len(kept),
        "history_compressed_messages": max(total - len(kept), 0),
    }
