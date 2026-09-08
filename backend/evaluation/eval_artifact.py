"""Uniform, reproducible evaluation artifact schema for HealthTrace.

Any metric number that appears in README/resume must be traceable to a real
``MANIFEST.json`` produced by one of the evaluation runners. This module gives
RAGCare, RAGAS, and MIRAGE a single artifact shape so every number can be traced
to data version + hash, experiment config, per-question ranks/scores, statistical
definitions, the exact reproduce command, and the git commit.

This module has no side effects: importing it does not connect to Milvus, call an
LLM, or read network state. The only third-party reuse is ``ragcare_metrics`` for
per-question retrieval metrics (Recall@K / MRR / nDCG), which is pure math.
"""

from __future__ import annotations

import hashlib
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from backend.evaluation.ragcare_metrics import retrieval_metrics_at_k

SCHEMA_VERSION = "1.0"

# Nested containers a record may carry metric values inside.
_METRIC_CONTAINERS = ("retrieval_metrics", "llm_judge", "ragas", "scores")

_REQUIRED_FIELDS = (
    "schema_version",
    "dataset_hash",
    "config",
    "summary",
    "per_question",
    "command",
    "commit",
)


def capture_git_commit(repo_root: Path | str | None = None) -> str:
    """Return the current git commit (``git rev-parse HEAD``) or ``"unknown"``."""
    root = str(repo_root) if repo_root is not None else None
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        commit = result.stdout.strip()
        return commit if commit else "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def capture_command() -> str:
    """Return the exact command line that could reproduce this artifact."""
    return "python " + " ".join(shlex.quote(part) for part in sys.argv)


