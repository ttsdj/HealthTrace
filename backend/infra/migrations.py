from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Engine, inspect, text

PHASE1_VERSION = "2026_07_22_phase1_document_domains"
PHASE2_VERSION = "2026_07_22_phase2_fact_candidates"


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
