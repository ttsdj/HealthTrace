from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Engine, inspect, text

PHASE1_VERSION = "2026_07_22_phase1_document_domains"
PHASE2_VERSION = "2026_07_22_phase2_fact_candidates"
PHASE3_VERSION = "2026_07_22_phase3_long_term_tasks"
PHASE4_VERSION = "2026_07_22_phase4_notification_delivery"
PHASE5_VERSION = "2026_07_24_phase5_access_security"
PHASE6_VERSION = "2026_07_24_phase6_jobs_golden_review"


def _get_migration_status(engine: Engine, version: str) -> dict:
    if not inspect(engine).has_table("healthtrace_schema_migrations"):
        return {"version": version, "status": "not_applied", "details": {}}

    from backend.db.models import SchemaMigration
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == version).first()
        return {
            "version": version,
            "status": record.status if record else "not_applied",
            "details": record.details_json if record else {},
        }


def get_phase1_migration_status(engine: Engine) -> dict:
    """Read Phase 1 state without creating or mutating migration tables."""
    return _get_migration_status(engine, PHASE1_VERSION)


def get_phase2_migration_status(engine: Engine) -> dict:
    """Read candidate-fact migration state without mutating the database."""
    return _get_migration_status(engine, PHASE2_VERSION)


def get_phase3_migration_status(engine: Engine) -> dict:
    """Read long-term task migration state without mutating the database."""
    return _get_migration_status(engine, PHASE3_VERSION)


def get_phase4_migration_status(engine: Engine) -> dict:
    """Read external notification delivery migration state without mutation."""
    return _get_migration_status(engine, PHASE4_VERSION)


def get_phase5_migration_status(engine: Engine) -> dict:
    """Read tenant access, audit, and sensitive-record migration state."""
    return _get_migration_status(engine, PHASE5_VERSION)


def get_phase6_migration_status(engine: Engine) -> dict:
    """Read durable background job and golden review migration state."""
    return _get_migration_status(engine, PHASE6_VERSION)


def _add_column_if_missing(conn, table_name: str, column_name: str, ddl: str) -> bool:
    columns = {item["name"] for item in inspect(conn).get_columns(table_name)}
    if column_name in columns:
        return False
    conn.execute(text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {ddl}"))
    return True


def apply_phase1_migration(engine: Engine) -> dict:
    """Apply an additive migration. No old column, row, collection, or file is removed."""
    from backend.db.models import PatientProfile, SchemaMigration, Tenant, User
    from backend.infra.database import Base

    Base.metadata.create_all(bind=engine)
    changes: list[str] = []
    now = datetime.utcnow()

    with engine.begin() as conn:
        if _add_column_if_missing(conn, "users", "tenant_id", "VARCHAR(64)"):
            changes.append("users.tenant_id")

        parent_columns = {
            "document_id": "VARCHAR(64) NOT NULL DEFAULT ''",
            "document_domain": "VARCHAR(32) NOT NULL DEFAULT 'public_medical'",
            "tenant_id": "VARCHAR(64)",
            "patient_id": "VARCHAR(64)",
            "owner_user_id": "INTEGER",
        }
        for column_name, ddl in parent_columns.items():
            if _add_column_if_missing(conn, "parent_chunks", column_name, ddl):
                changes.append(f"parent_chunks.{column_name}")

        indexes = {
            "ix_users_tenant_id": "users (tenant_id)",
            "ix_parent_chunks_document_id": "parent_chunks (document_id)",
            "ix_parent_chunks_document_domain": "parent_chunks (document_domain)",
            "ix_parent_chunks_tenant_patient": "parent_chunks (tenant_id, patient_id)",
        }
        for index_name, target in indexes.items():
            conn.execute(text(f"CREATE INDEX IF NOT EXISTS {index_name} ON {target}"))

        conn.execute(
            text(
                "UPDATE parent_chunks SET document_domain = 'public_medical' "
                "WHERE document_domain IS NULL OR document_domain = ''"
            )
        )

    # ORM access is safe only after the compatibility columns exist.
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        for user in db.query(User).all():
            patient = db.query(PatientProfile).filter(PatientProfile.user_id == user.id).first()
            tenant_id = user.tenant_id or (patient.tenant_id if patient else f"tenant-{uuid4()}")
            if db.query(Tenant).filter(Tenant.id == tenant_id).first() is None:
                db.add(Tenant(id=tenant_id, name=f"Personal workspace for {user.username}"))
                db.flush()
            user.tenant_id = tenant_id
            if patient is None:
                db.add(
                    PatientProfile(
                        id=f"patient-{uuid4()}",
                        tenant_id=tenant_id,
                        user_id=user.id,
                        display_name=user.username,
                    )
                )

        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE1_VERSION).first()
        details = {
            "mode": "additive",
            "changes": changes,
            "indexes": list(indexes),
            "rollback": "feature_flag",
        }
        if record is None:
            db.add(
                SchemaMigration(
                    version=PHASE1_VERSION,
                    status="applied",
                    details_json=details,
                    applied_at=now,
                    updated_at=now,
                )
            )
        else:
            record.status = "applied"
            record.details_json = details
            record.updated_at = now
        db.commit()

    return {"version": PHASE1_VERSION, "status": "applied", "changes": changes}


