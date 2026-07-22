from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import HealthNotification, HealthNotificationDelivery, User
from backend.infra.database import Base
from backend.infra.migrations import (
    apply_phase4_notification_delivery_migration,
    get_phase4_migration_status,
    rollback_phase4_notification_delivery_migration,
)
from backend.patient.scope import ensure_user_scope
from backend.tasks.notifications import dispatch_pending_deliveries
from backend.tasks.service import HealthTaskService, execute_ready_task_runs, process_due_tasks


def _session(tmp_path, name="deliveries.db"):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return engine, sessionmaker(bind=engine, expire_on_commit=False)


def _execute_reminder(db: Session, *, consent: bool, channels: list[str]):
    user = User(username="alice", password_hash="x")
    db.add(user)
    db.flush()
    scope = ensure_user_scope(db, user)
    due = datetime(2026, 8, 1, 9, 0)
    task, _ = HealthTaskService(db, scope).create_draft(
        task_type="reminder",
        title="复查提醒",
        due_at=due,
        idempotency_key="external-reminder-1",
        payload={
            "notification_channels": channels,
            "external_notification_consent": consent,
            "notification_email": "patient@example.com",
        },
    )
    HealthTaskService(db, scope).confirm(task.id, now=due)
    process_due_tasks(db, now=due)
    execute_ready_task_runs(db, now=due)
    db.commit()
    return task, due


def test_external_failure_does_not_rollback_completed_task_or_in_app_notice(tmp_path):
    _, SessionLocal = _session(tmp_path)
    with SessionLocal() as db:
        task, due = _execute_reminder(db, consent=True, channels=["in_app", "webhook"])

        def fail(*_):
            raise TimeoutError("synthetic webhook timeout")

        dispatch_pending_deliveries(db, now=due, senders={"webhook": fail})
        db.commit()
        delivery = db.query(HealthNotificationDelivery).one()
        notice = db.query(HealthNotification).one()

    assert task.status == "completed"
    assert notice.status == "unread"
    assert delivery.status == "retry_wait"
    assert delivery.attempt_count == 1


def test_delivery_retries_idempotently_then_succeeds(tmp_path):
    _, SessionLocal = _session(tmp_path, "retry-delivery.db")
    calls = {"count": 0}
    with SessionLocal() as db:
        _, due = _execute_reminder(db, consent=True, channels=["webhook"])

        def flaky(*_):
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("first attempt")

        dispatch_pending_deliveries(db, now=due, senders={"webhook": flaky})
        assert dispatch_pending_deliveries(db, now=due + timedelta(seconds=29), senders={"webhook": flaky}) == []
        dispatch_pending_deliveries(db, now=due + timedelta(seconds=31), senders={"webhook": flaky})
        db.commit()
        delivery = db.query(HealthNotificationDelivery).one()

    assert calls["count"] == 2
    assert delivery.status == "delivered"
    assert delivery.attempt_count == 2


def test_external_channel_without_explicit_consent_is_skipped(tmp_path):
    _, SessionLocal = _session(tmp_path, "consent.db")
    with SessionLocal() as db:
        _execute_reminder(db, consent=False, channels=["email"])
        delivery = db.query(HealthNotificationDelivery).one()
    assert delivery.status == "skipped"
    assert delivery.attempt_count == 0
    assert "consent" in delivery.error_message


def test_phase4_migration_is_additive_and_rollback_preserves_table(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'phase4.db'}")
    Base.metadata.create_all(engine)
    result = apply_phase4_notification_delivery_migration(engine)
    assert result["status"] == "applied"
    assert get_phase4_migration_status(engine)["status"] == "applied"
    rolled_back = rollback_phase4_notification_delivery_migration(engine)
    assert rolled_back["data_preserved"] is True
    assert get_phase4_migration_status(engine)["status"] == "rolled_back"
