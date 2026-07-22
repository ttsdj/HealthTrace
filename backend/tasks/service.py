from __future__ import annotations

import socket
from datetime import datetime, timedelta
from typing import Callable
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.db.models import (
    HealthGoal,
    HealthNotification,
    HealthTask,
    HealthTaskRun,
    PatientFact,
    PatientTimelineEvent,
)
from backend.patient.scope import PatientScope
from backend.patient.time import utc_naive

SUPPORTED_TASK_TYPES = {
    "reminder",
    "follow_up",
    "measurement_plan",
    "periodic_summary",
    "health_goal_check",
}


def _now(value: datetime | None = None) -> datetime:
    return utc_naive(value) if value else datetime.utcnow()


def _append_task_transition(task: HealthTask, status: str, reason: str, at: datetime) -> None:
    payload = dict(task.payload_json or {})
    history = list(payload.get("task_transitions") or [])
    history.append({"status": status, "reason": reason, "timestamp": at.isoformat()})
    payload["task_transitions"] = history[-50:]
    task.payload_json = payload


class HealthGoalService:
    def __init__(self, db: Session, scope: PatientScope):
        self.db = db
        self.scope = scope

    def _query(self):
        return self.db.query(HealthGoal).filter(
            HealthGoal.tenant_id == self.scope.tenant_id,
            HealthGoal.patient_id == self.scope.patient_id,
        )

    def create(
        self,
        *,
        title: str,
        idempotency_key: str,
        description: str = "",
        target: dict | None = None,
        starts_at: datetime | None = None,
        due_at: datetime | None = None,
    ) -> tuple[HealthGoal, bool]:
        existing = self._query().filter(HealthGoal.idempotency_key == idempotency_key).first()
        if existing is not None:
            return existing, False
        goal = HealthGoal(
            id=f"goal-{uuid4()}",
            tenant_id=self.scope.tenant_id,
            patient_id=self.scope.patient_id,
            title=title,
            description=description,
            status="active",
            target_json=target or {},
            progress_json={},
            idempotency_key=idempotency_key,
            starts_at=utc_naive(starts_at) if starts_at else None,
            due_at=utc_naive(due_at) if due_at else None,
        )
        self.db.add(goal)
        self.db.flush()
        return goal, True

    def list(self, include_archived: bool = False) -> list[HealthGoal]:
        query = self._query()
        if not include_archived:
            query = query.filter(HealthGoal.status != "archived")
        return query.order_by(HealthGoal.created_at.desc()).all()

    def update_progress(self, goal_id: str, progress: dict) -> HealthGoal | None:
        goal = self._query().filter(HealthGoal.id == goal_id).first()
        if goal is None or goal.status == "archived":
            return None
        goal.progress_json = dict(progress)
        goal.updated_at = datetime.utcnow()
        return goal

    def archive(self, goal_id: str, now: datetime | None = None) -> HealthGoal | None:
        goal = self._query().filter(HealthGoal.id == goal_id).first()
        if goal is None:
            return None
        timestamp = _now(now)
        goal.status = "archived"
        goal.archived_at = timestamp
        goal.updated_at = timestamp
        return goal


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

        created_at = datetime.utcnow()
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
            consecutive_failures=0,
        )
        _append_task_transition(task, "draft", "user_or_agent_requested", created_at)
        _append_task_transition(task, "waiting_confirmation", "sensitive_write_requires_confirmation", created_at)
        self.db.add(task)
        self.db.flush()
        return task, True

    def confirm(self, task_id: str, now: datetime | None = None) -> HealthTask | None:
        task = self._query().filter(HealthTask.id == task_id).first()
        if task is None:
            return None
        if task.status == "waiting_confirmation":
            confirmed_at = _now(now)
            task.status = "active"
            task.confirmed_at = confirmed_at
            task.next_run_at = max(task.due_at, confirmed_at)
            task.updated_at = confirmed_at
            _append_task_transition(task, "active", "user_confirmed", confirmed_at)
        return task

    def cancel(self, task_id: str, now: datetime | None = None) -> HealthTask | None:
        task = self._query().filter(HealthTask.id == task_id).first()
        if task is None:
            return None
        if task.status not in {"completed", "cancelled"}:
            timestamp = _now(now)
            task.status = "cancelled"
            task.cancelled_at = timestamp
            task.next_run_at = None
            task.updated_at = timestamp
            _append_task_transition(task, "cancelled", "user_cancelled", timestamp)
        return task

    def list_tasks(self, include_terminal: bool = True) -> list[HealthTask]:
        query = self._query()
        if not include_terminal:
            query = query.filter(HealthTask.status.notin_(["completed", "cancelled", "failed"]))
        return query.order_by(HealthTask.created_at.desc()).all()

    def list_runs(self, task_id: str | None = None) -> list[HealthTaskRun]:
        query = self.db.query(HealthTaskRun).filter(
            HealthTaskRun.tenant_id == self.scope.tenant_id,
            HealthTaskRun.patient_id == self.scope.patient_id,
        )
        if task_id:
            query = query.filter(HealthTaskRun.task_id == task_id)
        return query.order_by(HealthTaskRun.scheduled_for.desc()).all()

    def submit_run_input(
        self, run_id: str, user_input: dict, now: datetime | None = None
    ) -> HealthTaskRun | None:
        run = self.db.query(HealthTaskRun).filter(
            HealthTaskRun.id == run_id,
            HealthTaskRun.tenant_id == self.scope.tenant_id,
            HealthTaskRun.patient_id == self.scope.patient_id,
        ).first()
        if run is None or run.status != "waiting_input":
            return None
        timestamp = _now(now)
        result = dict(run.result_json or {})
        result["user_input"] = user_input
        result.setdefault("task_transitions", []).append(
            {"status": "completed", "reason": "user_input_received", "timestamp": timestamp.isoformat()}
        )
        run.result_json = result
        run.status = "completed"
        run.completed_at = timestamp
        run.updated_at = timestamp
        task = self._query().filter(HealthTask.id == run.task_id).first()
        if task and not task.interval_seconds:
            task.status = "completed"
            task.completed_at = timestamp
            task.updated_at = timestamp
            _append_task_transition(task, "completed", "required_input_received", timestamp)
        return run

    def retry_run(self, run_id: str, now: datetime | None = None) -> HealthTaskRun | None:
        run = self.db.query(HealthTaskRun).filter(
            HealthTaskRun.id == run_id,
            HealthTaskRun.tenant_id == self.scope.tenant_id,
            HealthTaskRun.patient_id == self.scope.patient_id,
        ).first()
        if run is None or run.status not in {"failed", "retry_wait"}:
            return None
        run.status = "ready"
        run.next_retry_at = _now(now)
        run.error_message = ""
        run.updated_at = _now(now)
        return run

    def list_notifications(self, unread_only: bool = False) -> list[HealthNotification]:
        query = self.db.query(HealthNotification).filter(
            HealthNotification.tenant_id == self.scope.tenant_id,
            HealthNotification.patient_id == self.scope.patient_id,
        )
        if unread_only:
            query = query.filter(HealthNotification.status == "unread")
        return query.order_by(HealthNotification.created_at.desc()).all()

    def mark_notification_read(
        self, notification_id: str, now: datetime | None = None
    ) -> HealthNotification | None:
        notification = self.db.query(HealthNotification).filter(
            HealthNotification.id == notification_id,
            HealthNotification.tenant_id == self.scope.tenant_id,
            HealthNotification.patient_id == self.scope.patient_id,
        ).first()
        if notification is None:
            return None
        notification.status = "read"
        notification.read_at = _now(now)
        return notification


