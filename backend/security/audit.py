from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.db.models import AuditEvent


def append_audit_event(
    db: Session,
    *,
    request_id: str,
    action: str,
    resource_type: str,
    outcome: str,
    actor_user_id: int | None = None,
    tenant_id: str | None = None,
    patient_id: str | None = None,
    resource_id: str = "",
    metadata: dict | None = None,
) -> AuditEvent:
    event = AuditEvent(
        id=f"audit-{uuid4()}",
        request_id=request_id,
        actor_user_id=actor_user_id,
        tenant_id=tenant_id,
        patient_id=patient_id,
        action=action[:120],
        resource_type=resource_type[:80],
        resource_id=resource_id[:160],
        outcome=outcome[:24],
        metadata_json=dict(metadata or {}),
        occurred_at=datetime.utcnow(),
    )
    db.add(event)
    db.flush()
    return event

