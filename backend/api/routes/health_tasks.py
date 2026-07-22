from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.infra.auth import get_db
from backend.patient.scope import PatientScope, get_current_patient_scope
from backend.schemas.tasks import (
    HealthGoalCreate,
    HealthGoalListResponse,
    HealthGoalProgressUpdate,
    HealthGoalResponse,
    HealthNotificationListResponse,
    HealthNotificationResponse,
    HealthTaskCreate,
    HealthTaskListResponse,
    HealthTaskResponse,
    HealthTaskRunInput,
    HealthTaskRunListResponse,
    HealthTaskRunResponse,
)
from backend.tasks.service import HealthGoalService, HealthTaskService

router = APIRouter(prefix="/patient", tags=["health-tasks"])


def _task_response(task, created: bool = False) -> HealthTaskResponse:
    return HealthTaskResponse(
        task_id=task.id,
        task_type=task.task_type,
        title=task.title,
        description=task.description,
        status=task.status,
        due_at=task.due_at,
        next_run_at=task.next_run_at,
        timezone=task.timezone,
        interval_seconds=task.interval_seconds,
        confirmation_required=task.confirmation_required,
        consecutive_failures=task.consecutive_failures,
        last_run_at=task.last_run_at,
        created=created,
    )


def _run_response(run) -> HealthTaskRunResponse:
    return HealthTaskRunResponse(
        run_id=run.id,
        task_id=run.task_id,
        status=run.status,
        scheduled_for=run.scheduled_for,
        result=run.result_json,
        error_message=run.error_message,
        attempt_count=run.attempt_count,
        max_attempts=run.max_attempts,
        next_retry_at=run.next_retry_at,
    )


def _goal_response(goal, created: bool = False) -> HealthGoalResponse:
    return HealthGoalResponse(
        goal_id=goal.id,
        title=goal.title,
        description=goal.description,
        status=goal.status,
        target=goal.target_json,
        progress=goal.progress_json,
        starts_at=goal.starts_at,
        due_at=goal.due_at,
        created=created,
    )


def _notification_response(item) -> HealthNotificationResponse:
    return HealthNotificationResponse(
        notification_id=item.id,
        notification_type=item.notification_type,
        title=item.title,
        body=item.body,
        status=item.status,
        task_id=item.task_id,
        run_id=item.run_id,
        payload=item.payload_json,
        created_at=item.created_at,
        read_at=item.read_at,
    )


@router.post("/tasks", response_model=HealthTaskResponse)
async def create_task(
    request: HealthTaskCreate,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    try:
        task, created = HealthTaskService(db, scope).create_draft(
            task_type=request.task_type,
            title=request.title,
            description=request.description,
            due_at=request.due_at,
            timezone=request.timezone,
            interval_seconds=request.interval_seconds,
            payload=request.payload,
            idempotency_key=request.idempotency_key,
        )
        db.commit()
        return _task_response(task, created)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/tasks/{task_id}/confirm", response_model=HealthTaskResponse)
async def confirm_task(
    task_id: str,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    task = HealthTaskService(db, scope).confirm(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Health task not found")
    db.commit()
    return _task_response(task)


@router.post("/tasks/{task_id}/cancel", response_model=HealthTaskResponse)
async def cancel_task(
    task_id: str,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    task = HealthTaskService(db, scope).cancel(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="Health task not found")
    db.commit()
    return _task_response(task)


@router.get("/tasks", response_model=HealthTaskListResponse)
async def list_tasks(
    include_terminal: bool = Query(default=True),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    tasks = HealthTaskService(db, scope).list_tasks(include_terminal)
    return HealthTaskListResponse(tasks=[_task_response(item) for item in tasks])


@router.get("/tasks/runs", response_model=HealthTaskRunListResponse)
async def list_task_runs(
    task_id: str | None = Query(default=None),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    runs = HealthTaskService(db, scope).list_runs(task_id)
    return HealthTaskRunListResponse(runs=[_run_response(item) for item in runs])


@router.post("/tasks/runs/{run_id}/input", response_model=HealthTaskRunResponse)
async def submit_task_run_input(
    run_id: str,
    request: HealthTaskRunInput,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    run = HealthTaskService(db, scope).submit_run_input(run_id, request.values)
    if run is None:
        raise HTTPException(status_code=404, detail="Waiting health task run not found")
    db.commit()
    return _run_response(run)


@router.post("/tasks/runs/{run_id}/retry", response_model=HealthTaskRunResponse)
async def retry_task_run(
    run_id: str,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    run = HealthTaskService(db, scope).retry_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Retryable health task run not found")
    db.commit()
    return _run_response(run)


@router.post("/goals", response_model=HealthGoalResponse)
async def create_goal(
    request: HealthGoalCreate,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    goal, created = HealthGoalService(db, scope).create(
        title=request.title,
        description=request.description,
        target=request.target,
        starts_at=request.starts_at,
        due_at=request.due_at,
        idempotency_key=request.idempotency_key,
    )
    db.commit()
    return _goal_response(goal, created)


@router.get("/goals", response_model=HealthGoalListResponse)
async def list_goals(
    include_archived: bool = Query(default=False),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    goals = HealthGoalService(db, scope).list(include_archived)
    return HealthGoalListResponse(goals=[_goal_response(item) for item in goals])


@router.patch("/goals/{goal_id}/progress", response_model=HealthGoalResponse)
async def update_goal_progress(
    goal_id: str,
    request: HealthGoalProgressUpdate,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    goal = HealthGoalService(db, scope).update_progress(goal_id, request.progress)
    if goal is None:
        raise HTTPException(status_code=404, detail="Active health goal not found")
    db.commit()
    return _goal_response(goal)


@router.post("/goals/{goal_id}/archive", response_model=HealthGoalResponse)
async def archive_goal(
    goal_id: str,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    goal = HealthGoalService(db, scope).archive(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail="Health goal not found")
    db.commit()
    return _goal_response(goal)


@router.get("/notifications", response_model=HealthNotificationListResponse)
async def list_notifications(
    unread_only: bool = Query(default=False),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    notifications = HealthTaskService(db, scope).list_notifications(unread_only)
    return HealthNotificationListResponse(
        notifications=[_notification_response(item) for item in notifications]
    )


@router.post("/notifications/{notification_id}/read", response_model=HealthNotificationResponse)
async def mark_notification_read(
    notification_id: str,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    notification = HealthTaskService(db, scope).mark_notification_read(notification_id)
    if notification is None:
        raise HTTPException(status_code=404, detail="Health notification not found")
    db.commit()
    return _notification_response(notification)
