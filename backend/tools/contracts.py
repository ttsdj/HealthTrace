from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class EvidenceItem(BaseModel):
    evidence_id: str
    source_type: str
    content: str
    title: str = ""
    document_id: str = ""
    chunk_id: str = ""
    page: int = 0
    score: float = 0.0
    patient_specific: bool = False
    verification_status: str = "retrieved"
    metadata: dict = Field(default_factory=dict)


class EvidenceBundle(BaseModel):
    source: Literal["public_rag", "patient_record", "medical_kg", "patient_fact"]
    query: str
    status: Literal["ok", "empty", "unavailable"]
    evidence: list[EvidenceItem] = Field(default_factory=list)
    retrieval_mode: str = ""
    attempts: list[dict] = Field(default_factory=list)
    error: str = ""


class PatientToolResult(BaseModel):
    tool_name: str
    status: Literal["ok", "empty", "capability_unavailable", "error"]
    data: list[dict] = Field(default_factory=list)
    error: str = ""
