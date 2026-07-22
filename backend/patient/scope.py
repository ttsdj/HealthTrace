from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from fastapi import Depends, HTTPException
from sqlalchemy.orm import Session

from backend.db.models import PatientProfile, Tenant, User
from backend.infra.auth import get_current_user, get_db


@dataclass(frozen=True)
class PatientScope:
    tenant_id: str
    patient_id: str
    user_id: int
    username: str


def ensure_user_scope(db: Session, user: User) -> PatientScope:
    """Provision one isolated personal tenant/patient scope for a user when absent."""
    patient = (
        db.query(PatientProfile)
        .filter(PatientProfile.user_id == user.id, PatientProfile.active.is_(True))
        .first()
    )
    if patient and user.tenant_id == patient.tenant_id:
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

    return PatientScope(tenant_id, patient.id, user.id, user.username)


def get_current_patient_scope(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> PatientScope:
    try:
        scope = ensure_user_scope(db, current_user)
        db.commit()
        return scope
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Unable to resolve patient data scope") from exc
