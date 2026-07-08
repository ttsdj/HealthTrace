"""单轮对话内 RAG trace 暂存（工具执行 → 流式结束后写入会话）。"""

from typing import Optional

from backend.rag.conflict import detect_kg_vector_conflict

_LAST_RAG_CONTEXT: Optional[dict] = None


def _merge_trace(previous: dict, incoming: dict) -> dict:
    previous_tool = previous.get("tool_name")
    incoming_tool = incoming.get("tool_name")
    tools = previous.get("tools_used") or ([previous_tool] if previous_tool else [])
    if incoming_tool and incoming_tool not in tools:
        tools.append(incoming_tool)

    merged = dict(previous)
    if incoming_tool == "search_medical_kg":
        merged["kg_trace"] = incoming
    elif incoming_tool == "search_knowledge_base":
        merged["vector_trace"] = incoming
    else:
        merged.setdefault("tool_traces", []).append(incoming)

    for key, value in incoming.items():
        if key in merged and key not in {
            "tool_used",
            "tool_name",
            "tools_used",
            "retrieved_chunks",
            "initial_retrieved_chunks",
            "expanded_retrieved_chunks",
        }:
            continue
        merged[key] = value

    if previous.get("retrieved_chunks") and not incoming.get("retrieved_chunks"):
        merged["retrieved_chunks"] = previous["retrieved_chunks"]
    if previous.get("initial_retrieved_chunks") and not incoming.get("initial_retrieved_chunks"):
        merged["initial_retrieved_chunks"] = previous["initial_retrieved_chunks"]
    if previous.get("expanded_retrieved_chunks") and not incoming.get("expanded_retrieved_chunks"):
        merged["expanded_retrieved_chunks"] = previous["expanded_retrieved_chunks"]

    cross_conflict = detect_kg_vector_conflict(
        merged.get("kg_trace"),
        merged.get("vector_trace"),
    )
    merged.update(cross_conflict)

    merged["tool_used"] = True
    merged["tool_name"] = " + ".join(tools) if tools else incoming_tool
    merged["tools_used"] = tools
    return merged


def get_last_rag_context(clear: bool = True) -> Optional[dict]:
    """获取最近一次 RAG 检索上下文，默认读取后清空。"""
    global _LAST_RAG_CONTEXT
    context = _LAST_RAG_CONTEXT
    if clear:
        _LAST_RAG_CONTEXT = None
    return context


def record_rag_context(rag_trace: dict) -> None:
    if rag_trace:
        global _LAST_RAG_CONTEXT
        previous = (_LAST_RAG_CONTEXT or {}).get("rag_trace")
        if previous:
            rag_trace = _merge_trace(previous, rag_trace)
        elif rag_trace.get("tool_name") == "search_medical_kg":
            rag_trace = {**rag_trace, "kg_trace": rag_trace}
        elif rag_trace.get("tool_name") == "search_knowledge_base":
            rag_trace = {**rag_trace, "vector_trace": rag_trace}
        _LAST_RAG_CONTEXT = {"rag_trace": rag_trace}
