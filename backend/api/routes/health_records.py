from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.infra.auth import get_db
from backend.patient.facts import (
    create_patient_fact,
    get_patient_timeline,
    list_patient_facts,
    retract_patient_fact,
)
from backend.patient.scope import (
    PatientScope,
    get_current_patient_scope,
    get_current_patient_write_scope,
)
from backend.patient.trends import calculate_observation_trend
from backend.schemas.health_records import (
    FactRetractionResponse,
    PatientFactCreate,
    PatientFactListResponse,
    PatientFactResponse,
    PatientTimelineResponse,
    ObservationTrendResponse,
    TimelineEventResponse,
)

router = APIRouter(prefix="/patient", tags=["health-records"])


def _fact_response(item) -> PatientFactResponse:
    return PatientFactResponse(
        fact_id=item.id,
        resource_type=item.resource_type,
        display=item.display,
        value=item.value_json,
        clinical_status=item.clinical_status,
        verification_status=item.verification_status,
        confidence=item.confidence,
        effective_start=item.effective_start,
        effective_end=item.effective_end,
        source_type=item.source_type,
        source_document_id=item.source_document_id,
        source_page=item.source_page,
        source_chunk_id=item.source_chunk_id,
    )


@router.post("/facts", response_model=PatientFactResponse)
async def create_fact(
    request: PatientFactCreate,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    try:
        fact = create_patient_fact(
            db,
            scope,
            resource_type=request.resource_type,
            display=request.display,
            effective_start=request.effective_start,
            effective_end=request.effective_end,
            code_system=request.code_system,
            code=request.code,
            value=request.value,
            clinical_status=request.clinical_status,
            verification_status="user_confirmed",
            confidence=1.0,
            source_type="user_reported",
            source_document_id=request.source_document_id,
            source_page=request.source_page,
            source_chunk_id=request.source_chunk_id,
            time_precision=request.time_precision,
        )
        db.commit()
        db.refresh(fact)
        return _fact_response(fact)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/facts", response_model=PatientFactListResponse)
async def list_facts(
    resource_type: str | None = Query(default=None),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    return PatientFactListResponse(
        facts=[_fact_response(item) for item in list_patient_facts(db, scope, resource_type)]
    )


@router.delete("/facts/{fact_id}", response_model=FactRetractionResponse)
async def retract_fact(
    fact_id: str,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    if not retract_patient_fact(db, scope, fact_id):
        raise HTTPException(status_code=404, detail="Patient fact not found")
    db.commit()
    return FactRetractionResponse(fact_id=fact_id, status="retracted")


@router.get("/timeline", response_model=PatientTimelineResponse)
async def timeline(
    limit: int = Query(default=100, ge=1, le=500),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    events = get_patient_timeline(db, scope, limit)
    return PatientTimelineResponse(
        events=[
            TimelineEventResponse(
                event_id=item.id,
                event_type=item.event_type,
                title=item.title,
                summary=item.summary,
                effective_at=item.effective_at,
                effective_end=item.effective_end,
                recorded_at=item.recorded_at,
                time_precision=item.time_precision,
                verification_status=item.verification_status,
                source_fact_id=item.source_fact_id,
                source_document_id=item.source_document_id,
                source_page=item.source_page,
                source_chunk_id=item.source_chunk_id,
            )
            for item in events
        ]
    )


@router.get("/observations/trends/{code}", response_model=ObservationTrendResponse)
async def observation_trend(
    code: str,
    metric: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=1000),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    return ObservationTrendResponse(
        **calculate_observation_trend(db, scope, code, metric=metric, limit=limit)
    )
