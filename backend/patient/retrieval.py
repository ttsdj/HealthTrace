from __future__ import annotations

import re

from backend.patient.scope import PatientScope

_SAFE_SCOPE_ID = re.compile(r"^[A-Za-z0-9-]{1,64}$")


def patient_scope_filter(scope: PatientScope, chunk_level: int = 3) -> str:
    for value in (scope.tenant_id, scope.patient_id):
        if not _SAFE_SCOPE_ID.fullmatch(value):
            raise ValueError("Invalid patient scope identifier")
    return (
        f'document_domain == "patient_private" and '
        f'tenant_id == "{scope.tenant_id}" and '
        f'patient_id == "{scope.patient_id}" and '
        f"chunk_level == {int(chunk_level)}"
    )


def retrieve_patient_records(
    query: str,
    scope: PatientScope,
    top_k: int = 5,
) -> dict:
    """Retrieve private evidence with mandatory server-derived scope filters."""
    from backend.indexing.embedding import embedding_service
    from backend.indexing.milvus_client import get_milvus_store

    store = get_milvus_store("patient_record")
    filter_expr = patient_scope_filter(scope)
    attempts: list[dict] = []
    dense = embedding_service.get_embedding(query)

    try:
        docs = store.hybrid_retrieve(dense, query, top_k=top_k, filter_expr=filter_expr)
        attempts.append({"mode": "hybrid", "status": "ok", "count": len(docs)})
        if docs:
            return {"docs": docs, "mode": "hybrid", "attempts": attempts}
    except Exception as exc:
        attempts.append({"mode": "hybrid", "status": "error", "error": str(exc)[:200]})

    try:
        docs = store.dense_retrieve(dense, top_k=top_k, filter_expr=filter_expr)
        attempts.append({"mode": "dense", "status": "ok", "count": len(docs)})
        if docs:
            return {"docs": docs, "mode": "dense", "attempts": attempts}
    except Exception as exc:
        attempts.append({"mode": "dense", "status": "error", "error": str(exc)[:200]})

    try:
        docs = store.sparse_retrieve(query, top_k=top_k, filter_expr=filter_expr)
        attempts.append({"mode": "sparse", "status": "ok", "count": len(docs)})
        if docs:
            return {"docs": docs, "mode": "sparse", "attempts": attempts}
    except Exception as exc:
        attempts.append({"mode": "sparse", "status": "error", "error": str(exc)[:200]})

    return {"docs": [], "mode": "no_results", "attempts": attempts}
