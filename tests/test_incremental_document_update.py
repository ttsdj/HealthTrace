from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import (
    DocumentRecord,
    DocumentVersion,
    DocumentVersionParentChunk,
    ParentChunk,
)
from backend.indexing.incremental_update import (
    IncrementalDocumentIndexer,
    build_embedding_reuse_plan,
    chunk_content_fingerprint,
)
from backend.indexing.text_normalization import (
    canonicalize_embedding_text,
    embedding_normalization_version,
)
from backend.infra.database import Base
from backend.infra.migrations import (
    apply_phase7_incremental_document_migration,
    apply_phase8_reversible_document_migration,
    get_phase7_migration_status,
    get_phase8_migration_status,
    rollback_phase7_incremental_document_migration,
)


class _FakeStore:
    collection_name = "incremental-test"

    def __init__(self, old_rows, *, fail_old_delete=False):
        self.old_rows = old_rows
        self.fail_old_delete = fail_old_delete
        self.delete_calls = []

    def init_collection(self):
        return None

    def query_all(self, _expression, output_fields=None):
        if output_fields == ["id"]:
            return [{"id": row["id"]} for row in self.old_rows]
        return [dict(row) for row in self.old_rows]

    def query(self, _expression, output_fields=None, limit=1):
        return [{"id": row["id"]} for row in self.old_rows[:limit]]

    def delete(self, expression):
        self.delete_calls.append(expression)
        if self.fail_old_delete and "10" in expression:
            raise RuntimeError("simulated old vector delete failure")
        return {"delete_count": 1}


class _FakeWriter:
    def __init__(self):
        self.precomputed = []
        self.inserted = []
        self.restored = []

    def prepare_documents(
        self,
        documents,
        *,
        precomputed_embeddings=None,
        batch_size=None,
        progress_callback=None,
    ):
        self.precomputed = list(precomputed_embeddings or [])
        rows = []
        for document, vector in zip(documents, self.precomputed):
            rows.append(
                {
                    **document,
                    "dense_embedding": vector or [9.0, 0.0],
                }
            )
        return rows

    def insert_prepared(self, rows, **_kwargs):
        if rows and rows[0].get("index_update_id"):
            self.inserted.extend(rows)
            return [20 + index for index in range(len(rows))]
        self.restored.extend(rows)
        return [30 + index for index in range(len(rows))]


def _old_vector_row():
    return {
        "id": 10,
        "dense_embedding": [1.0, 0.0],
        "text": "stable medical paragraph",
        "filename": "guide.html",
        "file_type": "HTML",
        "file_path": "old",
        "page_number": 0,
        "chunk_idx": 2,
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


def _new_documents(canonical: Path):
    parent = {
        "text": "new parent text",
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
        "text": "stable medical paragraph",
        "filename": "guide.html",
        "file_type": "HTML",
        "file_path": str(canonical),
        "page_number": 0,
        "chunk_idx": 2,
        "chunk_id": "new-leaf-after-positional-shift",
        "parent_chunk_id": "new-parent",
        "root_chunk_id": "new-parent",
        "chunk_level": 3,
    }
    return [parent], [leaf]


def _test_session(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_VERSION_RETENTION_ENABLED", "false")
    engine = create_engine(
        f"sqlite:///{tmp_path / 'incremental.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(
        "backend.indexing.incremental_update.SessionLocal",
        TestSession,
    )
    return engine, TestSession


def _seed_old_version(session_factory, canonical):
    with session_factory() as db:
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
                text="old parent text",
                filename="guide.html",
                file_type="HTML",
                file_path=str(canonical),
                chunk_level=1,
                document_id="public-guide",
                document_domain="public_medical",
            )
        )
        db.commit()


def test_content_fingerprint_reuses_vectors_across_positional_id_changes():
    old = _old_vector_row()
    new = [{"text": old["text"], "chunk_id": "different-positional-id"}]

    embeddings, reused, embedded = build_embedding_reuse_plan(new, [old])

    assert chunk_content_fingerprint(new[0]["text"]) == new[0]["content_fingerprint"]
    assert embeddings == [[1.0, 0.0]]
    assert reused == 1
    assert embedded == 0


def test_embedding_identity_normalizes_width_and_whitespace(monkeypatch):
    monkeypatch.delenv("DOCUMENT_EMBEDDING_IGNORE_LINE_PATTERNS", raising=False)
    assert canonicalize_embedding_text("Ａ药\t适用\n于 某病") == "A药 适用 于 某病"
    assert chunk_content_fingerprint("Ａ药\t适用\n于 某病") == chunk_content_fingerprint(
        "A药 适用 于 某病"
    )


def test_embedding_identity_can_exclude_configured_metadata_lines(monkeypatch):
    monkeypatch.setenv(
        "DOCUMENT_EMBEDDING_IGNORE_LINE_PATTERNS",
        r"^生成时间[:：].*$;;^导出时间[:：].*$",
    )
    assert chunk_content_fingerprint(
        "正文\n生成时间：2026-07-25"
    ) == chunk_content_fingerprint("正文\n生成时间：2026-07-26")


