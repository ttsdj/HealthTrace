from __future__ import annotations

import hashlib
import os
import re
import unicodedata

EMBEDDING_NORMALIZATION_VERSION = "nfkc_ws_v1"


def embedding_normalization_version() -> str:
    patterns = os.getenv("DOCUMENT_EMBEDDING_IGNORE_LINE_PATTERNS", "").strip()
    if not patterns:
        return EMBEDDING_NORMALIZATION_VERSION
    rules_hash = hashlib.sha256(patterns.encode("utf-8")).hexdigest()[:12]
    return f"{EMBEDDING_NORMALIZATION_VERSION}:{rules_hash}"


def canonicalize_embedding_text(text: str) -> str:
    """Return the exact canonical text used for both embedding and fingerprints."""
    normalized = unicodedata.normalize("NFKC", text or "")
    patterns = os.getenv("DOCUMENT_EMBEDDING_IGNORE_LINE_PATTERNS", "").strip()
    if patterns:
        rules = [
            re.compile(item.strip())
            for item in patterns.split(";;")
            if item.strip()
        ]
        normalized = "\n".join(
            line
            for line in normalized.splitlines()
            if not any(rule.search(line) for rule in rules)
        )
    return re.sub(r"\s+", " ", normalized).strip()


def embedding_content_fingerprint(text: str) -> str:
    return hashlib.sha256(canonicalize_embedding_text(text).encode("utf-8")).hexdigest()


def placement_fingerprint(document: dict) -> str:
    fields = (
        str(document.get("document_id") or ""),
        str(document.get("page_number", 0) or 0),
        str(document.get("section_path") or ""),
        str(document.get("chunk_kind") or ""),
        str(document.get("structure_type") or ""),
        str(document.get("parent_chunk_id") or ""),
        str(document.get("root_chunk_id") or ""),
    )
    return hashlib.sha256("\x1f".join(fields).encode("utf-8")).hexdigest()
