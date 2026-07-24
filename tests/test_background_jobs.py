from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.db.models import BackgroundJob
from backend.infra.database import Base
from backend.jobs import queue


def _session(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'jobs.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(queue, "SessionLocal", Session)
    return Session


def test_durable_job_claim_progress_retry_and_completion(tmp_path, monkeypatch):
    Session = _session(tmp_path, monkeypatch)
    with Session() as db:
        job, created = queue.enqueue_job(
            db,
            job_type="test",
            payload={"value": 1},
            progress={"steps": [{"key": "work", "status": "pending", "percent": 0}]},
            idempotency_key="test-job-one",
            max_attempts=2,
        )
        db.commit()
        job_id = job.id
        assert created is True

    with Session() as db:
        assert queue.claim_jobs(db, limit=1) == [job_id]
        db.commit()

    reporter = queue.JobReporter(job_id)
    reporter.update_step(job_id, "work", 50, "running", "halfway")
    with Session() as db:
        job = db.query(BackgroundJob).filter_by(id=job_id).one()
        assert job.progress_json["steps"][0]["percent"] == 50
        assert job.status == "running"

    queue.fail_or_retry_job(job_id, RuntimeError("temporary"))
    with Session() as db:
        job = db.query(BackgroundJob).filter_by(id=job_id).one()
        assert job.status == "retry_wait"
        job.run_after = datetime.utcnow() - timedelta(seconds=1)
        db.commit()

    with Session() as db:
        assert queue.claim_jobs(db, limit=1) == [job_id]
        db.commit()
    queue.complete_job(job_id, {"ok": True})
    with Session() as db:
        job = db.query(BackgroundJob).filter_by(id=job_id).one()
        assert job.status == "completed"
        assert job.result_json == {"ok": True}
        assert job.completed_at is not None


def test_stale_job_recovery_is_bounded(tmp_path, monkeypatch):
    Session = _session(tmp_path, monkeypatch)
    old = datetime.utcnow() - timedelta(hours=1)
    with Session() as db:
        job, _ = queue.enqueue_job(
            db,
            job_type="test",
            payload={},
            idempotency_key="stale-job-one",
            max_attempts=1,
        )
        job.status = "running"
        job.attempt_count = 1
        job.locked_at = old
        db.commit()

    with Session() as db:
        assert queue.requeue_stale_jobs(db, stale_after_seconds=60) == 1
        db.commit()
        recovered = db.query(BackgroundJob).one()
        assert recovered.status == "failed"
        assert recovered.error_message == "stale_worker_timeout"
