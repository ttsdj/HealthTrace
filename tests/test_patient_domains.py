from __future__ import annotations

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from backend.db.models import DocumentRecord, PatientProfile, Tenant, User
from backend.infra.database import Base
from backend.infra.migrations import PHASE1_VERSION, apply_phase1_migration, rollback_phase1_migration
from backend.patient.documents import scope_document_chunks
from backend.patient.retrieval import patient_scope_filter
from backend.patient.scope import PatientScope, ensure_user_scope


def test_user_scopes_and_document_queries_are_isolated(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'scopes.db'}")
    Base.metadata.create_all(engine)

    with Session(engine) as db:
        alice = User(username="alice", password_hash="x", role="user")
        bob = User(username="bob", password_hash="x", role="user")
        db.add_all([alice, bob])
        db.flush()
        alice_scope = ensure_user_scope(db, alice)
        bob_scope = ensure_user_scope(db, bob)
        db.add_all(
            [
                DocumentRecord(
                    id="doc-alice",
                    document_domain="patient_private",
                    tenant_id=alice_scope.tenant_id,
                    patient_id=alice_scope.patient_id,
                    owner_user_id=alice.id,
                    filename="alice.pdf",
                ),
                DocumentRecord(
                    id="doc-bob",
                    document_domain="patient_private",
                    tenant_id=bob_scope.tenant_id,
                    patient_id=bob_scope.patient_id,
                    owner_user_id=bob.id,
                    filename="bob.pdf",
                ),
            ]
        )
        db.commit()

        alice_docs = (
            db.query(DocumentRecord)
            .filter(
                DocumentRecord.tenant_id == alice_scope.tenant_id,
                DocumentRecord.patient_id == alice_scope.patient_id,
                DocumentRecord.owner_user_id == alice.id,
            )
            .all()
        )

    assert alice_scope.tenant_id != bob_scope.tenant_id
    assert alice_scope.patient_id != bob_scope.patient_id
    assert [item.id for item in alice_docs] == ["doc-alice"]


def test_patient_chunk_ids_are_namespaced_and_scoped():
    scope = PatientScope("tenant-a", "patient-a", 7, "alice")
    docs = [
        {
            "chunk_id": "leaf-1",
            "parent_chunk_id": "parent-1",
            "root_chunk_id": "parent-1",
            "text": "private evidence",
        },
        {
            "chunk_id": "parent-1",
            "parent_chunk_id": "",
            "root_chunk_id": "parent-1",
            "text": "private parent",
        },
    ]

    scoped = scope_document_chunks(
        docs,
        document_id="doc-1",
        scope=scope,
        storage_uri="private/path.pdf",
    )

    assert scoped[0]["chunk_id"].startswith("doc-1:")
    assert scoped[0]["parent_chunk_id"] == scoped[1]["chunk_id"]
    assert scoped[0]["tenant_id"] == "tenant-a"
    assert scoped[0]["patient_id"] == "patient-a"
    assert scoped[0]["document_domain"] == "patient_private"


def test_patient_milvus_filter_is_mandatory_and_server_scoped():
    scope = PatientScope("tenant-a", "patient-a", 7, "alice")

    expression = patient_scope_filter(scope)

    assert 'document_domain == "patient_private"' in expression
    assert 'tenant_id == "tenant-a"' in expression
    assert 'patient_id == "patient-a"' in expression
    assert "chunk_level == 3" in expression


def test_phase1_migration_is_additive_and_rollback_preserves_data(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy.db'}")
    with engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE users ("
                "id INTEGER PRIMARY KEY, username VARCHAR(100) NOT NULL UNIQUE, "
                "password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL, "
                "created_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE parent_chunks ("
                "chunk_id VARCHAR(512) PRIMARY KEY, text TEXT NOT NULL, "
                "filename VARCHAR(255) NOT NULL, file_type VARCHAR(50) NOT NULL, "
                "file_path VARCHAR(1024) NOT NULL, page_number INTEGER NOT NULL, "
                "parent_chunk_id VARCHAR(512) NOT NULL, root_chunk_id VARCHAR(512) NOT NULL, "
                "chunk_level INTEGER NOT NULL, chunk_idx INTEGER NOT NULL, updated_at DATETIME NOT NULL)"
            )
        )
        conn.execute(
            text(
                "INSERT INTO users VALUES "
                "(1, 'legacy', 'x', 'user', '2026-01-01 00:00:00')"
            )
        )
        conn.execute(
            text(
                "INSERT INTO parent_chunks VALUES "
                "('old-chunk', 'legacy text', 'old.pdf', 'PDF', '', 1, '', '', 1, 0, "
                "'2026-01-01 00:00:00')"
            )
        )

    result = apply_phase1_migration(engine)
    assert result["status"] == "applied"
    assert {item["name"] for item in inspect(engine).get_columns("parent_chunks")} >= {
        "document_domain",
        "tenant_id",
        "patient_id",
        "document_id",
    }

    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM parent_chunks")).scalar_one() == 1
        assert conn.execute(text("SELECT document_domain FROM parent_chunks")).scalar_one() == "public_medical"
        assert conn.execute(text("SELECT COUNT(*) FROM patient_profiles")).scalar_one() == 1

    rollback = rollback_phase1_migration(engine)
    assert rollback == {
        "version": PHASE1_VERSION,
        "status": "rolled_back",
        "data_preserved": True,
    }
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM parent_chunks")).scalar_one() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM patient_profiles")).scalar_one() == 1