def process_due_tasks(db: Session, now: datetime | None = None) -> list[HealthTaskRun]:
    """Enqueue durable due runs; uniqueness makes repeated scheduler ticks idempotent."""
    timestamp = _now(now)
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
        run = db.query(HealthTaskRun).filter(
            HealthTaskRun.task_id == task.id,
            HealthTaskRun.run_key == run_key,
        ).first()
        if run is None:
            run = HealthTaskRun(
                id=f"run-{uuid4()}",
                task_id=task.id,
                tenant_id=task.tenant_id,
                patient_id=task.patient_id,
                run_key=run_key,
                status="ready",
                scheduled_for=scheduled_for,
                result_json={
                    "task_type": task.task_type,
                    "title": task.title,
                    "task_transitions": [
                        {"status": "ready", "reason": "scheduler_enqueued", "timestamp": timestamp.isoformat()}
                    ],
                },
                max_attempts=max(1, min(int((task.payload_json or {}).get("max_attempts", 3)), 5)),
                updated_at=timestamp,
            )
            db.add(run)
            created.append(run)

        if task.interval_seconds:
            task.next_run_at = scheduled_for + timedelta(seconds=task.interval_seconds)
        else:
            task.status = "due"
            task.next_run_at = None
        task.updated_at = timestamp
        _append_task_transition(task, task.status, "scheduler_enqueued_run", timestamp)
    db.flush()
    return created


