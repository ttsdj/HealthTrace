"""Evidence formatting and fusion helpers for RAG traces."""

from __future__ import annotations

import os
from collections import defaultdict
from typing import Iterable


def fusion_mode() -> str:
    mode = os.getenv("RAG_FUSION_MODE", "context_packing").strip().lower()
    if mode not in {"context_packing", "refine"}:
        return "context_packing"
    return mode


def format_retrieval_context(docs: Iterable[dict]) -> str:
    """Format evidence in a stable way for the agent prompt/tool output.

    context_packing keeps the retrieval order. refine groups by source so a
    downstream answer model can read one document at a time.
    """

    docs = list(docs)
    if not docs:
        return ""

    mode = fusion_mode()
    if mode == "refine":
        grouped: dict[str, list[dict]] = defaultdict(list)
        for doc in docs:
            grouped[doc.get("filename", "Unknown")].append(doc)
        chunks = []
        counter = 1
        for source, items in grouped.items():
            lines = [f"Source: {source}"]
            for doc in items:
                page = doc.get("page_number", "N/A")
                section = doc.get("section_path") or "unknown section"
                text = doc.get("text", "")
                lines.append(f"[{counter}] Page {page} | {section}\n{text}")
                counter += 1
            chunks.append("\n\n".join(lines))
        return "\n\n--- refine source boundary ---\n\n".join(chunks)

    chunks = []
    for i, doc in enumerate(docs, 1):
        source = doc.get("filename", "Unknown")
        page = doc.get("page_number", "N/A")
        section = doc.get("section_path") or "unknown section"
        kind = doc.get("chunk_kind") or doc.get("structure_type") or "text"
        text = doc.get("text", "")
        chunks.append(f"[{i}] {source} (Page {page}, {kind}, {section}):\n{text}")
    return "\n\n---\n\n".join(chunks)
