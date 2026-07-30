from contextvars import ContextVar

from langchain_core.tools import tool

from backend.chat.rag_context import record_rag_context
from backend.kg import search_medical_kg_text
from backend.tools.medical_retrieval import format_evidence_bundle, retrieve_public_medical_evidence

_KNOWLEDGE_TOOL_CALLS_THIS_TURN: ContextVar[int] = ContextVar(
    "healthtrace_knowledge_tool_calls_this_turn",
    default=0,
)


def reset_knowledge_tool_calls() -> None:
    """每轮对话开始时重置知识库工具调用计数。"""
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN.set(0)


def _try_acquire_knowledge_tool_call() -> bool:
    calls = _KNOWLEDGE_TOOL_CALLS_THIS_TURN.get()
    if calls >= 1:
        return False
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN.set(calls + 1)
    return True


@tool("search_knowledge_base")
def search_knowledge_base(query: str) -> str:
    """Search for information in the knowledge base using hybrid retrieval (dense + sparse vectors)."""
    if not _try_acquire_knowledge_tool_call():
        return (
            "TOOL_CALL_LIMIT_REACHED: search_knowledge_base has already been called once in this turn. "
            "Use the existing retrieval result and provide the final answer directly."
        )

    bundle, rag_trace = retrieve_public_medical_evidence(query)
    record_rag_context(rag_trace)
    if bundle.status == "unavailable":
        return (
            "Medical vector retrieval is temporarily unavailable. "
            "Proceed with conservative general medical knowledge, clearly state that retrieved evidence was unavailable, "
            "and avoid definitive diagnosis or prescription-level advice."
        )

    return "Retrieved Medical Vector Evidence:\n" + format_evidence_bundle(bundle)


@tool("search_medical_kg")
def search_medical_kg(query: str) -> str:
    """Search structured medical facts in the Neo4j knowledge graph."""
    kg_text, kg_trace = search_medical_kg_text(query)
    record_rag_context({"tool_used": True, "tool_name": "search_medical_kg", **kg_trace})
    return "Structured Medical KG Evidence:\n" + kg_text
