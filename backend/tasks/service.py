from __future__ import annotations

from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.db.models import HealthTask, HealthTaskRun
from backend.patient.scope import PatientScope
from backend.patient.time import utc_naive

SUPPORTED_TASK_TYPES = {
    "reminder",
    "follow_up",
    "measurement_plan",
    "periodic_summary",
    "health_goal_check",
}


class HealthTaskService:
    def __init__(self, db: Session, scope: PatientScope):
        self.db = db
        self.scope = scope

    def _query(self):
        return self.db.query(HealthTask).filter(
            HealthTask.tenant_id == self.scope.tenant_id,
            HealthTask.patient_id == self.scope.patient_id,
        )

    def create_draft(
        self,
        *,
        task_type: str,
        title: str,
        due_at: datetime,
        idempotency_key: str,
        description: str = "",
        timezone: str = "Asia/Shanghai",
        interval_seconds: int | None = None,
        payload: dict | None = None,
    ) -> tuple[HealthTask, bool]:
        if task_type not in SUPPORTED_TASK_TYPES:
            raise ValueError(f"Unsupported health task type: {task_type}")
        if interval_seconds is not None and interval_seconds < 60:
            raise ValueError("Recurring task interval must be at least 60 seconds")
        due_at = utc_naive(due_at)
        existing = self._query().filter(HealthTask.idempotency_key == idempotency_key).first()
        if existing is not None:
            return existing, False

        task = HealthTask(
            id=f"task-{uuid4()}",
            tenant_id=self.scope.tenant_id,
            patient_id=self.scope.patient_id,
            task_type=task_type,
            title=title,
            description=description,
            status="waiting_confirmation",
            due_at=due_at,
            next_run_at=None,
            timezone=timezone,
            interval_seconds=interval_seconds,
            payload_json=payload or {},
            idempotency_key=idempotency_key,
            confirmation_required=True,
            created_by_user_id=self.scope.user_id,
        )
        self.db.add(task)
        self.db.flush()
        return task, True

    def confirm(self, task_id: str, now: datetime | None = None) -> HealthTask | None:
        task = self._query().filter(HealthTask.id == task_id).first()
        if task is None:
            return None
        if task.status == "waiting_confirmation":
            confirmed_at = utc_naive(now) if now else datetime.utcnow()
            task.status = "active"
            task.confirmed_at = confirmed_at
            task.next_run_at = max(task.due_at, confirmed_at)
            task.updated_at = confirmed_at
        return task

    def cancel(self, task_id: str, now: datetime | None = None) -> HealthTask | None:
        task = self._query().filter(HealthTask.id == task_id).first()
        if task is None:
            return None
        if task.status not in {"completed", "cancelled"}:
            timestamp = utc_naive(now) if now else datetime.utcnow()
            task.status = "cancelled"
            task.cancelled_at = timestamp
            task.next_run_at = None
            task.updated_at = timestamp
        return task

    def list_tasks(self, include_terminal: bool = True) -> list[HealthTask]:
        query = self._query()
        if not include_terminal:
            query = query.filter(HealthTask.status.notin_(["completed", "cancelled"]))
        return query.order_by(HealthTask.created_at.desc()).all()

    def list_runs(self, task_id: str | None = None) -> list[HealthTaskRun]:
        query = self.db.query(HealthTaskRun).filter(
            HealthTaskRun.tenant_id == self.scope.tenant_id,
            HealthTaskRun.patient_id == self.scope.patient_id,
        )
        if task_id:
            query = query.filter(HealthTaskRun.task_id == task_id)
        return query.order_by(HealthTaskRun.scheduled_for.desc()).all()


def process_due_tasks(db: Session, now: datetime | None = None) -> list[HealthTaskRun]:
    """Create durable due runs; uniqueness makes repeated scheduler ticks idempotent."""
    timestamp = utc_naive(now) if now else datetime.utcnow()
    due = (
        db.query(HealthTask)
        .filter(
            HealthTask.status == "active",
            HealthTask.next_run_at.isnot(None),
            HealthTask.next_run_at <= timestamp,
        )
        .order_by(HealthTask.next_run_at.asc())
        .all()
    )
    created: list[HealthTaskRun] = []
    for task in due:
        scheduled_for = task.next_run_at or task.due_at
        run_key = scheduled_for.isoformat(timespec="seconds")
        run = (
            db.query(HealthTaskRun)
            .filter(HealthTaskRun.task_id == task.id, HealthTaskRun.run_key == run_key)
            .first()
        )
        if run is None:
            run = HealthTaskRun(
                id=f"run-{uuid4()}",
                task_id=task.id,
                tenant_id=task.tenant_id,
                patient_id=task.patient_id,
                run_key=run_key,
                status="ready",
                scheduled_for=scheduled_for,
                result_json={"task_type": task.task_type, "title": task.title},
            )
            db.add(run)
            created.append(run)

        if task.interval_seconds:
            task.next_run_at = scheduled_for + timedelta(seconds=task.interval_seconds)
        else:
            task.status = "due"
            task.next_run_at = None
        task.updated_at = timestamp
    db.flush()
    return created
