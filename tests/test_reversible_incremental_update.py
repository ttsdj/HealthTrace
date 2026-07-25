import re
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    DocumentRecord,
    DocumentVersion,
    DocumentVersionParentChunk,
    ParentChunk,
)
from backend.indexing.incremental_update import IncrementalDocumentIndexer
from backend.indexing.text_normalization import embedding_normalization_version
from backend.indexing.version_retention import purge_expired_document_versions
from backend.infra.database import Base


class _MutableStore:
    def __init__(self, collection_name: str, rows: list[dict], next_id: int):
        self.collection_name = collection_name
        self.rows = [dict(row) for row in rows]
        self.next_id = next_id

    def init_collection(self):
        return None

    def query(self, expression="", output_fields=None, limit=10000):
        return self.query_all(expression, output_fields=output_fields)[:limit]

    def query_all(self, expression="", output_fields=None):
        rows = list(self.rows)
        match = re.search(r'archived_version_id == "([^"]+)"', expression)
        if match:
            rows = [
                row
                for row in rows
                if row.get("archived_version_id") == match.group(1)
            ]
        if output_fields:
            return [
                {field: row.get(field) for field in output_fields if field in row}
                for row in rows
            ]
        return [dict(row) for row in rows]

    def delete(self, expression):
        id_match = re.search(r"id in \[([0-9,]+)\]", expression)
        if id_match:
            ids = {int(value) for value in id_match.group(1).split(",")}
            before = len(self.rows)
            self.rows = [row for row in self.rows if int(row.get("id", -1)) not in ids]
            return {"delete_count": before - len(self.rows)}
        update_match = re.search(r'(?:index|archive)_update_id == "([^"]+)"', expression)
        if update_match:
            update_id = update_match.group(1)
            before = len(self.rows)
            self.rows = [
                row
                for row in self.rows
                if row.get("index_update_id") != update_id
                and row.get("archive_update_id") != update_id
            ]
            return {"delete_count": before - len(self.rows)}
        version_match = re.search(r'archived_version_id == "([^"]+)"', expression)
        if version_match:
            version_id = version_match.group(1)
            before = len(self.rows)
            self.rows = [
                row
                for row in self.rows
                if row.get("archived_version_id") != version_id
            ]
            return {"delete_count": before - len(self.rows)}
        return {"delete_count": 0}


class _MutableWriter:
    embedding_service = None

    def __init__(self, store: _MutableStore):
        self.store = store

    def prepare_documents(self, documents, *, precomputed_embeddings=None, **_kwargs):
        return [
            {
                **document,
                "dense_embedding": vector or [0.0, 1.0],
            }
            for document, vector in zip(documents, precomputed_embeddings or [])
        ]

    def insert_prepared(self, rows, **_kwargs):
        ids = []
        for row in rows:
            row_id = self.store.next_id
            self.store.next_id += 1
            self.store.rows.append({"id": row_id, **row})
            ids.append(row_id)
        return ids


def _old_row(canonical: Path):
    return {
        "id": 10,
        "dense_embedding": [1.0, 0.0],
        "text": "stable paragraph",
        "filename": "guide.html",
        "file_type": "HTML",
        "file_path": str(canonical),
        "page_number": 0,
        "chunk_idx": 1,
        "chunk_id": "old-leaf",
        "parent_chunk_id": "old-parent",
        "root_chunk_id": "old-parent",
        "chunk_level": 3,
        "document_id": "public-guide",
        "document_domain": "public_medical",
        "tenant_id": "",
        "patient_id": "",
        "owner_user_id": 0,
        "embedding_normalization_version": embedding_normalization_version(),
    }


def _chunks(canonical: Path):
    parent = {
        "text": "new parent",
        "filename": "guide.html",
        "file_type": "HTML",
        "file_path": str(canonical),
        "page_number": 0,
        "chunk_idx": 0,
        "chunk_id": "new-parent",
        "parent_chunk_id": "",
        "root_chunk_id": "new-parent",
        "chunk_level": 1,
    }
    leaf = {
        "text": "stable paragraph",
        "filename": "guide.html",
        "file_type": "HTML",
        "file_path": str(canonical),
        "page_number": 0,
        "chunk_idx": 1,
        "chunk_id": "new-leaf",
        "parent_chunk_id": "new-parent",
        "root_chunk_id": "new-parent",
        "chunk_level": 3,
    }
    return [parent], [leaf]


