from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db.models import DocumentRecord, PatientFactCandidate
from backend.infra.auth import get_db
from backend.patient.fact_candidates import (
    confirm_fact_candidate,
    extract_candidates_for_document,
    list_fact_candidates,
    reject_fact_candidate,
)
from backend.patient.scope import (
    PatientScope,
    get_current_patient_scope,
    get_current_patient_write_scope,
)
from backend.schemas.fact_candidates import (
    FactCandidateConfirmRequest,
    FactCandidateConfirmResponse,
    FactCandidateExtractionRequest,
    FactCandidateExtractionResponse,
    FactCandidateListResponse,
    FactCandidateRejectResponse,
    FactCandidateResponse,
)

router = APIRouter(prefix="/patient", tags=["fact-candidates"])


def _candidate_response(item: PatientFactCandidate) -> FactCandidateResponse:
    return FactCandidateResponse(
        candidate_id=item.id,
        document_id=item.document_id,
        resource_type=item.resource_type,
        display=item.display,
        value=item.value_json,
        clinical_status=item.clinical_status,
        effective_start=item.effective_start,
        effective_end=item.effective_end,
        time_precision=item.time_precision,
        confidence=item.confidence,
        source_page=item.source_page,
        source_chunk_id=item.source_chunk_id,
        evidence_text=item.evidence_text,
        extraction_method=item.extraction_method,
        status=item.status,
        confirmed_fact_id=item.confirmed_fact_id,
        created_at=item.created_at,
    )


@router.post(
    "/documents/{document_id}/fact-candidates/extract",
    response_model=FactCandidateExtractionResponse,
)
async def extract_fact_candidates(
    document_id: str,
    request: FactCandidateExtractionRequest,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    try:
        candidates, method, warning = extract_candidates_for_document(
            db,
            scope,
            document_id,
            use_llm=request.use_llm,
            consent_external_processing=request.consent_external_processing,
        )
        record = (
            db.query(DocumentRecord)
            .filter(
                DocumentRecord.id == document_id,
                DocumentRecord.tenant_id == scope.tenant_id,
                DocumentRecord.patient_id == scope.patient_id,
            )
            .first()
        )
        if record is not None:
            record.metadata_json = {
                **(record.metadata_json or {}),
                "fact_extraction": {
                    "status": "completed",
                    "method": method,
                    "candidate_count": len(candidates),
                    "warning": warning,
                    "completed_at": datetime.utcnow().isoformat(),
                },
            }
            record.updated_at = datetime.utcnow()
        db.commit()
        return FactCandidateExtractionResponse(
            document_id=document_id,
            extraction_method=method,
            candidate_count=len(candidates),
            warning=warning,
            candidates=[_candidate_response(item) for item in candidates],
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail="Patient fact extraction failed") from exc


@router.get("/fact-candidates", response_model=FactCandidateListResponse)
async def get_fact_candidates(
    status: str | None = Query(default=None, pattern="^(pending|confirmed|rejected)$"),
    document_id: str | None = Query(default=None),
    scope: PatientScope = Depends(get_current_patient_scope),
    db: Session = Depends(get_db),
):
    candidates = list_fact_candidates(
        db,
        scope,
        status=status,
        document_id=document_id,
    )
    return FactCandidateListResponse(
        candidates=[_candidate_response(item) for item in candidates]
    )


@router.post(
    "/fact-candidates/{candidate_id}/confirm",
    response_model=FactCandidateConfirmResponse,
)
async def confirm_candidate(
    candidate_id: str,
    request: FactCandidateConfirmRequest,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    try:
        candidate, fact = confirm_fact_candidate(
            db,
            scope,
            candidate_id,
            request.model_dump(exclude_none=True),
        )
        db.commit()
        db.refresh(candidate)
        return FactCandidateConfirmResponse(
            candidate=_candidate_response(candidate),
            fact_id=fact.id,
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post(
    "/fact-candidates/{candidate_id}/reject",
    response_model=FactCandidateRejectResponse,
)
async def reject_candidate(
    candidate_id: str,
    scope: PatientScope = Depends(get_current_patient_write_scope),
    db: Session = Depends(get_db),
):
    try:
        candidate = reject_fact_candidate(db, scope, candidate_id)
        db.commit()
        return FactCandidateRejectResponse(candidate_id=candidate.id, status="rejected")
    except ValueError as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc
