from datetime import datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import DocumentRecord, PatientTimelineEvent, User
from backend.infra.database import Base
from backend.kg import patient_graph as graph_module
from backend.kg.patient_graph import PatientGraphClient, mirror_patient_fact_to_graph
from backend.patient.facts import create_patient_fact
from backend.patient.scope import ensure_user_scope


def _make_fact(db, scope, *, resource_type, display, effective_start, **kwargs):
    fact = create_patient_fact(
        db,
        scope,
        resource_type=resource_type,
        display=display,
        effective_start=effective_start,
        **kwargs,
    )
    event = db.query(PatientTimelineEvent).filter_by(source_fact_id=fact.id).one()
    return fact, event


def _recording_client():
    """Client that bypasses the real driver and records (cypher, params) calls."""
    client = PatientGraphClient.__new__(PatientGraphClient)
    recorded = []
    client._run = lambda cypher, **params: recorded.append((cypher, params)) or []
    return client, recorded


def test_write_patient_fact_builds_nodes_edges_props_and_is_scoped(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        db.add(
            DocumentRecord(
                id="doc-1",
                document_domain="patient_private",
                tenant_id=scope.tenant_id,
                patient_id=scope.patient_id,
                owner_user_id=user.id,
                filename="alice.pdf",
                status="indexed",
            )
        )
        db.flush()
        fact, event = _make_fact(
            db,
            scope,
            resource_type="Condition",
            display="2型糖尿病",
            code_system="ICD-10",
            code="E11",
            effective_start=datetime(2026, 7, 1),
            source_document_id="doc-1",
            source_chunk_id="chunk-42",
        )
        db.commit()

    monkeypatch.setenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", "on")
    client, recorded = _recording_client()
    assert client.write_patient_fact(scope, fact, event) is True
    assert len(recorded) == 2

    write_cypher, write_params = recorded[0]
    assert "MERGE" in write_cypher
    assert "CREATE" not in write_cypher  # idempotency: no destructive/duplicate creates
    assert ":Condition" in write_cypher or "Condition" in write_cypher
    assert ":HAS_CONDITION" in write_cypher
    assert ":TimelineEvent" in write_cypher
    assert ":HAS_TIMELINE" in write_cypher

    assert write_params["tenant_id"] == scope.tenant_id
    assert write_params["patient_id"] == scope.patient_id
    assert write_params["resource_key"] == fact.id
    assert write_params["fact_id"] == fact.id
    assert write_params["code"] == "E11"
    assert write_params["display"] == "2型糖尿病"
    assert write_params["value"] == {}
    assert write_params["status"] == "active"
    assert write_params["verification_status"] == "user_confirmed"
    assert write_params["effective_at"] == datetime(2026, 7, 1).isoformat()
    assert "source" in write_params
    assert "doc-1" in write_params["source"] and "chunk-42" in write_params["source"]
    assert write_params["event_key"] == event.id
    assert write_params["event_id"] == event.id

    chain_cypher, chain_params = recorded[1]
    assert ":NEXT" in chain_cypher
    assert chain_params["tenant_id"] == scope.tenant_id
    assert chain_params["patient_id"] == scope.patient_id


def test_write_patient_fact_is_observation_scoped_and_medication_labeled(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph-obs.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        obs_fact, obs_event = _make_fact(
            db,
            scope,
            resource_type="Observation",
            display="空腹血糖",
            code="1558-6",
            effective_start=datetime(2026, 7, 1),
            value={"value": 6.2, "unit": "mmol/L"},
        )
        med_fact, med_event = _make_fact(
            db,
            scope,
            resource_type="MedicationStatement",
            display="二甲双胍",
            effective_start=datetime(2026, 7, 2),
        )
        db.commit()

    monkeypatch.setenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", "on")
    client, recorded = _recording_client()
    assert client.write_patient_fact(scope, obs_fact, obs_event) is True
    assert client.write_patient_fact(scope, med_fact, med_event) is True

    obs_cypher = recorded[0][0]
    med_cypher = recorded[2][0]
    assert ":OBSERVED" in obs_cypher
    assert ":Observation" in obs_cypher
    assert ":TAKES" in med_cypher
    assert ":Medication" in med_cypher
    assert recorded[0][1]["patient_id"] == scope.patient_id
    assert recorded[2][1]["patient_id"] == scope.patient_id


def test_write_patient_fact_never_crosses_patient_scope(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'graph-scope.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        alice = User(username="alice", password_hash="x", role="user")
        bob = User(username="bob", password_hash="x", role="user")
        db.add_all([alice, bob])
        db.flush()
        alice_scope = ensure_user_scope(db, alice)
        bob_scope = ensure_user_scope(db, bob)
        alice_fact, alice_event = _make_fact(
            db, alice_scope, resource_type="Condition", display="Alice condition",
            effective_start=datetime(2026, 1, 1),
        )
        bob_fact, bob_event = _make_fact(
            db, bob_scope, resource_type="Condition", display="Bob condition",
            effective_start=datetime(2026, 1, 1),
        )
        db.commit()

    monkeypatch.setenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", "on")
    client, recorded = _recording_client()
    client.write_patient_fact(alice_scope, alice_fact, alice_event)
    client.write_patient_fact(bob_scope, bob_fact, bob_event)

    assert recorded[0][1]["tenant_id"] == alice_scope.tenant_id
    assert recorded[0][1]["patient_id"] == alice_scope.patient_id
    assert recorded[2][1]["tenant_id"] == bob_scope.tenant_id
    assert recorded[2][1]["patient_id"] == bob_scope.patient_id
    assert recorded[0][1]["patient_id"] != recorded[2][1]["patient_id"]


def test_write_patient_fact_is_noop_when_disabled(monkeypatch):
    monkeypatch.delenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", raising=False)
    client, recorded = _recording_client()
    assert client.write_patient_fact(object(), object(), object()) is None
    assert recorded == []
    assert mirror_patient_fact_to_graph(object(), object(), object()) is None


def test_query_patient_timeline_is_graceful_and_scoped(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", "off")
    client = PatientGraphClient.__new__(PatientGraphClient)
    # disabled -> [] without touching the driver
    assert client.query_patient_timeline(object()) == []

    monkeypatch.setenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", "on")

    def _boom(*args, **kwargs):
        raise RuntimeError("neo4j down")

    client._run = _boom
    # enabled but driver raises -> graceful empty result, never raises
    assert client.query_patient_timeline(object()) == []


def test_create_patient_fact_succeeds_when_graph_client_raises(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", "on")
    monkeypatch.setattr(
        graph_module,
        "get_patient_graph_client",
        lambda: (_ for _ in ()).throw(RuntimeError("neo4j down")),
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'graph-down.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        fact = create_patient_fact(
            db,
            scope,
            resource_type="Condition",
            display="高血压",
            effective_start=datetime(2026, 7, 1),
        )
        db.commit()
        assert fact.id
        assert fact.verification_status == "user_confirmed"


def test_create_patient_fact_is_noop_when_graph_disabled(tmp_path):
    assert graph_module.patient_graph_enabled() is False
    engine = create_engine(f"sqlite:///{tmp_path / 'graph-off.db'}")
    Base.metadata.create_all(engine)
    with Session(engine, expire_on_commit=False) as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        fact = create_patient_fact(
            db,
            scope,
            resource_type="Observation",
            display="体重",
            effective_start=datetime(2026, 7, 1),
            value={"value": 70, "unit": "kg"},
        )
        db.commit()
        assert fact.id
