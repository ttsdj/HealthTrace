"""Deterministic-first, structured intent routing for HealthTrace.

Safety routing is never delegated to a model.  A small model may refine normal
requests when explicitly enabled, but every model response is validated against
the same contract and falls back to deterministic routing on any failure.
"""

from __future__ import annotations

import os
import json
import re
import threading
from enum import StrEnum
from time import perf_counter

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from backend.medical_nlp.intent import analyze_medical_query
from backend.medical_nlp.safety import analyze_medical_safety, redact_sensitive_text


class Intent(StrEnum):
    URGENT_CARE = "urgent_care"
    SYMPTOM_ASSESSMENT = "symptom_assessment"
    MEDICATION_SAFETY = "medication_safety"
    REPORT_INTERPRETATION = "report_interpretation"
    PATIENT_RECORD_QUERY = "patient_record_query"
    TIMELINE_TREND = "timeline_trend"
    LIFESTYLE_GUIDANCE = "lifestyle_guidance"
    CARE_NAVIGATION = "care_navigation"
    HEALTH_TASK = "health_task"
    GENERAL_MEDICAL_QA = "general_medical_qa"


INTENT_ROUTER_PROMPT_VERSION = "intent-router-v1"


class IntentRoute(BaseModel):
    """The bounded contract shared by the Planner and Executor."""

    primary_intent: Intent
    secondary_intents: list[Intent] = Field(default_factory=list)
    complexity: str = Field(default="simple", pattern="^(simple|complex)$")
    needs_patient_context: bool = False
    requires_write_confirmation: bool = False
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    route_source: str = "deterministic"
    reason_code: str = ""
    latency_ms: float = 0.0
    prompt_version: str = INTENT_ROUTER_PROMPT_VERSION
    safety: dict = Field(default_factory=dict)


class _ModelRoute(BaseModel):
    primary_intent: Intent
    secondary_intents: list[Intent] = Field(default_factory=list)
    complexity: str = Field(default="simple", pattern="^(simple|complex)$")
    needs_patient_context: bool = False
    requires_write_confirmation: bool = False
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason_code: str = "fastmodel"


_ROUTER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You route a personal-health request. Return only the structured schema. "
            "Do not diagnose. The allowed intents are: {intents}. "
            "Set requires_write_confirmation only for an explicit reminder/task request. "
            "Set needs_patient_context only when the user asks about their own records, "
            "reports, trend, history, medicines, allergies, or a personalised decision.",
        ),
        ("human", "Request: {query}"),
    ]
)

_JSON_ROUTER_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "You route a personal-health request. Return exactly one JSON object, no Markdown. "
            "Schema: {{\"primary_intent\": one allowed intent, \"secondary_intents\": [], "
            "\"complexity\": \"simple\" or \"complex\", \"needs_patient_context\": boolean, "
            "\"requires_write_confirmation\": boolean, \"confidence\": number 0 to 1, "
            "\"reason_code\": short string}}. Allowed intents: {intents}. "
            "Do not diagnose. requires_write_confirmation is true only for an explicit reminder/task request.",
        ),
        ("human", "Request: {query}"),
    ]
)

_FAST_MODEL = None
_FAST_MODEL_LOCK = threading.Lock()


def _contains(text: str, *terms: str) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _deterministic_route(query: str, safety: dict) -> IntentRoute:
    """High-precision routing that is also the availability fallback."""
    text = (query or "").strip()
    medical = analyze_medical_query(text)

    if safety["high_risk_medical"]:
        return IntentRoute(
            primary_intent=Intent.URGENT_CARE,
            complexity="simple",
            confidence=1.0,
            route_source="deterministic_safety",
            reason_code="high_risk_medical",
            safety=safety,
        )
    if _contains(text, "提醒我", "提醒", "随访", "测量计划", "创建任务", "帮我记"):
        return IntentRoute(
            primary_intent=Intent.HEALTH_TASK,
            needs_patient_context=True,
            requires_write_confirmation=True,
            confidence=0.99,
            reason_code="explicit_health_task",
            safety=safety,
        )
    if _contains(text, "附近医院", "哪里就医", "去哪里看", "去哪个科", "挂什么号", "医院", "急诊在哪"):
        return IntentRoute(
            primary_intent=Intent.CARE_NAVIGATION,
            confidence=0.98,
            reason_code="care_navigation_terms",
            safety=safety,
        )
    if _contains(text, "趋势", "变化", "波动", "升高", "降低", "历次", "时间线", "就诊记录"):
        return IntentRoute(
            primary_intent=Intent.TIMELINE_TREND,
            needs_patient_context=True,
            complexity="complex",
            confidence=0.98,
            reason_code="timeline_or_trend_terms",
            safety=safety,
        )
    if _contains(text, "我的报告", "体检报告", "检查报告", "化验报告", "影像检查报告", "尿检报告", "检查单", "检验单"):
        return IntentRoute(
            primary_intent=Intent.REPORT_INTERPRETATION,
            needs_patient_context=True,
            confidence=0.98,
            reason_code="report_terms",
            safety=safety,
        )
    if _contains(text, "我的病史", "既往病史", "我的过敏", "我的用药", "我目前正在使用", "我的病历", "我的档案"):
        return IntentRoute(
            primary_intent=Intent.PATIENT_RECORD_QUERY,
            needs_patient_context=True,
            confidence=0.98,
            reason_code="patient_record_terms",
            safety=safety,
        )
    if safety["dosage_guard_triggered"] or medical.intent == "drug_info" or _contains(text, "阿莫西林", "青霉素过敏", "药物过敏"):
        return IntentRoute(
            primary_intent=Intent.MEDICATION_SAFETY,
            needs_patient_context=True,
            confidence=0.97,
            reason_code="medication_or_dosage_terms",
            safety=safety,
        )
    if _contains(text, "饮食", "忌口", "吃什么", "运动", "睡眠", "减重", "生活方式"):
        return IntentRoute(
            primary_intent=Intent.LIFESTYLE_GUIDANCE,
            confidence=0.95,
            reason_code="lifestyle_terms",
            safety=safety,
        )
    if medical.symptoms:
        return IntentRoute(
            primary_intent=Intent.SYMPTOM_ASSESSMENT,
            complexity="complex" if len(medical.symptoms) > 1 else "simple",
            confidence=0.94,
            reason_code="symptom_entities",
            safety=safety,
        )
    return IntentRoute(
        primary_intent=Intent.GENERAL_MEDICAL_QA,
        confidence=0.90,
        reason_code="general_medical_fallback",
        safety=safety,
    )