def _create_notification(
    db: Session,
    task: HealthTask,
    run: HealthTaskRun,
    *,
    notification_type: str,
    title: str,
    body: str,
    payload: dict | None = None,
) -> HealthNotification:
    dedup_key = f"run:{run.id}:{notification_type}"
    existing = db.query(HealthNotification).filter(
        HealthNotification.tenant_id == task.tenant_id,
        HealthNotification.patient_id == task.patient_id,
        HealthNotification.dedup_key == dedup_key,
    ).first()
    if existing is not None:
        return existing
    notification = HealthNotification(
        id=f"notice-{uuid4()}",
        tenant_id=task.tenant_id,
        patient_id=task.patient_id,
        task_id=task.id,
        run_id=run.id,
        notification_type=notification_type,
        title=title,
        body=body,
        payload_json=payload or {},
        dedup_key=dedup_key,
    )
    db.add(notification)
    db.flush()
    from backend.tasks.notifications import create_external_deliveries

    create_external_deliveries(db, notification, task)
    return notification


def _periodic_summary(db: Session, task: HealthTask) -> dict:
    facts = db.query(PatientFact).filter(
        PatientFact.tenant_id == task.tenant_id,
        PatientFact.patient_id == task.patient_id,
        PatientFact.active.is_(True),
        PatientFact.verification_status.in_(["user_confirmed", "clinician_verified"]),
    ).all()
    events = db.query(PatientTimelineEvent).filter(
        PatientTimelineEvent.tenant_id == task.tenant_id,
        PatientTimelineEvent.patient_id == task.patient_id,
        PatientTimelineEvent.active.is_(True),
    ).order_by(PatientTimelineEvent.effective_at.desc()).limit(10).all()
    goals = db.query(HealthGoal).filter(
        HealthGoal.tenant_id == task.tenant_id,
        HealthGoal.patient_id == task.patient_id,
        HealthGoal.status == "active",
    ).all()
    type_counts: dict[str, int] = {}
    for fact in facts:
        type_counts[fact.resource_type] = type_counts.get(fact.resource_type, 0) + 1
    summary = (
        f"本周期共有 {len(facts)} 条已核验健康事实、{len(events)} 条近期时间轴事件，"
        f"当前有 {len(goals)} 个健康目标。"
    )
    if events:
        summary += " 最近事件：" + "；".join(item.title for item in events[:3]) + "。"
    return {
        "summary": summary,
        "verified_fact_count": len(facts),
        "fact_type_counts": type_counts,
        "recent_event_count": len(events),
        "active_goal_count": len(goals),
        "recent_events": [
            {"title": item.title, "effective_at": item.effective_at.isoformat()} for item in events[:5]
        ],
    }


def _evaluate_health_goal(db: Session, task: HealthTask) -> tuple[str, dict]:
    payload = task.payload_json or {}
    goal_id = payload.get("goal_id")
    query = db.query(HealthGoal).filter(
        HealthGoal.tenant_id == task.tenant_id,
        HealthGoal.patient_id == task.patient_id,
        HealthGoal.status == "active",
    )
    goal = query.filter(HealthGoal.id == goal_id).first() if goal_id else query.first()
    if goal is None:
        return "waiting_input", {"reason": "no_active_health_goal"}
    target = goal.target_json or {}
    code = target.get("code")
    observation_query = db.query(PatientFact).filter(
        PatientFact.tenant_id == task.tenant_id,
        PatientFact.patient_id == task.patient_id,
        PatientFact.resource_type == "Observation",
        PatientFact.active.is_(True),
        PatientFact.verification_status.in_(["user_confirmed", "clinician_verified"]),
    )
    if code:
        observation_query = observation_query.filter(PatientFact.code == code)
    observation = observation_query.order_by(PatientFact.effective_start.desc()).first()
    if observation is None:
        return "waiting_input", {"goal_id": goal.id, "reason": "verified_observation_missing"}

    raw_value = (observation.value_json or {}).get("value")
    target_value = target.get("value")
    operator = target.get("operator", "lte")
    met = None
    try:
        actual = float(raw_value)
        expected = float(target_value)
        met = actual <= expected if operator == "lte" else actual >= expected if operator == "gte" else actual == expected
    except (TypeError, ValueError):
        actual = raw_value
        expected = target_value
    progress = {
        "goal_id": goal.id,
        "observation_fact_id": observation.id,
        "actual": actual,
        "target": expected,
        "operator": operator,
        "met": met,
        "evaluated_at": datetime.utcnow().isoformat(),
    }
    goal.progress_json = progress
    goal.updated_at = datetime.utcnow()
    return "completed", progress


