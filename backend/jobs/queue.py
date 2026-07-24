from __future__ import annotations

import os
import socket
from copy import deepcopy
from datetime import datetime, timedelta
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.db.models import BackgroundJob
from backend.infra.database import SessionLocal

TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def enqueue_job(
    db: Session,
    *,
    job_type: str,
    payload: dict,
    idempotency_key: str,
    progress: dict | None = None,
    queue_name: str = "default",
    tenant_id: str | None = None,
    patient_id: str | None = None,
    created_by_user_id: int | None = None,
    max_attempts: int = 3,
    job_id: str | None = None,
) -> tuple[BackgroundJob, bool]:
    existing = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.job_type == job_type,
            BackgroundJob.idempotency_key == idempotency_key,
        )
        .first()
    )
    if existing is not None:
        return existing, False
    now = datetime.utcnow()
    job = BackgroundJob(
        id=job_id or f"job-{uuid4()}",
        job_type=job_type,
        queue_name=queue_name,
        tenant_id=tenant_id,
        patient_id=patient_id,
        created_by_user_id=created_by_user_id,
        status="queued",
        payload_json=dict(payload),
        progress_json=dict(progress or {}),
        result_json={},
        idempotency_key=idempotency_key,
        max_attempts=max(1, min(int(max_attempts), 10)),
        run_after=now,
        updated_at=now,
    )
    db.add(job)
    db.flush()
    return job, True


def claim_jobs(
    db: Session,
    *,
    queue_name: str = "default",
    limit: int = 4,
    worker_id: str | None = None,
    now: datetime | None = None,
) -> list[str]:
    timestamp = now or datetime.utcnow()
    worker = worker_id or f"{socket.gethostname()}:{os.getpid()}"
    query = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.queue_name == queue_name,
            BackgroundJob.status.in_(["queued", "retry_wait"]),
            BackgroundJob.run_after <= timestamp,
        )
        .order_by(BackgroundJob.run_after.asc(), BackgroundJob.created_at.asc())
        .limit(max(1, min(limit, 50)))
    )
    if db.bind and db.bind.dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    jobs = query.all()
    for job in jobs:
        job.status = "running"
        job.attempt_count += 1
        job.locked_at = timestamp
        job.updated_at = timestamp
        job.worker_id = worker
    db.flush()
    return [job.id for job in jobs]


def requeue_stale_jobs(
    db: Session,
    *,
    stale_after_seconds: int = 900,
    now: datetime | None = None,
) -> int:
    timestamp = now or datetime.utcnow()
    cutoff = timestamp - timedelta(seconds=max(60, stale_after_seconds))
    rows = (
        db.query(BackgroundJob)
        .filter(
            BackgroundJob.status == "running",
            BackgroundJob.locked_at.is_not(None),
            BackgroundJob.locked_at < cutoff,
        )
        .all()
    )
    for job in rows:
        if job.attempt_count >= job.max_attempts:
            job.status = "failed"
            job.error_message = "stale_worker_timeout"
            job.completed_at = timestamp
        else:
            job.status = "retry_wait"
            job.run_after = timestamp
            job.error_message = "stale_worker_requeued"
        job.locked_at = None
        job.worker_id = ""
        job.updated_at = timestamp
    db.flush()
    return len(rows)


