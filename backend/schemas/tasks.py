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
    consecutive_failures: int = 0
    last_run_at: datetime | None = None
    created: bool = False


class HealthTaskListResponse(BaseModel):
    tasks: list[HealthTaskResponse]


class HealthTaskRunResponse(BaseModel):
    run_id: str
    task_id: str
    status: str
    scheduled_for: datetime
    result: dict
    error_message: str = ""
    attempt_count: int = 0
    max_attempts: int = 3
    next_retry_at: datetime | None = None


class HealthTaskRunListResponse(BaseModel):
    runs: list[HealthTaskRunResponse]


class HealthTaskRunInput(BaseModel):
    values: dict = Field(default_factory=dict)


class HealthGoalCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=4000)
    target: dict = Field(default_factory=dict)
    starts_at: datetime | None = None
    due_at: datetime | None = None
    idempotency_key: str = Field(min_length=8, max_length=120)


class HealthGoalProgressUpdate(BaseModel):
    progress: dict = Field(default_factory=dict)


class HealthGoalResponse(BaseModel):
    goal_id: str
    title: str
    description: str
    status: str
    target: dict
    progress: dict
    starts_at: datetime | None
    due_at: datetime | None
    created: bool = False


class HealthGoalListResponse(BaseModel):
    goals: list[HealthGoalResponse]


class HealthNotificationResponse(BaseModel):
    notification_id: str
    notification_type: str
    title: str
    body: str
    status: str
    task_id: str | None
    run_id: str | None
    payload: dict
    created_at: datetime
    read_at: datetime | None


class HealthNotificationListResponse(BaseModel):
    notifications: list[HealthNotificationResponse]
