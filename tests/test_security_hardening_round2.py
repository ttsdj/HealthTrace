"""Regression tests for the 2026-09-12 hardening round.

Each test locks a fix that would have failed before the change:

1. tool authorization no longer fails open without a Planner plan;
2. tokens carry a jti and can be revoked server-side before expiry;
3. registration enforces a username charset and a password floor;
4. patient uploads are type-checked and size-capped;
5. evaluation report output_id cannot traverse the filesystem;
6. the unauthenticated health payload carries booleans only, never hosts,
   URLs or raw exception text;
7. weather tool output never contains the AMap key after a request failure.
"""

import asyncio

import pytest

from backend.agent.tool_registry import tool_is_allowed
from backend.evaluation.jobs import _SAFE_OUTPUT_ID
from backend.infra import auth
from backend.patient.documents import save_patient_upload, validate_patient_upload
from backend.tools import weather


class _FakeRedisStore:
    """Minimal stand-in for backend.infra.cache.cache used by the denylist."""

    def __init__(self):
        self.store = {}

    def get_json(self, key):
        return self.store.get(key)

    def set_json(self, key, value, ttl=None):
        self.store[key] = value


@pytest.fixture()
def fake_cache(monkeypatch):
    store = _FakeRedisStore()
    monkeypatch.setattr(auth, "cache", store)
    return store


def test_write_tool_is_denied_without_a_planner_plan():
    # The ContextVar default is None (no plan activated), which is exactly the
    # fail-open situation this round closed.
    assert not tool_is_allowed("create_health_reminder_draft")
    assert tool_is_allowed("search_knowledge_base")
    assert tool_is_allowed("get_current_weather")
    assert tool_is_allowed("search_nearby_medical_care")


def test_create_access_token_carries_a_jti(fake_cache):
    token = auth.create_access_token("alice", "user")
    claims = auth.pyjwt.decode(
        token,
        auth.jwt_secret_key(),
        algorithms=[auth.ALGORITHM],
        options={"verify_exp": False},
    )
    assert claims["jti"]
    assert not auth.token_is_revoked(claims["jti"])


def test_revoked_token_is_denied_before_expiry(fake_cache):
    token = auth.create_access_token("alice", "user")
    assert auth.revoke_access_token(token) is True
    claims = auth.pyjwt.decode(
        token,
        auth.jwt_secret_key(),
        algorithms=[auth.ALGORITHM],
        options={"verify_exp": False},
    )
    assert auth.token_is_revoked(claims["jti"])


def test_revoke_returns_false_for_garbage_tokens(fake_cache):
    assert auth.revoke_access_token("not-a-token") is False


@pytest.mark.parametrize(
    "username",
    ["ab", "has space", "colon:name", "x" * 33, "../etc"],
)
def test_register_rejects_unsafe_usernames(username):
    from pydantic import ValidationError

    from backend.schemas.auth import RegisterRequest

    with pytest.raises(ValidationError):
        RegisterRequest(username=username, password="example-test-password")


def test_register_rejects_short_passwords():
    from pydantic import ValidationError

    from backend.schemas.auth import RegisterRequest

    with pytest.raises(ValidationError):
        RegisterRequest(username="alice", password="short")


@pytest.mark.parametrize(
    "filename",
    ["report.pdf", "scan.PNG", "notes.docx", "page.html", "photo.jpeg"],
)
def test_patient_upload_accepts_parseable_types(filename):
    assert validate_patient_upload(filename) == filename


@pytest.mark.parametrize(
    "filename",
    ["payload.exe", "archive.zip", "script.sh", "no-extension"],
)
def test_patient_upload_rejects_unparseable_types(filename):
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as excinfo:
        validate_patient_upload(filename)
    assert excinfo.value.status_code == 415


def test_patient_upload_enforces_size_cap(tmp_path):
    class FakeUpload:
        def __init__(self, payload: bytes):
            self._payload = payload

        async def read(self, size: int) -> bytes:
            data = self._payload[:size]
            self._payload = self._payload[size:]
            return data

    from fastapi import HTTPException

    target = tmp_path / "out.bin"
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(
            save_patient_upload(FakeUpload(b"x" * 32), target, max_bytes=16)
        )
    assert excinfo.value.status_code == 413
    assert not target.exists()


@pytest.mark.parametrize("bad", ["../../etc", "a/b", "a\\b", "..", "", "x" * 101])
def test_evaluation_output_id_rejects_traversal(bad):
    assert not _SAFE_OUTPUT_ID.fullmatch(bad)


def test_safe_output_id_accepts_normal_ids():
    assert _SAFE_OUTPUT_ID.fullmatch("policy-v1")
    assert _SAFE_OUTPUT_ID.fullmatch("20260912-000000")


def test_unauthenticated_health_payload_hides_diagnostics(monkeypatch):
    from backend.api.routes import health as health_module

    def leaky_check():
        return {"ok": False, "error": "postgres db.internal:5432 is down: boom"}

    monkeypatch.setattr(health_module, "_check_postgres", leaky_check)
    monkeypatch.setattr(health_module, "_check_redis", leaky_check)
    monkeypatch.setattr(health_module, "_check_milvus", leaky_check)
    monkeypatch.setattr(health_module, "_check_llm_config", leaky_check)
    monkeypatch.setattr(health_module, "_check_neo4j", leaky_check)

    payload = asyncio.run(health_module.health())

    serialized = repr(payload)
    assert "db.internal" not in serialized
    assert "boom" not in serialized
    assert payload["ready"] is False
    assert payload["core_services"]["postgres"] == {"ok": False}
    assert payload["optional_services"]["neo4j"] == {"ok": False}


def test_weather_failure_output_never_contains_the_api_key(monkeypatch):
    class FakeResponse:
        def raise_for_status(self):
            raise weather.requests.HTTPError(
                "400 Client Error: bad request for url: "
                "https://restapi.amap.com/v3/weather/weatherInfo?key=SECRET_KEY&city=110000"
            )

    monkeypatch.setattr(
        weather, "AMAP_WEATHER_API", "https://restapi.amap.com/v3/weather/weatherInfo"
    )
    monkeypatch.setattr(weather, "AMAP_API_KEY", "SECRET_KEY")
    monkeypatch.setattr(
        weather.requests,
        "get",
        lambda *args, **kwargs: FakeResponse(),
    )

    result = weather.get_current_weather("110000")

    assert "SECRET_KEY" not in result
