from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from backend.db.models import DocumentRecord, DocumentVersion
from backend.indexing.milvus_client import get_milvus_store


@dataclass(frozen=True)
class VersionRetentionResult:
    scanned_versions: int
    purged_versions: int
    deleted_vectors: int
    dry_run: bool

    def to_dict(self) -> dict:
        return asdict(self)


def _archive_kind(document_domain: str) -> str:
    return (
        "medical_qa_archive"
        if document_domain == "public_medical"
        else "patient_record_archive"
    )


def purge_expired_document_versions(
    db: Session,
    *,
    now: datetime | None = None,
    limit: int = 50,
    dry_run: bool = False,
) -> VersionRetentionResult:
    """Purge expired cold vectors while preserving version and parent audit rows."""
    timestamp = now or datetime.utcnow()
    rows = (
        db.query(DocumentVersion, DocumentRecord)
        .join(DocumentRecord, DocumentRecord.id == DocumentVersion.document_id)
        .filter(
            DocumentVersion.status.in_(["superseded", "deleted"]),
            DocumentVersion.retention_until.is_not(None),
            DocumentVersion.retention_until <= timestamp,
        )
        .order_by(DocumentVersion.retention_until.asc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    if dry_run:
        return VersionRetentionResult(
            scanned_versions=len(rows),
            purged_versions=0,
            deleted_vectors=0,
            dry_run=True,
        )

    deleted_vectors = 0
    purged_versions = 0
    stores = {}
    for version, record in rows:
        kind = _archive_kind(record.document_domain)
        store = stores.setdefault(kind, get_milvus_store(kind))
        store.init_collection()
        escaped_id = version.id.replace("\\", "\\\\").replace('"', '\\"')
        result = store.delete(f'archived_version_id == "{escaped_id}"')
        if isinstance(result, dict):
            deleted_vectors += int(result.get("delete_count", 0) or 0)
        version.status = "expired"
        version.metadata_json = {
            **(version.metadata_json or {}),
            "archive_purged_at": timestamp.isoformat(),
            "archive_raw_file_preserved": True,
        }
        purged_versions += 1

    if rows:
        db.flush()
    return VersionRetentionResult(
        scanned_versions=len(rows),
        purged_versions=purged_versions,
        deleted_vectors=deleted_vectors,
        dry_run=False,
    )


def retention_gc_enabled() -> bool:
    return (
        os.getenv("HEALTHTRACE_VERSION_RETENTION_ENABLED", "true").lower() == "true"
        and os.getenv("HEALTHTRACE_VERSION_GC_ENABLED", "true").lower() == "true"
    )
