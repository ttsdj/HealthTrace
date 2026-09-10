"""Regression tests for the hardening fixes.

Each test here failed before its fix, so they document the exact behaviour that
must not come back:

* the JWT signing key no longer falls back to a guessable constant,
* interpolated Milvus filter values are escaped,
* the ``clinician`` clinical-review credential is not self-service.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import resources
from backend.app import create_app
from backend.db.models import TenantMembership, User
from backend.indexing.milvus_client import escape_filter_value
from backend.infra import auth
from backend.infra.auth import (
    ALGORITHM,
    JWTConfigurationError,
    create_access_token,
    get_db,
    is_placeholder_secret,
    jwt_secret_key,
)
from backend.infra.database import Base
from tests.conftest import TEST_JWT_SECRET


def _app_client(tmp_path, name="security-hardening.db"):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}
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
    return TestClient(app), Session


def _register(client, username, **extra):
    response = client.post(
        "/auth/register",
        json={"username": username, "password": "example-test-password", **extra},
    )
    assert response.status_code == 200, response.text
    return response.json()


# --------------------------------------------------------------------------
# JWT signing key must fail closed
# --------------------------------------------------------------------------


def test_signing_key_is_returned_when_properly_configured(monkeypatch):
    monkeypatch.setenv("JWT_SECRET_KEY", TEST_JWT_SECRET)
    assert jwt_secret_key() == TEST_JWT_SECRET


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "   ",
        "short",
        "change-this-secret",
        "replace-with-your-own-secret-value",
        "example-secret-that-is-long-enough-to-pass",
    ],
)
def test_signing_key_rejects_missing_short_and_placeholder_values(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    else:
        monkeypatch.setenv("JWT_SECRET_KEY", value)
    with pytest.raises(JWTConfigurationError):
        jwt_secret_key()


def test_placeholder_detection_matches_the_documented_markers():
    assert is_placeholder_secret(None) is True
    assert is_placeholder_secret("") is True
    assert is_placeholder_secret("REPLACE-me") is True
    assert is_placeholder_secret("a" * 40) is False


def test_create_access_token_refuses_to_sign_without_a_key(monkeypatch):
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    # Must not silently fall back to the old "change-this-secret" constant.
    with pytest.raises(Exception) as excinfo:
        create_access_token("alice", "user")
    assert getattr(excinfo.value, "status_code", None) == 503


def test_signing_algorithm_is_pinned_to_hs256(monkeypatch):
    """JWT_ALGORITHM was previously read from the environment."""
    monkeypatch.setenv("JWT_ALGORITHM", "none")
    assert ALGORITHM == "HS256"


# --------------------------------------------------------------------------
# Milvus filter expressions must escape interpolated values
# --------------------------------------------------------------------------


def test_escape_filter_value_neutralizes_quotes_and_backslashes():
    assert escape_filter_value('x" or filename != "') == 'x\\" or filename != \\"'
    assert escape_filter_value("back\\slash") == "back\\\\slash"
    assert escape_filter_value("plain-name.pdf") == "plain-name.pdf"


def test_public_document_delete_escapes_the_filename(tmp_path, monkeypatch):
    """The unescaped filter let one request delete the whole collection.

    ``filename == "x" or filename != ""`` matches every row, so a filename
    containing a quote must never reach Milvus verbatim.
    """
    captured: dict[str, str] = {}

    class StubMilvus:
        def init_collection(self):
            pass

        def delete(self, filter_expr):
            captured["filter_expr"] = filter_expr
            return {"delete_count": 3}

    class StubParentChunks:
        def delete_by_filename(self, filename):
            return 0

    engine = create_engine(f"sqlite:///{tmp_path / 'delete.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(resources, "milvus_manager", StubMilvus())
    monkeypatch.setattr(resources, "parent_chunk_store", StubParentChunks())
    monkeypatch.setattr(
        resources,
        "SessionLocal",
        sessionmaker(bind=engine, autoflush=False, expire_on_commit=False),
    )

    injected = 'x" or filename != "'
    deleted = resources.delete_document_transactionally(injected)

    assert deleted == 3
    assert captured["filter_expr"] == 'filename == "x\\" or filename != \\""'
    # The escaped form keeps the attacker's text inside the string literal: only
    # the two delimiters are unescaped, so this stays one clause instead of two.
    unescaped_quotes = re.findall(r'(?<!\\)"', captured["filter_expr"])
    assert len(unescaped_quotes) == 2


# --------------------------------------------------------------------------
# The API must not authenticate against an unconfigured key
# --------------------------------------------------------------------------


def test_login_is_unavailable_when_the_signing_key_is_missing(tmp_path, monkeypatch):
    client, _ = _app_client(tmp_path)
    assert (
        client.post(
            "/auth/register",
            json={"username": "hardening-user", "password": "example-test-password"},
        ).status_code
        == 200
    )

    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    login = client.post(
        "/auth/login",
        json={"username": "hardening-user", "password": "example-test-password"},
    )
    # Password verification still succeeds, but no token may be minted.
    assert login.status_code == 503
    assert "access_token" not in login.json()


# --------------------------------------------------------------------------
# The clinical review credential must not be self-service
# --------------------------------------------------------------------------


def test_tenant_admin_cannot_mint_the_clinician_credential(tmp_path):
    """Self-registration is enough to own a personal tenant, so a tenant admin
    could otherwise appoint themselves the clinician whose approval
    golden_readiness() reports as clinical_claim_allowed.
    """
    client, Session = _app_client(tmp_path)
    alice = _register(client, "escalation-alice")
    _register(client, "escalation-bob")
    alice_headers = {"Authorization": f"Bearer {alice['access_token']}"}

    # Reaching any patient-scoped route makes alice the owner of her own tenant.
    assert client.get("/patient/facts", headers=alice_headers).status_code == 200

    escalated = client.post(
        "/tenant/members",
        headers=alice_headers,
        json={"username": "escalation-alice", "role": "clinician"},
    )
    assert escalated.status_code == 403

    # The same guard blocks appointing a second account as the reviewer.
    delegated = client.post(
        "/tenant/members",
        headers=alice_headers,
        json={"username": "escalation-bob", "role": "clinician"},
    )
    assert delegated.status_code == 403

    with Session() as db:
        assert db.query(TenantMembership).filter_by(role="clinician").count() == 0

    # Ordinary membership is unaffected, so the tenant directory still works.
    seated = client.post(
        "/tenant/members",
        headers=alice_headers,
        json={"username": "escalation-bob", "role": "viewer"},
    )
    assert seated.status_code == 200
    assert seated.json()["role"] == "viewer"


def test_platform_administrator_can_still_assign_the_clinician_role(tmp_path, monkeypatch):
    """The guard has to leave the legitimate provisioning path open."""
    monkeypatch.setattr(auth, "ADMIN_INVITE_CODE", "test-invite-code")
    client, Session = _app_client(tmp_path)
    _register(client, "escalation-bob")
    admin = _register(
        client,
        "escalation-operator",
        role="admin",
        admin_code="test-invite-code",
    )
    assert admin["role"] == "admin"
    admin_headers = {"Authorization": f"Bearer {admin['access_token']}"}

    seated = client.post(
        "/tenant/members",
        headers=admin_headers,
        json={"username": "escalation-bob", "role": "clinician"},
    )
    assert seated.status_code == 200, seated.text
    assert seated.json()["role"] == "clinician"

    with Session() as db:
        membership = db.query(TenantMembership).filter_by(role="clinician").one()
        reviewer = db.query(User).filter(User.id == membership.user_id).one()
        # This is the credential golden_review.reviewer_role() accepts.
        assert reviewer.username == "escalation-bob"
