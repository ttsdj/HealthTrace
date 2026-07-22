from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.db.models import DocumentRecord, PatientFact, PatientTimelineEvent
from backend.patient.scope import PatientScope
from backend.patient.time import utc_naive

FHIR_LIKE_RESOURCE_TYPES = {
    "Condition",
    "Observation",
    "MedicationStatement",
    "AllergyIntolerance",
    "DiagnosticReport",
    "Procedure",
    "Immunization",
}


def _scope_fact_query(db: Session, scope: PatientScope):
    return db.query(PatientFact).filter(
        PatientFact.tenant_id == scope.tenant_id,
        PatientFact.patient_id == scope.patient_id,
    )


def assert_document_in_scope(db: Session, scope: PatientScope, document_id: str | None) -> None:
    if not document_id:
        return
    exists = (
        db.query(DocumentRecord.id)
        .filter(
            DocumentRecord.id == document_id,
            DocumentRecord.document_domain == "patient_private",
            DocumentRecord.tenant_id == scope.tenant_id,
            DocumentRecord.patient_id == scope.patient_id,
            DocumentRecord.owner_user_id == scope.user_id,
        )
        .first()
    )
    if exists is None:
        raise ValueError("Source document is outside the authenticated patient scope")


def create_patient_fact(
    db: Session,
    scope: PatientScope,
    *,
    resource_type: str,
    display: str,
    effective_start: datetime,
    code_system: str = "",
    code: str = "",
    value: dict | None = None,
    clinical_status: str = "active",
    verification_status: str = "user_confirmed",
    confidence: float = 1.0,
    source_type: str = "user_reported",
    source_document_id: str | None = None,
    source_page: int | None = None,
    source_chunk_id: str | None = None,
    effective_end: datetime | None = None,
    time_precision: str = "datetime",
) -> PatientFact:
    if resource_type not in FHIR_LIKE_RESOURCE_TYPES:
        raise ValueError(f"Unsupported FHIR-like resource type: {resource_type}")
    assert_document_in_scope(db, scope, source_document_id)
    effective_start = utc_naive(effective_start)
    effective_end = utc_naive(effective_end) if effective_end else None

    fact = PatientFact(
        id=f"fact-{uuid4()}",
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
        resource_type=resource_type,
        code_system=code_system,
        code=code,
        display=display,
        value_json=value or {},
        clinical_status=clinical_status,
        verification_status=verification_status,
        confidence=max(0.0, min(float(confidence), 1.0)),
        effective_start=effective_start,
        effective_end=effective_end,
        source_type=source_type,
        source_document_id=source_document_id,
        source_page=source_page,
        source_chunk_id=source_chunk_id,
        asserted_by_user_id=scope.user_id,
    )
    db.add(fact)
    db.flush()

    event = PatientTimelineEvent(
        id=f"event-{uuid4()}",
        tenant_id=scope.tenant_id,
        patient_id=scope.patient_id,
        event_type=resource_type,
        title=display,
        summary=_event_summary(resource_type, display, value or {}),
        effective_at=effective_start,
        effective_end=effective_end,
        time_precision=time_precision,
        source_fact_id=fact.id,
        source_document_id=source_document_id,
        source_page=source_page,
        source_chunk_id=source_chunk_id,
        verification_status=verification_status,
    )
    db.add(event)
    db.flush()
    return fact


def _event_summary(resource_type: str, display: str, value: dict) -> str:
    rendered_value = value.get("value")
    unit = value.get("unit", "")
    if rendered_value is None:
        return f"{resource_type}: {display}"
    return f"{resource_type}: {display} = {rendered_value} {unit}".strip()


def list_patient_facts(
    db: Session,
    scope: PatientScope,
    resource_type: str | None = None,
    include_inactive: bool = False,
) -> list[PatientFact]:
    query = _scope_fact_query(db, scope)
    if resource_type:
        query = query.filter(PatientFact.resource_type == resource_type)
    if not include_inactive:
        query = query.filter(PatientFact.active.is_(True))
    return query.order_by(PatientFact.effective_start.desc(), PatientFact.created_at.desc()).all()


def get_patient_timeline(
    db: Session,
    scope: PatientScope,
    limit: int = 100,
) -> list[PatientTimelineEvent]:
    return (
        db.query(PatientTimelineEvent)
        .filter(
            PatientTimelineEvent.tenant_id == scope.tenant_id,
            PatientTimelineEvent.patient_id == scope.patient_id,
            PatientTimelineEvent.active.is_(True),
        )
        .order_by(PatientTimelineEvent.effective_at.desc(), PatientTimelineEvent.recorded_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )


def retract_patient_fact(db: Session, scope: PatientScope, fact_id: str) -> bool:
    fact = _scope_fact_query(db, scope).filter(PatientFact.id == fact_id).first()
    if fact is None:
        return False
    fact.active = False
    fact.clinical_status = "entered-in-error"
    fact.updated_at = datetime.utcnow()
    (
        db.query(PatientTimelineEvent)
        .filter(
            PatientTimelineEvent.source_fact_id == fact.id,
            PatientTimelineEvent.tenant_id == scope.tenant_id,
            PatientTimelineEvent.patient_id == scope.patient_id,
        )
        .update({"active": False}, synchronize_session=False)
    )
    return True
