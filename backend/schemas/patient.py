from datetime import datetime

from pydantic import BaseModel, Field


class PatientScopeResponse(BaseModel):
    tenant_id: str
    patient_id: str


class PatientDocumentInfo(BaseModel):
    document_id: str
    filename: str
    file_type: str
    status: str
    created_at: datetime
    updated_at: datetime
    chunks_processed: int = 0


class PatientDocumentListResponse(BaseModel):
    documents: list[PatientDocumentInfo]


class PatientDocumentUploadResponse(BaseModel):
    document_id: str
    filename: str
    parent_chunks: int
    leaf_chunks: int
    status: str


class PatientSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    top_k: int = Field(default=5, ge=1, le=20)


class PatientEvidenceItem(BaseModel):
    text: str
    filename: str = ""
    page_number: int = 0
    chunk_id: str = ""
    document_id: str = ""
    score: float = 0.0


class PatientSearchResponse(BaseModel):
    mode: str
    evidence: list[PatientEvidenceItem]
    attempts: list[dict]


class PatientDocumentDeleteResponse(BaseModel):
    document_id: str
    vectors_deleted: int
    parent_chunks_deleted: int
    raw_file_deleted: bool
