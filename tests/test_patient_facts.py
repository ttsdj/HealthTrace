from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import PatientTimelineEvent, User
from backend.infra.database import Base
from backend.patient.facts import create_patient_fact, get_patient_timeline, list_patient_facts
from backend.patient.scope import ensure_user_scope


def test_fhir_like_fact_creates_traceable_timeline_event(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        fact = create_patient_fact(
            db,
            scope,
            resource_type="Observation",
            display="空腹血糖",
            effective_start=datetime(2026, 7, 1, 8, 30),
            code_system="LOINC",
            code="1558-6",
            value={"value": 6.2, "unit": "mmol/L"},
            source_page=2,
            source_chunk_id="chunk-2",
        )
        db.commit()

        event = db.query(PatientTimelineEvent).filter_by(source_fact_id=fact.id).one()

    assert event.effective_at == datetime(2026, 7, 1, 8, 30)
    assert event.source_chunk_id == "chunk-2"
    assert event.source_page == 2
    assert "6.2 mmol/L" in event.summary


def test_timeline_orders_by_clinical_time_not_source_chunk(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'timeline.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        create_patient_fact(
            db,
            scope,
            resource_type="Condition",
            display="较早事件",
            effective_start=datetime(2025, 1, 1),
            source_chunk_id="zzz-parent",
        )
        create_patient_fact(
            db,
            scope,
            resource_type="Condition",
            display="较晚事件",
            effective_start=datetime(2026, 1, 1),
            source_chunk_id="aaa-parent",
        )
        db.commit()

        events = get_patient_timeline(db, scope)

    assert [item.title for item in events] == ["较晚事件", "较早事件"]


def test_patient_fact_queries_never_cross_scope(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'fact-isolation.db'}")
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
            resource_type="Condition",
            display="Bob private condition",
            effective_start=datetime(2020, 1, 1),
        )
        db.commit()

        alice_facts = list_patient_facts(db, alice_scope)

    assert [item.display for item in alice_facts] == ["青霉素过敏"]
