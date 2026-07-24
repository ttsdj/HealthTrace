from __future__ import annotations

from time import perf_counter
from uuid import uuid4

from fastapi import Request

from backend.infra.auth import get_db
from backend.infra.database import SessionLocal
from backend.security.audit import append_audit_event
from backend.observability.metrics import record_http_request

_AUDITED_PREFIXES = (
    "/auth",
    "/patient",
    "/documents",
    "/chat",
    "/sessions",
    "/evaluation",
    "/tenant",
    "/audit",
)


def _resource_type(path: str) -> str:
    parts = [part for part in path.split("/") if part]
    return parts[0] if parts else "root"


async def audit_request_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "").strip()[:64] or uuid4().hex
    request.state.request_id = request_id
    started = perf_counter()
    status_code = 500
    error_type = ""
    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        path = request.url.path or "/"
        route = getattr(request.scope.get("route"), "path", None) or path
        elapsed = perf_counter() - started
        record_http_request(
            method=request.method,
            route=route,
            status_code=status_code,
            duration_seconds=elapsed,
        )
        if path.startswith(_AUDITED_PREFIXES):
            user = getattr(request.state, "current_user", None)
            scope = getattr(request.state, "patient_scope", None)
            metadata = {
                "method": request.method,
                "route": route[:500],
                "status_code": status_code,
                "latency_ms": round(elapsed * 1000, 2),
            }
            if error_type:
                metadata["error_type"] = error_type
            override = request.app.dependency_overrides.get(get_db)
            db_provider = override or get_db
            provider_result = db_provider()
            if hasattr(provider_result, "__next__"):
                provider = provider_result
                db = next(provider)
            else:
                provider = None
                db = provider_result if provider_result is not None else SessionLocal()
            try:
                append_audit_event(
                    db,
                    request_id=request_id,
                    actor_user_id=getattr(user, "id", None),
                    tenant_id=getattr(scope, "tenant_id", None),
                    patient_id=getattr(scope, "patient_id", None),
                    action=f"{request.method} {route}"[:120],
                    resource_type=_resource_type(route),
                    outcome="success" if status_code < 400 else "denied" if status_code < 500 else "error",
                    metadata=metadata,
                )
                db.commit()
            except Exception:
                db.rollback()
            finally:
                if provider is not None:
                    try:
                        next(provider)
                    except StopIteration:
                        pass
                else:
                    db.close()
