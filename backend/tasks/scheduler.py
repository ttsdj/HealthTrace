from __future__ import annotations

import asyncio
import os

from backend.infra.database import SessionLocal
from backend.tasks.service import process_due_tasks

_scheduler_task: asyncio.Task | None = None


async def _scheduler_loop() -> None:
    interval = max(10, int(os.getenv("HEALTHTRACE_TASK_POLL_SECONDS", "30")))
    while True:
        db = SessionLocal()
        try:
            process_due_tasks(db)
            db.commit()
        except Exception as exc:
            db.rollback()
            print(f"Health task scheduler tick skipped: {type(exc).__name__}")
        finally:
            db.close()
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
