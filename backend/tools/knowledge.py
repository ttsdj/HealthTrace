from langchain_core.tools import tool

from backend.chat.rag_context import record_rag_context
from backend.kg import search_medical_kg_text
from backend.rag.fusion import format_retrieval_context
from backend.rag.pipeline import run_rag_graph

_KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0


def reset_knowledge_tool_calls() -> None:
    """每轮对话开始时重置知识库工具调用计数。"""
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN = 0


def _try_acquire_knowledge_tool_call() -> bool:
    global _KNOWLEDGE_TOOL_CALLS_THIS_TURN
    if _KNOWLEDGE_TOOL_CALLS_THIS_TURN >= 1:
        return False
    _KNOWLEDGE_TOOL_CALLS_THIS_TURN += 1
    return True


@tool("search_knowledge_base")
def search_knowledge_base(query: str) -> str:
    """Search for information in the knowledge base using hybrid retrieval (dense + sparse vectors)."""
    if not _try_acquire_knowledge_tool_call():
        return (
            "TOOL_CALL_LIMIT_REACHED: search_knowledge_base has already been called once in this turn. "
            "Use the existing retrieval result and provide the final answer directly."
        )

    try:
        rag_result = run_rag_graph(query)
    except Exception as exc:
        record_rag_context(
            {
                "tool_used": True,
                "tool_name": "search_knowledge_base",
                "retrieval_degraded": True,
                "retrieval_failure_reason": str(exc)[:300],
                "retrieval_mode": "unavailable",
                "retrieval_attempts": [
                    {"mode": "rag_graph", "status": "error", "error": str(exc)[:200]}
                ],
                "recall_count": 0,
            }
        )
        return (
            "Medical vector retrieval is temporarily unavailable. "
            "Proceed with conservative general medical knowledge, clearly state that retrieved evidence was unavailable, "
            "and avoid definitive diagnosis or prescription-level advice."
        )

    docs = rag_result.get("docs", []) if isinstance(rag_result, dict) else []
    rag_trace = rag_result.get("rag_trace", {}) if isinstance(rag_result, dict) else {}
    record_rag_context(rag_trace)

    if not docs:
        return "No relevant documents found in the knowledge base."

    return "Retrieved Medical Vector Evidence:\n" + format_retrieval_context(docs)


@tool("search_medical_kg")
def search_medical_kg(query: str) -> str:
    """Search structured medical facts in the Neo4j knowledge graph."""
    kg_text, kg_trace = search_medical_kg_text(query)
    record_rag_context({"tool_used": True, "tool_name": "search_medical_kg", **kg_trace})
    return "Structured Medical KG Evidence:\n" + kg_text
