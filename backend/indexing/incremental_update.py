from __future__ import annotations

import hashlib
import os
import shutil
import threading
from collections import defaultdict, deque
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from functools import wraps
from pathlib import Path
from uuid import uuid4

from sqlalchemy import func, or_, text as sql_text

from backend.db.models import (
    DocumentRecord,
    DocumentVersion,
    DocumentVersionParentChunk,
    ParentChunk,
)
from backend.indexing.milvus_client import MilvusStore, get_milvus_store
from backend.indexing.milvus_writer import MilvusWriter
from backend.indexing.parent_chunk_store import ParentChunkStore
from backend.indexing.text_normalization import (
    embedding_content_fingerprint,
    embedding_normalization_version,
    placement_fingerprint,
)
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
    "content_fingerprint",
    "placement_fingerprint",
    "embedding_normalization_version",
    "document_version",
    "content_sha256",
    "archived_version_id",
    "archive_update_id",
    "retention_until_epoch",
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
    archived_vectors: int = 0
    unchanged: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class IncrementalRollbackResult:
    document_id: str
    version: int
    previous_version: int
    parent_chunks: int
    leaf_chunks: int
    restored_vectors: int
    archived_vectors: int

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
    return embedding_content_fingerprint(text)


def build_embedding_reuse_plan(
    new_documents: list[dict],
    old_rows: list[dict],
) -> tuple[list[list[float] | None], int, int]:
    """Match duplicate-safe queues of old vectors to new normalized chunk content."""
    reusable: dict[str, deque[list[float]]] = defaultdict(deque)
    current_normalization_version = embedding_normalization_version()
    for row in old_rows:
        vector = row.get("dense_embedding")
        normalization_version = str(row.get("embedding_normalization_version") or "")
        if vector and normalization_version == current_normalization_version:
            reusable[chunk_content_fingerprint(str(row.get("text") or ""))].append(vector)

    embeddings: list[list[float] | None] = []
    reused = 0
    for document in new_documents:
        fingerprint = chunk_content_fingerprint(str(document.get("text") or ""))
        document["content_fingerprint"] = fingerprint
        document["embedding_normalization_version"] = current_normalization_version
        document["placement_fingerprint"] = placement_fingerprint(document)
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


def _active_vector_payload(
    row: dict,
    *,
    update_id: str,
    version_number: int,
    content_sha256: str,
    canonical_path: Path,
) -> dict:
    payload = {
        key: value
        for key, value in row.items()
        if key
        not in {
            "id",
            "archived_version_id",
            "archive_update_id",
            "retention_until_epoch",
        }
    }
    payload.update(
        {
            "index_update_id": update_id,
            "document_version": version_number,
            "content_sha256": content_sha256,
            "file_path": str(canonical_path),
        }
    )
    return payload


def _archive_vector_payloads(
    rows: list[dict],
    *,
    version_id: str,
    version_number: int,
    update_id: str,
    retention_until: datetime,
) -> list[dict]:
    archived = _restorable_vector_rows(rows)
    for row in archived:
        row.update(
            {
                "archived_version_id": version_id,
                "archive_update_id": update_id,
                "document_version": version_number,
                "retention_until_epoch": int(retention_until.timestamp()),
                "content_fingerprint": row.get("content_fingerprint")
                or chunk_content_fingerprint(str(row.get("text") or "")),
            }
        )
    return archived


def _deduplicate_archive_rows(rows: list[dict]) -> list[dict]:
    """Collapse retry duplicates without relying on archive Milvus row IDs."""
    unique: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        key = (
            str(row.get("chunk_id") or ""),
            str(
                row.get("content_fingerprint")
                or chunk_content_fingerprint(str(row.get("text") or ""))
            ),
            str(row.get("parent_chunk_id") or ""),
        )
        unique[key] = row
    return list(unique.values())


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


def _snapshot_metadata(document: dict) -> dict:
    payload = _parent_payload(document)
    payload.pop("text", None)
    payload.pop("updated_at", None)
    payload.pop("chunk_id", None)
    return payload


def _snapshot_parent_documents(
    db,
    *,
    version_id: str,
    document_id: str,
    documents: list[dict],
) -> None:
    existing = (
        db.query(DocumentVersionParentChunk.id)
        .filter(DocumentVersionParentChunk.version_id == version_id)
        .first()
    )
    if existing is not None:
        return
    for document in documents:
        chunk_id = str(document.get("chunk_id") or "")
        if not chunk_id:
            continue
        db.add(
            DocumentVersionParentChunk(
                id=f"vpc-{uuid4().hex}",
                version_id=version_id,
                document_id=document_id,
                chunk_id=chunk_id,
                text=str(document.get("text") or ""),
                metadata_json=_snapshot_metadata(document),
            )
        )


