from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import create_app
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
        json={"username": "api-user", "password": "safe-test-password"},
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
