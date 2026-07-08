"""Lazy chat package exports.

Keeping package initialization lightweight prevents utility modules such as
``backend.chat.rag_context`` from constructing the Agent and recursively
importing the tool registry.
"""

from __future__ import annotations

__all__ = [
    "chat_with_agent",
    "chat_with_agent_stream",
    "storage",
    "emit_rag_step",
    "set_rag_step_queue",
]


def __getattr__(name: str):
    if name in {"chat_with_agent", "chat_with_agent_stream", "storage"}:
        from backend.chat import service

        return getattr(service, name)
    if name in {"emit_rag_step", "set_rag_step_queue"}:
        from backend.chat import streaming

        return getattr(streaming, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
