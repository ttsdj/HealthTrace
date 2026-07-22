from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import ChatMessage, ChatSession, HealthNotification, HealthTaskRun, User
from backend.infra.database import Base
from backend.observability.service import build_observability_summary


def test_observability_aggregates_traces_without_content(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'observability.db'}")
    Base.metadata.create_all(engine)
    now = datetime(2026, 7, 22, 12, 0)
    with Session(engine) as db:
        user = User(username="alice", password_hash="x", role="admin")
        db.add(user)
        db.flush()
        session = ChatSession(user_id=user.id, session_id="private-session")
        db.add(session)
        db.flush()
        db.add(
            ChatMessage(
                session_ref_id=session.id,
                message_type="ai",
                content="sensitive answer that must not be returned",
                timestamp=now - timedelta(minutes=5),
                rag_trace={
                    "evidence_state": "SUFFICIENT",
                    "action": "ANSWER",
                    "retrieval_mode": "hybrid",
                    "ragas_quality_score": 0.8,
                    "tool_calls": [
                        {"tool_name": "search_knowledge_base", "status": "ok", "latency_ms": 120},
                        {"tool_name": "search_medical_kg", "status": "error", "latency_ms": 400},
                    ],
                },
            )
        )
        db.add(
            HealthTaskRun(
                id="run-1",
                task_id="task-1",
                tenant_id="tenant-1",
                patient_id="patient-1",
                run_key="run-key-1",
                status="retry_wait",
                scheduled_for=now,
                attempt_count=2,
                created_at=now,
            )
        )
        db.add(
            HealthNotification(
                id="notice-1",
                tenant_id="tenant-1",
                patient_id="patient-1",
                notification_type="reminder",
                title="private title",
                dedup_key="notice-key-1",
                created_at=now,
            )
        )
        db.commit()

        summary = build_observability_summary(db, hours=24, now=now)

    assert summary["chat"]["trace_turns"] == 1
    assert summary["chat"]["retrieval_modes"] == {"hybrid": 1}
    assert summary["tools"]["success_rate"] == 0.5
    assert summary["tools"]["latency_p95_ms"] == 400
    assert summary["tasks"]["retry_rate"] == 1.0
    assert summary["quality"]["ragas_quality_score"] == 0.8
    assert "sensitive" not in str(summary)
    assert "alice" not in str(summary)