def _fast_model_route(query: str) -> _ModelRoute:
    """Call an OpenAI-compatible FastModel only when explicitly configured."""
    from langchain_openai import ChatOpenAI

    model_name = os.getenv("INTENT_MODEL") or os.getenv("FAST_MODEL") or os.getenv("MODEL")
    api_key = os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY")
    base_url = os.getenv("BASE_URL")
    if not (model_name and api_key and base_url):
        raise RuntimeError("intent model configuration is incomplete")
    global _FAST_MODEL
    if _FAST_MODEL is None:
        with _FAST_MODEL_LOCK:
            if _FAST_MODEL is None:
                _FAST_MODEL = ChatOpenAI(
                    model=model_name,
                    api_key=api_key,
                    base_url=base_url,
                    temperature=0,
                    timeout=float(os.getenv("HEALTHTRACE_INTENT_ROUTER_TIMEOUT_SECONDS", "2")),
                    max_retries=0,
                )
    intents = ", ".join(item.value for item in Intent)
    mode = os.getenv("HEALTHTRACE_INTENT_ROUTER_STRUCTURED_MODE", "json_prompt").lower()
    if mode == "native":
        prompt = _ROUTER_PROMPT.format_messages(intents=intents, query=query)
        return _FAST_MODEL.with_structured_output(_ModelRoute).invoke(prompt)

    # Many OpenAI-compatible FastModel endpoints do not implement
    # response_format/json_schema. Prompted JSON plus Pydantic validation keeps
    # the contract equally strict while retaining broad provider compatibility.
    prompt = _JSON_ROUTER_PROMPT.format_messages(intents=intents, query=query)
    response = _FAST_MODEL.invoke(prompt)
    content = str(getattr(response, "content", response)).strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.IGNORECASE)
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        raise ValueError("FastModel did not return a JSON object")
    return _ModelRoute.model_validate(json.loads(match.group(0)))


def route_intent(query: str) -> IntentRoute:
    """Route one request, preserving a deterministic safety and fallback path."""
    started = perf_counter()
    redacted, _ = redact_sensitive_text(query)
    safety = analyze_medical_safety(redacted)
    deterministic = _deterministic_route(redacted, safety)
    mode = os.getenv("HEALTHTRACE_INTENT_ROUTER_MODE", "rules").strip().lower()

    # Safety, explicit write requests and patient-data semantics remain
    # deterministic even in FastModel mode.
    protected = {
        Intent.URGENT_CARE,
        Intent.HEALTH_TASK,
        Intent.MEDICATION_SAFETY,
        Intent.PATIENT_RECORD_QUERY,
        Intent.REPORT_INTERPRETATION,
        Intent.TIMELINE_TREND,
    }
    result = deterministic
    if mode in {"fastmodel", "auto"} and deterministic.primary_intent not in protected:
        try:
            model_route = _fast_model_route(redacted)
            result = IntentRoute(
                **model_route.model_dump(),
                route_source="fastmodel",
                safety=safety,
            )
            # Write capability can never be granted by an implicit model guess.
            result.requires_write_confirmation = False
        except Exception:
            result.route_source = "deterministic_fallback"
            result.reason_code = f"{deterministic.reason_code}:fastmodel_unavailable"

    result.latency_ms = round((perf_counter() - started) * 1000, 3)
    return result
