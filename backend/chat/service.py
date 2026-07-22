import asyncio
import json
import os

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, SystemMessage

from backend.agent.orchestrator import finalize_consultation, prepare_consultation
from backend.agent.tool_audit import get_tool_audit, reset_tool_audit
from backend.care_navigation.context import LocationContext, use_location_context
from backend.care_navigation.triage import assess_care_navigation_need
from backend.chat.runtime import agent, fast_model
from backend.chat.storage import ConversationStorage
from backend.chat.rag_context import get_last_rag_context
from backend.chat.streaming import set_rag_step_queue
from backend.memory import memory_service
from backend.medical_nlp.safety import analyze_medical_safety, redact_sensitive_text
from backend.patient.context import build_verified_patient_context  # compatibility extension point
from backend.rag.context_compression import summarize_history_window
from backend.tools import reset_knowledge_tool_calls

storage = ConversationStorage()

CONTEXT_WINDOW_MESSAGES = 6


def _build_context_messages(
    messages: list,
    persistent_note: str,
    user_text: str,
    memory_note: str = "",
    location_context: dict | None = None,
) -> list:
    short_term, _ = summarize_history_window(messages)
    context_messages: list = []
    if persistent_note:
        context_messages.append(
            SystemMessage(
                content=(
                    "【对话持久化笔记（你的工作记忆）】\n"
                    f"{persistent_note}\n"
                    "请参考以上笔记保持对话连贯性，避免重复回答已解决的问题。"
                )
            )
        )
    if memory_note:
        context_messages.append(
            SystemMessage(
                content=(
                    "【HealthTrace 记忆检索结果】\n"
                    f"{memory_note}\n"
                    "请仅在和本轮问题相关时使用这些记忆，并避免把未确认信息当作诊断结论。"
                )
            )
        )
    navigation = assess_care_navigation_need(user_text)
    location = LocationContext.from_value(location_context)
    if location.usable:
        context_messages.append(
            SystemMessage(
                content=(
                    "【本轮定位授权】用户已明确授权本轮请求使用浏览器定位。"
                    "精确坐标由服务端请求上下文保管；若知识证据不足或需要线下检查，"
                    "可调用一次 search_nearby_medical_care，不得猜测坐标。"
                )
            )
        )
    elif navigation["care_navigation_recommended"]:
        context_messages.append(
            SystemMessage(
                content=(
                    "【本轮定位状态】用户尚未授权精确定位。若需要附近医院，"
                    "请先让用户点击定位按钮授权；本轮不得调用地图并猜测位置。"
                )
            )
        )
    context_messages.extend(short_term)
    context_messages.append(HumanMessage(content=user_text))
    return context_messages


def _build_trace_base(
    messages: list,
    user_text: str,
    location_context: dict | None = None,
) -> dict:
    _, history_meta = summarize_history_window(messages)
    _, privacy_meta = redact_sensitive_text(user_text)
    safety_meta = analyze_medical_safety(user_text)
    navigation_meta = assess_care_navigation_need(user_text)
    location = LocationContext.from_value(location_context)
    return {
        **history_meta,
        **privacy_meta,
        **safety_meta,
        **navigation_meta,
        "location_authorized": location.usable,
    }


def _merge_trace_base(rag_trace: dict | None, trace_base: dict) -> dict:
    merged = dict(rag_trace or {})
    for key, value in trace_base.items():
        merged.setdefault(key, value)
    return merged


def _friendly_model_error(error: Exception) -> str:
    error_msg = str(error)
    print(f"Model invocation error: {error_msg!r}")
    lowered = error_msg.lower()
    if "unauthorized" in lowered or "401" in lowered or "api key" in lowered:
        return "模型认证失败，请检查本地 .env 中的模型密钥和 BASE_URL。"
    if "not found" in lowered or "model" in lowered and "404" in lowered:
        return "模型名称不可用，请检查 .env 中的 MODEL 配置。"
    if "response_format" in error_msg or "invalid_request_error" in error_msg:
        return "模型服务暂时不可用，请稍后重试。"
    if "rate" in lowered or "429" in lowered:
        return "请求过于频繁或额度受限，请稍后重试。"
    if "timeout" in lowered:
        return "模型请求超时，请稍后重试。"
    if len(error_msg) > 180:
        return "系统处理异常，请稍后重试。"
    return error_msg


