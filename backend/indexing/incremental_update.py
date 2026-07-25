from __future__ import annotations

import hashlib
import os
import re
import shutil
import threading
import unicodedata
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime
from functools import wraps
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, or_, text as sql_text

from backend.db.models import DocumentRecord, DocumentVersion, ParentChunk
from backend.indexing.milvus_client import MilvusStore
from backend.indexing.milvus_writer import MilvusWriter
from backend.indexing.parent_chunk_store import ParentChunkStore
from backend.infra.cache import cache
from backend.infra.database import SessionLocal

CORE_VECTOR_FIELDS = [
    "id",
    "dense_embedding",
    "text",
    "filename",
    "file_type",
    "file_path",
    "page_number",
    "chunk_idx",
    "chunk_id",
    "parent_chunk_id",
    "root_chunk_id",
    "chunk_level",
    "document_id",
    "document_domain",
    "tenant_id",
    "patient_id",
    "owner_user_id",
]
_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _serialized_document_update(function):
    @wraps(function)
    def wrapper(self, *args, **kwargs):
        document_id = str(kwargs.get("document_id") or "")
        with _LOCKS_GUARD:
            process_lock = _LOCKS.setdefault(document_id, threading.Lock())
        timeout = max(1, int(os.getenv("HEALTHTRACE_DOCUMENT_UPDATE_LOCK_SECONDS", "30")))
        if not process_lock.acquire(timeout=timeout):
            raise RuntimeError("Another update for this document is still running")

        advisory_db = None
        advisory_locked = False
        try:
            advisory_db = SessionLocal()
            dialect = advisory_db.get_bind().dialect.name
            if dialect == "postgresql":
                advisory_locked = bool(
                    advisory_db.execute(
                        sql_text("SELECT pg_try_advisory_lock(hashtext(:document_id))"),
                        {"document_id": document_id},
                    ).scalar()
                )
                if not advisory_locked:
                    raise RuntimeError("Another update for this document is still running")
            return function(self, *args, **kwargs)
        finally:
            if advisory_db is not None:
                if advisory_locked:
                    try:
                        advisory_db.execute(
                            sql_text("SELECT pg_advisory_unlock(hashtext(:document_id))"),
                            {"document_id": document_id},
                        )
                    except Exception:
                        pass
                advisory_db.close()
            process_lock.release()

    return wrapper


@dataclass(frozen=True)
class IncrementalUpdateResult:
    document_id: str
    version: int
    parent_chunks: int
    leaf_chunks: int
    reused_vectors: int
    embedded_vectors: int
    deleted_vectors: int
    unchanged: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


class PartialVectorDeleteError(RuntimeError):
    def __init__(self, message: str, deleted_ids: list[int]):
        super().__init__(message)
        self.deleted_ids = deleted_ids


def public_document_id(filename: str) -> str:
    digest = hashlib.sha256(filename.strip().lower().encode("utf-8")).hexdigest()[:40]
    return f"public-{digest}"


def chunk_content_fingerprint(text: str) -> str:
    """Stable identity used to reuse an embedding across positional chunk ID changes."""
    normalized = unicodedata.normalize("NFC", text or "")
    normalized = re.sub(r"\s+", " ", normalized).strip()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_embedding_reuse_plan(
    new_documents: list[dict],
    old_rows: list[dict],
) -> tuple[list[list[float] | None], int, int]:
    """Match duplicate-safe queues of old vectors to new normalized chunk content."""
    reusable: dict[str, deque[list[float]]] = defaultdict(deque)
    for row in old_rows:
        vector = row.get("dense_embedding")
        if vector:
            reusable[chunk_content_fingerprint(str(row.get("text") or ""))].append(vector)

    embeddings: list[list[float] | None] = []
    reused = 0
    for document in new_documents:
        fingerprint = chunk_content_fingerprint(str(document.get("text") or ""))
        document["content_fingerprint"] = fingerprint
        if reusable[fingerprint]:
            embeddings.append(reusable[fingerprint].popleft())
            reused += 1
        else:
            embeddings.append(None)
    return embeddings, reused, len(new_documents) - reused


