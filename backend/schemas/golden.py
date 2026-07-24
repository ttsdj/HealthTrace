from datetime import datetime

from pydantic import BaseModel, Field


class GoldenImportRequest(BaseModel):
    path: str = "evaluation/healthtrace_agent_v1.jsonl"
    dataset_name: str = Field(default="healthtrace_agent", min_length=1, max_length=100)
    dataset_version: str = Field(default="v1", min_length=1, max_length=60)
    clinical_review_required: bool = True


class GoldenReviewRequest(BaseModel):
    decision: str = Field(pattern="^(approve|reject|request_changes)$")
    labels: dict = Field(default_factory=dict)
    comment: str = Field(default="", max_length=4000)


class GoldenCaseResponse(BaseModel):
    case_id: str
    external_case_id: str
    dataset_name: str
    dataset_version: str
    category: str
    question: str
    expected: dict
    source_reference: str
    status: str
    clinical_review_required: bool
    review_count: int
    updated_at: datetime


class GoldenReviewResponse(BaseModel):
    review_id: str
    case_id: str
    reviewer_user_id: int
    reviewer_role: str
    decision: str
    labels: dict
    comment: str
    updated_at: datetime

