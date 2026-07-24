from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from backend.db.models import (
    GoldenEvaluationCase,
    GoldenEvaluationReview,
    User,
)
from backend.env import PROJECT_ROOT
from backend.evaluation.golden_review import (
    golden_readiness,
    import_jsonl_cases,
    submit_case_review,
)
from backend.infra.auth import get_current_user, get_db, require_admin
from backend.schemas.golden import (
    GoldenCaseResponse,
    GoldenImportRequest,
    GoldenReviewRequest,
    GoldenReviewResponse,
)

router = APIRouter(prefix="/evaluation/golden", tags=["golden-evaluation"])


def _case_response(db: Session, item: GoldenEvaluationCase) -> GoldenCaseResponse:
    review_count = (
        db.query(GoldenEvaluationReview)
        .filter(GoldenEvaluationReview.case_id == item.id)
        .count()
    )
    return GoldenCaseResponse(
        case_id=item.id,
        external_case_id=item.external_case_id,
        dataset_name=item.dataset_name,
        dataset_version=item.dataset_version,
        category=item.category,
        question=item.question,
        expected=item.expected_json,
        source_reference=item.source_reference,
        status=item.status,
        clinical_review_required=item.clinical_review_required,
        review_count=review_count,
        updated_at=item.updated_at,
    )


@router.post("/import")
async def import_golden_cases(
    request: GoldenImportRequest,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    source = (PROJECT_ROOT / request.path).resolve()
    try:
        source.relative_to((PROJECT_ROOT / "evaluation").resolve())
    except ValueError as exc:
        raise HTTPException(
            status_code=400,
            detail="Golden source must be inside the evaluation directory",
        ) from exc
    if not source.is_file():
        raise HTTPException(status_code=404, detail="Golden source file not found")
    try:
        result = import_jsonl_cases(
            db,
            path=source,
            dataset_name=request.dataset_name,
            dataset_version=request.dataset_version,
            created_by_user_id=current_user.id,
            clinical_review_required=request.clinical_review_required,
        )
        db.commit()
        return {
            **result,
            "readiness": golden_readiness(
                db, request.dataset_name, request.dataset_version
            ),
        }
    except (ValueError, OSError) as exc:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.get("/cases", response_model=list[GoldenCaseResponse])
async def list_golden_cases(
    dataset_name: str = Query(default="healthtrace_agent"),
    dataset_version: str = Query(default="v1"),
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(GoldenEvaluationCase).filter(
        GoldenEvaluationCase.dataset_name == dataset_name,
        GoldenEvaluationCase.dataset_version == dataset_version,
    )
    if status:
        query = query.filter(GoldenEvaluationCase.status == status)
    rows = query.order_by(GoldenEvaluationCase.external_case_id.asc()).limit(limit).all()
    return [_case_response(db, item) for item in rows]


@router.post("/cases/{case_id}/reviews", response_model=GoldenReviewResponse)
async def review_golden_case(
    case_id: str,
    request: GoldenReviewRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        _, review = submit_case_review(
            db,
            case_id=case_id,
            reviewer=current_user,
            decision=request.decision,
            labels=request.labels,
            comment=request.comment,
        )
        db.commit()
        return GoldenReviewResponse(
            review_id=review.id,
            case_id=review.case_id,
            reviewer_user_id=review.reviewer_user_id,
            reviewer_role=review.reviewer_role,
            decision=review.decision,
            labels=review.labels_json,
            comment=review.comment,
            updated_at=review.updated_at,
        )
    except LookupError as exc:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except (PermissionError, ValueError) as exc:
        db.rollback()
        raise HTTPException(status_code=403, detail=str(exc)) from exc


@router.get("/readiness")
async def golden_dataset_readiness(
    dataset_name: str = Query(default="healthtrace_agent"),
    dataset_version: str = Query(default="v1"),
    _: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return golden_readiness(db, dataset_name, dataset_version)