def _escape_filter_value(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _delete_ids(store: MilvusStore, ids: list[int], batch_size: int = 500) -> int:
    deleted = 0
    deleted_ids: list[int] = []
    for offset in range(0, len(ids), batch_size):
        batch = ids[offset : offset + batch_size]
        if not batch:
            continue
        try:
            result = store.delete(f"id in [{','.join(str(value) for value in batch)}]")
        except Exception as exc:
            raise PartialVectorDeleteError(str(exc), deleted_ids) from exc
        deleted += int(result.get("delete_count", 0)) if isinstance(result, dict) else len(batch)
        deleted_ids.extend(batch)
    return deleted


def _restorable_vector_rows(rows: list[dict]) -> list[dict]:
    restored: list[dict] = []
    for row in rows:
        payload = {key: value for key, value in row.items() if key != "id"}
        if payload.get("dense_embedding") and payload.get("text"):
            restored.append(payload)
    return restored


def _parent_payload(document: dict) -> dict:
    return {
        "chunk_id": str(document.get("chunk_id") or ""),
        "text": str(document.get("text") or ""),
        "filename": str(document.get("filename") or ""),
        "file_type": str(document.get("file_type") or ""),
        "file_path": str(document.get("file_path") or ""),
        "page_number": int(document.get("page_number", 0) or 0),
        "parent_chunk_id": str(document.get("parent_chunk_id") or ""),
        "root_chunk_id": str(document.get("root_chunk_id") or ""),
        "chunk_level": int(document.get("chunk_level", 0) or 0),
        "chunk_idx": int(document.get("chunk_idx", 0) or 0),
        "document_id": str(document.get("document_id") or ""),
        "document_domain": str(document.get("document_domain") or "public_medical"),
        "tenant_id": document.get("tenant_id") or None,
        "patient_id": document.get("patient_id") or None,
        "owner_user_id": document.get("owner_user_id") or None,
        "updated_at": datetime.utcnow(),
    }


class IncrementalDocumentIndexer:
    """Cross-store saga for a document version switch with compensating rollback."""

    def __init__(
        self,
        store: MilvusStore,
        writer: MilvusWriter,
        parent_store: ParentChunkStore | None = None,
    ):
        self.store = store
        self.writer = writer
        self.parent_store = parent_store or ParentChunkStore()

    def _old_vector_rows(
        self,
        *,
        document_id: str,
        filename: str,
        document_domain: str,
    ) -> list[dict]:
        escaped_id = _escape_filter_value(document_id)
        escaped_name = _escape_filter_value(filename)
        if document_domain == "public_medical":
            expression = f'filename == "{escaped_name}"'
        else:
            expression = f'document_id == "{escaped_id}"'
        return self.store.query_all(expression, output_fields=CORE_VECTOR_FIELDS)

    def _has_current_vectors(
        self,
        *,
        document_id: str,
        filename: str,
        document_domain: str,
    ) -> bool:
        if document_domain == "public_medical":
            expression = f'filename == "{_escape_filter_value(filename)}"'
        else:
            expression = f'document_id == "{_escape_filter_value(document_id)}"'
        try:
            self.store.init_collection()
            return bool(self.store.query(expression, output_fields=["id"], limit=1))
        except Exception:
            return False

    @staticmethod
    def _parent_filter(db, document_id: str, filename: str, document_domain: str):
        if document_domain == "public_medical":
            return db.query(ParentChunk).filter(
                or_(
                    ParentChunk.document_id == document_id,
                    ParentChunk.filename == filename,
                )
            )
        return db.query(ParentChunk).filter(ParentChunk.document_id == document_id)

    @_serialized_document_update
    def apply(
        self,
        *,
        document_id: str,
        document_domain: str,
        filename: str,
        file_type: str,
        canonical_path: Path,
        staging_path: Path,
        content_sha256: str,
        parent_documents: list[dict],
        leaf_documents: list[dict],
        tenant_id: str | None = None,
        patient_id: str | None = None,
        owner_user_id: int | None = None,
        created_by_user_id: int | None = None,
        progress_callback=None,
    ) -> IncrementalUpdateResult:
        if os.getenv("HEALTHTRACE_INCREMENTAL_UPDATE_ENABLED", "true").lower() != "true":
            raise RuntimeError("Incremental document updates are disabled")
        if not leaf_documents:
            raise ValueError("No searchable leaf chunks were generated")
        if not staging_path.exists():
            raise FileNotFoundError(f"Staging file does not exist: {staging_path}")

        update_id = f"upd-{uuid4().hex}"
        version_id = f"ver-{uuid4().hex}"
        old_record_snapshot: dict = {}
        version_number = 1

        db = SessionLocal()
        try:
            record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
            if record is None:
                record = DocumentRecord(
                    id=document_id,
                    document_domain=document_domain,
                    tenant_id=tenant_id,
                    patient_id=patient_id,
                    owner_user_id=owner_user_id,
                    filename=filename,
                    file_type=file_type,
                    storage_uri=str(canonical_path),
                    status="uploaded",
                )
                db.add(record)
                db.flush()

            old_record_snapshot = {
                "status": record.status,
                "content_sha256": record.content_sha256,
                "storage_uri": record.storage_uri,
                "metadata_json": dict(record.metadata_json or {}),
                "error_message": record.error_message,
            }
            if (
                record.status == "indexed"
                and record.content_sha256
                and record.content_sha256 == content_sha256
                and self._has_current_vectors(
                    document_id=document_id,
                    filename=filename,
                    document_domain=document_domain,
                )
            ):
                current_version = int((record.metadata_json or {}).get("active_version", 1))
                db.rollback()
                try:
                    if staging_path.resolve() != canonical_path.resolve():
                        staging_path.unlink()
                except OSError:
                    pass
                return IncrementalUpdateResult(
                    document_id=document_id,
                    version=current_version,
                    parent_chunks=int((record.metadata_json or {}).get("parent_chunks", 0)),
                    leaf_chunks=int((record.metadata_json or {}).get("leaf_chunks", 0)),
                    reused_vectors=int((record.metadata_json or {}).get("leaf_chunks", 0)),
                    embedded_vectors=0,
                    deleted_vectors=0,
                    unchanged=True,
                )

            maximum = (
                db.query(func.max(DocumentVersion.version_number))
                .filter(DocumentVersion.document_id == document_id)
                .scalar()
            )
            maximum = int(maximum or 0)
            if maximum == 0 and record.status == "indexed":
                legacy_counts = record.metadata_json or {}
                db.add(
                    DocumentVersion(
                        id=f"ver-{uuid4().hex}",
                        document_id=document_id,
                        version_number=1,
                        content_sha256=record.content_sha256,
                        storage_uri=record.storage_uri,
                        status="active",
                        parent_chunk_count=int(legacy_counts.get("parent_chunks", 0)),
                        leaf_chunk_count=int(legacy_counts.get("leaf_chunks", 0)),
                        metadata_json={"source": "legacy_inventory"},
                        activated_at=record.updated_at,
                    )
                )
                maximum = 1
            version_number = maximum + 1
            db.add(
                DocumentVersion(
                    id=version_id,
                    document_id=document_id,
                    version_number=version_number,
                    content_sha256=content_sha256,
                    storage_uri=str(canonical_path),
                    status="preparing",
                    created_by_user_id=created_by_user_id,
                    metadata_json={"update_id": update_id},
                )
            )
            record.status = "updating"
            record.error_message = ""
            record.updated_at = datetime.utcnow()
            db.commit()
        finally:
            db.close()

        old_rows: list[dict] = []
        inserted_ids: list[int] = []
        old_ids: list[int] = []
        deleted_old_ids: list[int] = []
        raw_replaced = False
        backup_path: Path | None = None
        activation_path = canonical_path.with_name(f".{canonical_path.name}.{update_id}.activate")
        cache_ids: set[str] = set()

        try:
            self.store.init_collection()
            old_rows = self._old_vector_rows(
                document_id=document_id,
                filename=filename,
                document_domain=document_domain,
            )
            old_ids = [int(row["id"]) for row in old_rows if row.get("id") is not None]

            for document in [*parent_documents, *leaf_documents]:
                document.update(
                    {
                        "document_id": document_id,
                        "document_domain": document_domain,
                        "tenant_id": tenant_id or "",
                        "patient_id": patient_id or "",
                        "owner_user_id": int(owner_user_id or 0),
                        "file_path": str(canonical_path),
                        "document_version": version_number,
                        "content_sha256": content_sha256,
                    }
                )
                cache_ids.add(str(document.get("chunk_id") or ""))
            for document in leaf_documents:
                document["index_update_id"] = update_id

            precomputed, reused, embedded = build_embedding_reuse_plan(
                leaf_documents,
                old_rows,
            )
            prepared = self.writer.prepare_documents(
                leaf_documents,
                precomputed_embeddings=precomputed,
                progress_callback=progress_callback,
            )
            inserted_ids = self.writer.insert_prepared(prepared)

            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(staging_path, activation_path)
            if canonical_path.exists() and staging_path.resolve() != canonical_path.resolve():
                backup_path = (
                    canonical_path.parent
                    / ".versions"
                    / document_id
                    / f"v{max(version_number - 1, 0)}-{update_id}"
                    / canonical_path.name
                )
                backup_path.parent.mkdir(parents=True, exist_ok=True)
                os.replace(canonical_path, backup_path)
            if staging_path.resolve() != canonical_path.resolve():
                os.replace(activation_path, canonical_path)
                raw_replaced = True

            db = SessionLocal()
            try:
                record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).one()
                parent_query = self._parent_filter(
                    db,
                    document_id,
                    filename,
                    document_domain,
                )
                old_parent_ids = [row.chunk_id for row in parent_query.all()]
                cache_ids.update(old_parent_ids)
                parent_query.delete(synchronize_session=False)
                db.flush()
                for document in parent_documents:
                    payload = _parent_payload(document)
                    if payload["chunk_id"]:
                        db.add(ParentChunk(**payload))

                active_versions = (
                    db.query(DocumentVersion)
                    .filter(
                        DocumentVersion.document_id == document_id,
                        DocumentVersion.status == "active",
                    )
                    .all()
                )
                for active in active_versions:
                    active.status = "superseded"
                    if backup_path is not None:
                        active.storage_uri = str(backup_path)

                try:
                    deleted = _delete_ids(self.store, old_ids)
                    deleted_old_ids = list(old_ids)
                except PartialVectorDeleteError as delete_error:
                    deleted_old_ids = list(delete_error.deleted_ids)
                    raise

                version = db.query(DocumentVersion).filter(DocumentVersion.id == version_id).one()
                version.status = "active"
                version.parent_chunk_count = len(parent_documents)
                version.leaf_chunk_count = len(leaf_documents)
                version.reused_vector_count = reused
                version.embedded_vector_count = embedded
                version.deleted_vector_count = deleted
                version.activated_at = datetime.utcnow()
                version.metadata_json = {
                    "update_id": update_id,
                    "old_vector_rows": len(old_rows),
                    "strategy": "content_fingerprint_embedding_reuse",
                }

                record.filename = filename
                record.file_type = file_type
                record.storage_uri = str(canonical_path)
                record.content_sha256 = content_sha256
                record.status = "indexed"
                record.error_message = ""
                record.metadata_json = {
                    **(record.metadata_json or {}),
                    "active_version": version_number,
                    "active_version_id": version_id,
                    "parent_chunks": len(parent_documents),
                    "leaf_chunks": len(leaf_documents),
                    "collection": self.store.collection_name,
                    "last_update": {
                        "update_id": update_id,
                        "reused_vectors": reused,
                        "embedded_vectors": embedded,
                        "deleted_vectors": deleted,
                    },
                }
                record.updated_at = datetime.utcnow()
                db.commit()
            except Exception:
                db.rollback()
                raise
            finally:
                db.close()

            for chunk_id in cache_ids:
                if chunk_id:
                    try:
                        cache.delete(self.parent_store._cache_key(chunk_id))
                    except Exception:
                        pass
            try:
                if staging_path.exists() and staging_path.resolve() != canonical_path.resolve():
                    staging_path.unlink()
            except OSError:
                # A stale staging copy is harmless and can be cleaned by retention policy.
                pass
            return IncrementalUpdateResult(
                document_id=document_id,
                version=version_number,
                parent_chunks=len(parent_documents),
                leaf_chunks=len(leaf_documents),
                reused_vectors=reused,
                embedded_vectors=embedded,
                deleted_vectors=deleted,
            )
        except Exception as exc:
            # Compensate in reverse order. The old version remains the source of truth.
            try:
                if inserted_ids:
                    _delete_ids(self.store, inserted_ids)
                else:
                    escaped_update = _escape_filter_value(update_id)
                    self.store.delete(f'index_update_id == "{escaped_update}"')
            except Exception:
                pass
            if deleted_old_ids and old_rows:
                try:
                    deleted_set = set(deleted_old_ids)
                    self.writer.insert_prepared(
                        _restorable_vector_rows(
                            [row for row in old_rows if int(row.get("id", -1)) in deleted_set]
                        )
                    )
                except Exception:
                    pass
            try:
                if raw_replaced:
                    if backup_path is not None and backup_path.exists():
                        os.replace(backup_path, canonical_path)
                    elif canonical_path.exists():
                        shutil.copy2(canonical_path, staging_path)
                        canonical_path.unlink()
                if activation_path.exists():
                    activation_path.unlink()
            except Exception:
                pass

            db = SessionLocal()
            try:
                record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
                if record is not None:
                    record.status = old_record_snapshot.get("status", "update_failed")
                    record.content_sha256 = old_record_snapshot.get("content_sha256", "")
                    record.storage_uri = old_record_snapshot.get("storage_uri", "")
                    record.metadata_json = old_record_snapshot.get("metadata_json", {})
                    record.error_message = str(exc)[:2000]
                    record.updated_at = datetime.utcnow()
                version = db.query(DocumentVersion).filter(DocumentVersion.id == version_id).first()
                if version is not None:
                    version.status = "failed"
                    version.error_message = str(exc)[:2000]
                db.commit()
            finally:
                db.close()
            raise
