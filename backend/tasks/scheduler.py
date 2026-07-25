from __future__ import annotations

import asyncio
import os
import time

from backend.indexing.version_retention import (
    purge_expired_document_versions,
    retention_gc_enabled,
)
from backend.infra.database import SessionLocal
from backend.tasks.service import execute_ready_task_runs, process_due_tasks
from backend.tasks.notifications import dispatch_pending_deliveries

_scheduler_task: asyncio.Task | None = None
_last_version_gc_at = 0.0


def _scheduler_tick() -> None:
    global _last_version_gc_at
    db = SessionLocal()
    try:
        process_due_tasks(db)
        execute_ready_task_runs(db)
        gc_interval = max(
            60,
            int(os.getenv("HEALTHTRACE_VERSION_GC_INTERVAL_SECONDS", "3600")),
        )
        monotonic_now = time.monotonic()
        if retention_gc_enabled() and monotonic_now - _last_version_gc_at >= gc_interval:
            purge_expired_document_versions(
                db,
                limit=int(os.getenv("HEALTHTRACE_VERSION_GC_BATCH_SIZE", "50")),
            )
            _last_version_gc_at = monotonic_now
        db.commit()
        if os.getenv("HEALTHTRACE_EXTERNAL_NOTIFICATIONS_ENABLED", "false").lower() == "true":
            dispatch_pending_deliveries(db)
            db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


async def _scheduler_loop() -> None:
    interval = max(10, int(os.getenv("HEALTHTRACE_TASK_POLL_SECONDS", "30")))
    while True:
        try:
            await asyncio.to_thread(_scheduler_tick)
        except Exception as exc:
            print(f"Health task scheduler tick skipped: {type(exc).__name__}")
        await asyncio.sleep(interval)


def start_health_task_scheduler() -> None:
    global _scheduler_task
    if os.getenv("HEALTHTRACE_TASK_SCHEDULER_ENABLED", "true").lower() != "true":
        return
    if _scheduler_task is None or _scheduler_task.done():
        _scheduler_task = asyncio.create_task(_scheduler_loop())


async def stop_health_task_scheduler() -> None:
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None
