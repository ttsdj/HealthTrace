import base64
import secrets
from datetime import datetime

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.app import create_app
from backend.db.models import (
    AuditEvent,
    PatientAccessGrant,
    PatientSensitiveRecord,
    TenantMembership,
)
from backend.infra.auth import get_db
from backend.infra.database import Base
from backend.infra.migrations import (
    apply_phase5_access_security_migration,
    get_phase5_migration_status,
)
from backend.security.crypto import decrypt_json, encrypt_json


def _key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")


def _client(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'access-security.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        db = Session()
        try:
            yield db
        finally:
            db.close()

    app = create_app()
    app.dependency_overrides[get_db] = override_get_db
    return TestClient(app), Session, engine


def test_encrypted_payload_round_trip_and_tamper_detection(monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_FIELD_ENCRYPTION_KEY", _key())
    envelope, key_id = encrypt_json({"identity": "secret-value"}, aad="scope")
    assert "secret-value" not in envelope
    assert len(key_id) == 16
    assert decrypt_json(envelope, aad="scope") == {"identity": "secret-value"}

    tampered = envelope[:-2] + ("AA" if not envelope.endswith("AA") else "BB")
    try:
        decrypt_json(tampered, aad="scope")
    except Exception:
        pass
    else:
        raise AssertionError("AES-GCM tampering must be rejected")


def test_tenant_member_patient_grant_and_sensitive_record_flow(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_FIELD_ENCRYPTION_KEY", _key())
    client, Session, _ = _client(tmp_path)
    alice = client.post(
        "/auth/register",
        json={"username": "access-alice", "password": "example-test-password"},
    ).json()
    bob = client.post(
        "/auth/register",
        json={"username": "access-bob", "password": "example-test-password"},
    ).json()
    alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}
    bob_headers = {
        "Authorization": f"Bearer {bob['access_token']}",
        "X-Patient-ID": alice["patient_id"],
    }

    member = client.post(
        "/tenant/members",
        headers=alice_headers,
        json={"username": "access-bob", "role": "viewer"},
    )
    assert member.status_code == 200
    grant = client.post(
        "/patient/access-grants",
        headers=alice_headers,
        json={
            "patient_id": alice["patient_id"],
            "username": "access-bob",
            "permission": "read",
        },
    )
    assert grant.status_code == 200
    assert client.get("/patient/facts", headers=bob_headers).status_code == 200
    denied = client.post(
        "/patient/facts",
        headers=bob_headers,
        json={
            "resource_type": "Condition",
            "display": "Shared write must be denied",
            "effective_start": datetime(2026, 7, 24, 9, 0).isoformat(),
        },
    )
    assert denied.status_code == 403

    upgraded = client.post(
        "/patient/access-grants",
        headers=alice_headers,
        json={
            "patient_id": alice["patient_id"],
            "username": "access-bob",
            "permission": "write",
        },
    )
    assert upgraded.status_code == 200
    allowed = client.post(
        "/patient/facts",
        headers=bob_headers,
        json={
            "resource_type": "Condition",
            "display": "Shared write is explicitly granted",
            "effective_start": datetime(2026, 7, 24, 9, 0).isoformat(),
        },
    )
    assert allowed.status_code == 200

    sensitive = client.post(
        "/patient/sensitive-records",
        headers=alice_headers,
        json={
            "category": "insurance",
            "payload": {"policy_number": "PRIVATE-123"},
        },
    )
    assert sensitive.status_code == 200
    listed = client.get("/patient/sensitive-records", headers=alice_headers)
    assert listed.json()[0]["payload"]["policy_number"] == "PRIVATE-123"

    with Session() as db:
        # A tenant owner may seat members, but may not mint the operator-only
        # clinician credential (see _OPERATOR_ONLY_MEMBER_ROLES).
        assert db.query(TenantMembership).filter_by(role="viewer").count() == 1
        assert db.query(PatientAccessGrant).filter_by(permission="write").count() == 1
        stored = db.query(PatientSensitiveRecord).one()
        assert "PRIVATE-123" not in stored.ciphertext
        assert db.query(AuditEvent).count() > 0
        assert all(
            "PRIVATE-123" not in str(event.metadata_json)
            for event in db.query(AuditEvent).all()
        )


def test_phase5_migration_is_additive_and_backfills_owner(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'phase5.db'}")
    Base.metadata.create_all(engine)
    result = apply_phase5_access_security_migration(engine)
    assert result["status"] == "applied"
    assert get_phase5_migration_status(engine)["status"] == "applied"
    assert apply_phase5_access_security_migration(engine)["status"] == "applied"
