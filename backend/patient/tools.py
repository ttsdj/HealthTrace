from __future__ import annotations

from collections.abc import Callable

from sqlalchemy.orm import Session

from backend.patient.facts import get_patient_timeline, list_patient_facts
from backend.patient.retrieval import retrieve_patient_records
from backend.patient.scope import PatientScope
from backend.patient.trends import calculate_observation_trend
from backend.tools.contracts import PatientToolResult
from backend.tasks.service import HealthTaskService


class PatientTools:
    """Typed patient tools with authorization scope injected by the backend."""

    def __init__(
        self,
        db: Session,
        scope: PatientScope,
        record_retriever: Callable | None = None,
    ):
        self._db = db
        self._scope = scope
        self._record_retriever = record_retriever or retrieve_patient_records

    def get_patient_allergies(self) -> PatientToolResult:
        return self._facts("get_patient_allergies", "AllergyIntolerance")

    def get_current_medications(self) -> PatientToolResult:
        return self._facts("get_current_medications", "MedicationStatement")

    def get_recent_conditions(self) -> PatientToolResult:
        return self._facts("get_recent_conditions", "Condition")

    def get_latest_observations(self) -> PatientToolResult:
        return self._facts("get_latest_observations", "Observation")

    def get_patient_timeline(self, limit: int = 50) -> PatientToolResult:
        events = [
            item
            for item in get_patient_timeline(self._db, self._scope, limit)
            if item.verification_status in {"user_confirmed", "clinician_verified"}
        ]
        data = [
            {
                "event_id": item.id,
                "event_type": item.event_type,
                "title": item.title,
                "summary": item.summary,
                "effective_at": item.effective_at.isoformat(),
                "verification_status": item.verification_status,
                "source_document_id": item.source_document_id,
                "source_page": item.source_page,
            }
            for item in events
        ]
        return PatientToolResult(
            tool_name="get_patient_timeline",
            status="ok" if data else "empty",
            data=data,
        )

    def search_patient_record_text(self, query: str, top_k: int = 5) -> PatientToolResult:
        try:
            result = self._record_retriever(query, self._scope, top_k)
        except Exception as exc:
            return PatientToolResult(
                tool_name="search_patient_record_text",
                status="error",
                error=str(exc)[:300],
            )
        return PatientToolResult(
            tool_name="search_patient_record_text",
            status="ok" if result["docs"] else "empty",
            data=result["docs"],
            metadata={
                "mode": result.get("mode", "unknown"),
                "attempts": result.get("attempts", []),
            },
        )

    def get_observation_trend(self, code: str, metric: str | None = None) -> PatientToolResult:
        try:
            trend = calculate_observation_trend(
                self._db,
                self._scope,
                code,
                metric=metric,
            )
        except Exception as exc:
            return PatientToolResult(
                tool_name="get_observation_trend",
                status="error",
                error=str(exc)[:300],
            )
        return PatientToolResult(
            tool_name="get_observation_trend",
            status="ok" if trend["count"] else "empty",
            data=[trend],
        )

    def create_health_reminder_draft(
        self,
        *,
        title: str,
        due_at,
        idempotency_key: str,
        description: str = "",
    ) -> PatientToolResult:
        """Create a confirmation-required draft; this never activates a task directly."""
        try:
            task, created = HealthTaskService(self._db, self._scope).create_draft(
                task_type="reminder",
                title=title,
                description=description,
                due_at=due_at,
                idempotency_key=idempotency_key,
            )
        except ValueError as exc:
            return PatientToolResult(
                tool_name="create_health_reminder_draft",
                status="error",
                error=str(exc),
            )
        return PatientToolResult(
            tool_name="create_health_reminder_draft",
            status="ok",
            data=[
                {
                    "task_id": task.id,
                    "status": task.status,
                    "confirmation_required": True,
                    "created": created,
                }
            ],
        )

    def _facts(self, tool_name: str, resource_type: str) -> PatientToolResult:
        facts = [
            item
            for item in list_patient_facts(self._db, self._scope, resource_type)
            if item.verification_status in {"user_confirmed", "clinician_verified"}
        ]
        data = [
            {
                "fact_id": item.id,
                "resource_type": item.resource_type,
                "display": item.display,
                "value": item.value_json,
                "effective_start": item.effective_start.isoformat() if item.effective_start else None,
                "verification_status": item.verification_status,
                "source_type": item.source_type,
                "source_document_id": item.source_document_id,
                "source_page": item.source_page,
            }
            for item in facts
        ]
        return PatientToolResult(tool_name=tool_name, status="ok" if data else "empty", data=data)
