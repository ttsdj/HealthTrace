"""RAGCare-QA download, normalization, leakage-safe corpus, and golden-set preparation."""
from __future__ import annotations

import hashlib
import json
import math
import random
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import requests

from backend.indexing.document_loader import DocumentLoader, sanitize_text

DATASET_ID = "ChatMED-Project/RAGCare-QA"
DATASET_CONFIG = "default"
DATASET_SPLIT = "train"
DATASET_ROWS_URL = "https://datasets-server.huggingface.co/rows"
DEFAULT_SEED = 20260627


def _write_jsonl(path: Path, rows: Iterable[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def read_jsonl(path: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _normalized(value: object) -> str:
    return sanitize_text(str(value or "")).strip()


def _stable_hash(*parts: str, length: int = 20) -> str:
    payload = "\x1f".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()[:length]


def download_ragcare_rows(
    raw_path: Path,
    *,
    force: bool = False,
    page_size: int = 100,
    timeout: float = 60.0,
) -> list[dict]:
    """Download all public RAGCare-QA rows through the Hugging Face dataset server."""
    if raw_path.exists() and not force:
        return read_jsonl(raw_path)

    rows: list[dict] = []
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        response = requests.get(
            DATASET_ROWS_URL,
            params={
                "dataset": DATASET_ID,
                "config": DATASET_CONFIG,
                "split": DATASET_SPLIT,
                "offset": offset,
                "length": page_size,
            },
            timeout=timeout,
        )
        response.raise_for_status()
        payload = response.json()
        total = int(payload.get("num_rows_total") or 0)
        page_rows = payload.get("rows") or []
        if not page_rows:
            break
        for item in page_rows:
            row = dict(item.get("row") or {})
            row["_row_idx"] = int(item.get("row_idx", len(rows)))
            rows.append(row)
        offset += len(page_rows)

    if not rows:
        raise RuntimeError("RAGCare-QA download returned no rows")
    _write_jsonl(raw_path, rows)
    return rows


def _field(row: dict, *names: str) -> str:
    for name in names:
        if name in row:
            return _normalized(row.get(name))
    lowered = {str(key).lower(): value for key, value in row.items()}
    for name in names:
        if name.lower() in lowered:
            return _normalized(lowered[name.lower()])
    return ""


def _page_number(page: str) -> int:
    digits = "".join(char if char.isdigit() else " " for char in page).split()
    return int(digits[0]) if digits else 0


def convert_rows(rows: Iterable[dict]) -> tuple[list[dict], list[dict]]:
    """Return a deduplicated context-only corpus and query/gold records."""
    corpus_by_id: dict[str, dict] = {}
    cases: list[dict] = []

    for position, row in enumerate(rows):
        question = _field(row, "Question", "question")
        context = _field(row, "Context", "context")
        if not question or not context:
            continue
        reference = _field(row, "Reference", "reference")
        page = _field(row, "Page", "page")
        answer = _field(row, "Answer", "answer")
        text_answer = _field(row, "Text Answer", "text_answer")
        rag_pipeline = _field(row, "RAG Pipeline", "rag_pipeline")
        complexity = _field(row, "Complexity", "complexity")
        medicine_type = _field(row, "Type (medicine)", "type_medicine")
        general_type = _field(row, "Type", "type")

        document_id = "ragcare-doc-" + _stable_hash(reference, page, context)
        query_id = "ragcare-q-" + _stable_hash(
            str(row.get("_row_idx", position)), question, document_id
        )
        corpus_by_id.setdefault(
            document_id,
            {
                "document_id": document_id,
                "text": context,
                "reference": reference,
                "source_page": page,
                "page_number": _page_number(page),
                "source_dataset": DATASET_ID,
            },
        )
        cases.append(
            {
                "query_id": query_id,
                "row_idx": int(row.get("_row_idx", position)),
                "question": question,
                "gold_document_ids": [document_id],
                "gold_answer": answer,
                "gold_text_answer": text_answer,
                "reference": reference,
                "source_page": page,
                "rag_pipeline": rag_pipeline,
                "complexity": complexity,
                "medicine_type": medicine_type,
                "type": general_type,
            }
        )

    return list(corpus_by_id.values()), cases


def _stratum(case: dict) -> tuple[str, str, str]:
    return (
        case.get("complexity") or "unknown",
        case.get("medicine_type") or case.get("type") or "unknown",
        case.get("rag_pipeline") or "unknown",
    )


def stratified_pilot_split(
    cases: list[dict],
    *,
    pilot_size: int = 100,
    seed: int = DEFAULT_SEED,
) -> tuple[list[dict], list[dict]]:
    if pilot_size <= 0 or pilot_size >= len(cases):
        raise ValueError("pilot_size must be between 1 and len(cases)-1")

    groups: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for case in cases:
        groups[_stratum(case)].append(case)

    rng = random.Random(seed)
    for group in groups.values():
        group.sort(key=lambda item: item["query_id"])
        rng.shuffle(group)

    target_ratio = pilot_size / len(cases)
    quotas: dict[tuple[str, str, str], int] = {}
    fractions: list[tuple[float, tuple[str, str, str]]] = []
    for key, group in groups.items():
        exact = len(group) * target_ratio
        quotas[key] = min(math.floor(exact), len(group))
        fractions.append((exact - math.floor(exact), key))

    allocated = sum(quotas.values())
    for _, key in sorted(fractions, key=lambda item: (-item[0], item[1])):
        if allocated >= pilot_size:
            break
        if quotas[key] < len(groups[key]):
            quotas[key] += 1
            allocated += 1

    if allocated < pilot_size:
        for key in sorted(groups):
            while allocated < pilot_size and quotas[key] < len(groups[key]):
                quotas[key] += 1
                allocated += 1

    pilot: list[dict] = []
    heldout: list[dict] = []
    for key, group in groups.items():
        pilot.extend(group[: quotas[key]])
        heldout.extend(group[quotas[key] :])
    pilot.sort(key=lambda item: item["row_idx"])
    heldout.sort(key=lambda item: item["row_idx"])
    return pilot, heldout


def build_leaf_chunks(
    corpus: Iterable[dict],
    *,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
) -> list[dict]:
    loader = DocumentLoader(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    leaves: list[dict] = []
    for document in corpus:
        document_id = document["document_id"]
        filename = f"ragcare-{document_id}"
        chunks = loader._split_page_to_three_levels(
            text=document["text"],
            base_doc={
                "filename": filename,
                "file_path": "",
                "file_type": "RAGCareContext",
                "page_number": int(document.get("page_number") or 0),
                "document_id": document_id,
                "reference": document.get("reference", ""),
                "source_page": document.get("source_page", ""),
                "source_dataset": DATASET_ID,
            },
            page_global_chunk_idx=0,
        )
        for chunk in chunks:
            if int(chunk.get("chunk_level") or 0) != 3:
                continue
            leaves.append(
                {
                    **chunk,
                    "document_id": document_id,
                    "reference": document.get("reference", ""),
                    "source_page": document.get("source_page", ""),
                    "source_dataset": DATASET_ID,
                }
            )
    return leaves


def prepare_ragcare_dataset(
    raw_path: Path,
    output_dir: Path,
    *,
    force_download: bool = False,
    seed: int = DEFAULT_SEED,
    pilot_size: int = 100,
    chunk_size: int = 800,
    chunk_overlap: int = 100,
) -> dict:
    rows = download_ragcare_rows(raw_path, force=force_download)
    corpus, cases = convert_rows(rows)
    pilot, heldout = stratified_pilot_split(cases, pilot_size=pilot_size, seed=seed)
    chunks = build_leaf_chunks(
        corpus,
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
    )

    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / "corpus.jsonl", corpus)
    _write_jsonl(output_dir / "chunks.jsonl", chunks)
    _write_jsonl(output_dir / "pilot.jsonl", pilot)
    _write_jsonl(output_dir / "heldout.jsonl", heldout)
    _write_jsonl(output_dir / "all.jsonl", cases)

    raw_sha256 = hashlib.sha256(raw_path.read_bytes()).hexdigest()
    manifest = {
        "dataset_id": DATASET_ID,
        "dataset_config": DATASET_CONFIG,
        "dataset_split": DATASET_SPLIT,
        "source_url": f"https://huggingface.co/datasets/{DATASET_ID}",
        "rows": len(rows),
        "corpus_documents": len(corpus),
        "leaf_chunks": len(chunks),
        "pilot_cases": len(pilot),
        "heldout_cases": len(heldout),
        "seed": seed,
        "chunk_size": chunk_size,
        "chunk_overlap": chunk_overlap,
        "raw_sha256": raw_sha256,
        "leakage_policy": "Only Context is indexed; Question and Answer fields remain in gold files.",
    }
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return manifest
