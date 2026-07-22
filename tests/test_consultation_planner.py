from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agent import planner
from backend.agent.planner import finalize_evidence_state, plan_consultation, preflight_response
from backend.agent.state import AgentAction, EvidenceState
from backend.db.models import User
from backend.infra.database import Base
from backend.patient.facts import create_patient_fact
from backend.patient.scope import ensure_user_scope


def test_high_risk_query_is_escalated_without_retrieval():
    plan = plan_consultation("missing-user", "我胸痛而且喘不过气")

    assert plan.evidence_state == EvidenceState.HIGH_RISK
    assert plan.action == AgentAction.ESCALATE_URGENT
    assert plan.required_evidence_sources == []
    assert "120" in preflight_response(plan)


def test_personal_medication_query_asks_for_missing_safety_fields(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    with TestSession() as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        ensure_user_scope(db, user)
        db.commit()
    monkeypatch.setattr(planner, "SessionLocal", TestSession)

    plan = plan_consultation("alice", "我能不能吃布洛芬片？")

    assert plan.action == AgentAction.ASK
    assert plan.evidence_state == EvidenceState.PATIENT_DATA_MISSING
    assert set(plan.missing_fields) == {"allergies", "current_medications", "conditions"}
    assert "过敏" in preflight_response(plan)


def test_personal_medication_query_passes_after_required_facts_exist(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'complete.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    with TestSession() as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        for resource_type, display in (
            ("AllergyIntolerance", "否认已知药物过敏"),
            ("MedicationStatement", "目前未使用其他药物"),
            ("Condition", "无已确认慢性病"),
        ):
            create_patient_fact(
                db,
                scope,
                resource_type=resource_type,
                display=display,
                effective_start=datetime(2026, 7, 22),
            )
        db.commit()
    monkeypatch.setattr(planner, "SessionLocal", TestSession)

    plan = plan_consultation("alice", "我能不能吃布洛芬片？")

    assert plan.action == AgentAction.ANSWER
    assert plan.missing_fields == []
    assert "patient_facts" in plan.required_evidence_sources
    assert "public_rag" in plan.required_evidence_sources


def test_personal_report_query_asks_for_missing_observations(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'missing-report.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    with TestSession() as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        ensure_user_scope(db, user)
        db.commit()
    monkeypatch.setattr(planner, "SessionLocal", TestSession)

    plan = plan_consultation("alice", "请结合我的检查报告解释血糖指标")

    assert plan.action == AgentAction.ASK
    assert plan.evidence_state == EvidenceState.PATIENT_DATA_MISSING
    assert set(plan.missing_fields) == {"observations", "diagnostic_reports"}
    assert plan.action_reason == "required_patient_context_missing"
    assert "检查" in preflight_response(plan)


def test_evidence_state_is_finalized_from_runtime_trace():
    assert finalize_evidence_state({"retrieved_chunks": [{"text": "evidence"}]})[
        "evidence_state"
    ] == "SUFFICIENT"
    assert finalize_evidence_state({"retrieval_mode": "no_results"})["action"] == "REFUSE"
    assert finalize_evidence_state({"conflict_detected": True})["evidence_state"] == "CONFLICTING"
