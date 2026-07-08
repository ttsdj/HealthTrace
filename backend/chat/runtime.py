import os

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI

from backend.tools import (
    get_current_weather,
    search_knowledge_base,
    search_medical_kg,
    search_nearby_medical_care,
)

API_KEY = os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY")
MODEL = os.getenv("MODEL")
FAST_MODEL = os.getenv("FAST_MODEL") or MODEL
BASE_URL = os.getenv("BASE_URL")


def _mask_secret(value: str | None) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 10:
        return "<set>"
    return f"{value[:6]}...{value[-4:]}"


def _log_llm_config() -> None:
    print(
        "[MedRetrieve LLM] "
        f"model={MODEL or '<missing>'}, "
        f"fast_model={FAST_MODEL or '<missing>'}, "
        f"base_url={BASE_URL or '<missing>'}, "
        f"api_key={_mask_secret(API_KEY)}"
    )

SYSTEM_PROMPT = (
    "You are MedRetrieve, a careful medical retrieval assistant for educational decision support. "
    "When responding, you may use tools to assist. "
    "Use search_medical_kg for structured disease, symptom, medicine, food, department, exam, and treatment facts. "
    "Use search_knowledge_base when users ask document or broader medical knowledge questions. "
    "Use search_nearby_medical_care when the user asks where to seek care, needs an in-person examination, "
    "or available evidence is insufficient and this request has verified location authorization. "
    "If location is not authorized, ask the user to enable the location button; never invent a location. "
    "For emergency warning signs, first tell the user to call 120 or seek immediate emergency care; map search must not delay this. "
    "Nearby results are navigation assistance, not diagnosis, hospital quality ranking, registration availability, or a service guarantee. "
    "When both structured KG facts and retrieved chunks are useful, cite both and explain their roles. "
    "If evidence indicates contraindications, caution, high-risk symptoms, dosage questions, or KG/vector disagreement, "
    "state the limitation clearly and avoid definitive diagnosis or prescription-level advice. "
    "Do not call the same tool repeatedly in one turn. At most one knowledge evidence tool call per turn. "
    "After a knowledge tool result, normally produce the final answer. Only when evidence is insufficient "
    "and this request has verified location authorization may you call search_nearby_medical_care once as a second tool. "
    "After a care navigation result, immediately produce the final answer and call no more tools. "
    "If the retrieved context is insufficient, answer honestly that you don't know instead of making up facts. "
    "When answering based on retrieved chunks, you MUST cite the source chunks using their index numbers inline, for example [1] or [2][3]. "
    "For medical topics, remind users that the answer is informational and cannot replace a clinician. "
    "If tool results include a Step-back Question/Answer, use that general principle to reason and answer, "
    "but do not reveal chain-of-thought. "
    "If you don't know the answer, admit it honestly."
)

TOOLS = [
    get_current_weather,
    search_knowledge_base,
    search_medical_kg,
    search_nearby_medical_care,
]

TOOL_BY_NAME = {t.name: t for t in TOOLS}


def _create_model(model_name: str, temperature: float = 0.3) -> ChatOpenAI:
    """创建 OpenAI-compatible ChatOpenAI 实例。"""
    if not API_KEY:
        raise RuntimeError("LLM_API_KEY or ARK_API_KEY is required")
    if not BASE_URL:
        raise RuntimeError("BASE_URL is required")
    if not model_name:
        raise RuntimeError("MODEL is required")
    return ChatOpenAI(
        model=model_name,
        api_key=API_KEY,
        base_url=BASE_URL,
        temperature=temperature,
    )


def _create_fast_model() -> ChatOpenAI:
    return _create_model(FAST_MODEL, temperature=0.2)


class SimpleAgent:
    """一个简单的 tool-calling agent，不依赖 create_agent 的复杂 response_format 逻辑。"""

    def __init__(self, model: ChatOpenAI, tools: list, system_prompt: str):
        self.model = model.bind_tools(tools)
        self.tools = TOOL_BY_NAME
        self.system_prompt = system_prompt

    def invoke(self, input_data: dict, config: dict = None) -> dict:
        messages = input_data.get("messages", [])
        full_messages = [SystemMessage(content=self.system_prompt)] + list(messages)

        result = self.model.invoke(full_messages)
        max_iterations = 4

        for _ in range(max_iterations):
            if not result.tool_calls:
                break

            full_messages.append(result)
            for tc in result.tool_calls:
                tool = self.tools.get(tc["name"])
                if tool is None:
                    obs = f"Tool '{tc['name']}' not found."
                else:
                    try:
                        obs = str(tool.invoke(tc["args"]))
                    except Exception as e:
                        obs = f"工具调用失败：{e}"
                full_messages.append(ToolMessage(content=obs, tool_call_id=tc["id"]))

            result = self.model.invoke(full_messages)

        return {"messages": full_messages, "output": result.content}

    async def astream(self, input_data: dict, stream_mode: str = "messages", config: dict = None):
        """异步流式调用，yield (msg, metadata) 元组。"""
        from langchain_core.messages import AIMessageChunk

        messages = input_data.get("messages", [])
        full_messages = [SystemMessage(content=self.system_prompt)] + list(messages)
        max_iterations = 4

        for _iteration in range(max_iterations):
            gathered = None
            tool_call_chunks: list[dict] = []

            async for chunk in self.model.astream(full_messages):
                # 累积 tool_call_chunks
                if hasattr(chunk, 'tool_call_chunks') and chunk.tool_call_chunks:
                    for tc in chunk.tool_call_chunks:
                        if isinstance(tc, dict) and tc.get("name"):
                            tool_call_chunks.append(tc)

                # 流式输出
                yield chunk, {}

                # 用最后一个有内容的 chunk 更新 gathered
                if chunk.content or (hasattr(chunk, 'tool_call_chunks') and chunk.tool_call_chunks):
                    gathered = chunk

            if gathered is None:
                break

            # 检查是否有 tool calls（从累积的 tool_call_chunks 或 gathered）
            has_tool_calls = bool(tool_call_chunks) or (
                hasattr(gathered, 'tool_calls') and gathered.tool_calls
            )

            if not has_tool_calls:
                break

            # 执行工具调用
            tc_list = gathered.tool_calls if hasattr(gathered, 'tool_calls') and gathered.tool_calls else []
            if not tc_list and tool_call_chunks:
                # 从 tool_call_chunks 重建 tool_calls
                tc_list = []
                current_tc = {}
                for tc in tool_call_chunks:
                    if tc.get("name"):
                        if current_tc:
                            tc_list.append(current_tc)
                        current_tc = {
                            "name": tc.get("name", ""),
                            "args": tc.get("args", {}),
                            "id": tc.get("id", ""),
                        }
                    else:
                        if "args" in tc:
                            current_tc["args"] = str(tc.get("args", ""))
                if current_tc:
                    tc_list.append(current_tc)

            full_messages.append(gathered)
            for tc in tc_list:
                tool = self.tools.get(tc["name"])
                if tool is None:
                    obs = f"Tool '{tc['name']}' not found."
                else:
                    try:
                        obs = str(tool.invoke(tc["args"]))
                    except Exception as e:
                        obs = f"工具调用失败：{e}"
                tc_id = tc.get("id", "")
                full_messages.append(ToolMessage(content=obs, tool_call_id=tc_id if tc_id else None))


_log_llm_config()
model = _create_model(MODEL, temperature=0.3)
fast_model = _create_fast_model()
agent = SimpleAgent(model=model, tools=TOOLS, system_prompt=SYSTEM_PROMPT)
