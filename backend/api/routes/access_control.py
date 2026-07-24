from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db.models import (
    AuditEvent,
    PatientAccessGrant,
    PatientProfile,
    PatientSensitiveRecord,
    TenantMembership,
    User,
)
from backend.infra.auth import get_current_user, get_db, require_admin
from backend.patient.scope import (
    PatientScope,
    ensure_user_scope,
    get_current_patient_scope,
    get_current_patient_write_scope,
)
from backend.schemas.access import (
    AuditEventResponse,
    PatientAccessGrantCreate,
    PatientAccessGrantResponse,
    SensitiveRecordCreate,
    SensitiveRecordResponse,
    TenantMemberCreate,
    TenantMemberResponse,
)
from backend.security.crypto import (
    EncryptionConfigurationError,
    decrypt_json,
    encrypt_json,
)

router = APIRouter(tags=["access-control"])


def _tenant_admin_membership(db: Session, user: User, tenant_id: str) -> TenantMembership:
    membership = (
        db.query(TenantMembership)
        .filter(
            TenantMembership.tenant_id == tenant_id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
            TenantMembership.role.in_(["owner", "admin"]),
        )
        .first()
    )
    if membership is None and user.role != "admin":
        raise HTTPException(status_code=403, detail="Tenant administrator permission required")
    return membership


def _member_response(db: Session, membership: TenantMembership) -> TenantMemberResponse:
    user = db.query(User).filter(User.id == membership.user_id).first()
    return TenantMemberResponse(
        membership_id=membership.id,
        tenant_id=membership.tenant_id,
        username=user.username if user else "deleted-user",
        role=membership.role,
        status=membership.status,
    )