def rollback_phase1_migration(engine: Engine) -> dict:
    """Disable Phase 1 without dropping data; physical cleanup is intentionally manual."""
    if get_phase1_migration_status(engine)["status"] == "not_applied":
        return {"version": PHASE1_VERSION, "status": "not_applied"}

    from backend.db.models import SchemaMigration
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE1_VERSION).first()
        if record is None:
            return {"version": PHASE1_VERSION, "status": "not_applied"}
        record.status = "rolled_back"
        record.updated_at = datetime.utcnow()
        record.details_json = {
            **(record.details_json or {}),
            "rollback": "HEALTHTRACE_PATIENT_DOMAIN_ENABLED=false",
        }
        db.commit()
    return {"version": PHASE1_VERSION, "status": "rolled_back", "data_preserved": True}


def apply_phase2_fact_candidate_migration(engine: Engine) -> dict:
    """Create candidate-fact staging storage without changing authoritative facts."""
    from backend.db.models import SchemaMigration
    from backend.infra.database import Base
    from sqlalchemy.orm import Session

    existed = inspect(engine).has_table("patient_fact_candidates")
    Base.metadata.create_all(bind=engine)
    if not inspect(engine).has_table("patient_fact_candidates"):
        raise RuntimeError("patient_fact_candidates table was not created")

    now = datetime.utcnow()
    changes = [] if existed else ["patient_fact_candidates"]
    details = {
        "mode": "additive",
        "changes": changes,
        "authoritative_write": "user_confirmation_required",
        "rollback": "feature_flag",
    }
    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE2_VERSION).first()
        if record is None:
            db.add(
                SchemaMigration(
                    version=PHASE2_VERSION,
                    status="applied",
                    details_json=details,
                    applied_at=now,
                    updated_at=now,
                )
            )
        else:
            record.status = "applied"
            record.details_json = details
            record.updated_at = now
        db.commit()
    return {"version": PHASE2_VERSION, "status": "applied", "changes": changes}


def rollback_phase2_fact_candidate_migration(engine: Engine) -> dict:
    """Disable candidate extraction without dropping candidates or confirmed facts."""
    status = get_phase2_migration_status(engine)
    if status["status"] == "not_applied":
        return {"version": PHASE2_VERSION, "status": "not_applied"}

    from backend.db.models import SchemaMigration
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE2_VERSION).first()
        if record is None:
            return {"version": PHASE2_VERSION, "status": "not_applied"}
        record.status = "rolled_back"
        record.updated_at = datetime.utcnow()
        record.details_json = {
            **(record.details_json or {}),
            "rollback": "HEALTHTRACE_FACT_CANDIDATES_ENABLED=false",
        }
        db.commit()
    return {"version": PHASE2_VERSION, "status": "rolled_back", "data_preserved": True}


