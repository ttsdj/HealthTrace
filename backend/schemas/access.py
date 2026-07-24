from datetime import datetime

from pydantic import BaseModel, Field


class TenantMemberCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    role: str = Field(default="viewer", pattern="^(admin|clinician|viewer)$")


class TenantMemberResponse(BaseModel):
    membership_id: str
    tenant_id: str
    username: str
    role: str
    status: str


class PatientAccessGrantCreate(BaseModel):
    patient_id: str = Field(min_length=1, max_length=64)
    username: str = Field(min_length=1, max_length=100)
    permission: str = Field(default="read", pattern="^(read|write|manage)$")
    expires_at: datetime | None = None


class PatientAccessGrantResponse(BaseModel):
    grant_id: str
    tenant_id: str
    patient_id: str
    username: str
    permission: str
    status: str
    expires_at: datetime | None


class SensitiveRecordCreate(BaseModel):
    category: str = Field(
        pattern="^(identity|contact|insurance|emergency_contact|clinical_note)$"
    )
    payload: dict
    metadata: dict = Field(default_factory=dict)


class SensitiveRecordResponse(BaseModel):
    record_id: str
    category: str
    payload: dict
    metadata: dict
    encryption_key_id: str
    created_at: datetime
    updated_at: datetime


class AuditEventResponse(BaseModel):
    event_id: str
    request_id: str
    actor_user_id: int | None
    tenant_id: str | None
    patient_id: str | None
    action: str
    resource_type: str
    resource_id: str
    outcome: str
    metadata: dict
    occurred_at: datetime

