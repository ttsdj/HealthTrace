from __future__ import annotations

import asyncio
from pathlib import Path

from langchain_core.messages import HumanMessage
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.api.routes.health as health_module
import backend.chat.storage as storage_module
from backend.chat.storage import ConversationStorage
from backend.db.models import User
from backend.infra.database import Base


class MemoryCache:
    def __init__(self) -> None:
        self.values: dict[str, object] = {}

    def get_json(self, key: str):
        return self.values.get(key)

    def set_json(self, key: str, value, ttl=None) -> None:
        del ttl
        self.values[key] = value

    def delete(self, key: str) -> None:
        self.values.pop(key, None)


def test_health_payload_reports_brand_mode_and_optional_degradation(monkeypatch):
    ok = lambda: {"ok": True}
    monkeypatch.setattr(health_module, "_check_postgres", ok)
    monkeypatch.setattr(health_module, "_check_redis", ok)
    monkeypatch.setattr(health_module, "_check_milvus", ok)
    monkeypatch.setattr(health_module, "_check_llm_config", ok)
    monkeypatch.setattr(
        health_module,
        "_check_neo4j",
        lambda: {"ok": False, "error": "optional service unavailable"},
    )
    monkeypatch.setenv("HEALTHTRACE_INFRA_MODE", "external")

    payload = asyncio.run(health_module.health())

    assert payload["service"] == "HealthTrace"
    assert payload["infra_mode"] == "external"
    assert payload["ready"] is True
    assert payload["optional_services"]["neo4j"]["ok"] is False


def test_health_checks_parse_configured_connection_targets():
    assert health_module._connection_target(
        "postgresql+psycopg2://user:pass@db.internal:5544/app",
        "127.0.0.1",
        5432,
    ) == ("db.internal", 5544)
    assert health_module._connection_target(
        "redis://cache.internal/0",
        "127.0.0.1",
        6379,
    ) == ("cache.internal", 6379)


def test_same_session_id_is_isolated_between_users(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'sessions.db'}")
    Base.metadata.create_all(engine)
    test_session = sessionmaker(bind=engine, expire_on_commit=False)
    with test_session() as db:
        db.add_all(
            [
                User(username="alice", password_hash="x", role="user"),
                User(username="bob", password_hash="x", role="user"),
            ]
        )
        db.commit()

    monkeypatch.setattr(storage_module, "SessionLocal", test_session)
    monkeypatch.setattr(storage_module, "cache", MemoryCache())
    storage = ConversationStorage()

    storage.save("alice", "same-session", [HumanMessage(content="alice-only")])
    storage.save("bob", "same-session", [HumanMessage(content="bob-only")])

    assert [item["content"] for item in storage.get_session_messages("alice", "same-session")] == [
        "alice-only"
    ]
    assert [item["content"] for item in storage.get_session_messages("bob", "same-session")] == [
        "bob-only"
    ]
    assert storage.session_exists("alice", "same-session") is True
    assert storage.session_exists("unknown", "same-session") is False


def test_repository_defaults_are_healthtrace_and_support_both_infra_modes():
    root = Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    compose = (root / "docker-compose.yml").read_text(encoding="utf-8")
    launcher = (root / "start.bat").read_text(encoding="utf-8")

    assert 'name = "healthtrace"' in pyproject
    assert "container_name: healthtrace-postgres" in compose
    assert "name: healthtrace" in compose
    assert 'INFRA_MODE=managed' in launcher
    assert '"%INFRA_MODE%"=="external"' in launcher
