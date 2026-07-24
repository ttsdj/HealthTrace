from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import uuid4

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from backend.db.models import (
    PatientAccessGrant,
    PatientProfile,
    Tenant,
    TenantMembership,
    User,
)
from backend.infra.auth import get_current_user, get_db

_PERMISSION_LEVEL = {"read": 1, "write": 2, "manage": 3}


@dataclass(frozen=True)
class PatientScope:
    tenant_id: str
    patient_id: str
    user_id: int
    username: str
    access_level: str = "manage"


def _ensure_owner_access(db: Session, user: User, patient: PatientProfile) -> None:
    membership = (
        db.query(TenantMembership)
        .filter(
            TenantMembership.tenant_id == patient.tenant_id,
            TenantMembership.user_id == user.id,
        )
        .first()
    )
    if membership is None:
        db.add(
            TenantMembership(
                id=f"membership-{uuid4()}",
                tenant_id=patient.tenant_id,
                user_id=user.id,
                role="owner",
                status="active",
            )
        )
    elif membership.role != "owner" or membership.status != "active":
        membership.role = "owner"
        membership.status = "active"
        membership.updated_at = datetime.utcnow()

    grant = (
        db.query(PatientAccessGrant)
        .filter(
            PatientAccessGrant.patient_id == patient.id,
            PatientAccessGrant.user_id == user.id,
        )
        .first()
    )
    if grant is None:
        db.add(
            PatientAccessGrant(
                id=f"grant-{uuid4()}",
                tenant_id=patient.tenant_id,
                patient_id=patient.id,
                user_id=user.id,
                permission="manage",
                status="active",
                granted_by_user_id=user.id,
            )
        )
    elif grant.permission != "manage" or grant.status != "active":
        grant.permission = "manage"
        grant.status = "active"
        grant.expires_at = None
        grant.updated_at = datetime.utcnow()


def ensure_user_scope(db: Session, user: User) -> PatientScope:
    """Provision one isolated personal tenant/patient scope for a user when absent."""
    patient = (
        db.query(PatientProfile)
        .filter(PatientProfile.user_id == user.id, PatientProfile.active.is_(True))
        .first()
    )
    if patient and user.tenant_id == patient.tenant_id:
        _ensure_owner_access(db, user, patient)
        return PatientScope(patient.tenant_id, patient.id, user.id, user.username)

    tenant_id = user.tenant_id or f"tenant-{uuid4()}"
    tenant = db.query(Tenant).filter(Tenant.id == tenant_id).first()
    if tenant is None:
        tenant = Tenant(id=tenant_id, name=f"Personal workspace for {user.username}")
        db.add(tenant)
        db.flush()

    user.tenant_id = tenant_id
    if patient is None:
        patient = PatientProfile(
            id=f"patient-{uuid4()}",
            tenant_id=tenant_id,
            user_id=user.id,
            display_name=user.username,
        )
        db.add(patient)
        db.flush()
    elif patient.tenant_id != tenant_id:
        raise RuntimeError("Patient scope and user tenant are inconsistent")

    _ensure_owner_access(db, user, patient)
    return PatientScope(tenant_id, patient.id, user.id, user.username)


def resolve_patient_scope(
    db: Session,
    user: User,
    *,
    patient_id: str | None = None,
    required_permission: str = "read",
) -> PatientScope:
    if required_permission not in _PERMISSION_LEVEL:
        raise ValueError(f"Unsupported patient permission: {required_permission}")

    own_scope = ensure_user_scope(db, user)
    if not patient_id or patient_id == own_scope.patient_id:
        return own_scope

    patient = (
        db.query(PatientProfile)
        .filter(PatientProfile.id == patient_id, PatientProfile.active.is_(True))
        .first()
    )
    if patient is None:
        raise HTTPException(status_code=404, detail="Patient profile not found")

    membership = (
        db.query(TenantMembership)
        .filter(
            TenantMembership.tenant_id == patient.tenant_id,
            TenantMembership.user_id == user.id,
            TenantMembership.status == "active",
        )
        .first()
    )
    grant = (
        db.query(PatientAccessGrant)
        .filter(
            PatientAccessGrant.patient_id == patient.id,
            PatientAccessGrant.user_id == user.id,
            PatientAccessGrant.status == "active",
        )
        .first()
    )
    now = datetime.utcnow()
    if (
        membership is None
        or grant is None
        or (grant.expires_at is not None and grant.expires_at <= now)
        or _PERMISSION_LEVEL.get(grant.permission, 0)
        < _PERMISSION_LEVEL[required_permission]
    ):
        raise HTTPException(status_code=403, detail="Patient access is not authorized")
    return PatientScope(
        patient.tenant_id,
        patient.id,
        user.id,
        user.username,
        grant.permission,
    )


def get_current_patient_scope(
    request: Request,
    x_patient_id: str | None = Header(default=None, alias="X-Patient-ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientScope:
    try:
        scope = resolve_patient_scope(
            db,
            current_user,
            patient_id=x_patient_id,
            required_permission="read",
        )
        db.commit()
        request.state.patient_scope = scope
        return scope
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to resolve patient data scope") from exc


def get_current_patient_write_scope(
    request: Request,
    x_patient_id: str | None = Header(default=None, alias="X-Patient-ID"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientScope:
    try:
        scope = resolve_patient_scope(
            db,
            current_user,
            patient_id=x_patient_id,
            required_permission="write",
        )
        db.commit()
        request.state.patient_scope = scope
        return scope
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to resolve patient write scope") from exc
