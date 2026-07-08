"""Deterministic care-navigation trigger and department hints."""

from __future__ import annotations

from backend.medical_nlp.safety import HIGH_RISK_PATTERNS


NAVIGATION_TERMS = (
    "附近医院",
    "附近的医院",
    "去哪看",
    "去哪里看",
    "挂什么科",
    "看什么科",
    "哪个科",
    "哪里检查",
    "去哪检查",
    "医院推荐",
    "急诊",
)

UNCERTAINTY_TERMS = (
    "无法判断",
    "不能判断",
    "不确定",
    "查不出来",
    "需要检查",
    "进一步检查",
    "线下就医",
)

DEPARTMENT_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("胸痛", "心慌", "心悸", "冠心病", "高血压"), "心血管内科"),
    (("咳嗽", "咳痰", "发热", "呼吸", "胸闷", "气短", "哮喘"), "呼吸内科"),
    (("腹痛", "胃痛", "腹泻", "恶心", "呕吐", "便血"), "消化内科"),
    (("头痛", "头晕", "抽搐", "意识", "肢体无力"), "神经内科"),
    (("皮疹", "瘙痒", "皮肤", "红疹"), "皮肤科"),
    (("骨折", "扭伤", "关节", "腰痛", "颈椎"), "骨科"),
    (("儿童", "孩子", "婴儿", "宝宝"), "儿科"),
    (("孕妇", "怀孕", "月经", "妇科"), "妇产科"),
    (("眼睛", "视力", "眼痛"), "眼科"),
    (("耳鸣", "鼻塞", "咽痛", "喉咙"), "耳鼻咽喉科"),
)


def recommend_department(medical_need: str) -> str:
    text = medical_need or ""
    if any(term in text for term in HIGH_RISK_PATTERNS):
        return "急诊科"
    for terms, department in DEPARTMENT_RULES:
        if any(term in text for term in terms):
            return department
    return "全科医学科或普通内科"


def assess_care_navigation_need(text: str) -> dict:
    query = text or ""
    high_risk_terms = [term for term in HIGH_RISK_PATTERNS if term in query]
    explicit_terms = [term for term in NAVIGATION_TERMS if term in query]
    uncertainty_terms = [term for term in UNCERTAINTY_TERMS if term in query]
    required = bool(explicit_terms or high_risk_terms)
    recommended = bool(required or uncertainty_terms)
    return {
        "care_navigation_required": required,
        "care_navigation_recommended": recommended,
        "care_navigation_reason": (
            "high_risk"
            if high_risk_terms
            else "explicit_request"
            if explicit_terms
            else "insufficient_information"
            if uncertainty_terms
            else ""
        ),
        "care_navigation_terms": high_risk_terms + explicit_terms + uncertainty_terms,
        "recommended_department": recommend_department(query),
        "urgency": "emergency" if high_risk_terms else "routine",
    }