def apply_phase3_long_term_task_migration(engine: Engine) -> dict:
    """Add reliable task execution, goal progress, and in-app notification storage."""
    from backend.db.models import SchemaMigration
    from backend.infra.database import Base
    from sqlalchemy.orm import Session

    Base.metadata.create_all(bind=engine)
    changes: list[str] = []
    column_sets = {
        "health_goals": {
            "progress_json": "JSON NOT NULL DEFAULT '{}'",
            "idempotency_key": "VARCHAR(120)",
            "archived_at": "TIMESTAMP",
        },
        "health_tasks": {
            "last_run_at": "TIMESTAMP",
            "consecutive_failures": "INTEGER NOT NULL DEFAULT 0",
        },
        "health_task_runs": {
            "attempt_count": "INTEGER NOT NULL DEFAULT 0",
            "max_attempts": "INTEGER NOT NULL DEFAULT 3",
            "next_retry_at": "TIMESTAMP",
            "started_at": "TIMESTAMP",
            "updated_at": "TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "worker_id": "VARCHAR(120) NOT NULL DEFAULT ''",
        },
    }
    with engine.begin() as conn:
        for table_name, columns in column_sets.items():
            for column_name, ddl in columns.items():
                if _add_column_if_missing(conn, table_name, column_name, ddl):
                    changes.append(f"{table_name}.{column_name}")
        conn.execute(
            text(
                "UPDATE health_goals SET idempotency_key = id "
                "WHERE idempotency_key IS NULL OR idempotency_key = ''"
            )
        )
        conn.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS uq_health_goal_idempotency_idx "
                "ON health_goals (tenant_id, patient_id, idempotency_key)"
            )
        )
        conn.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_health_task_runs_retry "
                "ON health_task_runs (status, next_retry_at)"
            )
        )

    now = datetime.utcnow()
    details = {
        "mode": "additive",
        "changes": changes,
        "created_tables": ["health_notifications"],
        "queue": "postgresql_durable_task_runs",
        "rollback": "feature_flag_and_scheduler_disable",
    }
    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE3_VERSION).first()
        if record is None:
            db.add(
                SchemaMigration(
                    version=PHASE3_VERSION,
                    status="applied",
                    details_json=details,
                    applied_at=now,
                    updated_at=now,
                )
            )
        else:
            record.status = "applied"
            record.details_json = details
            record.updated_at = now
        db.commit()
    return {"version": PHASE3_VERSION, "status": "applied", "changes": changes}


def rollback_phase3_long_term_task_migration(engine: Engine) -> dict:
    """Disable execution features without dropping tasks, runs, goals, or notifications."""
    status = get_phase3_migration_status(engine)
    if status["status"] == "not_applied":
        return {"version": PHASE3_VERSION, "status": "not_applied"}

    from backend.db.models import SchemaMigration
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE3_VERSION).first()
        if record is None:
            return {"version": PHASE3_VERSION, "status": "not_applied"}
        record.status = "rolled_back"
        record.updated_at = datetime.utcnow()
        record.details_json = {
            **(record.details_json or {}),
            "rollback": "HEALTHTRACE_TASK_SCHEDULER_ENABLED=false",
        }
        db.commit()
    return {"version": PHASE3_VERSION, "status": "rolled_back", "data_preserved": True}


def apply_phase4_notification_delivery_migration(engine: Engine) -> dict:
    """Create additive, independently retryable external notification delivery storage."""
    from backend.db.models import SchemaMigration
    from backend.infra.database import Base
    from sqlalchemy.orm import Session

    existed = inspect(engine).has_table("health_notification_deliveries")
    Base.metadata.create_all(bind=engine)
    if not inspect(engine).has_table("health_notification_deliveries"):
        raise RuntimeError("health_notification_deliveries table was not created")
    now = datetime.utcnow()
    changes = [] if existed else ["health_notification_deliveries"]
    details = {
        "mode": "additive",
        "changes": changes,
        "delivery_channels": ["webhook", "email"],
        "fallback": "in_app_notification_is_committed_first",
        "rollback": "disable_external_dispatcher",
    }
    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE4_VERSION).first()
        if record is None:
            db.add(SchemaMigration(version=PHASE4_VERSION, status="applied", details_json=details, applied_at=now, updated_at=now))
        else:
            record.status = "applied"
            record.details_json = details
            record.updated_at = now
        db.commit()
    return {"version": PHASE4_VERSION, "status": "applied", "changes": changes}


