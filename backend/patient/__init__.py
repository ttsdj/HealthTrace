"""Patient-scoped identity, documents, facts, and task services."""

from backend.patient.scope import PatientScope, ensure_user_scope

__all__ = ["PatientScope", "ensure_user_scope"]
