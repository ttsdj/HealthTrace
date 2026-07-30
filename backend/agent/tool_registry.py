"""Single policy registry for the eight core patient, knowledge and task tools."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass

from backend.agent.intent_router import Intent, IntentRoute


@dataclass(frozen=True)
class ToolSpec:
    name: str
    domain: str
    read_only: bool
    requires_confirmation: bool = False
    max_attempts: int = 2
    bulkhead_limit: int = 4


# This list is deliberately small and stable.  Care navigation and weather are
# supporting operational integrations, not patient/knowledge/task core tools.
CORE_TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("search_knowledge_base", "knowledge", True, bulkhead_limit=4),
    ToolSpec("search_medical_kg", "knowledge", True, bulkhead_limit=4),
    ToolSpec("get_patient_allergies", "patient", True, bulkhead_limit=6),
    ToolSpec("get_current_medications", "patient", True, bulkhead_limit=6),
    ToolSpec("get_latest_observations", "patient", True, bulkhead_limit=6),
    ToolSpec("search_patient_record_text", "patient", True, bulkhead_limit=3),
    ToolSpec("get_patient_timeline", "patient", True, bulkhead_limit=4),
    ToolSpec("create_health_reminder_draft", "task", False, True, 1, 2),
)
TOOL_BY_NAME = {item.name: item for item in CORE_TOOLS}
SUPPORT_TOOL_NAMES = {"search_nearby_medical_care", "get_current_weather"}


def core_tool_manifest() -> list[dict]:
    return [asdict(item) for item in CORE_TOOLS]


def build_execution_plan(route: IntentRoute) -> dict:
    """Compile a bounded capability plan; the Executor enforces this allowlist."""
    allowed: list[str] = ["search_knowledge_base"]
    parallel: list[list[str]] = []
    if route.primary_intent in {
        Intent.SYMPTOM_ASSESSMENT,
        Intent.MEDICATION_SAFETY,
        Intent.REPORT_INTERPRETATION,
        Intent.GENERAL_MEDICAL_QA,
    }:
        allowed.append("search_medical_kg")
        parallel.append(["search_knowledge_base", "search_medical_kg"])
    if route.primary_intent == Intent.MEDICATION_SAFETY:
        allowed.extend(["get_patient_allergies", "get_current_medications"])
    elif route.primary_intent == Intent.REPORT_INTERPRETATION:
        allowed.extend(["get_latest_observations", "search_patient_record_text"])
    elif route.primary_intent == Intent.PATIENT_RECORD_QUERY:
        allowed.extend([
            "get_patient_allergies",
            "get_current_medications",
            "get_latest_observations",
            "search_patient_record_text",
        ])
    elif route.primary_intent == Intent.TIMELINE_TREND:
        allowed.extend(["get_latest_observations", "get_patient_timeline"])
    elif route.primary_intent == Intent.HEALTH_TASK:
        allowed = ["create_health_reminder_draft"]

    if route.primary_intent == Intent.CARE_NAVIGATION:
        allowed = ["search_knowledge_base", "search_nearby_medical_care"]
    if route.primary_intent == Intent.URGENT_CARE:
        allowed = []
    allowed = list(dict.fromkeys(allowed))
    return {
        "allowed_tools": allowed,
        "parallel_groups": parallel,
        "requires_confirmation": route.requires_write_confirmation,
        "write_action_requested": route.primary_intent == Intent.HEALTH_TASK,
        "tool_manifest_version": "core-tools-v1",
    }


_EXECUTION_PLAN: ContextVar[dict | None] = ContextVar(
    "healthtrace_execution_plan", default=None
)


@contextmanager
def execution_plan_context(plan: dict | None):
    token = _EXECUTION_PLAN.set(plan)
    try:
        yield
    finally:
        _EXECUTION_PLAN.reset(token)


def tool_is_allowed(tool_name: str) -> bool:
    plan = _EXECUTION_PLAN.get()
    if plan is None:
        return True
    return tool_name in set(plan.get("allowed_tools") or [])


def spec_for(tool_name: str) -> ToolSpec | None:
    return TOOL_BY_NAME.get(tool_name)
