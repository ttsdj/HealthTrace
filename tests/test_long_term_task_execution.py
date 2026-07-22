from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from backend.db.models import HealthNotification, HealthTaskRun, PatientFact, PatientTimelineEvent, User
from backend.infra.database import Base
from backend.patient.scope import ensure_user_scope
from backend.tasks.service import (
    HealthGoalService,
    HealthTaskService,
    execute_ready_task_runs,
    process_due_tasks,
)


def _database(tmp_path, name: str):
    engine = create_engine(f"sqlite:///{tmp_path / name}")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _user_scope(db: Session, username: str = "alice"):
    user = User(username=username, password_hash="x", role="user")
    db.add(user)
    db.flush()
    return ensure_user_scope(db, user)


def test_reminder_executes_once_and_creates_one_notification(tmp_path):
    SessionLocal = _database(tmp_path, "reminder.db")
    due = datetime(2026, 8, 1, 9, 0)
    with SessionLocal() as db:
        scope = _user_scope(db)
        service = HealthTaskService(db, scope)
        task, _ = service.create_draft(
            task_type="reminder",
            title="复查血压",
            due_at=due,
            idempotency_key="reminder-execution-1",
        )
        service.confirm(task.id, now=due - timedelta(minutes=1))
        process_due_tasks(db, now=due)
        first = execute_ready_task_runs(db, now=due)
        second = execute_ready_task_runs(db, now=due + timedelta(minutes=1))
        db.commit()

        run = db.query(HealthTaskRun).one()
        notices = db.query(HealthNotification).all()

    assert len(first) == 1
    assert second == []
    assert run.status == "completed"
    assert task.status == "completed"
    assert len(notices) == 1
    assert notices[0].status == "unread"


def test_failed_task_run_retries_with_backoff_then_completes(tmp_path):
    SessionLocal = _database(tmp_path, "retry.db")
    due = datetime(2026, 8, 1, 9, 0)
    calls = {"count": 0}

    def flaky_executor(db, task, run):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("synthetic timeout")
        return "completed", {"recovered": True}

    with SessionLocal() as db:
        scope = _user_scope(db)
        service = HealthTaskService(db, scope)
        task, _ = service.create_draft(
            task_type="follow_up",
            title="症状随访",
            due_at=due,
            idempotency_key="retry-followup-1",
        )
        service.confirm(task.id, now=due)
        process_due_tasks(db, now=due)
        execute_ready_task_runs(db, now=due, executor=flaky_executor)
        run = db.query(HealthTaskRun).one()
        assert run.status == "retry_wait"
        assert run.attempt_count == 1

        assert execute_ready_task_runs(
            db, now=due + timedelta(seconds=29), executor=flaky_executor
        ) == []
        execute_ready_task_runs(db, now=due + timedelta(seconds=31), executor=flaky_executor)
        db.commit()

    assert run.status == "completed"
    assert run.attempt_count == 2
    assert run.result_json["recovered"] is True


def test_periodic_summary_uses_verified_patient_data_and_is_patient_scoped(tmp_path):
    SessionLocal = _database(tmp_path, "summary.db")
    due = datetime(2026, 8, 1, 9, 0)
    with SessionLocal() as db:
        alice_scope = _user_scope(db, "alice")
        bob_scope = _user_scope(db, "bob")
        db.add_all(
            [
                PatientFact(
                    id="fact-alice",
                    tenant_id=alice_scope.tenant_id,
                    patient_id=alice_scope.patient_id,
                    resource_type="Condition",
                    display="已核验病史",
                    verification_status="user_confirmed",
                    active=True,
                ),
                PatientFact(
                    id="fact-bob",
                    tenant_id=bob_scope.tenant_id,
                    patient_id=bob_scope.patient_id,
                    resource_type="Condition",
                    display="其他患者病史",
                    verification_status="user_confirmed",
                    active=True,
                ),
                PatientTimelineEvent(
                    id="event-alice",
                    tenant_id=alice_scope.tenant_id,
                    patient_id=alice_scope.patient_id,
                    event_type="Condition",
                    title="近期复诊",
                    effective_at=due - timedelta(days=1),
                    verification_status="user_confirmed",
                    active=True,
                ),
            ]
        )
        service = HealthTaskService(db, alice_scope)
        task, _ = service.create_draft(
            task_type="periodic_summary",
            title="每周健康摘要",
            due_at=due,
            interval_seconds=604800,
            idempotency_key="weekly-summary-1",
        )
        service.confirm(task.id, now=due)
        process_due_tasks(db, now=due)
        execute_ready_task_runs(db, now=due)
        db.commit()
        run = service.list_runs()[0]
        notice = service.list_notifications()[0]

    assert run.result_json["verified_fact_count"] == 1
    assert run.result_json["recent_event_count"] == 1
    assert "近期复诊" in notice.body
    assert "其他患者病史" not in notice.body
    assert task.status == "active"


def test_measurement_task_waits_for_input_and_goal_creation_is_idempotent(tmp_path):
    SessionLocal = _database(tmp_path, "input-goal.db")
    due = datetime(2026, 8, 1, 9, 0)
    with SessionLocal() as db:
        scope = _user_scope(db)
        goals = HealthGoalService(db, scope)
        first_goal, created = goals.create(
            title="控制收缩压",
            target={"code": "systolic", "operator": "lte", "value": 130, "unit": "mmHg"},
            idempotency_key="bp-goal-2026",
        )
        second_goal, second_created = goals.create(
            title="重复请求不会新建",
            idempotency_key="bp-goal-2026",
        )
        tasks = HealthTaskService(db, scope)
        task, _ = tasks.create_draft(
            task_type="measurement_plan",
            title="记录血压",
            due_at=due,
            payload={"input_schema": {"systolic": "number", "diastolic": "number"}},
            idempotency_key="bp-measurement-1",
        )
        tasks.confirm(task.id, now=due)
        process_due_tasks(db, now=due)
        execute_ready_task_runs(db, now=due)
        run = tasks.list_runs()[0]
        assert run.status == "waiting_input"
        assert task.status == "waiting_input"

        completed = tasks.submit_run_input(run.id, {"systolic": 126, "diastolic": 82})
        db.commit()

    assert created is True
    assert second_created is False
    assert first_goal.id == second_goal.id
    assert completed is not None and completed.status == "completed"
    assert task.status == "completed"