def _stable_json_sort(value: Any) -> Any:
    """Deterministically reorder dict keys so hashing is independent of key order."""
    if isinstance(value, dict):
        return {key: _stable_json_sort(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_stable_json_sort(item) for item in value]
    return value


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        _stable_json_sort(payload),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def hash_identity(*parts: str) -> str:
    """Stable 32-char sha256 over an ordered set of provenance strings."""
    payload = "\x1f".join(str(part) for part in parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()[:32]


_DATASET_HASH_FIELDS = (
    "dataset_id",
    "dataset_config",
    "dataset_split",
    "source_url",
    "raw_sha256",
    "seed",
    "chunk_size",
    "chunk_overlap",
    "rows",
    "corpus_documents",
    "leaf_chunks",
    "pilot_cases",
    "heldout_cases",
)


def hash_dataset_manifest(manifest: dict[str, Any]) -> str:
    """Canonical dataset hash from a dataset *manifest* (data version + hash).

    Only provenance/identity fields are included; none of these carry per-question
    metric values, so the hash is stable for a given dataset version and will not
    change across runs that use the same data.
    """
    payload = {
        key: manifest[key]
        for key in _DATASET_HASH_FIELDS
        if key in manifest
    }
    return hash_identity(_canonical_json(payload))


def compute_retrieval_metrics(
    ranked_docs: Iterable[dict],
    gold_document_ids: Iterable[str],
    *,
    k: int = 5,
) -> dict[str, Any]:
    """Per-question retrieval metrics (Recall@K / MRR / nDCG) over ranked docs.

    Thin wrapper over ``ragcare_metrics.retrieval_metrics_at_k`` so RAGCare uses the
    same metric implementation everywhere instead of re-implementing it.
    """
    return retrieval_metrics_at_k(ranked_docs, gold_document_ids, k=k)


def _extract_metric(record: dict[str, Any], key: str) -> float | None:
    """Pull a metric value out of a per-question record, wherever it lives."""
    if key in record and record.get(key) is not None:
        value = record.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return None
    for container in _METRIC_CONTAINERS:
        nested = record.get(container)
        if isinstance(nested, dict):
            candidate = nested
            if container == "ragas" and isinstance(nested.get("scores"), dict):
                candidate = nested.get("scores")
            if key in candidate and candidate.get(key) is not None:
                try:
                    return float(candidate[key])
                except (TypeError, ValueError):
                    return None
    return None


def summarize_metrics(
    records: list[dict[str, Any]],
    keys: Iterable[str],
) -> dict[str, Any]:
    """Aggregate a set of named metrics across per-question records.

    Returns ``{"cases": len(records), "<key>": mean or None, ...}`` where each mean
    is computed only over records that produced a value for that key. This mirrors
    the macro-average convention used by the evaluation runners.
    """
    result: dict[str, Any] = {"cases": len(records)}
    for key in keys:
        values = [
            value
            for record in records
            if (value := _extract_metric(record, key)) is not None
        ]
        result[key] = round(sum(values) / len(values), 6) if values else None
    return result


def validate_artifact(artifact: dict[str, Any]) -> list[str]:
    """Return a list of validation errors; an empty list means the artifact is valid."""
    errors: list[str] = []
    for field in _REQUIRED_FIELDS:
        if field not in artifact:
            errors.append(f"missing field: {field}")
            continue
        value = artifact.get(field)
        if field == "per_question":
            if not isinstance(value, list):
                errors.append("per_question must be a list")
        elif value is None or value == "":
            errors.append(f"field '{field}' must be non-empty")
    return errors


def build_artifact(
    *,
    dataset_hash: str,
    config: dict[str, Any],
    per_question: list[dict[str, Any]],
    statistical_definitions: dict[str, Any],
    command: str,
    commit: str,
    summary: dict[str, Any] | None = None,
    metric_keys: Iterable[str] = (),
    schema_version: str = SCHEMA_VERSION,
    created_at: str | None = None,
    notes: str = "",
) -> dict[str, Any]:
    """Assemble and validate a uniform evaluation artifact.

    If ``summary`` is not supplied, it is derived from ``per_question`` via
    :func:`summarize_metrics` for the given ``metric_keys``.
    """
    if summary is None:
        summary = summarize_metrics(per_question, metric_keys)

    artifact: dict[str, Any] = {
        "schema_version": schema_version,
        "created_at": created_at or datetime.now().isoformat(timespec="seconds"),
        "dataset_hash": dataset_hash,
        "config": config,
        "summary": summary,
        "per_question": per_question,
        "statistical_definitions": statistical_definitions,
        "command": command,
        "commit": commit,
    }
    if notes:
        artifact["notes"] = notes

    errors = validate_artifact(artifact)
    if errors:
        raise ValueError("Invalid evaluation artifact: " + "; ".join(errors))
    return artifact


def write_artifact(
    artifact: dict[str, Any],
    directory: Path | str,
    filename: str = "MANIFEST.json",
) -> Path:
    """Write a validated artifact to ``<directory>/<filename>`` and return its path."""
    if validate_artifact(artifact):
        # Re-raise through build's validation so the error message is consistent.
        raise ValueError(
            "Invalid evaluation artifact: " + "; ".join(validate_artifact(artifact))
        )
    output_dir = Path(directory)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / filename
    path.write_text(
        json.dumps(artifact, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path


def standard_statistical_definitions() -> dict[str, Any]:
    """The canonical statistical definitions recorded in every artifact."""
    return {
        "recall_at_K": (
            "Fraction of the query's gold documents retrieved within the "
            "top-K ranked list. recall@K = |(retrieved top-K) ∩ gold| / |gold|."
        ),
        "mrr": (
            "Mean reciprocal rank: for each query, 1 / rank of the first gold "
            "document in the ranked list (0 if none retrieved). MRR is the mean "
            "over queries."
        ),
        "ndcg_at_K": (
            "Normalized discounted cumulative gain at K: DCG = Σ(1/ld2(rank+1)) over "
            "gold documents within top-K, divided by the ideal DCG. Uses binary "
            "relevance (gold retrieved => 1)."
        ),
        "precision_at_5": "Gold documents within the top-5 divided by 5.",
        "f1_at_5": "Harmonic mean of precision@5 and recall@5.",
        "answer_token_f1": (
            "Character/token F1 between a generated answer and the gold reference, "
            "computed over normalized answer tokens."
        ),
        "choice_accuracy": (
            "1.0 if the predicted option label matches the gold label, else 0.0; "
            "only defined for single-choice questions."
        ),
        "macro_average": (
            "Each per-query metric is averaged equally across queries. No metric is "
            "weighted by query length or document count."
        ),
    }
