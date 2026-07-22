from sqlalchemy import create_engine, inspect, text

from backend.infra.migrations import (
    PHASE3_VERSION,
    apply_phase3_long_term_task_migration,
    get_phase3_migration_status,
)


def test_phase3_migration_is_additive_and_idempotent(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'phase3.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE health_goals (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64), patient_id VARCHAR(64), title VARCHAR(300), description TEXT, status VARCHAR(32), target_json JSON, starts_at TIMESTAMP, due_at TIMESTAMP, created_at TIMESTAMP, updated_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE health_tasks (id VARCHAR(64) PRIMARY KEY, tenant_id VARCHAR(64), patient_id VARCHAR(64), task_type VARCHAR(40), title VARCHAR(300), description TEXT, status VARCHAR(32), due_at TIMESTAMP, next_run_at TIMESTAMP, timezone VARCHAR(64), interval_seconds INTEGER, payload_json JSON, idempotency_key VARCHAR(120), confirmation_required BOOLEAN, confirmed_at TIMESTAMP, cancelled_at TIMESTAMP, completed_at TIMESTAMP, created_by_user_id INTEGER, created_at TIMESTAMP, updated_at TIMESTAMP)"))
        conn.execute(text("CREATE TABLE health_task_runs (id VARCHAR(64) PRIMARY KEY, task_id VARCHAR(64), tenant_id VARCHAR(64), patient_id VARCHAR(64), run_key VARCHAR(120), status VARCHAR(32), scheduled_for TIMESTAMP, result_json JSON, error_message TEXT, created_at TIMESTAMP, completed_at TIMESTAMP)"))
        conn.execute(text("INSERT INTO health_goals (id, tenant_id, patient_id, title, status, target_json) VALUES ('goal-old', 'tenant-a', 'patient-a', '旧目标', 'active', '{}')"))

    first = apply_phase3_long_term_task_migration(engine)
    second = apply_phase3_long_term_task_migration(engine)
    inspector = inspect(engine)

    assert first["status"] == "applied"
    assert second["status"] == "applied"
    assert inspector.has_table("health_notifications")
    assert {item["name"] for item in inspector.get_columns("health_task_runs")} >= {
        "attempt_count",
        "max_attempts",
        "next_retry_at",
        "worker_id",
    }
    with engine.connect() as conn:
        key = conn.execute(text("SELECT idempotency_key FROM health_goals WHERE id = 'goal-old'")).scalar_one()
    assert key == "goal-old"
    assert get_phase3_migration_status(engine)["version"] == PHASE3_VERSION
    assert get_phase3_migration_status(engine)["status"] == "applied"
