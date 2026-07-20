"""Import MedRAG/textbooks corpus into an isolated Milvus collection."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from backend.env import load_env

load_env()

from datasets import load_dataset  # noqa: E402

from backend.indexing.document_loader import sanitize_text  # noqa: E402
from backend.indexing.embedding import embedding_service  # noqa: E402
from backend.indexing.milvus_client import MilvusStore  # noqa: E402
from backend.indexing.milvus_writer import MilvusWriter  # noqa: E402

DATASET_ID = "MedRAG/textbooks"
DEFAULT_COLLECTION = "med_mirage_textbooks_v1"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "data" / "medrag" / "textbooks"


def _hash_id(*parts: str, length: int = 20) -> str:
    payload = "\x1f".join(parts).encode("utf-8", errors="ignore")
    return hashlib.sha256(payload).hexdigest()[:length]


def _split_text(text: str, *, max_chars: int, overlap: int) -> list[str]:
    text = sanitize_text(text).strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return chunks


def stream_textbook_rows(*, split: str = "train") -> Iterable[dict]:
    yield from load_dataset(DATASET_ID, split=split, streaming=True)


def build_docs(
    rows: Iterable[dict],
    *,
    max_rows: int = 0,
    max_chars: int = 1200,
    overlap: int = 120,
) -> list[dict]:
    docs: list[dict] = []
    for row_index, row in enumerate(rows, 1):
        if max_rows and row_index > max_rows:
            break
        source_id = sanitize_text(str(row.get("id") or f"row-{row_index}"))
        title = sanitize_text(str(row.get("title") or "MedRAG textbook"))
        content = sanitize_text(str(row.get("contents") or row.get("content") or ""))
        if not content:
            continue
        for chunk_index, chunk in enumerate(
            _split_text(content, max_chars=max_chars, overlap=overlap)
        ):
            chunk_id = f"medrag-textbooks::{source_id}::{chunk_index}"
            root_id = f"medrag-textbooks::{source_id}"
            docs.append(
                {
                    "text": chunk,
                    "filename": title[:255],
                    "file_type": "MedRAGTextbook",
                    "file_path": DATASET_ID,
                    "page_number": 0,
                    "chunk_idx": chunk_index,
                    "chunk_id": chunk_id,
                    "parent_chunk_id": root_id,
                    "root_chunk_id": root_id,
                    "chunk_level": 3,
                    "chunk_kind": "textbook_chunk",
                    "structure_type": "textbook_passage",
                    "section_path": title,
                    "source_dataset": DATASET_ID,
                    "source_record_id": source_id,
                    "source_hash": _hash_id(source_id, chunk),
                }
            )
    return docs


def _write_manifest(output_dir: Path, manifest: dict) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--collection", default=DEFAULT_COLLECTION)
    parser.add_argument("--split", default="train")
    parser.add_argument("--max-rows", type=int, default=500)
    parser.add_argument("--max-chars", type=int, default=1200)
    parser.add_argument("--overlap", type=int, default=120)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    docs = build_docs(
        stream_textbook_rows(split=args.split),
        max_rows=args.max_rows,
        max_chars=args.max_chars,
        overlap=args.overlap,
    )
    manifest = {
        "dataset_id": DATASET_ID,
        "split": args.split,
        "collection": args.collection,
        "max_rows": args.max_rows,
        "chunks": len(docs),
        "max_chars": args.max_chars,
        "overlap": args.overlap,
        "note": "MedRAG textbook corpus for MIRAGE RAG-agent evaluation.",
    }
    _write_manifest(DEFAULT_OUTPUT_DIR / args.collection, manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    if args.dry_run:
        for sample in docs[:3]:
            print(json.dumps(sample, ensure_ascii=False)[:800])
        return

    store = MilvusStore.for_collection(args.collection)
    if store.has_collection():
        existing = len(store.query(output_fields=["chunk_id"], limit=1))
        if existing:
            print(
                f"Collection {args.collection} already exists and is non-empty. "
                "Use another --collection to avoid duplicate inserts."
            )
            return

    writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=store)
    writer.write_documents(
        docs,
        batch_size=args.batch_size,
        progress_callback=lambda done, total: print(
            f"Imported textbook chunks: {done}/{total}",
            flush=True,
        ),
    )
    print(f"Imported {len(docs)} chunks into {args.collection}")


if __name__ == "__main__":
    main()
