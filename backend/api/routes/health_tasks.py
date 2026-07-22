from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.infra.auth import get_db
from backend.patient.scope import PatientScope, get_current_patient_scope
from backend.schemas.tasks import (
    HealthTaskCreate,
    HealthTaskListResponse,
    HealthTaskResponse,
    HealthTaskRunListResponse,
    HealthTaskRunResponse,
)
from backend.tasks.service import HealthTaskService

router = APIRouter(prefix="/patient/tasks", tags=["health-tasks"])


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
        created=created,
    )


@router.post("", response_model=HealthTaskResponse)
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


@router.post("/{task_id}/confirm", response_model=HealthTaskResponse)
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


@router.post("/{task_id}/cancel", response_model=HealthTaskResponse)
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


@router.get("", response_model=HealthTaskListResponse)
async def list_tasks(
    include_terminal: bool = Query(default=True),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    tasks = HealthTaskService(db, scope).list_tasks(include_terminal)
    return HealthTaskListResponse(tasks=[_task_response(item) for item in tasks])


@router.get("/runs", response_model=HealthTaskRunListResponse)
async def list_task_runs(
    task_id: str | None = Query(default=None),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    runs = HealthTaskService(db, scope).list_runs(task_id)
    return HealthTaskRunListResponse(
        runs=[
            HealthTaskRunResponse(
                run_id=item.id,
                task_id=item.task_id,
                status=item.status,
                scheduled_for=item.scheduled_for,
                result=item.result_json,
            )
            for item in runs
        ]
    )
