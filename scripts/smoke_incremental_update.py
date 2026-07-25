"""Real PostgreSQL + Milvus smoke test for the incremental update saga."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from backend.env import PROJECT_ROOT, load_env


def _document(text, *, filename, file_path, chunk_id, level, parent_id=""):
    return {
        "text": text,
        "filename": filename,
        "file_type": "HTML",
        "file_path": str(file_path),
        "page_number": 0,
        "chunk_idx": 0,
        "chunk_id": chunk_id,
        "parent_chunk_id": parent_id,
        "root_chunk_id": parent_id or chunk_id,
        "chunk_level": level,
    }


def main() -> int:
    load_env()
    os.environ["EMBEDDING_BACKEND"] = "hash"
    os.environ.setdefault("DENSE_EMBEDDING_DIM", "1024")

    from backend.db.models import DocumentRecord, DocumentVersion, ParentChunk
    from backend.indexing import IncrementalDocumentIndexer, MilvusStore, MilvusWriter
    from backend.indexing.embedding import EmbeddingService
    from backend.infra.database import SessionLocal

    suffix = uuid4().hex[:12]
    document_id = f"public-smoke-{suffix}"
    filename = f"incremental-smoke-{suffix}.html"
    root = PROJECT_ROOT / "data" / "incremental-smoke"
    canonical = root / filename
    stage1 = root / f"stage1-{filename}"
    stage2 = root / f"stage2-{filename}"
    collection = f"healthtrace_incremental_smoke_{suffix}"
    archive_collection = f"{collection}_archive"
    store = MilvusStore.for_collection(collection)
    archive_store = MilvusStore.for_collection(archive_collection)
    writer = MilvusWriter(EmbeddingService(), store)
    indexer = IncrementalDocumentIndexer(
        store,
        writer,
        archive_store=archive_store,
        archive_writer=MilvusWriter(EmbeddingService(), archive_store),
    )
    file_paths: set[Path] = {canonical, stage1, stage2}

    try:
        root.mkdir(parents=True, exist_ok=True)
        stage1.write_text("version one", encoding="utf-8")
        first = indexer.apply(
            document_id=document_id,
            document_domain="public_medical",
            filename=filename,
            file_type="HTML",
            canonical_path=canonical,
            staging_path=stage1,
            content_sha256=hashlib.sha256(stage1.read_bytes()).hexdigest(),
            parent_documents=[
                _document(
                    "version one parent",
                    filename=filename,
                    file_path=canonical,
                    chunk_id=f"{document_id}-parent-v1",
                    level=1,
                )
            ],
            leaf_documents=[
                _document(
                    "stable medical evidence",
                    filename=filename,
                    file_path=canonical,
                    chunk_id=f"{document_id}-stable-v1",
                    level=3,
                    parent_id=f"{document_id}-parent-v1",
                ),
                _document(
                    "removed evidence",
                    filename=filename,
                    file_path=canonical,
                    chunk_id=f"{document_id}-removed",
                    level=3,
                    parent_id=f"{document_id}-parent-v1",
                ),
            ],
        )

        stage2.write_text("version two", encoding="utf-8")
        second = indexer.apply(
            document_id=document_id,
            document_domain="public_medical",
            filename=filename,
            file_type="HTML",
            canonical_path=canonical,
            staging_path=stage2,
            content_sha256=hashlib.sha256(stage2.read_bytes()).hexdigest(),
            parent_documents=[
                _document(
                    "version two parent",
                    filename=filename,
                    file_path=canonical,
                    chunk_id=f"{document_id}-parent-v2",
                    level=1,
                )
            ],
            leaf_documents=[
                _document(
                    "stable medical evidence",
                    filename=filename,
                    file_path=canonical,
                    chunk_id=f"{document_id}-stable-v2",
                    level=3,
                    parent_id=f"{document_id}-parent-v2",
                ),
                _document(
                    "new evidence",
                    filename=filename,
                    file_path=canonical,
                    chunk_id=f"{document_id}-new",
                    level=3,
                    parent_id=f"{document_id}-parent-v2",
                ),
            ],
        )
        assert second.reused_vectors == 1
        assert second.embedded_vectors == 1
        assert second.version == 2
        assert second.archived_vectors == 2

        rollback = indexer.rollback(
            document_id=document_id,
            target_version_number=1,
        )
        assert rollback.version == 1
        assert rollback.previous_version == 2
        assert rollback.restored_vectors == 2
        assert canonical.read_text(encoding="utf-8") == "version one"
        print(
            json.dumps(
                {
                    "first": first.to_dict(),
                    "second": second.to_dict(),
                    "rollback": rollback.to_dict(),
                },
                indent=2,
            )
        )
        return 0
    finally:
        db = SessionLocal()
        try:
            for version in db.query(DocumentVersion).filter_by(document_id=document_id).all():
                if version.storage_uri:
                    file_paths.add(Path(version.storage_uri))
            db.query(ParentChunk).filter_by(document_id=document_id).delete(
                synchronize_session=False
            )
            db.query(DocumentVersion).filter_by(document_id=document_id).delete(
                synchronize_session=False
            )
            db.query(DocumentRecord).filter_by(id=document_id).delete(
                synchronize_session=False
            )
            db.commit()
        finally:
            db.close()
        try:
            store.drop_collection()
        except Exception:
            pass
        try:
            archive_store.drop_collection()
        except Exception:
            pass
        for path in file_paths:
            try:
                if path.is_file() and root.resolve() in path.resolve().parents:
                    path.unlink()
            except OSError:
                pass


if __name__ == "__main__":
    raise SystemExit(main())
