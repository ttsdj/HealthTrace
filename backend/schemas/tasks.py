from datetime import datetime

from pydantic import BaseModel, Field


class HealthTaskCreate(BaseModel):
    task_type: str
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    due_at: datetime
    timezone: str = Field(default="Asia/Shanghai", max_length=64)
    interval_seconds: int | None = Field(default=None, ge=60)
    payload: dict = Field(default_factory=dict)
    idempotency_key: str = Field(min_length=8, max_length=120)


class HealthTaskResponse(BaseModel):
    task_id: str
    task_type: str
    title: str
    description: str
    status: str
    due_at: datetime
    next_run_at: datetime | None
    timezone: str
    interval_seconds: int | None
    confirmation_required: bool
    created: bool = False


class HealthTaskListResponse(BaseModel):
    tasks: list[HealthTaskResponse]


class HealthTaskRunResponse(BaseModel):
    run_id: str
    task_id: str
    status: str
    scheduled_for: datetime
    result: dict


class HealthTaskRunListResponse(BaseModel):
    runs: list[HealthTaskRunResponse]
