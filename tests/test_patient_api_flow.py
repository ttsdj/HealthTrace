from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import create_app
from backend.db.models import DocumentRecord, ParentChunk, User
from backend.infra.auth import get_db
from backend.infra.database import Base


def test_authenticated_patient_fact_timeline_and_task_flow(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'api-flow.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    register = client.post(
        "/auth/register",
        json={"username": "api-user", "password": "example-test-password"},
    )
    assert register.status_code == 200
    auth = register.json()
    assert auth["tenant_id"].startswith("tenant-")
    assert auth["patient_id"].startswith("patient-")
    headers = {"Authorization": f"Bearer {auth['access_token']}"}

    fact = client.post(
        "/patient/facts",
        headers=headers,
        json={
            "resource_type": "Observation",
            "display": "家庭血压",
            "effective_start": datetime(2026, 7, 22, 8, 0).isoformat(),
            "value": {"value": 128, "unit": "mmHg"},
        },
    )
    assert fact.status_code == 200
    assert fact.json()["verification_status"] == "user_confirmed"

    timeline = client.get("/patient/timeline", headers=headers)
    assert timeline.status_code == 200
    assert timeline.json()["events"][0]["title"] == "家庭血压"

    task = client.post(
        "/patient/tasks",
        headers=headers,
        json={
            "task_type": "reminder",
            "title": "明日记录血压",
            "due_at": datetime(2026, 7, 23, 8, 0).isoformat(),
            "idempotency_key": "api-flow-bp-reminder",
        },
    )
    assert task.status_code == 200
    assert task.json()["status"] == "waiting_confirmation"
    task_id = task.json()["task_id"]

    confirmed = client.post(f"/patient/tasks/{task_id}/confirm", headers=headers)
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "active"


def test_document_candidate_confirmation_is_scoped_end_to_end(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'candidate-api-flow.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = TestSession()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    alice = client.post(
        "/auth/register",
        json={"username": "candidate-alice", "password": "example-test-password"},
    ).json()
    bob = client.post(
        "/auth/register",
        json={"username": "candidate-bob", "password": "example-test-password"},
    ).json()
    alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
    bob_headers = {"Authorization": f"Bearer {bob['access_token']}"}

    with TestSession() as db:
        user = db.query(User).filter(User.username == "candidate-alice").one()
        document = DocumentRecord(
            id="candidate-api-document",
            document_domain="patient_private",
            tenant_id=alice["tenant_id"],
            patient_id=alice["patient_id"],
            owner_user_id=user.id,
            filename="blood-pressure.html",
            file_type="HTML",
            status="indexed",
        )
        parent = ParentChunk(
            chunk_id="candidate-api-parent",
            text="2026-07-21\n血压：128/82 mmHg",
            filename=document.filename,
            file_type="HTML",
            chunk_level=1,
            document_id=document.id,
            document_domain="patient_private",
            tenant_id=alice["tenant_id"],
            patient_id=alice["patient_id"],
            owner_user_id=user.id,
        )
        db.add_all([document, parent])
        db.commit()

    extracted = client.post(
        "/patient/documents/candidate-api-document/fact-candidates/extract",
        headers=alice_headers,
        json={"use_llm": False, "consent_external_processing": False},
    )
    assert extracted.status_code == 200
    body = extracted.json()
    assert body["extraction_method"] == "local_rules"
    assert body["candidate_count"] == 1
    candidate_id = body["candidates"][0]["candidate_id"]

    assert client.get("/patient/fact-candidates", headers=bob_headers).json()["candidates"] == []
    forbidden = client.post(
        f"/patient/fact-candidates/{candidate_id}/confirm",
        headers=bob_headers,
        json={},
    )
    assert forbidden.status_code == 400

    confirmed = client.post(
        f"/patient/fact-candidates/{candidate_id}/confirm",
        headers=alice_headers,
        json={},
    )
    assert confirmed.status_code == 200
    assert confirmed.json()["candidate"]["status"] == "confirmed"
    assert len(client.get("/patient/facts", headers=alice_headers).json()["facts"]) == 1
    assert len(client.get("/patient/timeline", headers=alice_headers).json()["events"]) == 1
    assert client.get("/patient/facts", headers=bob_headers).json()["facts"] == []
