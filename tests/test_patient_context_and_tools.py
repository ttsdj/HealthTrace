from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import DocumentRecord, User
from backend.infra.database import Base
from backend.patient import context as context_module
from backend.patient.context import (
    build_verified_patient_context,
    required_resource_types,
    should_query_patient_context,
)
from backend.patient.facts import create_patient_fact
from backend.patient.scope import ensure_user_scope
from backend.patient.tools import PatientTools


def test_context_planner_does_not_access_patient_data_for_general_question():
    assert should_query_patient_context("高血压通常有哪些症状？") is False
    assert should_query_patient_context("结合我的病史，我能服用这个药吗？") is True
    assert required_resource_types("结合我的病史，我能服用这个药吗？") == [
        "AllergyIntolerance",
        "MedicationStatement",
        "Condition",
    ]


def test_typed_patient_tools_return_only_scoped_verified_facts(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tools.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        alice = User(username="alice", password_hash="x", role="user")
        bob = User(username="bob", password_hash="x", role="user")
        db.add_all([alice, bob])
        db.flush()
        alice_scope = ensure_user_scope(db, alice)
        bob_scope = ensure_user_scope(db, bob)
        create_patient_fact(
            db,
            alice_scope,
            resource_type="AllergyIntolerance",
            display="青霉素过敏",
            effective_start=datetime(2020, 1, 1),
        )
        create_patient_fact(
            db,
            bob_scope,
            resource_type="AllergyIntolerance",
            display="Bob allergy",
            effective_start=datetime(2020, 1, 1),
        )
        db.commit()

        result = PatientTools(db, alice_scope).get_patient_allergies()

    assert result.status == "ok"
    assert [item["display"] for item in result.data] == ["青霉素过敏"]
    assert result.data[0]["verification_status"] == "user_confirmed"


def test_write_tool_only_creates_confirmation_required_draft(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'write-tool.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        tools = PatientTools(db, scope)
        result = tools.create_health_reminder_draft(
            title="复查",
            due_at=datetime(2026, 8, 1),
            idempotency_key="tool-reminder-0001",
        )
        db.commit()

    assert result.status == "ok"
    assert result.data[0]["status"] == "waiting_confirmation"
    assert result.data[0]["confirmation_required"] is True


def test_personal_context_includes_only_the_authenticated_patient_record(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'patient-context.db'}")
    Base.metadata.create_all(engine)
    TestSession = lambda: Session(engine)
    with TestSession() as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        db.add(
            DocumentRecord(
                id="doc-alice",
                document_domain="patient_private",
                tenant_id=scope.tenant_id,
                patient_id=scope.patient_id,
                owner_user_id=user.id,
                filename="alice-report.pdf",
                status="indexed",
            )
        )
        db.commit()

    monkeypatch.setattr(context_module, "SessionLocal", TestSession)

    def fake_retrieve(query, resolved_scope, top_k):
        assert resolved_scope.tenant_id == scope.tenant_id
        assert resolved_scope.patient_id == scope.patient_id
        assert top_k == 3
        return {
            "docs": [
                {
                    "filename": "alice-report.pdf",
                    "page_number": 1,
                    "text": "合成患者报告证据",
                }
            ],
            "mode": "hybrid",
            "attempts": [{"mode": "hybrid", "status": "ok", "count": 1}],
        }

    monkeypatch.setattr(context_module, "retrieve_patient_records", fake_retrieve)

    rendered, metadata = build_verified_patient_context("alice", "结合我的病历解释近期咳嗽")

    assert "合成患者报告证据" in rendered
    assert "未结构化核验" in rendered
    assert metadata["patient_record_accessed"] is True
    assert metadata["patient_record_hits"] == 1
    assert metadata["patient_record_mode"] == "hybrid"


def test_structured_facts_survive_patient_record_retrieval_failure(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'patient-context-fallback.db'}")
    Base.metadata.create_all(engine)
    TestSession = lambda: Session(engine)
    with TestSession() as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        create_patient_fact(
            db,
            scope,
            resource_type="Condition",
            display="已核验的合成病史",
            effective_start=datetime(2026, 7, 1),
        )
        db.add(
            DocumentRecord(
                id="doc-alice",
                document_domain="patient_private",
                tenant_id=scope.tenant_id,
                patient_id=scope.patient_id,
                owner_user_id=user.id,
                filename="alice-report.pdf",
                status="indexed",
            )
        )
        db.commit()

    monkeypatch.setattr(context_module, "SessionLocal", TestSession)
    monkeypatch.setattr(
        context_module,
        "retrieve_patient_records",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("synthetic outage")),
    )

    rendered, metadata = build_verified_patient_context("alice", "结合我的病史说明风险")

    assert "已核验的合成病史" in rendered
    assert metadata["patient_fact_count"] == 1
    assert metadata["patient_record_mode"] == "unavailable"
