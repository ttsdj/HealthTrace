from datetime import datetime

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session

from backend.db.models import (
    DocumentRecord,
    ParentChunk,
    PatientFact,
    PatientTimelineEvent,
    User,
)
from backend.infra.database import Base
from backend.infra.migrations import (
    PHASE2_VERSION,
    apply_phase2_fact_candidate_migration,
    get_phase2_migration_status,
)
from backend.patient import fact_candidates as candidate_module
from backend.patient.fact_candidates import (
    confirm_fact_candidate,
    extract_candidates_for_document,
    list_fact_candidates,
    reject_fact_candidate,
)
from backend.patient.scope import ensure_user_scope


def _seed_document(db: Session, username: str, document_id: str, text: str):
    user = User(username=username, password_hash="x", role="user")
    db.add(user)
    db.flush()
    scope = ensure_user_scope(db, user)
    db.add(
        DocumentRecord(
            id=document_id,
            document_domain="patient_private",
            tenant_id=scope.tenant_id,
            patient_id=scope.patient_id,
            owner_user_id=user.id,
            filename=f"{document_id}.html",
            file_type="HTML",
            status="indexed",
        )
    )
    db.add(
        ParentChunk(
            chunk_id=f"{document_id}:parent",
            text=text,
            filename=f"{document_id}.html",
            file_type="HTML",
            chunk_level=1,
            document_id=document_id,
            document_domain="patient_private",
            tenant_id=scope.tenant_id,
            patient_id=scope.patient_id,
            owner_user_id=user.id,
        )
    )
    db.flush()
    return scope


def test_phase2_candidate_migration_is_additive_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'phase2.db'}")
    tables = [
        table
        for table in Base.metadata.sorted_tables
        if table.name != "patient_fact_candidates"
    ]
    Base.metadata.create_all(engine, tables=tables)

    assert get_phase2_migration_status(engine)["status"] == "not_applied"
    first = apply_phase2_fact_candidate_migration(engine)
    second = apply_phase2_fact_candidate_migration(engine)

    assert first == {
        "version": PHASE2_VERSION,
        "status": "applied",
        "changes": ["patient_fact_candidates"],
    }
    assert second["changes"] == []
    assert inspect(engine).has_table("patient_fact_candidates")


def test_candidate_requires_confirmation_before_fact_and_timeline(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'candidate.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        alice_scope = _seed_document(
            db,
            "alice",
            "doc-alice",
            "2026-07-20\n过敏史：青霉素过敏\n血压：128/82 mmHg",
        )
        bob_scope = _seed_document(db, "bob", "doc-bob", "诊断：合成疾病")
        db.commit()

        candidates, method, warning = extract_candidates_for_document(
            db,
            alice_scope,
            "doc-alice",
            use_llm=False,
            consent_external_processing=False,
        )
        db.commit()

        assert method == "local_rules"
        assert warning == ""
        assert {item.resource_type for item in candidates} == {
            "AllergyIntolerance",
            "Observation",
        }
        assert all(item.status == "pending" for item in candidates)
        assert db.query(PatientFact).count() == 0
        assert db.query(PatientTimelineEvent).count() == 0
        assert list_fact_candidates(db, bob_scope) == []

        allergy = next(item for item in candidates if item.resource_type == "AllergyIntolerance")
        confirmed_candidate, fact = confirm_fact_candidate(db, alice_scope, allergy.id, {})
        db.commit()

        assert confirmed_candidate.status == "confirmed"
        assert fact.verification_status == "user_confirmed"
        assert fact.source_type == "document_extracted_user_confirmed"
        assert db.query(PatientFact).count() == 1
        assert db.query(PatientTimelineEvent).count() == 1

        same_candidate, same_fact = confirm_fact_candidate(db, alice_scope, allergy.id, {})
        assert same_candidate.id == confirmed_candidate.id
        assert same_fact.id == fact.id
        assert db.query(PatientFact).count() == 1

        observation = next(item for item in candidates if item.resource_type == "Observation")
        rejected = reject_fact_candidate(db, alice_scope, observation.id)
        db.commit()
        assert rejected.status == "rejected"
        assert db.query(PatientFact).count() == 1

        with pytest.raises(ValueError, match="not found"):
            confirm_fact_candidate(db, bob_scope, allergy.id, {})


def test_llm_extraction_requires_consent_and_falls_back_to_rules(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'candidate-fallback.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        scope = _seed_document(
            db,
            "alice",
            "doc-alice",
            "2026-07-20\n当前用药：二甲双胍 500mg",
        )
        db.commit()

        with pytest.raises(ValueError, match="Explicit consent"):
            extract_candidates_for_document(
                db,
                scope,
                "doc-alice",
                use_llm=True,
                consent_external_processing=False,
            )

        monkeypatch.setattr(
            candidate_module,
            "_llm_extract",
            lambda text: (_ for _ in ()).throw(RuntimeError("synthetic model outage")),
        )
        candidates, method, warning = extract_candidates_for_document(
            db,
            scope,
            "doc-alice",
            use_llm=True,
            consent_external_processing=True,
        )

    assert method == "local_rules_fallback"
    assert "synthetic model outage" in warning
    assert [item.resource_type for item in candidates] == ["MedicationStatement"]
