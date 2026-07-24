from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy.orm import Session

from backend.db.models import (
    GoldenEvaluationCase,
    GoldenEvaluationReview,
    TenantMembership,
    User,
)


def _canonical_hash(question: str, expected: dict) -> str:
    payload = json.dumps(
        {"question": question.strip(), "expected": expected},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def import_jsonl_cases(
    db: Session,
    *,
    path: str | Path,
    dataset_name: str,
    dataset_version: str,
    created_by_user_id: int | None = None,
    clinical_review_required: bool = True,
) -> dict:
    source = Path(path)
    created = 0
    updated = 0
    for line_number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("contains_phi") is True:
            raise ValueError(f"Line {line_number} contains PHI and cannot enter the golden workflow")
        external_case_id = str(row.get("case_id") or row.get("id") or "").strip()
        question = str(row.get("query") or row.get("question") or "").strip()
        if not external_case_id or not question:
            raise ValueError(f"Line {line_number} is missing case_id or question")
        expected = dict(row.get("expected") or {})
        for source_key, target_key in (
            ("expected_action", "action"),
            ("expected_evidence_state", "evidence_state"),
            ("expected_sources", "sources"),
            ("expected_patient_tools", "patient_tools"),
            ("reference_answer", "reference_answer"),
            ("gold_evidence", "gold_evidence"),
        ):
            if source_key in row:
                expected[target_key] = row[source_key]
        digest = _canonical_hash(question, expected)
        case = (
            db.query(GoldenEvaluationCase)
            .filter(
                GoldenEvaluationCase.dataset_name == dataset_name,
                GoldenEvaluationCase.dataset_version == dataset_version,
                GoldenEvaluationCase.external_case_id == external_case_id,
            )
            .first()
        )
        if case is None:
            case = GoldenEvaluationCase(
                id=f"golden-{uuid4()}",
                dataset_name=dataset_name,
                dataset_version=dataset_version,
                external_case_id=external_case_id,
                category=str(row.get("category") or ""),
                question=question,
                expected_json=expected,
                source_reference=str(row.get("source_reference") or ""),
                content_sha256=digest,
                status="draft",
                clinical_review_required=clinical_review_required,
                contains_phi=False,
                created_by_user_id=created_by_user_id,
            )
            db.add(case)
            created += 1
        elif case.content_sha256 != digest:
            case.question = question
            case.expected_json = expected
            case.category = str(row.get("category") or case.category)
            case.content_sha256 = digest
            case.status = "draft"
            case.updated_at = datetime.utcnow()
            db.query(GoldenEvaluationReview).filter(
                GoldenEvaluationReview.case_id == case.id
            ).delete(synchronize_session=False)
            updated += 1
    db.flush()
    return {
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "created": created,
        "updated": updated,
    }


def reviewer_role(db: Session, user: User) -> str:
    if user.role == "clinician":
        return "clinician"
    if (
        db.query(TenantMembership)
        .filter(
            TenantMembership.user_id == user.id,
            TenantMembership.role == "clinician",
            TenantMembership.status == "active",
        )
        .first()
        is not None
    ):
        return "clinician"
    if user.role == "admin":
        return "engineering"
    raise PermissionError("Golden set review requires clinician or administrator role")


def _refresh_case_status(db: Session, case: GoldenEvaluationCase) -> str:
    reviews = (
        db.query(GoldenEvaluationReview)
        .filter(GoldenEvaluationReview.case_id == case.id)
        .all()
    )
    if any(item.decision == "reject" for item in reviews):
        case.status = "rejected"
    elif any(item.decision == "request_changes" for item in reviews):
        case.status = "changes_requested"
    else:
        approvals = [item for item in reviews if item.decision == "approve"]
        required = max(1, int(os.getenv("HEALTHTRACE_GOLDEN_REQUIRED_APPROVALS", "2")))
        has_clinician = any(item.reviewer_role == "clinician" for item in approvals)
        if len(approvals) >= required and (
            not case.clinical_review_required or has_clinician
        ):
            case.status = "approved"
        elif approvals:
            case.status = "in_review"
        else:
            case.status = "draft"
    case.updated_at = datetime.utcnow()
    return case.status


def submit_case_review(
    db: Session,
    *,
    case_id: str,
    reviewer: User,
    decision: str,
    labels: dict | None = None,
    comment: str = "",
) -> tuple[GoldenEvaluationCase, GoldenEvaluationReview]:
    if decision not in {"approve", "reject", "request_changes"}:
        raise ValueError("Unsupported golden review decision")
    case = (
        db.query(GoldenEvaluationCase)
        .filter(GoldenEvaluationCase.id == case_id)
        .first()
    )
    if case is None:
        raise LookupError("Golden evaluation case not found")
    role = reviewer_role(db, reviewer)
    review = (
        db.query(GoldenEvaluationReview)
        .filter(
            GoldenEvaluationReview.case_id == case.id,
            GoldenEvaluationReview.reviewer_user_id == reviewer.id,
        )
        .first()
    )
    if review is None:
        review = GoldenEvaluationReview(
            id=f"review-{uuid4()}",
            case_id=case.id,
            reviewer_user_id=reviewer.id,
            reviewer_role=role,
            decision=decision,
            labels_json=dict(labels or {}),
            comment=comment,
        )
        db.add(review)
        db.flush()
    else:
        review.reviewer_role = role
        review.decision = decision
        review.labels_json = dict(labels or {})
        review.comment = comment
        review.updated_at = datetime.utcnow()
    _refresh_case_status(db, case)
    db.flush()
    return case, review


def golden_readiness(db: Session, dataset_name: str, dataset_version: str) -> dict:
    rows = (
        db.query(GoldenEvaluationCase)
        .filter(
            GoldenEvaluationCase.dataset_name == dataset_name,
            GoldenEvaluationCase.dataset_version == dataset_version,
        )
        .all()
    )
    counts: dict[str, int] = {}
    for item in rows:
        counts[item.status] = counts.get(item.status, 0) + 1
    approved = counts.get("approved", 0)
    return {
        "dataset_name": dataset_name,
        "dataset_version": dataset_version,
        "case_count": len(rows),
        "status_counts": dict(sorted(counts.items())),
        "approved_count": approved,
        "ready": bool(rows) and approved == len(rows),
        "clinical_claim_allowed": bool(rows)
        and approved == len(rows)
        and all(item.clinical_review_required for item in rows),
    }


def export_approved_cases(
    db: Session,
    *,
    dataset_name: str,
    dataset_version: str,
    output_path: str | Path,
) -> int:
    rows = (
        db.query(GoldenEvaluationCase)
        .filter(
            GoldenEvaluationCase.dataset_name == dataset_name,
            GoldenEvaluationCase.dataset_version == dataset_version,
            GoldenEvaluationCase.status == "approved",
        )
        .order_by(GoldenEvaluationCase.external_case_id.asc())
        .all()
    )
    target = Path(output_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8") as handle:
        for item in rows:
            handle.write(
                json.dumps(
                    {
                        "case_id": item.external_case_id,
                        "category": item.category,
                        "query": item.question,
                        "expected": item.expected_json,
                        "source_reference": item.source_reference,
                        "golden_review_status": item.status,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return len(rows)