def rollback_phase4_notification_delivery_migration(engine: Engine) -> dict:
    """Disable external delivery without dropping notification or delivery history."""
    status = get_phase4_migration_status(engine)
    if status["status"] == "not_applied":
        return {"version": PHASE4_VERSION, "status": "not_applied"}
    from backend.db.models import SchemaMigration
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        record = db.query(SchemaMigration).filter(SchemaMigration.version == PHASE4_VERSION).first()
        if record is None:
            return {"version": PHASE4_VERSION, "status": "not_applied"}
        record.status = "rolled_back"
        record.updated_at = datetime.utcnow()
        record.details_json = {**(record.details_json or {}), "rollback": "HEALTHTRACE_EXTERNAL_NOTIFICATIONS_ENABLED=false"}
        db.commit()
    return {"version": PHASE4_VERSION, "status": "rolled_back", "data_preserved": True}


def apply_phase5_access_security_migration(engine: Engine) -> dict:
    """Add multi-member access, audit, and encrypted-record tables without rewriting data."""
    from backend.db.models import (
        PatientAccessGrant,
        PatientProfile,
        SchemaMigration,
        TenantMembership,
        User,
    )
    from backend.infra.database import Base
    from sqlalchemy.orm import Session

    table_names = {
        "tenant_memberships",
        "patient_access_grants",
        "patient_sensitive_records",
        "audit_events",
    }
    before = {name for name in table_names if inspect(engine).has_table(name)}
    Base.metadata.create_all(bind=engine)
    missing = {name for name in table_names if not inspect(engine).has_table(name)}
    if missing:
        raise RuntimeError(f"Phase 5 tables were not created: {sorted(missing)}")

    now = datetime.utcnow()
    with Session(engine) as db:
        for user in db.query(User).all():
            if not user.tenant_id:
                continue
            patient = (
                db.query(PatientProfile)
                .filter(
                    PatientProfile.user_id == user.id,
                    PatientProfile.tenant_id == user.tenant_id,
                )
                .first()
            )
            if patient is None:
                continue
            membership = (
                db.query(TenantMembership)
                .filter(
                    TenantMembership.tenant_id == user.tenant_id,
                    TenantMembership.user_id == user.id,
                )
                .first()
            )
            if membership is None:
                db.add(
                    TenantMembership(
                        id=f"membership-{uuid4()}",
                        tenant_id=user.tenant_id,
                        user_id=user.id,
                        role="owner",
                        status="active",
                    )
                )
            grant = (
                db.query(PatientAccessGrant)
                .filter(
                    PatientAccessGrant.patient_id == patient.id,
                    PatientAccessGrant.user_id == user.id,
                )
                .first()
            )
            if grant is None:
                db.add(
                    PatientAccessGrant(
                        id=f"grant-{uuid4()}",
                        tenant_id=user.tenant_id,
                        patient_id=patient.id,
                        user_id=user.id,
                        permission="manage",
                        status="active",
                        granted_by_user_id=user.id,
                    )
                )

        changes = sorted(table_names - before)
        details = {
            "mode": "additive",
            "changes": changes,
            "owner_grants_backfilled": True,
            "sensitive_payload": "AES-256-GCM application envelope",
            "audit_payload_policy": "metadata_only_no_request_or_response_body",
            "rollback": "disable shared access and sensitive-record endpoints",
        }
        record = (
            db.query(SchemaMigration)
            .filter(SchemaMigration.version == PHASE5_VERSION)
            .first()
        )
        if record is None:
            db.add(
                SchemaMigration(
                    version=PHASE5_VERSION,
                    status="applied",
                    details_json=details,
                    applied_at=now,
                    updated_at=now,
                )
            )
        else:
            record.status = "applied"
            record.details_json = details
            record.updated_at = now
        db.commit()
    return {"version": PHASE5_VERSION, "status": "applied", "changes": changes}


