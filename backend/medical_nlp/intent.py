"""Rule-based medical intent recognition and entity extraction.

This is intentionally lightweight: it gives deterministic routing hints before
we introduce a heavier NER model. Neo4j is still the source of truth for exact
entity names; this module only normalizes common user wording.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import re


SYMPTOM_ALIASES = {
    "咳嗽": ["咳嗽", "咳", "干咳", "咳痰", "有点咳", "一直咳"],
    "发热": ["发热", "发烧", "低烧", "高烧", "体温高"],
    "头痛": ["头痛", "头疼", "脑袋疼"],
    "胸闷": ["胸闷", "胸口闷", "气短"],
    "呼吸困难": ["呼吸困难", "喘不上气", "喘不过气", "气喘"],
    "腹痛": ["腹痛", "肚子痛", "肚子疼", "胃疼"],
    "腹泻": ["腹泻", "拉肚子", "拉稀"],
    "恶心": ["恶心", "想吐", "反胃"],
    "呕吐": ["呕吐", "吐了", "一直吐"],
    "乏力": ["乏力", "没力气", "无力", "疲劳"],
    "皮疹": ["皮疹", "起疹子", "红疹", "疹子"],
    "鼻塞": ["鼻塞", "鼻子堵"],
    "流涕": ["流涕", "流鼻涕"],
    "咽痛": ["咽痛", "嗓子疼", "喉咙痛", "喉咙疼"],
}

DISEASE_HINTS = [
    "百日咳",
    "肺炎",
    "感冒",
    "流感",
    "支气管炎",
    "哮喘",
    "肺结核",
    "糖尿病",
    "高血压",
    "冠心病",
    "胃炎",
    "肠炎",
]

DRUG_SUFFIXES = ("片", "胶囊", "颗粒", "口服液", "糖浆", "注射液", "丸", "散")

INTENT_KEYWORDS = {
    "symptom_to_disease": ["什么病", "可能是", "怎么回事", "原因", "有点", "症状"],
    "disease_profile": ["是什么", "介绍", "简介", "病因", "预防", "治疗", "检查", "并发"],
    "drug_info": ["药", "用药", "吃什么药", "生产商", "厂家"],
    "diet_advice": ["吃什么", "忌口", "宜吃", "不能吃", "饮食"],
}


@dataclass
class MedicalIntentResult:
    intent: str = "general_medical_qa"
    diseases: list[str] = field(default_factory=list)
    symptoms: list[str] = field(default_factory=list)
    drugs: list[str] = field(default_factory=list)
    matched_terms: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "intent": self.intent,
            "diseases": self.diseases,
            "symptoms": self.symptoms,
            "drugs": self.drugs,
            "matched_terms": self.matched_terms,
        }


def _dedupe(items: list[str]) -> list[str]:
    seen = set()
    out = []
    for item in items:
        item = item.strip()
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _contains_any(text: str, keywords: list[str]) -> bool:
    return any(keyword in text for keyword in keywords)


def _extract_symptoms(text: str) -> tuple[list[str], list[str]]:
    symptoms: list[str] = []
    matched: list[str] = []
    for canonical, aliases in SYMPTOM_ALIASES.items():
        for alias in aliases:
            if alias in text:
                symptoms.append(canonical)
                matched.append(alias)
                break
    return _dedupe(symptoms), _dedupe(matched)


def _extract_diseases(text: str) -> list[str]:
    return _dedupe([item for item in DISEASE_HINTS if item in text])


def _extract_drugs(text: str) -> list[str]:
    candidates = re.findall(r"[\u4e00-\u9fffA-Za-z0-9]{2,12}", text)
    drugs = [
        item
        for item in candidates
        if item.endswith(DRUG_SUFFIXES) or any(keyword in item for keyword in ("红霉素", "阿莫西林", "布洛芬"))
    ]
    return _dedupe(drugs)


def _classify_intent(text: str, diseases: list[str], symptoms: list[str], drugs: list[str]) -> str:
    if drugs or _contains_any(text, INTENT_KEYWORDS["drug_info"]):
        return "drug_info"
    if symptoms and _contains_any(text, INTENT_KEYWORDS["symptom_to_disease"]):
        return "symptom_to_disease"
    if diseases and _contains_any(text, INTENT_KEYWORDS["diet_advice"]):
        return "diet_advice"
    if diseases or _contains_any(text, INTENT_KEYWORDS["disease_profile"]):
        return "disease_profile"
    if symptoms:
        return "symptom_to_disease"
    return "general_medical_qa"


def analyze_medical_query(query: str) -> MedicalIntentResult:
    text = (query or "").strip()
    symptoms, matched_symptom_terms = _extract_symptoms(text)
    diseases = _extract_diseases(text)
    drugs = _extract_drugs(text)
    intent = _classify_intent(text, diseases, symptoms, drugs)
    return MedicalIntentResult(
        intent=intent,
        diseases=diseases,
        symptoms=symptoms,
        drugs=drugs,
        matched_terms=matched_symptom_terms + diseases + drugs,
    )
