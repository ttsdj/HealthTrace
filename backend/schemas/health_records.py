from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ResourceType = Literal[
    "Condition",
    "Observation",
    "MedicationStatement",
    "AllergyIntolerance",
    "DiagnosticReport",
    "Procedure",
    "Immunization",
]


class PatientFactCreate(BaseModel):
    resource_type: ResourceType
    display: str = Field(min_length=1, max_length=500)
    effective_start: datetime
    effective_end: datetime | None = None
    code_system: str = Field(default="", max_length=255)
    code: str = Field(default="", max_length=100)
    value: dict = Field(default_factory=dict)
    clinical_status: str = Field(default="active", max_length=40)
    source_document_id: str | None = None
    source_page: int | None = Field(default=None, ge=0)
    source_chunk_id: str | None = None
    time_precision: Literal["datetime", "day", "month", "year", "approximate"] = "datetime"


class PatientFactResponse(BaseModel):
    fact_id: str
    resource_type: str
    display: str
    value: dict
    clinical_status: str
    verification_status: str
    confidence: float
    effective_start: datetime | None
    effective_end: datetime | None
    source_type: str
    source_document_id: str | None
    source_page: int | None
    source_chunk_id: str | None


class PatientFactListResponse(BaseModel):
    facts: list[PatientFactResponse]


class TimelineEventResponse(BaseModel):
    event_id: str
    event_type: str
    title: str
    summary: str
    effective_at: datetime
    effective_end: datetime | None
    recorded_at: datetime
    time_precision: str
    verification_status: str
    source_fact_id: str | None
    source_document_id: str | None
    source_page: int | None
    source_chunk_id: str | None


class PatientTimelineResponse(BaseModel):
    events: list[TimelineEventResponse]


class FactRetractionResponse(BaseModel):
    fact_id: str
    status: str
