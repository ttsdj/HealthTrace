from __future__ import annotations

import os

from backend.db.models import User
from backend.infra.database import SessionLocal
from backend.patient.planner import plan_patient_tool_calls
from backend.patient.retrieval import retrieve_patient_records
from backend.patient.scope import ensure_user_scope
from backend.patient.tools import PatientTools

_PERSONAL_MARKERS = ("我", "我的", "本人", "结合病历", "结合报告", "结合我的")


def should_query_patient_context(query: str) -> bool:
    return any(marker in (query or "") for marker in _PERSONAL_MARKERS)


def required_resource_types(query: str) -> list[str]:
    text = query or ""
    required: list[str] = []
    if any(
        marker in text
        for marker in ("药", "服用", "剂量", "停药", "能不能吃", "可以吃", "正在吃")
    ):
        required.extend(["AllergyIntolerance", "MedicationStatement", "Condition"])
    if any(marker in text for marker in ("检查", "报告", "指标", "血压", "血糖", "化验")):
        required.extend(["Observation", "DiagnosticReport"])
    if any(marker in text for marker in ("过敏", "禁忌")):
        required.append("AllergyIntolerance")
    if any(marker in text for marker in ("病史", "诊断", "疾病")):
        required.append("Condition")
    return list(dict.fromkeys(required))


def build_verified_patient_context(username: str, query: str) -> tuple[str, dict]:
    """Build minimum verified patient context through constrained typed tools."""
    if not should_query_patient_context(query):
        return "", {"patient_context_accessed": False, "reason": "general_query"}

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            return "", {"patient_context_accessed": False, "reason": "user_not_found"}
        scope = ensure_user_scope(db, user)
        db.commit()
        resource_types = required_resource_types(query)
        planned_calls = plan_patient_tool_calls(query)
        tools = PatientTools(db, scope, record_retriever=retrieve_patient_records)
        results = []
        for call in planned_calls:
            method = getattr(tools, call.tool_name, None)
            if method is None:
                continue
            try:
                result = method(**call.arguments)
            except Exception as exc:
                from backend.tools.contracts import PatientToolResult

                result = PatientToolResult(
                    tool_name=call.tool_name,
                    status="error",
                    error=str(exc)[:300],
                )
            results.append(result)

        max_chars = max(500, int(os.getenv("PATIENT_CONTEXT_MAX_CHARS", "3000")))
        sections: list[str] = []
        fact_rows = [
            item
            for result in results
            if result.tool_name
            in {
                "get_patient_allergies",
                "get_current_medications",
                "get_recent_conditions",
                "get_latest_observations",
            }
            for item in result.data
        ]
        if fact_rows:
            fact_lines = ["【患者结构化事实：仅包含用户或临床人员已确认的数据】"]
            for item in fact_rows:
                fact_lines.append(
                    f"- {item['resource_type']}: {item['display']}；值={item.get('value') or {}}；"
                    f"时间={item.get('effective_start') or '时间未知'}；"
                    f"状态={item['verification_status']}；来源={item['source_type']}"
                )
            sections.append("\n".join(fact_lines))

        timeline_rows = [
            item
            for result in results
            if result.tool_name == "get_patient_timeline"
            for item in result.data
        ]
        if timeline_rows:
            lines = ["【患者健康时间线：仅包含已确认事件】"]
            for item in timeline_rows[:10]:
                lines.append(f"- {item['effective_at']}: {item['title']}；{item['summary']}")
            sections.append("\n".join(lines))

        trend_rows = [
            item
            for result in results
            if result.tool_name == "get_observation_trend"
            for item in result.data
        ]
        if trend_rows:
            trend = trend_rows[0]
            sections.append(
                "【患者指标趋势：仅用于辅助判断，不构成诊断】\n"
                f"- {trend['code']}({trend['metric']}): {trend['direction']}；"
                f"首值={trend.get('first')}；末值={trend.get('latest')}；"
                f"样本数={trend['count']}；单位={trend.get('unit', '')}"
            )

        record_result = next(
            (result for result in results if result.tool_name == "search_patient_record_text"),
            None,
        )
        record_docs = record_result.data if record_result else []
        if record_docs:
            record_lines = [
                "【患者私有文档检索证据：仅作来源证据，未结构化核验，不得直接当作确诊事实】"
            ]
            for index, item in enumerate(record_docs, start=1):
                record_lines.append(
                    f"[{index}] {item.get('filename', '患者文档')} 第"
                    f"{int(item.get('page_number', 0)) + 1}页：{item.get('text', '')}"
                )
            sections.append("\n".join(record_lines))

        context = "\n\n".join(sections)[:max_chars]
        return context, {
            "patient_context_accessed": True,
            "required_patient_fields": resource_types,
            "patient_fact_count": len(fact_rows),
            "patient_record_accessed": record_result is not None,
            "patient_record_hits": len(record_docs),
            "patient_record_mode": (
                record_result.metadata.get("mode", "retrieved")
                if record_result and record_result.status in {"ok", "empty"}
                else "unavailable"
                if record_result and record_result.status == "error"
                else "not_requested"
            ),
            "patient_record_attempts": (
                record_result.metadata.get("attempts", []) if record_result else []
            ),
            "patient_tool_plan": [
                {
                    "tool_name": call.tool_name,
                    "arguments": call.arguments,
                    "reason": call.reason,
                }
                for call in planned_calls
            ],
            "patient_tool_results": [
                {
                    "tool_name": result.tool_name,
                    "status": result.status,
                    "count": len(result.data),
                    "error": result.error,
                }
                for result in results
            ],
            "patient_context_chars": len(context),
        }
    except Exception as exc:
        db.rollback()
        return "", {
            "patient_context_accessed": False,
            "reason": "patient_context_unavailable",
            "error": str(exc)[:200],
        }
    finally:
        db.close()