def _default_executor(db: Session, task: HealthTask, run: HealthTaskRun) -> tuple[str, dict]:
    if task.task_type in {"reminder", "follow_up"}:
        body = task.description or ("该进行计划中的随访了。" if task.task_type == "follow_up" else "你设置的健康提醒已到期。")
        _create_notification(
            db,
            task,
            run,
            notification_type=task.task_type,
            title=task.title,
            body=body,
        )
        return "completed", {"delivered_via": "in_app", "message": body}
    if task.task_type == "measurement_plan":
        body = task.description or "请记录本次测量值，提交后该轮任务才会完成。"
        _create_notification(
            db,
            task,
            run,
            notification_type="measurement_request",
            title=task.title,
            body=body,
        )
        return "waiting_input", {"required_input": (task.payload_json or {}).get("input_schema", {})}
    if task.task_type == "periodic_summary":
        summary = _periodic_summary(db, task)
        _create_notification(
            db,
            task,
            run,
            notification_type="periodic_summary",
            title=task.title,
            body=summary["summary"],
            payload=summary,
        )
        return "completed", summary
    if task.task_type == "health_goal_check":
        status, result = _evaluate_health_goal(db, task)
        body = "健康目标已完成本轮检查。" if status == "completed" else "缺少目标或已核验测量值，请补充后继续。"
        _create_notification(
            db,
            task,
            run,
            notification_type="health_goal_check",
            title=task.title,
            body=body,
            payload=result,
        )
        return status, result
    raise ValueError(f"Unsupported task type: {task.task_type}")


def execute_ready_task_runs(
    db: Session,
    now: datetime | None = None,
    limit: int = 20,
    executor: Callable[[Session, HealthTask, HealthTaskRun], tuple[str, dict]] | None = None,
) -> list[HealthTaskRun]:
    """Claim and execute durable runs with bounded retries and persisted outcomes."""
    timestamp = _now(now)
    query = db.query(HealthTaskRun).filter(
        (
            (HealthTaskRun.status == "ready")
            | (
                (HealthTaskRun.status == "retry_wait")
                & (HealthTaskRun.next_retry_at.isnot(None))
                & (HealthTaskRun.next_retry_at <= timestamp)
            )
        )
    ).order_by(HealthTaskRun.scheduled_for.asc()).limit(max(1, min(limit, 100)))
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    runs = query.all()
    handled: list[HealthTaskRun] = []
    worker_id = f"{socket.gethostname()}:{uuid4().hex[:8]}"
    execute = executor or _default_executor

    for run in runs:
        task = db.query(HealthTask).filter(HealthTask.id == run.task_id).first()
        if task is None or task.status == "cancelled":
            run.status = "cancelled"
            run.completed_at = timestamp
            run.updated_at = timestamp
            handled.append(run)
            continue

        run.status = "running"
        run.started_at = timestamp
        run.updated_at = timestamp
        run.worker_id = worker_id
        run.attempt_count += 1
        result = dict(run.result_json or {})
        result.setdefault("task_transitions", []).append(
            {"status": "running", "reason": "worker_claimed", "timestamp": timestamp.isoformat()}
        )
        run.result_json = result
        db.flush()

        try:
            final_status, output = execute(db, task, run)
            finished = _now(now)
            result = dict(run.result_json or {})
            result.update(output or {})
            result.setdefault("task_transitions", []).append(
                {"status": final_status, "reason": "executor_finished", "timestamp": finished.isoformat()}
            )
            run.result_json = result
            run.status = final_status
            run.error_message = ""
            run.next_retry_at = None
            run.updated_at = finished
            task.last_run_at = finished
            task.consecutive_failures = 0
            if final_status == "completed":
                run.completed_at = finished
                if not task.interval_seconds:
                    task.status = "completed"
                    task.completed_at = finished
                    _append_task_transition(task, "completed", "run_completed", finished)
            elif final_status == "waiting_input":
                if not task.interval_seconds:
                    task.status = "waiting_input"
                    _append_task_transition(task, "waiting_input", "run_requires_user_input", finished)
            task.updated_at = finished
        except Exception as exc:
            failed_at = _now(now)
            run.error_message = f"{type(exc).__name__}: {str(exc)[:400]}"
            run.updated_at = failed_at
            task.consecutive_failures += 1
            if run.attempt_count < run.max_attempts:
                delay = min(3600, 30 * (2 ** (run.attempt_count - 1)))
                run.status = "retry_wait"
                run.next_retry_at = failed_at + timedelta(seconds=delay)
                reason = "transient_execution_failure"
            else:
                run.status = "failed"
                run.completed_at = failed_at
                reason = "retry_budget_exhausted"
                if not task.interval_seconds:
                    task.status = "failed"
            result = dict(run.result_json or {})
            result.setdefault("task_transitions", []).append(
                {"status": run.status, "reason": reason, "timestamp": failed_at.isoformat()}
            )
            run.result_json = result
            _append_task_transition(task, task.status, reason, failed_at)
            task.updated_at = failed_at
        handled.append(run)
    db.flush()
    return handled