def test_changed_normalization_rules_force_reembedding(monkeypatch):
    monkeypatch.delenv("DOCUMENT_EMBEDDING_IGNORE_LINE_PATTERNS", raising=False)
    old = _old_vector_row()
    monkeypatch.setenv("DOCUMENT_EMBEDDING_IGNORE_LINE_PATTERNS", r"^生成时间[:：].*$")
    new = [{"text": old["text"], "chunk_id": "same-content"}]

    embeddings, reused, embedded = build_embedding_reuse_plan(new, [old])

    assert embeddings == [None]
    assert reused == 0
    assert embedded == 1


def test_phase7_migration_is_additive_and_rollback_preserves_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'phase7.db'}")

    result = apply_phase7_incremental_document_migration(engine)
    assert result["status"] == "applied"
    assert get_phase7_migration_status(engine)["status"] == "applied"

    rolled_back = rollback_phase7_incremental_document_migration(engine)
    assert rolled_back["data_preserved"] is True
    assert get_phase7_migration_status(engine)["status"] == "rolled_back"


def test_phase8_migration_adds_reversible_version_storage(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'phase8.db'}")
    apply_phase7_incremental_document_migration(engine)

    result = apply_phase8_reversible_document_migration(engine)

    assert result["status"] == "applied"
    assert get_phase8_migration_status(engine)["status"] == "applied"


def test_incremental_switch_reuses_embedding_and_activates_version(tmp_path, monkeypatch):
    _, TestSession = _test_session(tmp_path, monkeypatch)
    canonical = tmp_path / "guide.html"
    staging = tmp_path / "staging" / "guide.html"
    canonical.write_text("old raw", encoding="utf-8")
    staging.parent.mkdir()
    staging.write_text("new raw", encoding="utf-8")
    _seed_old_version(TestSession, canonical)
    parents, leaves = _new_documents(canonical)
    store = _FakeStore([_old_vector_row()])
    writer = _FakeWriter()

    result = IncrementalDocumentIndexer(store, writer).apply(
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

    assert result.version == 2
    assert result.reused_vectors == 1
    assert result.embedded_vectors == 0
    assert canonical.read_text(encoding="utf-8") == "new raw"
    assert writer.precomputed == [[1.0, 0.0]]
    assert any("10" in expression for expression in store.delete_calls)
    with TestSession() as db:
        record = db.query(DocumentRecord).filter_by(id="public-guide").one()
        assert record.status == "indexed"
        assert record.metadata_json["active_version"] == 2
        assert db.query(ParentChunk).filter_by(document_id="public-guide").one().chunk_id == "new-parent"
        versions = (
            db.query(DocumentVersion)
            .filter_by(document_id="public-guide")
            .order_by(DocumentVersion.version_number)
            .all()
        )
        assert [item.status for item in versions] == ["superseded", "active"]


def test_same_hash_is_not_noop_when_vectors_are_missing(tmp_path, monkeypatch):
    _, TestSession = _test_session(tmp_path, monkeypatch)
    canonical = tmp_path / "guide.html"
    staging = tmp_path / "staging" / "guide.html"
    canonical.write_text("same raw", encoding="utf-8")
    staging.parent.mkdir()
    staging.write_text("same raw", encoding="utf-8")
    _seed_old_version(TestSession, canonical)
    with TestSession() as db:
        record = db.query(DocumentRecord).filter_by(id="public-guide").one()
        record.content_sha256 = "same-sha"
        db.commit()
    parents, leaves = _new_documents(canonical)
    store = _FakeStore([])
    writer = _FakeWriter()

    result = IncrementalDocumentIndexer(store, writer).apply(
        document_id="public-guide",
        document_domain="public_medical",
        filename="guide.html",
        file_type="HTML",
        canonical_path=canonical,
        staging_path=staging,
        content_sha256="same-sha",
        parent_documents=parents,
        leaf_documents=leaves,
    )

    assert result.unchanged is False
    assert result.embedded_vectors == 1
    assert len(writer.inserted) == 1


def test_failed_old_vector_delete_restores_raw_db_and_active_version(tmp_path, monkeypatch):
    _, TestSession = _test_session(tmp_path, monkeypatch)
    canonical = tmp_path / "guide.html"
    staging = tmp_path / "staging" / "guide.html"
    canonical.write_text("old raw", encoding="utf-8")
    staging.parent.mkdir()
    staging.write_text("new raw", encoding="utf-8")
    _seed_old_version(TestSession, canonical)
    parents, leaves = _new_documents(canonical)
    store = _FakeStore([_old_vector_row()], fail_old_delete=True)
    writer = _FakeWriter()

    with pytest.raises(RuntimeError, match="simulated old vector delete failure"):
        IncrementalDocumentIndexer(store, writer).apply(
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

    assert canonical.read_text(encoding="utf-8") == "old raw"
    assert any("20" in expression for expression in store.delete_calls)
    with TestSession() as db:
        record = db.query(DocumentRecord).filter_by(id="public-guide").one()
        assert record.status == "indexed"
        assert record.content_sha256 == "old-sha"
        assert db.query(ParentChunk).filter_by(document_id="public-guide").one().chunk_id == "old-parent"
        active = db.query(DocumentVersion).filter_by(document_id="public-guide", status="active").one()
        failed = db.query(DocumentVersion).filter_by(document_id="public-guide", status="failed").one()
        assert active.version_number == 1
        assert failed.version_number == 2
