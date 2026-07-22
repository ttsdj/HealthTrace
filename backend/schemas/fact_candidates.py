from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.health_records import ResourceType


class FactCandidateExtractionRequest(BaseModel):
    use_llm: bool = True
    consent_external_processing: bool = False


class FactCandidateResponse(BaseModel):
    candidate_id: str
    document_id: str
    resource_type: str
    display: str
    value: dict
    clinical_status: str
    effective_start: datetime | None
    effective_end: datetime | None
    time_precision: str
    confidence: float
    source_page: int | None
    source_chunk_id: str | None
    evidence_text: str
    extraction_method: str
    status: str
    confirmed_fact_id: str | None
    created_at: datetime


class FactCandidateListResponse(BaseModel):
    candidates: list[FactCandidateResponse]


class FactCandidateExtractionResponse(BaseModel):
    document_id: str
    extraction_method: str
    candidate_count: int
    warning: str = ""
    candidates: list[FactCandidateResponse]


class FactCandidateConfirmRequest(BaseModel):
    resource_type: ResourceType | None = None
    display: str | None = Field(default=None, min_length=1, max_length=500)
    value: dict | None = None
    clinical_status: str | None = Field(default=None, max_length=40)
    effective_start: datetime | None = None
    effective_end: datetime | None = None
    time_precision: Literal["datetime", "day", "month", "year", "approximate"] | None = None


class FactCandidateConfirmResponse(BaseModel):
    candidate: FactCandidateResponse
    fact_id: str
    timeline_created: bool = True


class FactCandidateRejectResponse(BaseModel):
    candidate_id: str
    status: Literal["rejected"]
