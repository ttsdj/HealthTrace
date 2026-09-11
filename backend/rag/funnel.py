"""Scope→Topic→Document funnel retrieval for HealthTrace.

The funnel is a deterministic-first, LLM-optional pre-narrowing pass that runs
before the existing Hybrid→Dense→Sparse degradation ladder:

* stage_scope   — pick the collection(s) and the mandatory tenant/patient filter
                  (public vs patient-record vs both).
* stage_topic   — derive topic terms / a narrowing Milvus filter and a set of
                  candidate intents from analyze_medical_query (+ optional
                  deterministic rewrite expansion).
* stage_document — discover candidate document_id(s) from parent metadata, then
                  run the existing retrieve_documents / retrieve_patient_records
                  ladder *within* those candidates, and merge/rank the chunks.

run_funnel composes the three stages. Every stage degrades gracefully — no stage
may raise into the pipeline — and run_funnel records scope/topic/document
decisions plus any degradation in a funnel_trace dict.

No metric or latency numbers are produced here; this layer only selects and
narrows retrieval.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field as dc_field
from typing import Any, List, Optional, Tuple

from backend.indexing.milvus_client import COLLECTION_DEFAULT_BY_KIND, get_milvus_store
from backend.medical_nlp.intent import analyze_medical_query
from backend.patient.context import required_resource_types, should_query_patient_context
from backend.patient.retrieval import (
    patient_scope_filter,
    patient_scope_predicate,
    retrieve_patient_records,
)
from backend.rag.utils import dedupe_documents, retrieve_documents, retrieval_trace_fields

PUBLIC_DOMAIN = "public"
PATIENT_DOMAIN = "patient"
BOTH_DOMAIN = "both"

_MEDICAL_QA_KIND = "medical_qa"
_PATIENT_RECORD_KIND = "patient_record"

_PUBLIC_COLLECTION = COLLECTION_DEFAULT_BY_KIND[_MEDICAL_QA_KIND]
_PATIENT_COLLECTION = COLLECTION_DEFAULT_BY_KIND[_PATIENT_RECORD_KIND]

_MAX_DOCUMENT_CANDIDATES = max(
    1,
    int(os.getenv("FUNNEL_MAX_DOCUMENT_CANDIDATES", "100") or "100"),
)


@dataclass(frozen=True)
class RetrievalScope:
    """Result of the Scope stage: which collection(s) + scope filter to search."""

    domain: str
    collections: Tuple[str, ...]
    filter_expr: str
    patient_scope: Optional[Any]
    patient_marker: bool
    resource_types: Tuple[str, ...]
    source: str
    degraded: bool = False

    @classmethod
    def public_fallback(cls, reason: str = "scope_error") -> "RetrievalScope":
        return cls(
            domain=PUBLIC_DOMAIN,
            collections=(_PUBLIC_COLLECTION,),
            filter_expr="",
            patient_scope=None,
            patient_marker=False,
            resource_types=(),
            source=reason,
            degraded=True,
        )


@dataclass
class TopicQuery:
    """Result of the Topic stage: narrowing terms/intents + optional Milvus filter."""

    topic_terms: Tuple[str, ...]
    candidate_intents: Tuple[str, ...]
    filter_expr: str
    document_terms: Tuple[str, ...]
    source: str
    degraded: bool = False

    @classmethod
    def default(cls, reason: str = "topic_error") -> "TopicQuery":
        return cls((), (), "", (), reason, True)


@dataclass
class DocumentCandidates:
    """Result of the Document stage: merged/ranked chunks + internal trace."""

    chunks: List[dict]
    source: str
    merged_from: Tuple[str, ...]
    document_ids: List[str]
    document_filter: str
    trace: dict = dc_field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scope stage
# ---------------------------------------------------------------------------


def stage_scope(
    question: str,
    patient_scope: Optional[Any] = None,
) -> RetrievalScope:
    """Select public vs patient-record vs both collection(s) and a scope filter."""
    try:
        marker = bool(should_query_patient_context(question))
        resource_types = tuple(required_resource_types(question))
        if patient_scope is None or not marker:
            return RetrievalScope(
                domain=PUBLIC_DOMAIN,
                collections=(_PUBLIC_COLLECTION,),
                filter_expr="",
                patient_scope=None,
                patient_marker=marker,
                resource_types=resource_types,
                source="public_default",
            )

        patient_filter = patient_scope_filter(patient_scope)
        medical = analyze_medical_query(question)
        has_general_entities = bool(medical.diseases or medical.symptoms or medical.drugs)
        if has_general_entities:
            domain = BOTH_DOMAIN
            collections = (_PUBLIC_COLLECTION, _PATIENT_COLLECTION)
        else:
            domain = PATIENT_DOMAIN
            collections = (_PATIENT_COLLECTION,)
        return RetrievalScope(
            domain=domain,
            collections=collections,
            filter_expr=patient_filter,
            patient_scope=patient_scope,
            patient_marker=marker,
            resource_types=resource_types,
            source="patient_scoped",
        )
    except Exception as exc:  # graceful degradation
        return RetrievalScope.public_fallback(f"scope_error:{type(exc).__name__}")


# ---------------------------------------------------------------------------
# Topic stage
# ---------------------------------------------------------------------------


def _sanitize_like(term: str) -> str:
    """Escape a free-text term for a Milvus ``like`` pattern (no wildcard injection)."""
    cleaned = (term or "").strip()
    cleaned = cleaned.replace("\\", "")
    cleaned = cleaned.replace('"', "")
    cleaned = cleaned.replace("%", "")
    cleaned = cleaned.replace("_", "")
    return cleaned


def stage_topic(
    question: str,
    intent: Any = None,
    rewrite_query: Any = None,
) -> TopicQuery:
    """Derive topic terms, candidate intents, and a narrowing Milvus filter."""
    try:
        medical = analyze_medical_query(question)

        terms: List[str] = []
        for seq in (medical.diseases, medical.symptoms, medical.drugs, medical.matched_terms):
            for item in seq:
                text = (item or "").strip()
                if text and text not in terms:
                    terms.append(text)

        # Optional, deterministic rewrite expansion (step-back / HyDE tokens).
        if rewrite_query:
            for token in re.split(r"[\s,，。;；]+", str(rewrite_query)):
                token = token.strip()
                if token and token not in terms:
                    terms.append(token)

        candidate_intents = []
        if intent is not None:
            candidate_intents.append(getattr(intent, "value", str(intent)))
        medical_intent = getattr(medical, "intent", None)
        if medical_intent and medical_intent not in candidate_intents:
            candidate_intents.append(medical_intent)

        sanitized = [t for t in (_sanitize_like(t) for t in terms) if t]
        filter_expr = ""
        if sanitized:
            filter_expr = "(" + " or ".join(f'text like "%{t}%"' for t in sanitized) + ")"

        return TopicQuery(
            topic_terms=tuple(terms),
            candidate_intents=tuple(candidate_intents),
            filter_expr=filter_expr,
            document_terms=tuple(sanitized),
            source="analyze_medical_query",
        )
    except Exception as exc:
        return TopicQuery.default(f"topic_error:{type(exc).__name__}")


# ---------------------------------------------------------------------------
# Document stage
# ---------------------------------------------------------------------------


def _candidate_document_ids(
    store: Any,
    topic: Optional[TopicQuery],
    max_documents: Optional[int] = None,
    scope_predicate: str = "",
) -> List[str]:
    """Best-effort document-level narrowing: distinct document_id(s) matching topic terms.

    Only touched when the store exposes ``query``/``query_all`` (real MilvusStore
    does; lightweight fake stores in tests may not). Degrades to ``[]`` (i.e. no
    document-level narrowing — search all documents) on any failure.

    ``scope_predicate`` must be supplied whenever the store is a patient-record
    collection: the topic terms alone match every tenant's documents, and the
    resulting id list is echoed to the caller in the funnel trace, so an
    unscoped query here discloses other tenants' document identifiers.
    """
    terms = topic.document_terms if topic else ()
    if not terms or max_documents == 0:
        return []
    if max_documents is None:
        max_documents = _MAX_DOCUMENT_CANDIDATES
    if not hasattr(store, "query_all") and not hasattr(store, "query"):
        return []

    clauses = " or ".join(f'section_path like "%{t}%"' for t in terms)
    filter_expr = f"chunk_level in [1, 2] and ({clauses})"
    if scope_predicate:
        filter_expr = f"{scope_predicate} and {filter_expr}"
    try:
        rows = store.query_all(filter_expr, ["document_id"])
    except Exception:
        try:
            rows = store.query(
                filter_expr,
                ["document_id"],
                limit=max_documents,
            )
        except Exception:
            return []

    document_ids: List[str] = []
    seen = set()
    for row in rows:
        did = str(row.get("document_id") or "").strip()
        if did and did not in seen:
            seen.add(did)
            document_ids.append(did)
        if len(document_ids) >= max_documents:
            break
    return document_ids


def stage_document(
    question: str,
    scope: RetrievalScope,
    topic: Optional[TopicQuery],
    top_k: int = 5,
) -> DocumentCandidates:
    """Run chunk-level retrieval (via the existing ladder) within docs of this scope."""
    topic_filter = topic.filter_expr if topic else ""
    trace = {
        "domain": scope.domain,
        "merged_from": [],
        "document_ids": [],
        "document_filter": topic_filter,
        "degraded": False,
        "sub_retrievals": [],
    }
    collected: List[dict] = []

    if scope.domain in (PUBLIC_DOMAIN, BOTH_DOMAIN):
        try:
            store = get_milvus_store(_MEDICAL_QA_KIND)
            document_ids = _candidate_document_ids(store, topic)
            result = retrieve_documents(
                question,
                top_k,
                filter_expr=topic_filter or "",
                candidate_document_ids=document_ids or None,
            )
            docs = result.get("docs", [])
            collected.extend(docs)
            trace["merged_from"].append(PUBLIC_DOMAIN)
            trace["document_ids"] = document_ids
            trace["sub_retrievals"].append({
                "domain": PUBLIC_DOMAIN,
                "count": len(docs),
                "meta": retrieval_trace_fields(result.get("meta", {}) or {}),
            })
        except Exception as exc:
            trace["degraded"] = True
            trace["public_error"] = str(exc)[:200]

    if scope.domain in (PATIENT_DOMAIN, BOTH_DOMAIN) and scope.patient_scope is not None:
        try:
            store = get_milvus_store(_PATIENT_RECORD_KIND)
            document_ids = _candidate_document_ids(
                store,
                topic,
                scope_predicate=patient_scope_predicate(scope.patient_scope),
            )
            result = retrieve_patient_records(
                question,
                scope.patient_scope,
                top_k,
                candidate_document_ids=document_ids or None,
            )
            docs = result.get("docs", [])
            collected.extend(docs)
            trace["merged_from"].append(PATIENT_DOMAIN)
            trace["document_ids"] = document_ids
            trace["sub_retrievals"].append({
                "domain": PATIENT_DOMAIN,
                "count": len(docs),
                "mode": result.get("mode", ""),
                "attempts": result.get("attempts", []),
            })
        except Exception as exc:
            trace["degraded"] = True
            trace["patient_error"] = str(exc)[:200]

    if not collected:
        trace["degraded"] = True

    merged = dedupe_documents(collected)
    for idx, item in enumerate(merged, 1):
        item["rrf_rank"] = idx

    source = BOTH_DOMAIN if set(trace["merged_from"]) == {PUBLIC_DOMAIN, PATIENT_DOMAIN} else (
        (trace["merged_from"][0] if trace["merged_from"] else scope.domain)
    )
    return DocumentCandidates(
        chunks=merged,
        source=source,
        merged_from=tuple(trace["merged_from"]),
        document_ids=trace["document_ids"],
        document_filter=topic_filter,
        trace=trace,
    )


# ---------------------------------------------------------------------------
# Funnel composition
# ---------------------------------------------------------------------------


def _scope_trace(scope: RetrievalScope) -> dict:
    return {
        "domain": scope.domain,
        "collections": list(scope.collections),
        "filter_expr": scope.filter_expr,
        "patient_marker": scope.patient_marker,
        "resource_types": list(scope.resource_types),
        "source": scope.source,
        "degraded": scope.degraded,
    }


def _topic_trace(topic: TopicQuery) -> dict:
    return {
        "topic_terms": list(topic.topic_terms),
        "candidate_intents": list(topic.candidate_intents),
        "filter_expr": topic.filter_expr,
        "source": topic.source,
        "degraded": topic.degraded,
    }


def _document_fallback(question: str, top_k: int, reason: str) -> DocumentCandidates:
    """Deterministic public fallback when the Document stage itself raises."""
    try:
        docs = retrieve_documents(question, top_k).get("docs", [])
    except Exception:
        docs = []
    return DocumentCandidates(
        chunks=docs,
        source=PUBLIC_DOMAIN,
        merged_from=(PUBLIC_DOMAIN,),
        document_ids=[],
        document_filter="",
        trace={"source": PUBLIC_DOMAIN, "degraded": True, "error": reason},
    )


def run_funnel(
    question: str,
    patient_scope: Optional[Any] = None,
    top_k: int = 5,
    intent: Any = None,
) -> Tuple[List[dict], dict]:
    """Compose Scope→Topic→Document; never raises; records decisions + degradation."""
    funnel_trace: dict = {
        "scope": {},
        "topic": {},
        "document": {},
        "stages": ["scope", "topic", "document"],
        "degraded": False,
        "degraded_stages": [],
        "final_domain": PUBLIC_DOMAIN,
        "final_public_fallback": False,
    }

    scope = None
    try:
        scope = stage_scope(question, patient_scope)
    except Exception as exc:
        scope = RetrievalScope.public_fallback(f"scope_error:{type(exc).__name__}")
        funnel_trace["degraded_stages"].append("scope")

    topic = None
    try:
        topic = stage_topic(question, intent)
    except Exception as exc:
        topic = TopicQuery.default(f"topic_error:{type(exc).__name__}")
        funnel_trace["degraded_stages"].append("topic")

    candidates = None
    try:
        candidates = stage_document(question, scope, topic, top_k)
    except Exception as exc:
        candidates = _document_fallback(question, top_k, f"document_error:{type(exc).__name__}")
        funnel_trace["degraded_stages"].append("document")

    funnel_trace["scope"] = _scope_trace(scope)
    funnel_trace["topic"] = _topic_trace(topic)
    funnel_trace["document"] = dict(candidates.trace or {})
    funnel_trace["final_domain"] = scope.domain

    document_degraded = bool((candidates.trace or {}).get("degraded", False))
    funnel_trace["degraded"] = bool(funnel_trace["degraded_stages"]) or document_degraded

    patient_signal = bool(scope.patient_marker)
    funnel_trace["final_public_fallback"] = (
        scope.domain == PUBLIC_DOMAIN
        and (patient_signal or bool(funnel_trace["degraded_stages"]))
    )

    return candidates.chunks or [], funnel_trace


__all__ = [
    "PUBLIC_DOMAIN",
    "PATIENT_DOMAIN",
    "BOTH_DOMAIN",
    "RetrievalScope",
    "TopicQuery",
    "DocumentCandidates",
    "stage_scope",
    "stage_topic",
    "stage_document",
    "run_funnel",
]
