"""RAG 检索步骤的 SSE 实时推送。

请求上下文使用 ContextVar 隔离。LangGraph/LangChain 的上下文线程池会把
ContextVar 复制到 Send 子分支，因此既支持协程请求隔离，也支持跨线程推送。
"""

import asyncio
from contextvars import ContextVar
from typing import Any

_RAG_STEP_CHANNEL: ContextVar[tuple[Any, asyncio.AbstractEventLoop] | None] = ContextVar(
    "healthtrace_rag_step_channel",
    default=None,
)
_SUB_AGENT_GROUP: ContextVar[str | None] = ContextVar(
    "healthtrace_sub_agent_group",
    default=None,
)


def set_rag_step_queue(queue) -> None:
    """设置 RAG 步骤队列，并捕获当前事件循环以便跨线程调度。"""
    if queue is None:
        _RAG_STEP_CHANNEL.set(None)
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.get_event_loop()
    _RAG_STEP_CHANNEL.set((queue, loop))


def set_sub_agent_group(group: str) -> None:
    """设置当前请求分支的子 Agent 分组标识。"""
    _SUB_AGENT_GROUP.set(group)


def clear_sub_agent_group() -> None:
    """清除当前线程的子 Agent 分组标识。"""
    _SUB_AGENT_GROUP.set(None)


def get_sub_agent_group():
    """获取当前请求分支的子 Agent 分组标识。"""
    return _SUB_AGENT_GROUP.get()


def emit_rag_step(icon: str, label: str, detail: str = "") -> None:
    """向队列发送一个 RAG 检索步骤。支持跨线程安全调用。"""
    channel = _RAG_STEP_CHANNEL.get()
    if channel is None:
        return
    queue, loop = channel
    step = {"icon": icon, "label": label, "detail": detail}
    group = get_sub_agent_group()
    if group:
        step["group"] = group
    try:
        if not loop.is_closed():
            loop.call_soon_threadsafe(queue.put_nowait, step)
    except Exception:
        pass
