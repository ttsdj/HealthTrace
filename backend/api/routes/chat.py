import json
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from backend.chat import chat_with_agent, chat_with_agent_stream
from backend.db.models import User
from backend.infra.auth import get_current_user
from backend.schemas import ChatRequest, ChatResponse

router = APIRouter(tags=["chat"])

logger = logging.getLogger("healthtrace.chat")


def _sanitized_upstream_error(exc: Exception) -> tuple[int, str]:
    """Map an upstream failure to a status code and a client-safe message.

    Raw provider errors embed base URLs, model names and request ids, so the
    full text goes to server logs only and callers get a generic message.
    """
    message = str(exc)
    logger.warning("chat upstream error: %s: %s", type(exc).__name__, message)
    match = re.search(r"Error code:\s*(\d{3})", message)
    if match:
        code = int(match.group(1))
        if code == 429:
            return 429, "上游模型服务触发限流/额度限制，请稍后重试或检查服务额度。"
        if code in (401, 403):
            return 502, "上游模型服务认证失败，请联系管理员检查服务配置。"
        return 502, "上游模型服务暂时不可用，请稍后重试。"
    return 500, "咨询服务暂时不可用，请稍后重试。"


@router.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    try:
        session_id = request.session_id or "default_session"
        location_context = (
            request.location.model_dump()
            if request.location is not None
            else None
        )
        # The consultation runtime is synchronous and may call an LLM, vector
        # store and database. Keep it off the ASGI event loop so SSE, liveness
        # and unrelated requests remain schedulable under chat load.
        resp = await run_in_threadpool(
            chat_with_agent,
            request.message,
            current_user.username,
            session_id,
            location_context,
        )
        if isinstance(resp, dict):
            return ChatResponse(**resp)
        return ChatResponse(response=resp)
    except Exception as e:
        status_code, detail = _sanitized_upstream_error(e)
        raise HTTPException(status_code=status_code, detail=detail)


@router.post("/chat/stream")
async def chat_stream_endpoint(request: ChatRequest, current_user: User = Depends(get_current_user)):
    async def event_generator():
        try:
            session_id = request.session_id or "default_session"
            location_context = (
                request.location.model_dump()
                if request.location is not None
                else None
            )
            async for chunk in chat_with_agent_stream(
                request.message,
                current_user.username,
                session_id,
                location_context,
            ):
                yield chunk
        except Exception as e:
            _, detail = _sanitized_upstream_error(e)
            error_data = {"type": "error", "content": detail}
            yield f"data: {json.dumps(error_data)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store, must-revalidate",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
