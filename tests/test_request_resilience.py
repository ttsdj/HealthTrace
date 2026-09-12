import asyncio
import threading
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.responses import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from starlette.requests import Request

from backend.api.routes import chat as chat_routes
from backend.db.models import AuditEvent
from backend.infra import auth
from backend.infra.database import Base
from backend.schemas import ChatRequest
from backend.security import middleware


def test_audited_request_reuses_one_session_for_dependency_and_audit(tmp_path, monkeypatch):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'request-resilience.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(auth, "SessionLocal", Session)
    monkeypatch.setattr(middleware, "SessionLocal", Session)

    app = FastAPI()
    middleware.configure_database_bulkhead(app)
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/auth/me",
            # The router sets this for a matched route, and the middleware only
            # audits matched routes: an unmatched path reached no endpoint, so
            # there is no authorization decision to record.
            "route": SimpleNamespace(path="/auth/me"),
            "headers": [],
            "app": app,
        }
    )
    seen = []

    async def call_next(current_request):
        provider = auth.get_db(current_request)
        db = next(provider)
        seen.append(db is current_request.state.db_session)
        try:
            return Response(status_code=200)
        finally:
            try:
                next(provider)
            except StopIteration:
                pass

    response = asyncio.run(middleware.audit_request_middleware(request, call_next))

    assert response.status_code == 200
    assert seen == [True]
    with Session() as db:
        assert db.query(AuditEvent).count() == 1


def test_sync_chat_runtime_runs_outside_event_loop(monkeypatch):
    main_thread = threading.get_ident()
    worker_threads = []

    def stub_chat(message, username, session_id, location_context):
        worker_threads.append(threading.get_ident())
        return {"response": f"{username}:{session_id}:{message}"}

    monkeypatch.setattr(chat_routes, "chat_with_agent", stub_chat)

    async def invoke():
        return await chat_routes.chat_endpoint(
            ChatRequest(message="worker-check", session_id="session-1"),
            SimpleNamespace(username="alice"),
        )

    response = asyncio.run(invoke())

    assert response.response == "alice:session-1:worker-check"
    assert worker_threads and worker_threads[0] != main_thread
