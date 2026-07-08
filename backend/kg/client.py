import os
from functools import lru_cache
from typing import Any

from backend.env import load_env
from backend.medical_nlp import analyze_medical_query

load_env()


DISEASE_ATTRIBUTES = [
    "疾病简介",
    "疾病病因",
    "预防措施",
    "治疗周期",
    "治愈概率",
    "疾病易感人群",
]

DISEASE_RELATIONS = [
    ("疾病使用药品", "药品", "所需药品"),
    ("疾病宜吃食物", "食物", "宜吃食物"),
    ("疾病忌吃食物", "食物", "忌吃食物"),
    ("疾病所需检查", "检查项目", "所需检查"),
    ("疾病所属科目", "科目", "所属科目"),
    ("疾病的症状", "疾病症状", "相关症状"),
    ("治疗的方法", "治疗方法", "治疗方法"),
    ("疾病并发疾病", "疾病", "并发疾病"),
]


def _clean_text(value: Any) -> str:
    return str(value).replace("\xa0", " ").strip()


class MedicalKGClient:
    def __init__(self) -> None:
        from neo4j import GraphDatabase

        self.database = os.getenv("NEO4J_DATABASE", "neo4j")
        self.driver = GraphDatabase.driver(
            os.getenv("NEO4J_URL", "bolt://localhost:7687"),
            auth=(os.getenv("NEO4J_USER", "neo4j"), os.getenv("NEO4J_PASSWORD", "")),
        )

    def _run(self, cypher: str, **params: Any) -> list[dict]:
        with self.driver.session(database=self.database) as session:
            result = session.run(cypher, **params)
            return [dict(record) for record in result]

    def find_diseases(self, query: str, limit: int = 3) -> list[str]:
        rows = self._run(
            """
            MATCH (d:`疾病`)
            WHERE $term CONTAINS d.`名称` OR d.`名称` CONTAINS $term
            RETURN d.`名称` AS name
            ORDER BY size(d.`名称`) DESC
            LIMIT $limit
            """,
            term=query,
            limit=limit,
        )
        return [row["name"] for row in rows if row.get("name")]

    def find_symptoms(self, query: str, limit: int = 3) -> list[str]:
        rows = self._run(
            """
            MATCH (s:`疾病症状`)
            WHERE $term CONTAINS s.`名称` OR s.`名称` CONTAINS $term
            RETURN s.`名称` AS name
            ORDER BY size(s.`名称`) DESC
            LIMIT $limit
            """,
            term=query,
            limit=limit,
        )
        return [row["name"] for row in rows if row.get("name")]

    def find_drugs(self, query: str, limit: int = 3) -> list[str]:
        rows = self._run(
            """
            MATCH (d:`药品`)
            WHERE $term CONTAINS d.`名称` OR d.`名称` CONTAINS $term
            RETURN d.`名称` AS name
            ORDER BY size(d.`名称`) DESC
            LIMIT $limit
            """,
            term=query,
            limit=limit,
        )
        return [row["name"] for row in rows if row.get("name")]

    def disease_profile(self, disease: str) -> dict[str, Any]:
        rows = self._run(
            """
            MATCH (d:`疾病` {`名称`: $disease})
            RETURN d AS node
            LIMIT 1
            """,
            disease=disease,
        )
        if not rows:
            return {}
        node = dict(rows[0]["node"])
        attributes = {
            key: _clean_text(node.get(key))
            for key in DISEASE_ATTRIBUTES
            if node.get(key)
        }
        relations: dict[str, list[str]] = {}
        for relation, label, title in DISEASE_RELATIONS:
            rel_rows = self._run(
                f"""
                MATCH (d:`疾病` {{`名称`: $disease}})-[:`{relation}`]->(n:`{label}`)
                RETURN n.`名称` AS name
                LIMIT 20
                """,
                disease=disease,
            )
            values = [_clean_text(row["name"]) for row in rel_rows if row.get("name")]
            if values:
                relations[title] = values
        return {"name": disease, "attributes": attributes, "relations": relations}

    def diseases_by_symptom(self, symptom: str) -> list[str]:
        rows = self._run(
            """
            MATCH (d:`疾病`)-[:`疾病的症状`]->(s:`疾病症状` {`名称`: $symptom})
            RETURN d.`名称` AS name
            LIMIT 20
            """,
            symptom=symptom,
        )
        return [row["name"] for row in rows if row.get("name")]

    def drug_producers(self, drug: str) -> list[str]:
        rows = self._run(
            """
            MATCH (m:`药品商`)-[:`生产`]->(d:`药品` {`名称`: $drug})
            RETURN m.`名称` AS name
            LIMIT 20
            """,
            drug=drug,
        )
        return [row["name"] for row in rows if row.get("name")]

    def search(self, query: str) -> dict[str, Any]:
        analysis = analyze_medical_query(query)
        diseases = analysis.diseases or self.find_diseases(query)
        symptoms = analysis.symptoms or self.find_symptoms(query)
        drugs = analysis.drugs or self.find_drugs(query)

        # Rule extraction is intentionally conservative. Keep Neo4j fuzzy
        # matching as a fallback so exact graph entities still win.
        if len(diseases) < 3:
            diseases = list(dict.fromkeys(diseases + self.find_diseases(query)))
        if len(symptoms) < 3:
            symptoms = list(dict.fromkeys(symptoms + self.find_symptoms(query)))
        if len(drugs) < 3:
            drugs = list(dict.fromkeys(drugs + self.find_drugs(query)))

        disease_profiles = [self.disease_profile(item) for item in diseases]
        symptom_matches = {
            symptom: self.diseases_by_symptom(symptom)
            for symptom in symptoms
        }
        drug_matches = {
            drug: self.drug_producers(drug)
            for drug in drugs
        }
        return {
            "analysis": analysis.as_dict(),
            "diseases": [item for item in disease_profiles if item],
            "symptoms": {k: v for k, v in symptom_matches.items() if v},
            "drugs": {k: v for k, v in drug_matches.items() if v},
        }