def _safe_degraded_medical_response(user_text: str, reason: str = "") -> str:
    topic = (user_text or "这个问题").strip()
    prefix = (
        "当前模型或检索链路出现临时降级，我先给出安全、保守的医学信息。"
        "以下内容仅供医学知识参考，不能替代医生面诊。"
    )
    if "咳" in topic or "咳嗽" in topic:
        return (
            f"{prefix}\n\n"
            "持续咳嗽可能与上呼吸道感染后咳嗽、鼻炎/鼻窦炎导致的鼻后滴漏、咳嗽变异性哮喘、"
            "支气管炎、肺炎、胃食管反流、慢阻肺、支气管扩张、肺结核等情况有关。"
            "如果伴有咯血、胸痛、呼吸困难、持续发热、夜间盗汗、明显消瘦，或咳嗽超过 3 周仍不缓解，"
            "建议尽快到呼吸内科就诊，医生可能会结合胸片/胸部 CT、血常规、肺功能、痰检等检查判断原因。"
        )
    return (
        f"{prefix}\n\n"
        f"关于“{topic}”，建议先关注症状持续时间、严重程度、是否有发热/胸痛/呼吸困难/出血/体重下降等危险信号。"
        "若症状持续、加重或影响日常生活，应到相应专科或全科门诊进一步评估。"
        + (f"\n\n降级原因：{reason[:160]}" if reason else "")
    )


def _clamp_score(value: float) -> float:
    return round(max(0.0, min(1.0, value)), 3)


def _attach_ragas_lite_scores(rag_trace: dict | None, response_content: str) -> dict:
    """Attach cheap runtime quality signals without blocking chat on full RAGAS."""
    trace = dict(rag_trace or {})
    chunks = trace.get("retrieved_chunks") or []
    recall_count = trace.get("recall_count")
    if recall_count is None:
        recall_count = len(chunks)
    degraded = bool(
        trace.get("retrieval_degraded")
        or trace.get("retrieval_failure_reason")
        or trace.get("fallback_error")
    )
    response_len = len((response_content or "").strip())

    context_relevance = 0.20 + min(len(chunks), 5) * 0.13 + min(int(recall_count or 0), 10) * 0.015
    if trace.get("retrieval_mode") in {"hybrid", "hybrid_rrf", "hybrid_rerank"}:
        context_relevance += 0.05
    if degraded:
        context_relevance -= 0.18

    faithfulness = 0.48 + (0.18 if chunks else 0.0) + (0.07 if trace.get("rerank_applied") else 0.0)
    if response_len < 80:
        faithfulness -= 0.14
    if degraded:
        faithfulness -= 0.16

    answer_relevance = 0.35 + min(response_len / 900, 1.0) * 0.42 + (0.08 if chunks else 0.0)
    if trace.get("high_risk_medical"):
        answer_relevance -= 0.03
    if degraded and not chunks:
        answer_relevance -= 0.08

    context_relevance = _clamp_score(context_relevance)
    faithfulness = _clamp_score(faithfulness)
    answer_relevance = _clamp_score(answer_relevance)
    quality = round((context_relevance + faithfulness + answer_relevance) / 3, 3)

    trace.update(
        {
            "ragas_context_relevance": context_relevance,
            "ragas_faithfulness": faithfulness,
            "ragas_answer_relevance": answer_relevance,
            "ragas_quality_score": quality,
            "ragas_evaluation_mode": "ragas_lite_runtime_no_reference",
            "ragas_evaluation_note": (
                "实时模式无人工 reference，分数用于工程监控；正式 RAGAS 分数请以离线评测报告为准。"
            ),
        }
    )
    return trace


async def update_persistent_note(
    current_note: str,
    user_text: str,
    ai_response: str,
) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(
        None,
        lambda: _update_persistent_note_sync(current_note, user_text, ai_response),
    )


def _generate_session_title_sync(user_text: str) -> str:
    try:
        prompt = (
            "请根据用户的首次提问，生成一个简短的对话标题（控制在 10 个字以内，不要标点）。\n"
            f"用户提问：{user_text}"
        )
        res = fast_model.invoke([SystemMessage(content=prompt)])
        title = (res.content or "").strip().strip('"').strip("。")
        return title or "新会话"
    except Exception as e:
        print(f"Title generation error: {e}")
        return "新会话"


