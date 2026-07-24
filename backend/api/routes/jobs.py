from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db.models import BackgroundJob, User
from backend.infra.auth import get_db, require_admin
from backend.jobs.queue import enqueue_job
from backend.patient.scope import PatientScope, get_current_patient_scope
from backend.evaluation.golden_review import golden_readiness
from backend.schemas.jobs import AgentEvaluationJobCreate, BackgroundJobResponse

router = APIRouter(tags=["background-jobs"])


def _response(job: BackgroundJob) -> BackgroundJobResponse:
    return BackgroundJobResponse(
        job_id=job.id,
        job_type=job.job_type,
        queue_name=job.queue_name,
        status=job.status,
        progress=job.progress_json,
        result=job.result_json,
        error_message=job.error_message,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        run_after=job.run_after,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )


@router.post("/evaluation/agent/jobs", response_model=BackgroundJobResponse)
async def enqueue_agent_evaluation(
    request: AgentEvaluationJobCreate,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    readiness = golden_readiness(db, request.dataset_name, request.dataset_version)
    if request.require_approved_golden and not readiness["ready"]:
        raise HTTPException(
            status_code=409,
            detail={
                "message": "Official evaluation requires a fully approved golden set",
                "readiness": readiness,
            },
        )
    job, _ = enqueue_job(
        db,
        job_type="agent_evaluation",
        queue_name="default",
        payload={
            "cases": request.cases,
            "output_id": request.output_id,
            "dataset_name": request.dataset_name,
            "dataset_version": request.dataset_version,
            "require_approved_golden": request.require_approved_golden,
        },
        progress={
            "current_step": "evaluate",
            "message": "Waiting for background evaluation",
            "steps": [
                {
                    "key": "evaluate",
                    "label": "Evaluate cases",
                    "percent": 0,
                    "status": "pending",
                    "message": "",
                }
            ],
        },
        idempotency_key=request.idempotency_key,
        created_by_user_id=current_user.id,
        max_attempts=2,
    )
    db.commit()
    return _response(job)


@router.get("/jobs/{job_id}", response_model=BackgroundJobResponse)
async def get_background_job(
    job_id: str,
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
    if job is None:
        raise HTTPException(status_code=404, detail="Background job not found")
    return _response(job)


@router.get("/patient/jobs/{job_id}", response_model=BackgroundJobResponse)
async def get_patient_background_job(
    job_id: str,
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    job = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.id == job_id,
            BackgroundJob.tenant_id == scope.tenant_id,
            BackgroundJob.patient_id == scope.patient_id,
        )
        .first()
    )
    if job is None:
        raise HTTPException(status_code=404, detail="Patient background job not found")
    return _response(job)


@router.get("/jobs", response_model=list[BackgroundJobResponse])
async def list_background_jobs(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    query = db.query(BackgroundJob)
    if status:
        query = query.filter(BackgroundJob.status == status)
    rows = query.order_by(BackgroundJob.created_at.desc()).limit(limit).all()
    return [_response(item) for item in rows]
