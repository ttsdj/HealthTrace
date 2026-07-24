from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor

from backend.infra.database import SessionLocal
from backend.jobs.queue import (
    claim_jobs,
    complete_job,
    fail_or_retry_job,
    requeue_stale_jobs,
)

_worker_task: asyncio.Task | None = None


def _execute_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        from backend.db.models import BackgroundJob

        job = db.query(BackgroundJob).filter(BackgroundJob.id == job_id).first()
        if job is None:
            return
        job_type = job.job_type
        payload = dict(job.payload_json or {})
    finally:
        db.close()

    try:
        if job_type == "document_upload":
            from backend.api.routes.documents import process_queued_upload

            result = process_queued_upload(job_id, payload)
        elif job_type == "document_delete":
            from backend.api.routes.documents import process_queued_delete

            result = process_queued_delete(job_id, payload)
        elif job_type == "agent_evaluation":
            from backend.evaluation.jobs import run_agent_evaluation_job

            result = run_agent_evaluation_job(job_id, payload)
        elif job_type == "patient_document_index":
            from backend.api.routes.patient_documents import (
                process_queued_patient_document,
            )

            result = process_queued_patient_document(job_id, payload)
        else:
            raise ValueError(f"Unsupported background job type: {job_type}")
        complete_job(job_id, result)
    except Exception as exc:
        fail_or_retry_job(job_id, exc)


def run_job_worker_once() -> int:
    db = SessionLocal()
    try:
        requeue_stale_jobs(
            db,
            stale_after_seconds=int(
                os.getenv("HEALTHTRACE_JOB_STALE_SECONDS", "900")
            ),
        )
        job_ids = claim_jobs(
            db,
            queue_name="default",
            limit=int(os.getenv("HEALTHTRACE_JOB_CONCURRENCY", "2")),
        )
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    if job_ids:
        workers = min(
            len(job_ids),
            max(1, int(os.getenv("HEALTHTRACE_JOB_CONCURRENCY", "2"))),
        )
        with ThreadPoolExecutor(
            max_workers=workers,
            thread_name_prefix="healthtrace-job",
        ) as pool:
            list(pool.map(_execute_job, job_ids))
    return len(job_ids)


async def _worker_loop() -> None:
    interval = max(1, int(os.getenv("HEALTHTRACE_JOB_POLL_SECONDS", "2")))
    while True:
        try:
            await asyncio.to_thread(run_job_worker_once)
        except Exception as exc:
            print(f"Background job worker tick skipped: {type(exc).__name__}")
        await asyncio.sleep(interval)


def start_background_job_worker() -> None:
    global _worker_task
    if os.getenv("HEALTHTRACE_JOB_WORKER_ENABLED", "true").lower() != "true":
        return
    if _worker_task is None or _worker_task.done():
        _worker_task = asyncio.create_task(_worker_loop())


async def stop_background_job_worker() -> None:
    global _worker_task
    if _worker_task is None:
        return
    _worker_task.cancel()
    try:
        await _worker_task
    except asyncio.CancelledError:
        pass
    _worker_task = None
