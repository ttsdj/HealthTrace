from datetime import datetime

from pydantic import BaseModel, Field


class AgentEvaluationJobCreate(BaseModel):
    cases: str = "evaluation/healthtrace_agent_v1.jsonl"
    output_id: str = Field(default="", max_length=100)
    idempotency_key: str = Field(min_length=8, max_length=160)
    dataset_name: str = Field(default="healthtrace_agent", max_length=100)
    dataset_version: str = Field(default="v1", max_length=60)
    require_approved_golden: bool = True


class BackgroundJobResponse(BaseModel):
    job_id: str
    job_type: str
    queue_name: str
    status: str
    progress: dict
    result: dict
    error_message: str
    attempt_count: int
    max_attempts: int
    run_after: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