async def generate_session_title(user_text: str) -> str:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, lambda: _generate_session_title_sync(user_text))


def _update_persistent_note_sync(current_note: str, user_text: str, ai_response: str) -> str:
    try:
        prompt = (
            "你是一个【Context Manager Agent】(上下文管理器)，负责维护多轮对话中的「持久化笔记」。\n"
            "笔记是模型在有限上下文窗口下的长效工作记忆，记录已解决的问题与关键事实。\n\n"
            "更新规则：\n"
            "1. 将新信息与现有笔记智能合并，不要简单拼接。\n"
            "2. 过滤噪音，控制在 500 字以内，用简明条目输出。\n"
            "3. 若信息冲突，保留最可靠或最新版本。\n\n"
            f"▼ 现有笔记：\n{current_note if current_note else '无'}\n\n"
            f"▼ 最新一轮对话：\n用户：{user_text}\nAI：{ai_response}\n\n"
            "请直接输出更新后的笔记（纯文本，不要解释或 Markdown 代码块）："
        )
        res = fast_model.invoke([SystemMessage(content=prompt)])
        return (res.content or "").strip()
    except Exception as e:
        print(f"Context Manager Error: {e}")
        return current_note


def chat_with_agent(
    user_text: str,
    user_id: str = "default_user",
    session_id: str = "default_session",
    location_context: dict | None = None,
):
    messages, metadata = storage.load_with_meta(user_id, session_id)
    persistent_note = metadata.get("persistent_note", "")
    is_first_message = len(messages) == 0

    get_last_rag_context(clear=True)
    reset_knowledge_tool_calls()
    reset_tool_audit()
    trace_base = _build_trace_base(messages, user_text, location_context)
    redacted_user_text, privacy_meta = redact_sensitive_text(user_text)
    consultation_state = prepare_consultation(user_id, session_id, redacted_user_text)
    guarded_response = consultation_state.get("guarded_response", "")

    if guarded_response:
        memory_hits = {}
        memory_note = ""
        patient_context = consultation_state.get("patient_context", "")
        patient_context_meta = consultation_state.get("patient_context_meta", {})
    else:
        try:
            memory_hits = memory_service.retrieve(user_text, user_id, session_id)
            memory_note = memory_service.format_for_prompt(memory_hits)
        except Exception as e:
            print(f"Memory retrieval error: {e}")
            memory_hits = {}
            memory_note = ""

        patient_context = consultation_state.get("patient_context", "")
        patient_context_meta = consultation_state.get("patient_context_meta", {})
    trace_base.update(patient_context_meta)
    if patient_context:
        memory_note = f"{patient_context}\n{memory_note}".strip()

    if trace_base.get("safety_notice"):
        memory_note = f"【医疗安全边界】{trace_base['safety_notice']}\n{memory_note}".strip()
    context_messages = _build_context_messages(
        messages,
        persistent_note,
        user_text,
        memory_note,
        location_context,
    )
    messages.append(HumanMessage(content=redacted_user_text))
    storage.save(user_id, session_id, messages)

    fallback_error = ""
    if guarded_response:
        result = {"output": guarded_response, "preflight_guarded": True}
    else:
        try:
            with use_location_context(location_context):
                result = agent.invoke(
                    {"messages": context_messages},
                    config={"recursion_limit": 8},
                )
        except Exception as exc:
            print(f"Agent invocation fallback activated: {exc!r}")
            fallback_error = str(exc)
            result = {
                "output": _safe_degraded_medical_response(user_text, _friendly_model_error(exc)),
                "fallback_error": str(exc),
            }

    response_content = ""
    if isinstance(result, dict):
        if "output" in result:
            response_content = result["output"]
        elif "messages" in result and result["messages"]:
            msg = result["messages"][-1]
            response_content = getattr(msg, "content", str(msg))
        else:
            response_content = str(result)
    elif hasattr(result, "content"):
        response_content = result.content
    else:
        response_content = str(result)

    if not (response_content or "").strip():
        response_content = _safe_degraded_medical_response(
            user_text,
            "agent returned an empty response",
        )

    rag_context = get_last_rag_context(clear=True)
    rag_trace = rag_context.get("rag_trace") if rag_context else None
    trace_base.update(privacy_meta)
    rag_trace = _merge_trace_base(rag_trace, trace_base)
    rag_trace["tool_calls"] = get_tool_audit(clear=True)
    response_content, rag_trace = finalize_consultation(
        consultation_state,
        rag_trace,
        response_content,
        fallback_error=fallback_error,
    )

    messages.append(AIMessage(content=response_content))
    try:
        memory_service.store_turn(user_id, session_id, redacted_user_text, response_content)
    except Exception as e:
        print(f"Memory write error: {e}")

    if rag_trace is not None:
        rag_trace["memory_hits"] = {
            key: len(value)
            for key, value in (memory_hits or {}).items()
        }
    rag_trace = _attach_ragas_lite_scores(rag_trace, response_content)

    save_meta = dict(metadata)
    if is_first_message:
        save_meta["title"] = _generate_session_title_sync(user_text)
    save_meta["persistent_note"] = _update_persistent_note_sync(
        persistent_note, redacted_user_text, response_content
    )

    extra_message_data = [None] * (len(messages) - 1) + [{"rag_trace": rag_trace}]
    storage.save(
        user_id,
        session_id,
        messages,
        metadata=save_meta,
        extra_message_data=extra_message_data,
    )

    return {
        "response": response_content,
        "rag_trace": rag_trace,
    }


