from __future__ import annotations

import asyncio
import os
from time import perf_counter
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

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


def _positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def configure_database_bulkhead(app) -> None:
    """Install per-app admission control for audited database paths."""
    app.state.database_bulkhead = asyncio.Semaphore(
        _positive_int_env("HEALTHTRACE_DB_BULKHEAD_LIMIT", 12)
    )
    try:
        app.state.database_bulkhead_wait_seconds = max(
            0.001,
            float(os.getenv("HEALTHTRACE_DB_BULKHEAD_WAIT_SECONDS", "0.05")),
        )
    except ValueError:
        app.state.database_bulkhead_wait_seconds = 0.05


def _persist_audit_event(db, **kwargs) -> None:
    """Write audit data without accidentally committing endpoint work.

    Endpoints that did not commit would previously be rolled back when their
    dependency Session closed.  Preserve that behavior before committing the
    independent audit event on the same physical Session.
    """
    try:
        if db.in_transaction():
            db.rollback()
        append_audit_event(db, **kwargs)
        db.commit()
    except Exception:
        db.rollback()


async def audit_request_middleware(request: Request, call_next):
    request_id = request.headers.get("X-Request-ID", "").strip()[:64] or uuid4().hex
    request.state.request_id = request_id
    started = perf_counter()
    status_code = 500
    error_type = ""
    path = request.url.path or "/"
    audited = path.startswith(_AUDITED_PREFIXES)
    bulkhead_acquired = False
    shared_db = None

    if audited:
        bulkhead = request.app.state.database_bulkhead
        try:
            await asyncio.wait_for(
                bulkhead.acquire(),
                timeout=request.app.state.database_bulkhead_wait_seconds,
            )
            bulkhead_acquired = True
        except TimeoutError:
            elapsed = perf_counter() - started
            record_http_request(
                method=request.method,
                route=path,
                status_code=503,
                duration_seconds=elapsed,
            )
            return JSONResponse(
                status_code=503,
                content={"detail": "database request capacity is temporarily exhausted"},
                headers={"Retry-After": "1", "X-Request-ID": request_id},
            )

        # Dependency overrides own their Session lifecycle (notably SQLite
        # tests). Production requests share this one Session end-to-end.
        if request.app.dependency_overrides.get(get_db) is None:
            shared_db = SessionLocal()
            request.state.db_session = shared_db

    try:
        response = await call_next(request)
        status_code = response.status_code
        response.headers["X-Request-ID"] = request_id
        return response
    except Exception as exc:
        error_type = type(exc).__name__
        raise
    finally:
        route = getattr(request.scope.get("route"), "path", None) or path
        elapsed = perf_counter() - started
        record_http_request(
            method=request.method,
            route=route,
            status_code=status_code,
            duration_seconds=elapsed,
        )
        if audited and bulkhead_acquired:
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
            try:
                if shared_db is not None:
                    await run_in_threadpool(
                        _persist_audit_event,
                        shared_db,
                        request_id=request_id,
                        actor_user_id=getattr(user, "id", None),
                        tenant_id=getattr(scope, "tenant_id", None),
                        patient_id=getattr(scope, "patient_id", None),
                        action=f"{request.method} {route}"[:120],
                        resource_type=_resource_type(route),
                        outcome="success" if status_code < 400 else "denied" if status_code < 500 else "error",
                        metadata=metadata,
                    )
                else:
                    # Test/custom dependency overrides retain their existing
                    # lifecycle and only use a temporary audit Session.
                    override = request.app.dependency_overrides.get(get_db)
                    provider_result = override() if override else SessionLocal()
                    if hasattr(provider_result, "__next__"):
                        provider = provider_result
                        db = next(provider)
                    else:
                        provider = None
                        db = provider_result
                    try:
                        await run_in_threadpool(
                            _persist_audit_event,
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
                    finally:
                        if provider is not None:
                            try:
                                next(provider)
                            except StopIteration:
                                pass
                        elif db is not None:
                            db.close()
            except Exception:
                # Availability protection must not turn audit persistence
                # failures into a second request failure.
                pass
            finally:
                if shared_db is not None:
                    shared_db.close()
                    request.state.db_session = None
        if bulkhead_acquired:
            request.app.state.database_bulkhead.release()
