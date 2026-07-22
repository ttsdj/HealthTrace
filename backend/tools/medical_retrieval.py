from __future__ import annotations

from hashlib import sha256

from backend.rag.pipeline import run_rag_graph
from backend.tools.contracts import EvidenceBundle, EvidenceItem


def retrieve_public_medical_evidence(query: str) -> tuple[EvidenceBundle, dict]:
    """Typed boundary around the existing public medical RAG graph."""
    try:
        result = run_rag_graph(query)
    except Exception as exc:
        return (
            EvidenceBundle(
                source="public_rag",
                query=query,
                status="unavailable",
                error=str(exc)[:300],
            ),
            {
                "tool_used": True,
                "tool_name": "search_knowledge_base",
                "retrieval_degraded": True,
                "retrieval_failure_reason": str(exc)[:300],
                "retrieval_mode": "unavailable",
                "recall_count": 0,
            },
        )

    docs = result.get("docs", []) if isinstance(result, dict) else []
    trace = result.get("rag_trace", {}) if isinstance(result, dict) else {}
    evidence = []
    for item in docs:
        chunk_id = str(item.get("chunk_id", ""))
        fallback_id = sha256(str(item).encode("utf-8")).hexdigest()[:24]
        evidence.append(
            EvidenceItem(
                evidence_id=f"public-{chunk_id or fallback_id}",
                source_type="medical_document",
                content=str(item.get("text", "")),
                title=str(item.get("filename", "")),
                document_id=str(item.get("document_id", "")),
                chunk_id=chunk_id,
                page=int(item.get("page_number", 0) or 0),
                score=float(item.get("score", 0.0) or 0.0),
                patient_specific=False,
                metadata={
                    "file_type": item.get("file_type", ""),
                    "parent_chunk_id": item.get("parent_chunk_id", ""),
                },
            )
        )

    return (
        EvidenceBundle(
            source="public_rag",
            query=query,
            status="ok" if evidence else "empty",
            evidence=evidence,
            retrieval_mode=str(trace.get("retrieval_mode", "")),
            attempts=list(trace.get("retrieval_attempts", []) or []),
        ),
        trace,
    )


def format_evidence_bundle(bundle: EvidenceBundle) -> str:
    if bundle.status == "unavailable":
        return "Medical vector evidence is temporarily unavailable."
    if not bundle.evidence:
        return "No relevant documents found in the knowledge base."
    lines = []
    for index, item in enumerate(bundle.evidence, start=1):
        location = f" page {item.page}" if item.page else ""
        lines.append(f"[{index}] {item.title}{location}\n{item.content}")
    return "\n\n".join(lines)