class IncrementalDocumentIndexer:
    """Cross-store saga for a document version switch with compensating rollback."""

    def __init__(
        self,
        store: MilvusStore,
        writer: MilvusWriter,
        parent_store: ParentChunkStore | None = None,
        archive_store: MilvusStore | None = None,
        archive_writer: MilvusWriter | None = None,
    ):
        self.store = store
        self.writer = writer
        self.parent_store = parent_store or ParentChunkStore()
        self.archive_store = archive_store
        self.archive_writer = archive_writer

    def _archive_resources(self, document_domain: str):
        if self.archive_store is None:
            kind = (
                "medical_qa_archive"
                if document_domain == "public_medical"
                else "patient_record_archive"
            )
            self.archive_store = get_milvus_store(kind)
        if self.archive_writer is None:
            self.archive_writer = MilvusWriter(
                getattr(self.writer, "embedding_service", None),
                self.archive_store,
            )
        return self.archive_store, self.archive_writer

    @staticmethod
    def _archive_expression(version_id: str) -> str:
        return (
            f'archived_version_id == "{_escape_filter_value(version_id)}"'
        )

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
        archive_inserted_ids: list[int] = []
        old_ids: list[int] = []
        deleted_old_ids: list[int] = []
        raw_replaced = False
        backup_path: Path | None = None
        activation_path = canonical_path.with_name(f".{canonical_path.name}.{update_id}.activate")
        cache_ids: set[str] = set()
        archived_count = 0
        retention_enabled = (
            os.getenv("HEALTHTRACE_VERSION_RETENTION_ENABLED", "true").lower()
            == "true"
        )
        retention_until = datetime.utcnow() + timedelta(
            days=max(1, int(os.getenv("HEALTHTRACE_VERSION_RETENTION_DAYS", "7")))
        )

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

            if (
                old_rows and retention_enabled
            ):
                archive_store, archive_writer = self._archive_resources(document_domain)
                archive_store.init_collection()
                version_db = SessionLocal()
                try:
                    old_active = (
                        version_db.query(DocumentVersion)
                        .filter(
                            DocumentVersion.document_id == document_id,
                            DocumentVersion.status == "active",
                        )
                        .order_by(DocumentVersion.version_number.desc())
                        .first()
                    )
                    old_version_id = old_active.id if old_active is not None else ""
                    old_version_number = (
                        old_active.version_number if old_active is not None else 0
                    )
                finally:
                    version_db.close()
                archive_rows = _archive_vector_payloads(
                    old_rows,
                    version_id=old_version_id,
                    version_number=old_version_number,
                    update_id=update_id,
                    retention_until=retention_until,
                )
                archive_inserted_ids = archive_writer.insert_prepared(archive_rows)
                archived_count = len(archive_rows)

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
                old_parent_rows = parent_query.all()
                old_parent_ids = [row.chunk_id for row in old_parent_rows]
                cache_ids.update(old_parent_ids)

                active_versions = (
                    db.query(DocumentVersion)
                    .filter(
                        DocumentVersion.document_id == document_id,
                        DocumentVersion.status == "active",
                    )
                    .all()
                )
                for active in active_versions:
                    _snapshot_parent_documents(
                        db,
                        version_id=active.id,
                        document_id=document_id,
                        documents=[
                            self.parent_store._to_dict(row) for row in old_parent_rows
                        ],
                    )
                    active.status = "superseded"
                    active.retention_until = retention_until if retention_enabled else None
                    if backup_path is not None:
                        active.storage_uri = str(backup_path)

                parent_query.delete(synchronize_session=False)
                db.flush()
                for document in parent_documents:
                    payload = _parent_payload(document)
                    if payload["chunk_id"]:
                        db.add(ParentChunk(**payload))
                _snapshot_parent_documents(
                    db,
                    version_id=version_id,
                    document_id=document_id,
                    documents=parent_documents,
                )

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
                version.retention_until = None
                version.activated_at = datetime.utcnow()
                version.metadata_json = {
                    "update_id": update_id,
                    "old_vector_rows": len(old_rows),
                    "strategy": "content_fingerprint_embedding_reuse",
                    "archived_vectors": archived_count,
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
                        "archived_vectors": archived_count,
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
                archived_vectors=archived_count,
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
            if archive_inserted_ids and self.archive_store is not None:
                try:
                    _delete_ids(self.archive_store, archive_inserted_ids)
                except Exception:
                    pass
            elif self.archive_store is not None:
                try:
                    self.archive_store.delete(
                        f'archive_update_id == "{_escape_filter_value(update_id)}"'
                    )
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

    @_serialized_document_update
    def rollback(
        self,
        *,
        document_id: str,
        target_version_number: int,
        created_by_user_id: int | None = None,
    ) -> IncrementalRollbackResult:
        """Restore a retained version using copied vector values and fresh Milvus IDs."""
        if os.getenv("HEALTHTRACE_VERSION_RETENTION_ENABLED", "true").lower() != "true":
            raise RuntimeError("Document version retention is disabled")

        db = SessionLocal()
        try:
            record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
            if record is None:
                raise LookupError("Document not found")
            current = (
                db.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.status == "active",
                )
                .order_by(DocumentVersion.version_number.desc())
                .first()
            )
            target = (
                db.query(DocumentVersion)
                .filter(
                    DocumentVersion.document_id == document_id,
                    DocumentVersion.version_number == target_version_number,
                )
                .first()
            )
            if current is None or target is None:
                raise LookupError("Document version not found")
            if current.id == target.id:
                raise ValueError("Target version is already active")
            if target.status not in {"superseded", "deleted"}:
                raise ValueError(f"Target version is not restorable: {target.status}")
            if target.retention_until is None or target.retention_until < datetime.utcnow():
                raise ValueError("Target version retention window has expired")

            snapshots = (
                db.query(DocumentVersionParentChunk)
                .filter(DocumentVersionParentChunk.version_id == target.id)
                .order_by(DocumentVersionParentChunk.created_at.asc())
                .all()
            )
            if not snapshots:
                raise ValueError("Target version has no parent chunk snapshot")
            record_snapshot = {
                "filename": record.filename,
                "file_type": record.file_type,
                "storage_uri": record.storage_uri,
                "content_sha256": record.content_sha256,
                "status": record.status,
                "metadata_json": dict(record.metadata_json or {}),
                "error_message": record.error_message,
            }
            document_domain = record.document_domain
            filename = record.filename
            canonical_path = Path(record.storage_uri)
            target_raw_path = Path(target.storage_uri)
            current_version_id = current.id
            current_version_number = current.version_number
            target_version_id = target.id
            target_sha = target.content_sha256
        finally:
            db.close()

        if not target_raw_path.exists():
            raise FileNotFoundError("Target version source file is not retained")

        update_id = f"rbk-{uuid4().hex}"
        retention_until = datetime.utcnow() + timedelta(
            days=max(1, int(os.getenv("HEALTHTRACE_VERSION_RETENTION_DAYS", "7")))
        )
        archive_store, archive_writer = self._archive_resources(document_domain)
        archive_store.init_collection()
        self.store.init_collection()
        target_archive_rows = _deduplicate_archive_rows(
            archive_store.query_all(
                self._archive_expression(target_version_id),
                output_fields=CORE_VECTOR_FIELDS,
            )
        )
        if not target_archive_rows:
            raise ValueError("Target version vectors are not retained")

        current_rows = self._old_vector_rows(
            document_id=document_id,
            filename=filename,
            document_domain=document_domain,
        )
        current_ids = [
            int(row["id"]) for row in current_rows if row.get("id") is not None
        ]
        target_active_rows = [
            _active_vector_payload(
                row,
                update_id=update_id,
                version_number=target_version_number,
                content_sha256=target_sha,
                canonical_path=canonical_path,
            )
            for row in target_archive_rows
        ]
        current_archive_rows = _archive_vector_payloads(
            current_rows,
            version_id=current_version_id,
            version_number=current_version_number,
            update_id=update_id,
            retention_until=retention_until,
        )

        inserted_active_ids: list[int] = []
        inserted_archive_ids: list[int] = []
        deleted_current_ids: list[int] = []
        raw_switched = False
        current_backup_path = (
            canonical_path.parent
            / ".versions"
            / document_id
            / f"v{current_version_number}-{update_id}"
            / canonical_path.name
        )
        activation_path = canonical_path.with_name(
            f".{canonical_path.name}.{update_id}.activate"
        )
        cache_ids: set[str] = set()
        try:
            inserted_archive_ids = archive_writer.insert_prepared(current_archive_rows)
            inserted_active_ids = self.writer.insert_prepared(target_active_rows)

            canonical_path.parent.mkdir(parents=True, exist_ok=True)
            current_backup_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target_raw_path, activation_path)
            if canonical_path.exists():
                os.replace(canonical_path, current_backup_path)
            os.replace(activation_path, canonical_path)
            raw_switched = True

            db = SessionLocal()
            try:
                record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).one()
                current = (
                    db.query(DocumentVersion)
                    .filter(DocumentVersion.id == current_version_id)
                    .one()
                )
                target = (
                    db.query(DocumentVersion)
                    .filter(DocumentVersion.id == target_version_id)
                    .one()
                )
                parent_query = self._parent_filter(
                    db,
                    document_id,
                    filename,
                    document_domain,
                )
                old_parent_rows = parent_query.all()
                cache_ids.update(row.chunk_id for row in old_parent_rows)
                _snapshot_parent_documents(
                    db,
                    version_id=current.id,
                    document_id=document_id,
                    documents=[self.parent_store._to_dict(row) for row in old_parent_rows],
                )
                parent_query.delete(synchronize_session=False)
                db.flush()

                restored_parent_count = 0
                for snapshot in (
                    db.query(DocumentVersionParentChunk)
                    .filter(DocumentVersionParentChunk.version_id == target.id)
                    .all()
                ):
                    metadata = dict(snapshot.metadata_json or {})
                    metadata.update(
                        {
                            "chunk_id": snapshot.chunk_id,
                            "text": snapshot.text,
                            "document_id": document_id,
                            "file_path": str(canonical_path),
                            "updated_at": datetime.utcnow(),
                        }
                    )
                    db.add(ParentChunk(**metadata))
                    cache_ids.add(snapshot.chunk_id)
                    restored_parent_count += 1

                try:
                    _delete_ids(self.store, current_ids)
                    deleted_current_ids = list(current_ids)
                except PartialVectorDeleteError as delete_error:
                    deleted_current_ids = list(delete_error.deleted_ids)
                    raise

                current.status = "superseded"
                current.storage_uri = str(current_backup_path)
                current.retention_until = retention_until
                current.metadata_json = {
                    **(current.metadata_json or {}),
                    "superseded_by_rollback": update_id,
                    "archived_vectors": len(current_archive_rows),
                }
                target.status = "active"
                target.storage_uri = str(canonical_path)
                target.retention_until = None
                target.activated_at = datetime.utcnow()
                target.metadata_json = {
                    **(target.metadata_json or {}),
                    "reactivated_by": update_id,
                    "reactivated_by_user_id": created_by_user_id,
                }

                record.storage_uri = str(canonical_path)
                record.content_sha256 = target.content_sha256
                record.status = "indexed"
                record.error_message = ""
                record.metadata_json = {
                    **(record.metadata_json or {}),
                    "active_version": target.version_number,
                    "active_version_id": target.id,
                    "parent_chunks": restored_parent_count,
                    "leaf_chunks": len(target_active_rows),
                    "last_rollback": {
                        "update_id": update_id,
                        "from_version": current.version_number,
                        "to_version": target.version_number,
                        "restored_vectors": len(target_active_rows),
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
                try:
                    cache.delete(self.parent_store._cache_key(chunk_id))
                except Exception:
                    pass
            return IncrementalRollbackResult(
                document_id=document_id,
                version=target_version_number,
                previous_version=current_version_number,
                parent_chunks=len(snapshots),
                leaf_chunks=len(target_active_rows),
                restored_vectors=len(target_active_rows),
                archived_vectors=len(current_archive_rows),
            )
        except Exception:
            try:
                if inserted_active_ids:
                    _delete_ids(self.store, inserted_active_ids)
                else:
                    self.store.delete(
                        f'index_update_id == "{_escape_filter_value(update_id)}"'
                    )
            except Exception:
                pass
            try:
                if inserted_archive_ids:
                    _delete_ids(archive_store, inserted_archive_ids)
                else:
                    archive_store.delete(
                        f'archive_update_id == "{_escape_filter_value(update_id)}"'
                    )
            except Exception:
                pass
            if deleted_current_ids:
                try:
                    deleted_set = set(deleted_current_ids)
                    self.writer.insert_prepared(
                        _restorable_vector_rows(
                            [
                                row
                                for row in current_rows
                                if int(row.get("id", -1)) in deleted_set
                            ]
                        )
                    )
                except Exception:
                    pass
            try:
                if raw_switched and current_backup_path.exists():
                    os.replace(current_backup_path, canonical_path)
                if activation_path.exists():
                    activation_path.unlink()
            except Exception:
                pass

            db = SessionLocal()
            try:
                record = db.query(DocumentRecord).filter(DocumentRecord.id == document_id).first()
                if record is not None:
                    for key, value in record_snapshot.items():
                        setattr(record, key, value)
                    record.updated_at = datetime.utcnow()
                db.commit()
            finally:
                db.close()
            raise
