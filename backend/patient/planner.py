from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable

from backend.env import load_env

ALLOWED_PATIENT_TOOLS = {
    "get_patient_allergies",
    "get_current_medications",
    "get_recent_conditions",
    "get_latest_observations",
    "get_patient_timeline",
    "search_patient_record_text",
    "get_observation_trend",
}


@dataclass(frozen=True)
class PatientToolCall:
    tool_name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    reason: str = ""


def _add(calls: list[PatientToolCall], name: str, reason: str, **arguments: Any) -> None:
    if name not in ALLOWED_PATIENT_TOOLS or any(item.tool_name == name for item in calls):
        return
    calls.append(PatientToolCall(name, arguments, reason))


def _trend_code(text: str) -> str:
    aliases = {
        "blood_pressure": ("血压", "收缩压", "舒张压", "blood pressure", "bp"),
        "blood_glucose": ("血糖", "空腹血糖", "餐后血糖", "glucose"),
        "weight": ("体重", "weight"),
        "heart_rate": ("心率", "脉搏", "heart rate", "pulse"),
        "temperature": ("体温", "temperature"),
    }
    lowered = text.lower()
    return next((code for code, words in aliases.items() if any(word in lowered for word in words)), "observation")


def plan_patient_tool_calls_rules(query: str) -> list[PatientToolCall]:
    text = query or ""
    lowered = text.lower()
    calls: list[PatientToolCall] = []
    if any(word in lowered for word in ("过敏", "禁忌", "allerg")):
        _add(calls, "get_patient_allergies", "allergy_or_contraindication_query")
    medication_query = any(word in lowered for word in ("药", "服用", "用药", "剂量", "medication", "drug"))
    if medication_query:
        _add(calls, "get_current_medications", "medication_query")
        _add(calls, "get_patient_allergies", "medication_safety_context")
        _add(calls, "get_recent_conditions", "medication_safety_context")
    if any(word in lowered for word in ("病史", "疾病", "诊断", "condition", "diagnos")):
        _add(calls, "get_recent_conditions", "condition_history_query")
    observation_query = any(
        word in lowered
        for word in ("检查", "报告", "指标", "血压", "血糖", "体重", "心率", "体温", "化验", "observation", "lab")
    )
    if observation_query:
        _add(calls, "get_latest_observations", "observation_query")
    if any(word in lowered for word in ("趋势", "变化", "升高", "降低", "波动", "trend", "change")):
        _add(calls, "get_observation_trend", "longitudinal_observation_query", code=_trend_code(text))
    if any(word in lowered for word in ("病历", "病史", "报告", "检查单", "文档", "record", "report")):
        _add(calls, "search_patient_record_text", "patient_document_query", query=text, top_k=3)
    if any(word in lowered for word in ("时间线", "什么时候", "历次", "就诊记录", "timeline", "history")):
        _add(calls, "get_patient_timeline", "timeline_query", limit=50)
    if not calls and any(word in lowered for word in ("我", "我的", "本人", "my ", "patient")):
        _add(calls, "get_patient_timeline", "generic_personal_context", limit=10)
    return calls[:5]


def _validate_calls(raw: Any) -> list[PatientToolCall]:
    if isinstance(raw, dict):
        raw = raw.get("calls", [])
    if not isinstance(raw, list):
        raise ValueError("Tool plan must be a list")
    calls: list[PatientToolCall] = []
    for item in raw[:5]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("tool_name", ""))
        if name not in ALLOWED_PATIENT_TOOLS:
            continue
        arguments = item.get("arguments") if isinstance(item.get("arguments"), dict) else {}
        if name == "search_patient_record_text":
            arguments = {"query": str(arguments.get("query", ""))[:4000], "top_k": min(max(int(arguments.get("top_k", 3)), 1), 5)}
        elif name == "get_observation_trend":
            arguments = {"code": str(arguments.get("code", "observation"))[:100]}
        elif name == "get_patient_timeline":
            arguments = {"limit": min(max(int(arguments.get("limit", 50)), 1), 100)}
        else:
            arguments = {}
        _add(calls, name, str(item.get("reason", "llm_selected"))[:200], **arguments)
    return calls


def _llm_plan(query: str) -> list[PatientToolCall]:
    load_env()
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=os.getenv("FAST_MODEL") or os.getenv("MODEL") or "",
        api_key=os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY") or "",
        base_url=os.getenv("BASE_URL") or None,
        temperature=0,
        timeout=float(os.getenv("PATIENT_TOOL_PLANNER_TIMEOUT", "8")),
        max_retries=0,
    )
    prompt = (
        "Select the minimum patient-data tools needed for the user query. "
        "Return JSON only: {\"calls\":[{\"tool_name\":...,\"arguments\":{},\"reason\":...}]}. "
        f"Allowed tools: {sorted(ALLOWED_PATIENT_TOOLS)}. Do not invent tools.\nQuery: {query}"
    )
    content = str(model.invoke(prompt).content)
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    return _validate_calls(json.loads(match.group(0) if match else content))


def plan_patient_tool_calls(
    query: str,
    llm_planner: Callable[[str], list[PatientToolCall]] | None = None,
) -> list[PatientToolCall]:
    rules = plan_patient_tool_calls_rules(query)
    if os.getenv("PATIENT_TOOL_PLANNER_MODE", "rules").lower() != "llm":
        return rules
    try:
        calls = (llm_planner or _llm_plan)(query)
        return _validate_calls([{"tool_name": item.tool_name, "arguments": item.arguments, "reason": item.reason} for item in calls]) or rules
    except Exception:
        return rules
