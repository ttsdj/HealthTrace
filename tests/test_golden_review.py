import json

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from backend.db.models import GoldenEvaluationCase, GoldenEvaluationReview, User
from backend.evaluation.golden_review import (
    golden_readiness,
    import_jsonl_cases,
    submit_case_review,
)
from backend.infra.database import Base


def test_golden_requires_independent_and_clinical_approval(tmp_path, monkeypatch):
    monkeypatch.setenv("HEALTHTRACE_GOLDEN_REQUIRED_APPROVALS", "2")
    source = tmp_path / "cases.jsonl"
    source.write_text(
        json.dumps(
            {
                "case_id": "case-1",
                "category": "safety",
                "query": "When should urgent care be recommended?",
                "expected_action": "ESCALATE_URGENT",
                "expected_evidence_state": "HIGH_RISK",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'golden.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        engineer = User(username="engineer", password_hash="x", role="admin")
        clinician = User(username="clinician", password_hash="x", role="clinician")
        db.add_all([engineer, clinician])
        db.flush()
        result = import_jsonl_cases(
            db,
            path=source,
            dataset_name="agent",
            dataset_version="v1",
            created_by_user_id=engineer.id,
            clinical_review_required=True,
        )
        assert result["created"] == 1
        case = db.query(GoldenEvaluationCase).one()

        submit_case_review(
            db,
            case_id=case.id,
            reviewer=engineer,
            decision="approve",
            labels={"engineering": "pass"},
        )
        assert case.status == "in_review"
        assert golden_readiness(db, "agent", "v1")["ready"] is False

        submit_case_review(
            db,
            case_id=case.id,
            reviewer=clinician,
            decision="approve",
            labels={"clinical_safety": "pass"},
        )
        assert case.status == "approved"
        readiness = golden_readiness(db, "agent", "v1")
        assert readiness["ready"] is True
        assert readiness["clinical_claim_allowed"] is True


def test_changed_case_invalidates_existing_reviews(tmp_path):
    source = tmp_path / "cases.jsonl"
    source.write_text(
        json.dumps({"case_id": "case-1", "query": "Original", "expected": {"a": 1}})
        + "\n",
        encoding="utf-8",
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'invalidate.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        reviewer = User(username="reviewer", password_hash="x", role="admin")
        db.add(reviewer)
        db.flush()
        import_jsonl_cases(
            db,
            path=source,
            dataset_name="agent",
            dataset_version="v1",
        )
        case = db.query(GoldenEvaluationCase).one()
        submit_case_review(
            db,
            case_id=case.id,
            reviewer=reviewer,
            decision="approve",
        )
        assert db.query(GoldenEvaluationReview).count() == 1

        source.write_text(
            json.dumps({"case_id": "case-1", "query": "Changed", "expected": {"a": 2}})
            + "\n",
            encoding="utf-8",
        )
        result = import_jsonl_cases(
            db,
            path=source,
            dataset_name="agent",
            dataset_version="v1",
        )
        assert result["updated"] == 1
        assert case.status == "draft"
        assert db.query(GoldenEvaluationReview).count() == 0


def test_phi_is_rejected_from_golden_import(tmp_path):
    source = tmp_path / "phi.jsonl"
    source.write_text(
        json.dumps(
            {
                "case_id": "phi-1",
                "query": "Contains a real patient record",
                "contains_phi": True,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'phi.db'}")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        try:
            import_jsonl_cases(
                db,
                path=source,
                dataset_name="agent",
                dataset_version="v1",
            )
        except ValueError as exc:
            assert "PHI" in str(exc)
        else:
            raise AssertionError("PHI-tagged cases must be rejected")
