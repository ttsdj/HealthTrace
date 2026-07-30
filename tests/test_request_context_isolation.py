import asyncio

from backend.chat.rag_context import get_last_rag_context, record_rag_context
from backend.chat.streaming import (
    clear_sub_agent_group,
    emit_rag_step,
    set_rag_step_queue,
    set_sub_agent_group,
)
from backend.tools.knowledge import (
    _try_acquire_knowledge_tool_call,
    reset_knowledge_tool_calls,
)


def test_rag_context_and_tool_budget_are_isolated_between_coroutines():
    async def worker(name: str):
        get_last_rag_context(clear=True)
        reset_knowledge_tool_calls()
        assert _try_acquire_knowledge_tool_call() is True
        record_rag_context(
            {
                "tool_used": True,
                "tool_name": "search_knowledge_base",
                "query": name,
            }
        )
        await asyncio.sleep(0)
        assert _try_acquire_knowledge_tool_call() is False
        return get_last_rag_context(clear=True)["rag_trace"]["query"]

    async def run():
        return await asyncio.gather(worker("request-a"), worker("request-b"))

    assert asyncio.run(run()) == ["request-a", "request-b"]


def test_rag_step_channels_are_request_scoped_and_propagate_to_worker_threads():
    async def worker(name: str):
        queue = asyncio.Queue()
        set_rag_step_queue(queue)
        set_sub_agent_group(name)
        try:
            await asyncio.to_thread(emit_rag_step, "test", f"step-{name}")
            return await asyncio.wait_for(queue.get(), timeout=1)
        finally:
            clear_sub_agent_group()
            set_rag_step_queue(None)

    async def run():
        return await asyncio.gather(worker("request-a"), worker("request-b"))

    steps = asyncio.run(run())
    assert steps[0]["label"] == "step-request-a"
    assert steps[0]["group"] == "request-a"
    assert steps[1]["label"] == "step-request-b"
    assert steps[1]["group"] == "request-b"
