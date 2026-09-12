"""Upload and delete job progress manager.

The current implementation keeps job state in process memory, which is enough
for local development. A later production deployment can move the same shape to
Redis or PostgreSQL without changing the API contract.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from threading import Lock
from typing import Literal
from uuid import uuid4


StepStatus = Literal["pending", "running", "completed", "failed"]
JobStatus = Literal["pending", "running", "completed", "failed"]


DEFAULT_STEPS = [
    ("upload", "文档上传"),
    ("cleanup", "清理旧版本"),
    ("parse", "解析与分块"),
    ("parent_store", "父级分块入库"),
    ("vector_store", "向量化入库"),
]

DELETE_STEPS = [
    ("prepare", "准备删除"),
    ("bm25", "同步 BM25 统计"),
    ("milvus", "删除向量数据"),
    ("parent_store", "删除父级分块"),
]


def _now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class UploadJobManager:
    """Thread-safe in-memory job state container."""

    # Terminal jobs are evicted after this window so the in-process registry
    # cannot grow without bound on a long-running worker.
    TERMINAL_JOB_TTL_SECONDS = 3600
    MAX_JOBS = 1000

    def __init__(self):
        self._jobs: dict[str, dict] = {}
        self._lock = Lock()

    def _prune_locked(self) -> None:
        now = datetime.now(UTC)
        terminal = {"completed", "failed"}
        expired = []
        for job_id, job in self._jobs.items():
            if job.get("status") not in terminal:
                continue
            try:
                age = (now - datetime.fromisoformat(job.get("updated_at", ""))).total_seconds()
            except ValueError:
                age = self.TERMINAL_JOB_TTL_SECONDS + 1
            if age > self.TERMINAL_JOB_TTL_SECONDS:
                expired.append(job_id)
        for job_id in expired:
            self._jobs.pop(job_id, None)
        if len(self._jobs) <= self.MAX_JOBS:
            return
        removable = sorted(
            (job_id for job_id, job in self._jobs.items() if job.get("status") in terminal),
            key=lambda job_id: self._jobs[job_id].get("updated_at", ""),
        )
        for job_id in removable[: len(self._jobs) - self.MAX_JOBS]:
            self._jobs.pop(job_id, None)

    def create_job(
        self,
        filename: str,
        *,
        steps: list[tuple[str, str]] | None = None,
        current_step: str = "upload",
        message: str = "等待上传",
        completion_step: str = "vector_store",
    ) -> dict:
        steps = steps or DEFAULT_STEPS
        job_id = uuid4().hex
        now = _now_iso()
        job = {
            "job_id": job_id,
            "filename": filename,
            "status": "pending",
            "current_step": current_step,
            "message": message,
            "completion_step": completion_step,
            "total_chunks": 0,
            "processed_chunks": 0,
            "error": None,
            "created_at": now,
            "updated_at": now,
            "steps": [
                {
                    "key": key,
                    "label": label,
                    "percent": 0,
                    "status": "pending",
                    "message": "",
                }
                for key, label in steps
            ],
        }
        with self._lock:
            self._prune_locked()
            self._jobs[job_id] = job
            return deepcopy(job)

    def get_job(self, job_id: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return deepcopy(job) if job else None

    def update_step(
        self,
        job_id: str,
        step_key: str,
        percent: int,
        status: StepStatus = "running",
        message: str = "",
        *,
        total_chunks: int | None = None,
        processed_chunks: int | None = None,
    ) -> dict | None:
        percent = max(0, min(100, int(percent)))
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None

            step = self._find_step(job, step_key)
            if not step:
                return None

            step["percent"] = percent
            step["status"] = status
            step["message"] = message
            job["status"] = "failed" if status == "failed" else "running"
            job["current_step"] = step_key
            job["message"] = message
            job["updated_at"] = _now_iso()

            if total_chunks is not None:
                job["total_chunks"] = int(total_chunks)
            if processed_chunks is not None:
                job["processed_chunks"] = int(processed_chunks)

            return deepcopy(job)

    def complete_step(self, job_id: str, step_key: str, message: str = "") -> dict | None:
        return self.update_step(job_id, step_key, 100, "completed", message)

    def complete_job(self, job_id: str, message: str = "文档入库完成") -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            for step in job["steps"]:
                if step["status"] != "failed":
                    step["percent"] = 100
                    step["status"] = "completed"
            job["status"] = "completed"
            job["current_step"] = job.get("completion_step") or job["current_step"]
            job["message"] = message
            job["error"] = None
            job["updated_at"] = _now_iso()
            return deepcopy(job)

    def fail_job(self, job_id: str, step_key: str, error: str) -> dict | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            step = self._find_step(job, step_key)
            if step:
                step["status"] = "failed"
                step["message"] = error
            job["status"] = "failed"
            job["current_step"] = step_key
            job["message"] = error
            job["error"] = error
            job["updated_at"] = _now_iso()
            return deepcopy(job)

    def list_jobs(self) -> list[dict]:
        with self._lock:
            self._prune_locked()
            return [deepcopy(job) for job in self._jobs.values()]

    @staticmethod
    def _find_step(job: dict, step_key: str) -> dict | None:
        for step in job["steps"]:
            if step["key"] == step_key:
                return step
        return None


upload_job_manager = UploadJobManager()
delete_job_manager = UploadJobManager()
