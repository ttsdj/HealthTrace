from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import User
from backend.infra.database import Base
from backend.patient.facts import create_patient_fact
from backend.patient.planner import plan_patient_tool_calls
from backend.patient.scope import ensure_user_scope
from backend.patient.tools import PatientTools


def _observation(db, scope, *, value, at, status="user_confirmed"):
    return create_patient_fact(
        db,
        scope,
        resource_type="Observation",
        display="家庭血压",
        code="blood_pressure",
        value={"systolic": value, "diastolic": 80, "unit": "mmHg"},
        effective_start=at,
        verification_status=status,
    )


def test_observation_trend_is_scoped_verified_and_sorted(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'trend.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        alice = User(username="alice", password_hash="x")
        bob = User(username="bob", password_hash="x")
        db.add_all([alice, bob])
        db.flush()
        alice_scope = ensure_user_scope(db, alice)
        bob_scope = ensure_user_scope(db, bob)
        start = datetime(2026, 7, 1)
        _observation(db, alice_scope, value=120, at=start)
        _observation(db, alice_scope, value=126, at=start + timedelta(days=3))
        _observation(db, alice_scope, value=200, at=start + timedelta(days=4), status="model_inferred")
        _observation(db, bob_scope, value=190, at=start + timedelta(days=5))
        db.commit()

        result = PatientTools(db, alice_scope).get_observation_trend("血压")

    assert result.status == "ok"
    trend = result.data[0]
    assert trend["count"] == 2
    assert trend["first"] == 120
    assert trend["latest"] == 126
    assert trend["direction"] == "rising"
    assert trend["slope_per_day"] == 2


def test_rule_planner_selects_minimum_allowlisted_tools():
    calls = plan_patient_tool_calls("结合我的血压变化和病历报告解释最近情况")
    names = [item.tool_name for item in calls]
    assert "get_latest_observations" in names
    assert "get_observation_trend" in names
    assert "search_patient_record_text" in names
    assert all(len(item.arguments) <= 2 for item in calls)


def test_llm_planner_failure_falls_back_to_rules(monkeypatch):
    monkeypatch.setenv("PATIENT_TOOL_PLANNER_MODE", "llm")

    def fail(_query):
        raise TimeoutError("synthetic planner timeout")

    calls = plan_patient_tool_calls("我的药物过敏情况", llm_planner=fail)
    names = [item.tool_name for item in calls]
    assert "get_patient_allergies" in names
    assert "get_current_medications" in names