def test_update_copies_vector_value_to_new_archive_id_and_rolls_back(
    tmp_path, monkeypatch
):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'rollback.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr("backend.indexing.incremental_update.SessionLocal", TestSession)
    monkeypatch.setenv("HEALTHTRACE_VERSION_RETENTION_ENABLED", "true")
    monkeypatch.setenv("HEALTHTRACE_VERSION_RETENTION_DAYS", "7")

    canonical = tmp_path / "guide.html"
    staging = tmp_path / "staging" / "guide.html"
    canonical.write_text("old raw", encoding="utf-8")
    staging.parent.mkdir()
    staging.write_text("new raw", encoding="utf-8")
    with TestSession() as db:
        db.add(
            DocumentRecord(
                id="public-guide",
                document_domain="public_medical",
                filename="guide.html",
                file_type="HTML",
                storage_uri=str(canonical),
                content_sha256="old-sha",
                status="indexed",
                metadata_json={"parent_chunks": 1, "leaf_chunks": 1},
            )
        )
        db.add(
            ParentChunk(
                chunk_id="old-parent",
                text="old parent",
                filename="guide.html",
                file_type="HTML",
                file_path=str(canonical),
                chunk_level=1,
                document_id="public-guide",
                document_domain="public_medical",
            )
        )
        db.commit()

    active_store = _MutableStore("active", [_old_row(canonical)], 100)
    archive_store = _MutableStore("archive", [], 1000)
    indexer = IncrementalDocumentIndexer(
        active_store,
        _MutableWriter(active_store),
        archive_store=archive_store,
        archive_writer=_MutableWriter(archive_store),
    )
    parents, leaves = _chunks(canonical)

    update = indexer.apply(
        document_id="public-guide",
        document_domain="public_medical",
        filename="guide.html",
        file_type="HTML",
        canonical_path=canonical,
        staging_path=staging,
        content_sha256="new-sha",
        parent_documents=parents,
        leaf_documents=leaves,
    )

    assert update.archived_vectors == 1
    assert archive_store.rows[0]["id"] == 1000
    assert archive_store.rows[0]["id"] != 10
    assert archive_store.rows[0]["dense_embedding"] == [1.0, 0.0]
    with TestSession() as db:
        old_version = db.query(DocumentVersion).filter_by(version_number=1).one()
        assert old_version.retention_until is not None
        assert (
            db.query(DocumentVersionParentChunk)
            .filter_by(version_id=old_version.id)
            .count()
            == 1
        )

    rollback = indexer.rollback(
        document_id="public-guide",
        target_version_number=1,
    )

    assert rollback.version == 1
    assert rollback.previous_version == 2
    assert canonical.read_text(encoding="utf-8") == "old raw"
    assert len(active_store.rows) == 1
    assert active_store.rows[0]["id"] not in {10, 100}
    assert active_store.rows[0]["dense_embedding"] == [1.0, 0.0]
    with TestSession() as db:
        assert db.query(DocumentVersion).filter_by(version_number=1).one().status == "active"
        assert (
            db.query(DocumentVersion).filter_by(version_number=2).one().status
            == "superseded"
        )
        assert (
            db.query(ParentChunk).filter_by(document_id="public-guide").one().chunk_id
            == "old-parent"
        )


def test_retention_gc_purges_only_vectors_and_preserves_raw_file(
    tmp_path, monkeypatch
):
    engine = create_engine(f"sqlite:///{tmp_path / 'gc.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    raw_path = tmp_path / "retained.html"
    raw_path.write_text("retained raw", encoding="utf-8")
    archive_store = _MutableStore(
        "archive",
        [
            {
                "id": 1000,
                "archived_version_id": "version-old",
                "dense_embedding": [1.0, 0.0],
                "text": "old",
            }
        ],
        1001,
    )
    monkeypatch.setattr(
        "backend.indexing.version_retention.get_milvus_store",
        lambda _kind: archive_store,
    )
    with TestSession() as db:
        db.add(
            DocumentRecord(
                id="public-guide",
                document_domain="public_medical",
                filename="guide.html",
                storage_uri=str(raw_path),
                status="indexed",
            )
        )
        db.add(
            DocumentVersion(
                id="version-old",
                document_id="public-guide",
                version_number=1,
                storage_uri=str(raw_path),
                status="superseded",
                retention_until=datetime.utcnow() - timedelta(seconds=1),
            )
        )
        db.commit()

        result = purge_expired_document_versions(db)
        db.commit()

        version = db.query(DocumentVersion).filter_by(id="version-old").one()
        assert result.purged_versions == 1
        assert version.status == "expired"
        assert raw_path.exists()
        assert archive_store.rows == []