async def chat_with_agent_stream(
    user_text: str,
    user_id: str = "default_user",
    session_id: str = "default_session",
    location_context: dict | None = None,
):
    if os.getenv("DISABLE_TOOL_STREAMING", "true").lower() == "true":
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None,
            lambda: chat_with_agent(user_text, user_id, session_id, location_context),
        )
        response_text = result.get("response", "") if isinstance(result, dict) else str(result)
        for start in range(0, len(response_text), 80):
            yield f"data: {json.dumps({'type': 'content', 'content': response_text[start:start + 80]})}\n\n"
            await asyncio.sleep(0)
        rag_trace = result.get("rag_trace") if isinstance(result, dict) else None
        if rag_trace:
            yield f"data: {json.dumps({'type': 'trace', 'rag_trace': rag_trace})}\n\n"
        yield "data: [DONE]\n\n"
        return

    messages, metadata = storage.load_with_meta(user_id, session_id)
    persistent_note = metadata.get("persistent_note", "")
    is_first_message = len(messages) == 0

    get_last_rag_context(clear=True)
    reset_knowledge_tool_calls()
    reset_tool_audit()
    trace_base = _build_trace_base(messages, user_text, location_context)
    redacted_user_text, privacy_meta = redact_sensitive_text(user_text)
    consultation_state = prepare_consultation(user_id, session_id, redacted_user_text)
    guarded_response = consultation_state.get("guarded_response", "")

    output_queue = asyncio.Queue()

    class _RagStepProxy:
        def put_nowait(self, step):
            output_queue.put_nowait({"type": "rag_step", "step": step})

    set_rag_step_queue(_RagStepProxy())

    if guarded_response:
        memory_hits = {}
        memory_note = ""
        patient_context = consultation_state.get("patient_context", "")
        patient_context_meta = consultation_state.get("patient_context_meta", {})
    else:
        try:
            memory_hits = memory_service.retrieve(user_text, user_id, session_id)
            memory_note = memory_service.format_for_prompt(memory_hits)
        except Exception as e:
            print(f"Memory retrieval error: {e}")
            memory_hits = {}
            memory_note = ""

        patient_context = consultation_state.get("patient_context", "")
        patient_context_meta = consultation_state.get("patient_context_meta", {})
    trace_base.update(patient_context_meta)
    if patient_context:
        memory_note = f"{patient_context}\n{memory_note}".strip()

    if trace_base.get("safety_notice"):
        memory_note = f"【医疗安全边界】{trace_base['safety_notice']}\n{memory_note}".strip()
    context_messages = _build_context_messages(
        messages,
        persistent_note,
        user_text,
        memory_note,
        location_context,
    )
    messages.append(HumanMessage(content=redacted_user_text))
    storage.save(user_id, session_id, messages)

    title_task = None
    if is_first_message:

        def _on_title_done(fut):
            try:
                title = fut.result()
                output_queue.put_nowait(
                    {"type": "session_title", "title": title, "session_id": session_id}
                )
            except Exception as e:
                print(f"Title task error: {e}")

        title_task = asyncio.create_task(generate_session_title(user_text))
        title_task.add_done_callback(_on_title_done)

    full_response = ""
    agent_error = ""

    async def _agent_worker():
        nonlocal full_response, agent_error
        try:
            if guarded_response:
                full_response = guarded_response
                await output_queue.put({"type": "content", "content": guarded_response})
                return
            with use_location_context(location_context):
                async for msg, _metadata in agent.astream(
                    {"messages": context_messages},
                    stream_mode="messages",
                    config={"recursion_limit": 8},
                ):
                    if not isinstance(msg, AIMessageChunk):
                        continue
                    if getattr(msg, "tool_call_chunks", None):
                        continue

                    content = ""
                    if isinstance(msg.content, str):
                        content = msg.content
                    elif isinstance(msg.content, list):
                        for block in msg.content:
                            if isinstance(block, str):
                                content += block
                            elif isinstance(block, dict) and block.get("type") == "text":
                                content += block.get("text", "")

                    if content:
                        full_response += content
                        await output_queue.put({"type": "content", "content": content})
        except Exception as e:
            agent_error = str(e)
            if not full_response:
                try:
                    loop = asyncio.get_running_loop()

                    def _invoke_sync():
                        with use_location_context(location_context):
                            return agent.invoke(
                                {"messages": context_messages},
                                config={"recursion_limit": 8},
                            )

                    fallback_result = await loop.run_in_executor(None, _invoke_sync)
                    if isinstance(fallback_result, dict):
                        fallback_text = fallback_result.get("output", "")
                    else:
                        fallback_text = getattr(fallback_result, "content", str(fallback_result))
                    if fallback_text:
                        full_response = fallback_text
                        await output_queue.put({"type": "content", "content": fallback_text})
                        return
                except Exception as fallback_error:
                    print(f"Non-streaming fallback error: {fallback_error!r}")
            await output_queue.put({"type": "error", "content": _friendly_model_error(e)})
        finally:
            await output_queue.put(None)

    agent_task = asyncio.create_task(_agent_worker())

    try:
        while True:
            event = await output_queue.get()
            if event is None:
                break
            yield f"data: {json.dumps(event)}\n\n"
    except GeneratorExit:
        agent_task.cancel()
        try:
            await agent_task
        except asyncio.CancelledError:
            pass
        raise
    finally:
        set_rag_step_queue(None)
        if not agent_task.done():
            agent_task.cancel()

    rag_context = get_last_rag_context(clear=True)
    rag_trace = rag_context.get("rag_trace") if rag_context else None
    trace_base.update(privacy_meta)
    rag_trace = _merge_trace_base(rag_trace, trace_base)
    rag_trace["tool_calls"] = get_tool_audit(clear=True)
    finalized_response, rag_trace = finalize_consultation(
        consultation_state,
        rag_trace,
        full_response,
        fallback_error=agent_error,
    )
    if finalized_response != full_response:
        full_response = finalized_response
        yield f"data: {json.dumps({'type': 'content_replace', 'content': full_response})}\n\n"
    if rag_trace is not None:
        rag_trace["memory_hits"] = {
            key: len(value)
            for key, value in (memory_hits or {}).items()
        }
    rag_trace = _attach_ragas_lite_scores(rag_trace, full_response)

    if rag_trace:
        yield f"data: {json.dumps({'type': 'trace', 'rag_trace': rag_trace})}\n\n"

    yield "data: [DONE]\n\n"

    save_meta = dict(metadata)
    if is_first_message and title_task is not None:
        try:
            save_meta["title"] = await title_task
        except Exception:
            pass

    try:
        save_meta["persistent_note"] = await update_persistent_note(
            persistent_note, redacted_user_text, full_response
        )
    except Exception as e:
        print(f"Update persistent note error: {e}")

    messages.append(AIMessage(content=full_response))
    try:
        memory_service.store_turn(user_id, session_id, redacted_user_text, full_response)
    except Exception as e:
        print(f"Memory write error: {e}")
    extra_message_data = [None] * (len(messages) - 1) + [{"rag_trace": rag_trace}]
    storage.save(
        user_id,
        session_id,
        messages,
        metadata=save_meta,
        extra_message_data=extra_message_data,
    )