@router.get("/tenant/members", response_model=list[TenantMemberResponse])
async def list_tenant_members(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = ensure_user_scope(db, current_user)
    _tenant_admin_membership(db, current_user, scope.tenant_id)
    rows = (
        db.query(TenantMembership)
        .filter(TenantMembership.tenant_id == scope.tenant_id)
        .order_by(TenantMembership.created_at.asc())
        .all()
    )
    db.commit()
    return [_member_response(db, item) for item in rows]


@router.post("/tenant/members", response_model=TenantMemberResponse)
async def add_tenant_member(
    request: TenantMemberCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    scope = ensure_user_scope(db, current_user)
    _tenant_admin_membership(db, current_user, scope.tenant_id)
    target = db.query(User).filter(User.username == request.username).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    membership = (
        db.query(TenantMembership)
        .filter(
            TenantMembership.tenant_id == scope.tenant_id,
            TenantMembership.user_id == target.id,
        )
        .first()
    )
    if membership is None:
        membership = TenantMembership(
            id=f"membership-{uuid4()}",
            tenant_id=scope.tenant_id,
            user_id=target.id,
            role=request.role,
            status="active",
        )
        db.add(membership)
    else:
        membership.role = request.role
        membership.status = "active"
        membership.updated_at = datetime.utcnow()
    db.commit()
    return _member_response(db, membership)


@router.post("/patient/access-grants", response_model=PatientAccessGrantResponse)
async def grant_patient_access(
    request: PatientAccessGrantCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    patient = db.query(PatientProfile).filter(PatientProfile.id == request.patient_id).first()
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient profile not found")
    _tenant_admin_membership(db, current_user, patient.tenant_id)
    target = db.query(User).filter(User.username == request.username).first()
    if target is None:
        raise HTTPException(status_code=404, detail="User not found")
    membership = (
        db.query(TenantMembership)
        .filter(
            TenantMembership.tenant_id == patient.tenant_id,
            TenantMembership.user_id == target.id,
            TenantMembership.status == "active",
        )
        .first()
    )
    if membership is None:
        raise HTTPException(
            status_code=409,
            detail="Add the user as an active tenant member before granting patient access",
        )
    grant = (
        db.query(PatientAccessGrant)
        .filter(
            PatientAccessGrant.patient_id == patient.id,
            PatientAccessGrant.user_id == target.id,
        )
        .first()
    )
    if grant is None:
        grant = PatientAccessGrant(
            id=f"grant-{uuid4()}",
            tenant_id=patient.tenant_id,
            patient_id=patient.id,
            user_id=target.id,
            permission=request.permission,
            status="active",
            granted_by_user_id=current_user.id,
            expires_at=request.expires_at,
        )
        db.add(grant)
    else:
        grant.permission = request.permission
        grant.status = "active"
        grant.expires_at = request.expires_at
        grant.granted_by_user_id = current_user.id
        grant.updated_at = datetime.utcnow()
    db.commit()
    return PatientAccessGrantResponse(
        grant_id=grant.id,
        tenant_id=grant.tenant_id,
        patient_id=grant.patient_id,
        username=target.username,
        permission=grant.permission,
        status=grant.status,
        expires_at=grant.expires_at,
    )


@router.delete("/patient/access-grants/{grant_id}")
async def revoke_patient_access(
    grant_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    grant = db.query(PatientAccessGrant).filter(PatientAccessGrant.id == grant_id).first()
    if grant is None:
        raise HTTPException(status_code=404, detail="Patient access grant not found")
    _tenant_admin_membership(db, current_user, grant.tenant_id)
    if grant.user_id == current_user.id and grant.permission == "manage":
        raise HTTPException(status_code=409, detail="The active owner grant cannot be revoked")
    grant.status = "revoked"
    grant.updated_at = datetime.utcnow()
    db.commit()
    return {"grant_id": grant.id, "status": grant.status}


def _sensitive_response(item: PatientSensitiveRecord) -> SensitiveRecordResponse:
    payload = decrypt_json(
        item.ciphertext,
        aad=f"{item.tenant_id}:{item.patient_id}:{item.id}:{item.category}",
    )
    return SensitiveRecordResponse(
        record_id=item.id,
        category=item.category,
        payload=payload,
        metadata=item.metadata_json,
        encryption_key_id=item.encryption_key_id,
        created_at=item.created_at,
        updated_at=item.updated_at,
    )


@router.post("/patient/sensitive-records", response_model=SensitiveRecordResponse)
async def create_sensitive_record(
    request: SensitiveRecordCreate,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    record_id = f"secret-{uuid4()}"
    aad = f"{scope.tenant_id}:{scope.patient_id}:{record_id}:{request.category}"
    try:
        ciphertext, key_id = encrypt_json(request.payload, aad=aad)
    except EncryptionConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    record = PatientSensitiveRecord(
        id=record_id,
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
        category=request.category,
        ciphertext=ciphertext,
        encryption_key_id=key_id,
        metadata_json=request.metadata,
        created_by_user_id=scope.user_id,
    )
    db.add(record)
    db.commit()
    return _sensitive_response(record)


@router.get("/patient/sensitive-records", response_model=list[SensitiveRecordResponse])
async def list_sensitive_records(
    category: str | None = Query(default=None),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    query = db.query(PatientSensitiveRecord).filter(
        PatientSensitiveRecord.tenant_id == scope.tenant_id,
        PatientSensitiveRecord.patient_id == scope.patient_id,
    )
    if category:
        query = query.filter(PatientSensitiveRecord.category == category)
    rows = query.order_by(PatientSensitiveRecord.created_at.desc()).all()
    try:
        return [_sensitive_response(item) for item in rows]
    except EncryptionConfigurationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@router.delete("/patient/sensitive-records/{record_id}")
async def delete_sensitive_record(
    record_id: str,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    record = (
        db.query(PatientSensitiveRecord)
        .filter(
            PatientSensitiveRecord.id == record_id,
            PatientSensitiveRecord.tenant_id == scope.tenant_id,
            PatientSensitiveRecord.patient_id == scope.patient_id,
        )
        .first()
    )
    if record is None:
        raise HTTPException(status_code=404, detail="Sensitive record not found")
    db.delete(record)
    db.commit()
    return {"record_id": record_id, "deleted": True}


@router.get("/audit/events", response_model=list[AuditEventResponse])
async def list_audit_events(
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    rows = db.query(AuditEvent).order_by(AuditEvent.occurred_at.desc()).limit(limit).all()
    return [
        AuditEventResponse(
            event_id=item.id,
            request_id=item.request_id,
            actor_user_id=item.actor_user_id,
            tenant_id=item.tenant_id,
            patient_id=item.patient_id,
            action=item.action,
            resource_type=item.resource_type,
            resource_id=item.resource_id,
            outcome=item.outcome,
            metadata=item.metadata_json,
            occurred_at=item.occurred_at,
        )
        for item in rows
    ]

