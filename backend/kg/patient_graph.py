"""Optional Neo4j patient temporal health graph.

Mirrors confirmed PatientFact + PatientTimelineEvent rows into a scoped, idempotent
Neo4j graph (patient nodes, typed resource nodes, and a TimelineEvent chain ordered by
effective_at). Neo4j is an OPTIONAL enhancement: every write is best-effort, gated by
HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED (default off), and never blocks or fails the
PostgreSQL patient-fact path.

The neo4j driver is imported lazily inside PatientGraphClient.__init__ so that importing
this module never requires neo4j to be installed.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

from backend.env import load_env

load_env()

logger = logging.getLogger(__name__)


def patient_graph_enabled() -> bool:
    """Env gate. Default OFF so patient facts work with zero Neo4j configured."""
    return os.getenv("HEALTHTRACE_NEO4J_PATIENT_GRAPH_ENABLED", "off").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


# resource_type -> (node label, Patient -> resource edge type)
RESOURCE_EDGES = {
    "Condition": ("Condition", "HAS_CONDITION"),
    "Observation": ("Observation", "OBSERVED"),
    "MedicationStatement": ("Medication", "TAKES"),
    "AllergyIntolerance": ("Allergy", "HAS_ALLERGY"),
    "Procedure": ("Procedure", "UNDERWENT"),
    "Immunization": ("Immunization", "IMMUNIZED"),
    "DiagnosticReport": ("DiagnosticReport", "HAS_REPORT"),
}


def _iso(value: Any) -> str | None:
    """Best-effort ISO timestamp for a naive-UTC datetime (or None)."""
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        try:
            return value.isoformat()
        except (TypeError, ValueError):
            return None
    return None


def _source_label(fact) -> str:
    """Compact provenance string linking the graph node back to the source."""
    parts = [fact.source_type or ""]
    if fact.source_document_id:
        parts.append(fact.source_document_id)
    if fact.source_chunk_id:
        parts.append(fact.source_chunk_id)
    return ":".join(parts)


class PatientGraphClient:
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

    def write_patient_fact(self, scope, fact, timeline_event) -> bool | None:
        """Merge the patient, typed resource node, TimelineEvent node, and the ordered NEXT chain.

        Idempotent: keyed on fact.id / timeline_event.id via MERGE. Returns True on success,
        None when disabled or on error. Never raises.
        """
        if not patient_graph_enabled():
            return None
        mapping = RESOURCE_EDGES.get(fact.resource_type)
        if mapping is None:
            logger.warning(
                "Patient graph: unsupported resource_type %s for fact %s",
                fact.resource_type,
                getattr(fact, "id", "?"),
            )
            return None
        label, rel = mapping
        try:
            params = {
                "tenant_id": scope.tenant_id,
                "patient_id": scope.patient_id,
                "resource_type": fact.resource_type,
                "resource_key": fact.id,
                "fact_id": fact.id,
                "code": fact.code or "",
                "code_system": fact.code_system or "",
                "display": fact.display or "",
                "value": fact.value_json or {},
                "effective_at": _iso(fact.effective_start),
                "effective_end": _iso(fact.effective_end),
                "status": fact.clinical_status or "",
                "verification_status": fact.verification_status or "",
                "source": _source_label(fact),
                "source_type": fact.source_type or "",
                "source_document_id": fact.source_document_id,
                "source_page": fact.source_page,
                "source_chunk_id": fact.source_chunk_id,
                "event_key": timeline_event.id,
                "event_id": timeline_event.id,
                "title": timeline_event.title or "",
                "summary": timeline_event.summary or "",
                "time_precision": timeline_event.time_precision or "datetime",
            }
            write_cypher = f"""
            MERGE (p:Patient {{tenant_id: $tenant_id, patient_id: $patient_id}})
            MERGE (r:{label} {{tenant_id: $tenant_id, patient_id: $patient_id, resource_key: $resource_key}})
            SET r.resource_type = $resource_type,
                r.fact_id = $fact_id,
                r.code = $code,
                r.code_system = $code_system,
                r.display = $display,
                r.value = $value,
                r.effective_at = $effective_at,
                r.effective_end = $effective_end,
                r.status = $status,
                r.verification_status = $verification_status,
                r.source = $source,
                r.source_type = $source_type,
                r.source_document_id = $source_document_id,
                r.source_page = $source_page,
                r.source_chunk_id = $source_chunk_id
            MERGE (p)-[:{rel}]->(r)
            MERGE (e:TimelineEvent {{tenant_id: $tenant_id, patient_id: $patient_id, event_key: $event_key}})
            SET e.event_id = $event_id,
                e.resource_type = $resource_type,
                e.title = $title,
                e.summary = $summary,
                e.effective_at = $effective_at,
                e.time_precision = $time_precision,
                e.verification_status = $verification_status,
                e.source_fact_id = $fact_id,
                e.source_document_id = $source_document_id,
                e.source_chunk_id = $source_chunk_id,
                e.source_page = $source_page
            MERGE (p)-[:HAS_TIMELINE]->(e)
            """
            chain_cypher = """
            MATCH (p:Patient {tenant_id: $tenant_id, patient_id: $patient_id})-[:HAS_TIMELINE]->(e:TimelineEvent)
            WITH e
            ORDER BY e.effective_at ASC, e.event_key ASC
            WITH collect(e) AS events
            FOREACH (idx IN range(0, size(events)-2) |
              FOREACH (cur IN [events[idx]] |
                FOREACH (nxt IN [events[idx+1]] |
                  MERGE (cur)-[:NEXT]->(nxt)
                )
              )
            )
            """
            self._run(write_cypher, **params)
            self._run(chain_cypher, **params)
            return True
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "Patient graph write failed for fact %s: %s",
                getattr(fact, "id", "?"),
                exc,
            )
            return None

    def query_patient_timeline(self, scope, limit: int = 50) -> list[dict]:
        """Return the scoped longitudinal timeline from Neo4j (read-only)."""
        if not patient_graph_enabled():
            return []
        try:
            rows = self._run(
                """
                MATCH (p:Patient {tenant_id: $tenant_id, patient_id: $patient_id})-[:HAS_TIMELINE]->(e:TimelineEvent)
                RETURN e.event_id AS event_id,
                       e.resource_type AS event_type,
                       e.title AS title,
                       e.summary AS summary,
                       e.effective_at AS effective_at,
                       e.verification_status AS verification_status,
                       e.source_document_id AS source_document_id
                ORDER BY e.effective_at DESC, e.event_key DESC
                LIMIT $limit
                """,
                tenant_id=scope.tenant_id,
                patient_id=scope.patient_id,
                limit=max(1, min(int(limit), 500)),
            )
            return rows
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning("Patient graph timeline query failed: %s", exc)
            return []


@lru_cache(maxsize=1)
def get_patient_graph_client() -> PatientGraphClient:
    return PatientGraphClient()


def mirror_patient_fact_to_graph(scope, fact, timeline_event) -> bool | None:
    """Best-effort, guarded side-effect used by create_patient_fact.

    Never raises. No-op (returns None) when Neo4j is disabled. When enabled but Neo4j is
    unreachable/misconfigured it logs a warning and returns None so PostgreSQL fact creation
    always succeeds.
    """
    if not patient_graph_enabled():
        return None
    try:
        return get_patient_graph_client().write_patient_fact(scope, fact, timeline_event)
    except Exception as exc:
        logger.warning(
            "Patient graph mirror failed for fact %s: %s",
            getattr(fact, "id", "?"),
            exc,
        )
        return None
