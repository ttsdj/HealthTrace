from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import User
from backend.infra.database import Base
from backend.patient.context import required_resource_types, should_query_patient_context
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
