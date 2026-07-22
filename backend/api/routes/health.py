import os
import socket
from urllib.parse import urlparse

from fastapi import APIRouter
from sqlalchemy import text

from backend.infra.database import SessionLocal
from backend.infra.cache import cache
from backend.indexing.milvus_client import COLLECTION_ENV_BY_KIND, get_milvus_store

router = APIRouter(tags=["health"])


def _connection_target(url: str, default_host: str, default_port: int) -> tuple[str, int]:
    try:
        parsed = urlparse(url)
        return parsed.hostname or default_host, parsed.port or default_port
    except (TypeError, ValueError):
        return default_host, default_port


def _port_open(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _check_postgres() -> dict:
    database_url = os.getenv("DATABASE_URL", "")
    if database_url.startswith("sqlite:"):
        try:
            db = SessionLocal()
            try:
                db.execute(text("SELECT 1"))
            finally:
                db.close()
            return {"ok": True, "dialect": "sqlite"}
        except Exception as exc:
            return {"ok": False, "dialect": "sqlite", "error": str(exc)}
    host, port = _connection_target(
        database_url, "127.0.0.1", 5432
    )
    if not _port_open(host, port):
        return {"ok": False, "error": f"postgres {host}:{port} is not reachable"}
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
        finally:
            db.close()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _check_redis() -> dict:
    host, port = _connection_target(os.getenv("REDIS_URL", ""), "127.0.0.1", 6379)
    if not _port_open(host, port):
        return {"ok": False, "error": f"redis {host}:{port} is not reachable"}
    try:
        cache._get_client().ping()
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _check_milvus() -> dict:
    if not _port_open(os.getenv("MILVUS_HOST", "127.0.0.1"), int(os.getenv("MILVUS_PORT", "19530"))):
        return {"ok": False, "error": "milvus port is not reachable", "collections": {}}
    collections = {}
    overall = True
    for kind in COLLECTION_ENV_BY_KIND:
        store = get_milvus_store(kind)
        try:
            exists = store.has_collection()
            collections[kind] = {
                "ok": True,
                "collection_name": store.collection_name,
                "exists": exists,
            }
        except Exception as exc:
            overall = False
            collections[kind] = {
                "ok": False,
                "collection_name": store.collection_name,
                "exists": False,
                "error": str(exc),
            }
    return {"ok": overall, "collections": collections}


def _check_neo4j() -> dict:
    host, port = _connection_target(os.getenv("NEO4J_URL", ""), "127.0.0.1", 7687)
    if not _port_open(host, port):
        return {"ok": False, "error": f"neo4j bolt {host}:{port} is not reachable"}
    try:
        from backend.kg.client import get_kg_client

        get_kg_client()._run("RETURN 1 AS ok")
        return {"ok": True}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _check_llm_config() -> dict:
    api_key = os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY")
    model = os.getenv("MODEL")
    base_url = os.getenv("BASE_URL")
    missing = [
        name
        for name, value in {
            "LLM_API_KEY/ARK_API_KEY": api_key,
            "MODEL": model,
            "BASE_URL": base_url,
        }.items()
        if not value
    ]
    if missing:
        return {"ok": False, "error": f"missing {', '.join(missing)}"}
    return {
        "ok": True,
        "model": model,
        "base_url": base_url,
        "api_key_set": True,
    }


def _patient_capabilities() -> dict:
    migration_status = "unknown"
    try:
        from backend.db.models import SchemaMigration
        from backend.infra.migrations import PHASE1_VERSION

        db = SessionLocal()
        try:
            record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE1_VERSION).first()
            migration_status = record.status if record else "not_applied"
        finally:
            db.close()
    except Exception:
        migration_status = "unavailable"
    return {
        "patient_domains_enabled": os.getenv(
            "HEALTHTRACE_PATIENT_DOMAIN_ENABLED", "true"
        ).lower()
        == "true",
        "phase1_migration": migration_status,
        "task_scheduler_enabled": os.getenv(
            "HEALTHTRACE_TASK_SCHEDULER_ENABLED", "true"
        ).lower()
        == "true",
        "multimodal_retrieval_enabled": False,
    }


@router.get("/health")
async def health():
    core_services = {
        "postgres": _check_postgres(),
        "redis": _check_redis(),
        "milvus": _check_milvus(),
        "llm": _check_llm_config(),
    }
    optional_services = {
        "neo4j": _check_neo4j(),
    }
    ready = all(item.get("ok") for item in core_services.values())
    return {
        "service": "HealthTrace",
        "infra_mode": os.getenv("HEALTHTRACE_INFRA_MODE", "managed").lower(),
        "ready": ready,
        "strict_startup": os.getenv("STRICT_STARTUP", "false").lower() == "true",
        "services": {**core_services, **optional_services},
        "core_services": core_services,
        "optional_services": optional_services,
        "capabilities": _patient_capabilities(),
    }