@lru_cache(maxsize=1)
def get_kg_client() -> MedicalKGClient:
    return MedicalKGClient()


def search_medical_kg_text(query: str) -> tuple[str, dict[str, Any]]:
    try:
        result = get_kg_client().search(query)
    except Exception as exc:
        return f"Neo4j medical knowledge graph is unavailable: {exc}", {
            "kg_error": str(exc),
            "kg_available": False,
        }

    sections: list[str] = []
    for item in result.get("diseases", []):
        lines = [f"疾病：{item['name']}"]
        for key, value in item.get("attributes", {}).items():
            lines.append(f"- {key}: {value}")
        for key, values in item.get("relations", {}).items():
            lines.append(f"- {key}: {'、'.join(values)}")
        sections.append("\n".join(lines))

    for symptom, diseases in result.get("symptoms", {}).items():
        sections.append(f"症状反查：{symptom} 可能相关疾病：{'、'.join(diseases)}")

    for drug, producers in result.get("drugs", {}).items():
        sections.append(f"药品生产商：{drug} 由 {'、'.join(producers)} 生产")

    meta = {
        "kg_available": True,
        "kg_hit_count": len(sections),
        "kg_disease_count": len(result.get("diseases", [])),
        "kg_symptom_count": len(result.get("symptoms", {})),
        "kg_drug_count": len(result.get("drugs", {})),
        "medical_intent": result.get("analysis", {}).get("intent", "general_medical_qa"),
        "medical_ner": result.get("analysis", {}),
        "kg_sources": ["Neo4j:RAGQnASystem"],
    }
    if not sections:
        return "No structured medical KG facts matched this query.", meta
    meta["kg_evidence"] = sections[:8]
    return "\n\n".join(sections), meta