class JobReporter:
    """Persist progress in short transactions so polling survives worker restarts."""

    def __init__(self, job_id: str):
        self.job_id = job_id

    def _mutate(self, callback) -> dict | None:
        db = SessionLocal()
        try:
            job = db.query(BackgroundJob).filter(BackgroundJob.id == self.job_id).first()
            if job is None:
                return None
            snapshot = deepcopy(job.progress_json or {})
            callback(snapshot)
            job.progress_json = snapshot
            job.updated_at = datetime.utcnow()
            job.locked_at = datetime.utcnow()
            db.commit()
            return deepcopy(snapshot)
        except Exception:
            db.rollback()
            return None
        finally:
            db.close()

    def get_job(self, _job_id: str | None = None) -> dict | None:
        db = SessionLocal()
        try:
            job = db.query(BackgroundJob).filter(BackgroundJob.id == self.job_id).first()
            return job_snapshot(job) if job else None
        finally:
            db.close()

    def update_step(
        self,
        _job_id: str,
        step_key: str,
        percent: int,
        status: str = "running",
        message: str = "",
        *,
        total_chunks: int | None = None,
        processed_chunks: int | None = None,
    ) -> dict | None:
        def update(snapshot: dict) -> None:
            for step in snapshot.get("steps", []):
                if step.get("key") == step_key:
                    step.update(
                        percent=max(0, min(100, int(percent))),
                        status=status,
                        message=message,
                    )
                    break
            snapshot["status"] = "failed" if status == "failed" else "running"
            snapshot["current_step"] = step_key
            snapshot["message"] = message
            snapshot["updated_at"] = datetime.utcnow().isoformat() + "Z"
            if total_chunks is not None:
                snapshot["total_chunks"] = int(total_chunks)
            if processed_chunks is not None:
                snapshot["processed_chunks"] = int(processed_chunks)

        return self._mutate(update)

    def complete_step(self, job_id: str, step_key: str, message: str = "") -> dict | None:
        return self.update_step(job_id, step_key, 100, "completed", message)

    def complete_job(self, _job_id: str, message: str = "Job completed") -> dict | None:
        def update(snapshot: dict) -> None:
            for step in snapshot.get("steps", []):
                if step.get("status") != "failed":
                    step.update(percent=100, status="completed")
            snapshot["status"] = "completed"
            snapshot["message"] = message
            snapshot["error"] = None
            snapshot["updated_at"] = datetime.utcnow().isoformat() + "Z"

        return self._mutate(update)

    def fail_job(self, _job_id: str, step_key: str, error: str) -> dict | None:
        def update(snapshot: dict) -> None:
            for step in snapshot.get("steps", []):
                if step.get("key") == step_key:
                    step.update(status="failed", message=error)
                    break
            snapshot["status"] = "failed"
            snapshot["current_step"] = step_key
            snapshot["message"] = error
            snapshot["error"] = error
            snapshot["updated_at"] = datetime.utcnow().isoformat() + "Z"

        return self._mutate(update)


def job_snapshot(job: BackgroundJob) -> dict:
    progress = deepcopy(job.progress_json or {})
    progress.setdefault("job_id", job.id)
    progress.setdefault("filename", str((job.payload_json or {}).get("filename", "")))
    progress["status"] = (
        progress.get("status")
        if job.status == "running" and progress.get("status") == "running"
        else job.status
    )
    progress["error"] = job.error_message or progress.get("error")
    progress["created_at"] = (
        progress.get("created_at") or job.created_at.isoformat() + "Z"
    )
    progress["updated_at"] = job.updated_at.isoformat() + "Z"
    if job.status == "retry_wait":
        progress["message"] = (
            f"Retry scheduled after attempt {job.attempt_count}: {job.error_message}"
        )
    return progress


def complete_job(job_id: str, result: dict | None = None) -> None:
    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if job is None:
            return
        now = datetime.utcnow()
        job.status = "completed"
        job.result_json = dict(result or {})
        job.error_message = ""
        job.locked_at = None
        job.completed_at = now
        job.updated_at = now
        db.commit()
    finally:
        db.close()


def fail_or_retry_job(job_id: str, exc: Exception) -> None:
    db = SessionLocal()
    try:
        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if job is None:
            return
        now = datetime.utcnow()
        job.error_message = f"{type(exc).__name__}: {str(exc)[:1000]}"
        job.locked_at = None
        job.worker_id = ""
        if job.attempt_count < job.max_attempts:
            delay = min(3600, 15 * (2 ** max(0, job.attempt_count - 1)))
            job.status = "retry_wait"
            job.run_after = now + timedelta(seconds=delay)
        else:
            job.status = "failed"
            job.completed_at = now
        job.updated_at = now
        progress = dict(job.progress_json or {})
        progress["status"] = job.status
        progress["error"] = job.error_message
        progress["message"] = (
            "Background job will retry automatically"
            if job.status == "retry_wait"
            else "Background job failed after bounded retries"
        )
        job.progress_json = progress
        db.commit()
    finally:
        db.close()

