from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import HealthTask, HealthTaskRun, User
from backend.infra.database import Base
from backend.patient.scope import ensure_user_scope
from backend.tasks.service import HealthTaskService, process_due_tasks


def test_task_creation_is_idempotent_and_requires_confirmation(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'tasks.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    due = datetime(2026, 8, 1, 9, 0, tzinfo=timezone(timedelta(hours=8)))

    with TestSession() as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        service = HealthTaskService(db, scope)
        first, first_created = service.create_draft(
            task_type="reminder",
            title="复查血糖",
            due_at=due,
            idempotency_key="follow-up-20260801",
        )
        second, second_created = service.create_draft(
            task_type="reminder",
            title="复查血糖",
            due_at=due,
            idempotency_key="follow-up-20260801",
        )
        db.commit()

        runs = process_due_tasks(db, now=datetime(2026, 8, 2))

    assert first.id == second.id
    assert first_created is True
    assert second_created is False
    assert first.status == "waiting_confirmation"
    assert runs == []


def test_confirmed_due_task_creates_exactly_one_run(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'due.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    due = datetime(2026, 8, 1, 9, 0)

    with TestSession() as db:
        user = User(username="alice", password_hash="x", role="user")
        db.add(user)
        db.flush()
        scope = ensure_user_scope(db, user)
        service = HealthTaskService(db, scope)
        task, _ = service.create_draft(
            task_type="follow_up",
            title="症状随访",
            due_at=due,
            idempotency_key="symptom-follow-up-1",
        )
        service.confirm(task.id, now=datetime(2026, 7, 31))
        db.commit()

        first = process_due_tasks(db, now=datetime(2026, 8, 1, 9, 1))
        db.commit()
        second = process_due_tasks(db, now=datetime(2026, 8, 1, 9, 2))
        db.commit()

        assert db.query(HealthTaskRun).count() == 1
        assert db.query(HealthTask).filter_by(id=task.id).one().status == "due"

    assert len(first) == 1
    assert second == []


def test_tasks_are_patient_scoped_and_cancellable(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'task-scope.db'}")
    Base.metadata.create_all(engine)
    TestSession = sessionmaker(bind=engine, expire_on_commit=False)
    with TestSession() as db:
        alice = User(username="alice", password_hash="x", role="user")
        bob = User(username="bob", password_hash="x", role="user")
        db.add_all([alice, bob])
        db.flush()
        alice_scope = ensure_user_scope(db, alice)
        bob_scope = ensure_user_scope(db, bob)
        alice_service = HealthTaskService(db, alice_scope)
        bob_service = HealthTaskService(db, bob_scope)
        task, _ = alice_service.create_draft(
            task_type="measurement_plan",
            title="记录血压",
            due_at=datetime(2026, 8, 1),
            idempotency_key="alice-bp-plan",
            interval_seconds=86400,
        )
        db.commit()

        assert bob_service.list_tasks() == []
        assert bob_service.cancel(task.id) is None
        cancelled = alice_service.cancel(task.id)
        db.commit()

    assert cancelled is not None
    assert cancelled.status == "cancelled"
