"""Rule-based medical safety and privacy guards."""

from __future__ import annotations

import re


HIGH_RISK_PATTERNS = (
    "胸痛",
    "呼吸困难",
    "喘不上气",
    "喘不过气",
    "气短",
    "昏迷",
    "意识不清",
    "抽搐",
    "大出血",
    "咯血",
    "咳血",
    "呕血",
    "黑便",
    "持续高热",
    "高热不退",
    "严重过敏",
    "过敏性休克",
    "自杀",
    "轻生",
    "想死",
    "自残",
    "过量服药",
    "吃多了药",
    "药吃多了",
    "中毒",
    "休克",
)

DOSAGE_PATTERNS = (
    "吃多少",
    "用量",
    "剂量",
    "几片",
    "几粒",
    "mg",
    "毫克",
    "加量",
    "减量",
    "一天几次",
    "一次几片",
    "一次几粒",
    "能不能一起吃",
    "可以一起吃",
    "联合用药",
)

SENSITIVE_PATTERNS = [
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号]"),
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "[身份证号]"),
    (re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}"), "[邮箱]"),
]


def redact_sensitive_text(text: str) -> tuple[str, dict]:
    redacted = text or ""
    replacements = 0
    for pattern, label in SENSITIVE_PATTERNS:
        redacted, count = pattern.subn(label, redacted)
        replacements += count
    return redacted, {
        "privacy_redaction_applied": replacements > 0,
        "privacy_redaction_count": replacements,
    }


def analyze_medical_safety(text: str) -> dict:
    query = text or ""
    high_risk_terms = [term for term in HIGH_RISK_PATTERNS if term in query]
    dosage_terms = [term for term in DOSAGE_PATTERNS if term.lower() in query.lower()]
    return {
        "safety_guard_enabled": True,
        "high_risk_medical": bool(high_risk_terms),
        "high_risk_terms": high_risk_terms,
        "dosage_guard_triggered": bool(dosage_terms),
        "dosage_terms": dosage_terms,
        "sensitive_confirmation_required": bool(high_risk_terms or dosage_terms),
        "safety_notice": build_safety_notice(high_risk_terms, dosage_terms),
    }


def build_safety_notice(high_risk_terms: list[str], dosage_terms: list[str]) -> str:
    notices = []
    if high_risk_terms:
        notices.append("检测到可能急危重症相关描述，请优先线下就医或拨打急救电话。")
    if dosage_terms:
        notices.append("检测到药品剂量相关问题，系统只能提供说明书级别信息，不能替代医生处方。")
    return " ".join(notices)