def rollback_phase5_access_security_migration(engine: Engine) -> dict:
    """Disable Phase 5 behavior while preserving memberships, grants, audit, and ciphertext."""
    status = get_phase5_migration_status(engine)
    if status["status"] == "not_applied":
        return {"version": PHASE5_VERSION, "status": "not_applied"}
    from backend.db.models import SchemaMigration
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        record = (
            db.query(SchemaMigration)
            .filter(SchemaMigration.version == PHASE5_VERSION)
            .first()
        )
        if record is None:
            return {"version": PHASE5_VERSION, "status": "not_applied"}
        record.status = "rolled_back"
        record.updated_at = datetime.utcnow()
        record.details_json = {
            **(record.details_json or {}),
            "rollback": "HEALTHTRACE_SHARED_PATIENT_ACCESS_ENABLED=false",
        }
        db.commit()
    return {
        "version": PHASE5_VERSION,
        "status": "rolled_back",
        "data_preserved": True,
    }


def apply_phase6_jobs_golden_migration(engine: Engine) -> dict:
    """Create durable jobs and review workflow tables without changing existing rows."""
    from backend.db.models import SchemaMigration
    from backend.infra.database import Base
    from sqlalchemy.orm import Session

    table_names = {
        "background_jobs",
        "golden_evaluation_cases",
        "golden_evaluation_reviews",
    }
    before = {name for name in table_names if inspect(engine).has_table(name)}
    Base.metadata.create_all(bind=engine)
    missing = {name for name in table_names if not inspect(engine).has_table(name)}
    if missing:
        raise RuntimeError(f"Phase 6 tables were not created: {sorted(missing)}")
    now = datetime.utcnow()
    changes = sorted(table_names - before)
    details = {
        "mode": "additive",
        "changes": changes,
        "queue": "postgresql_short_claim_skip_locked",
        "golden_gate": "independent_reviews_and_clinician_approval",
        "rollback": "disable worker and review endpoints",
    }
    with Session(engine) as db:
        record = (
            db.query(SchemaMigration)
            .filter(SchemaMigration.version == PHASE6_VERSION)
            .first()
        )
        if record is None:
            db.add(
                SchemaMigration(
                    version=PHASE6_VERSION,
                    status="applied",
                    details_json=details,
                    applied_at=now,
                    updated_at=now,
                )
            )
        else:
            record.status = "applied"
            record.details_json = details
            record.updated_at = now
        db.commit()
    return {"version": PHASE6_VERSION, "status": "applied", "changes": changes}


def rollback_phase6_jobs_golden_migration(engine: Engine) -> dict:
    """Disable Phase 6 execution while preserving queued jobs and all review evidence."""
    status = get_phase6_migration_status(engine)
    if status["status"] == "not_applied":
        return {"version": PHASE6_VERSION, "status": "not_applied"}
    from backend.db.models import SchemaMigration
    from sqlalchemy.orm import Session

    with Session(engine) as db:
        record = (
            db.query(SchemaMigration)
            .filter(SchemaMigration.version == PHASE6_VERSION)
            .first()
        )
        if record is None:
            return {"version": PHASE6_VERSION, "status": "not_applied"}
        record.status = "rolled_back"
        record.updated_at = datetime.utcnow()
        record.details_json = {
            **(record.details_json or {}),
            "rollback": "HEALTHTRACE_JOB_WORKER_ENABLED=false",
        }
        db.commit()
    return {
        "version": PHASE6_VERSION,
        "status": "rolled_back",
        "data_preserved": True,
    }
