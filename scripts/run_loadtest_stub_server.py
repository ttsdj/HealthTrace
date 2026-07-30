"""Run the real HealthTrace API with only the LLM-facing chat calls stubbed.

This entry point is intentionally opt-in.  It keeps authentication, middleware,
PostgreSQL access and HTTP/SSE handling real while preventing load tests from
calling an external model.
"""

from __future__ import annotations

import asyncio
import json
import os
import time


if os.getenv("HEALTHTRACE_LOADTEST_ALLOW_STUB", "").lower() != "true":
    raise SystemExit(
        "Refusing to start: set HEALTHTRACE_LOADTEST_ALLOW_STUB=true explicitly."
    )


from backend.api.routes import chat as chat_routes  # noqa: E402
from backend.app import app  # noqa: E402


STUB_LATENCY_MS = max(0, int(os.getenv("HEALTHTRACE_LOADTEST_STUB_LATENCY_MS", "50")))


def _stub_chat(
    message: str,
    username: str,
    session_id: str,
    location_context: dict | None = None,
) -> dict:
    # The production non-streaming route calls a synchronous agent function
    # directly. A blocking sleep deliberately preserves that scheduling shape.
    time.sleep(STUB_LATENCY_MS / 1000)
    return {
        "response": f"stub:{username}:{session_id}:{message}",
        "rag_trace": {
            "tool_used": False,
            "retrieval_stage": "loadtest_stub",
        },
    }


async def _stub_chat_stream(
    message: str,
    username: str,
    session_id: str,
    location_context: dict | None = None,
):
    marker = f"stub:{username}:{session_id}:{message}"
    per_chunk_seconds = STUB_LATENCY_MS / 3000
    for index in range(3):
        if per_chunk_seconds:
            await asyncio.sleep(per_chunk_seconds)
        event = {
            "type": "content",
            "content": f"{marker}:chunk-{index}",
        }
        yield f"data: {json.dumps(event)}\n\n"
    yield "data: [DONE]\n\n"


chat_routes.chat_with_agent = _stub_chat
chat_routes.chat_with_agent_stream = _stub_chat_stream


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "18000")),
        log_level=os.getenv("LOG_LEVEL", "warning"),
        access_log=False,
    )
