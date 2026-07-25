from typing import List, Optional

from pydantic import BaseModel


class DocumentInfo(BaseModel):
    filename: str
    file_type: str
    chunk_count: int
    uploaded_at: Optional[str] = None


class DocumentListResponse(BaseModel):
    documents: List[DocumentInfo]


class DocumentVersionInfo(BaseModel):
    version: int
    status: str
    content_sha256: str
    parent_chunks: int
    leaf_chunks: int
    reused_vectors: int
    embedded_vectors: int
    deleted_vectors: int
    created_at: str
    activated_at: Optional[str] = None
    error: str = ""


class DocumentVersionListResponse(BaseModel):
    document_id: str
    filename: str
    versions: List[DocumentVersionInfo]


class DocumentUploadResponse(BaseModel):
    filename: str
    chunks_processed: int
    message: str
    version: int = 1
    reused_vectors: int = 0
    embedded_vectors: int = 0
    deleted_vectors: int = 0
    unchanged: bool = False


class DocumentUploadStartResponse(BaseModel):
    job_id: str
    filename: str
    message: str


class UploadStepInfo(BaseModel):
    key: str
    label: str
    percent: int
    status: str
    message: str = ""


class DocumentUploadJobResponse(BaseModel):
    job_id: str
    filename: str
    status: str
    current_step: str
    message: str
    total_chunks: int = 0
    processed_chunks: int = 0
    error: Optional[str] = None
    created_at: str
    updated_at: str
    steps: List[UploadStepInfo]


class DocumentDeleteStartResponse(BaseModel):
    job_id: str
    filename: str
    message: str


class DocumentDeleteJobResponse(DocumentUploadJobResponse):
    pass


class DocumentDeleteResponse(BaseModel):
    filename: str
    chunks_deleted: int
    message: str
