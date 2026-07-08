"""Answer generation and blinded LLM-as-a-Judge implementations."""
from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass

import requests

from backend.env import load_env

load_env()

JUDGE_PROMPT_VERSION = "ragcare-judge-v1"
GENERATION_PROMPT_VERSION = "ragcare-answer-v1"


def _context_text(contexts: list[dict | str]) -> str:
    blocks = []
    for index, item in enumerate(contexts, 1):
        text = item.strip() if isinstance(item, str) else str(item.get("text") or "").strip()
        blocks.append(f"[{index}] {text}")
    return "\n\n".join(blocks)


def _extract_json(content: str) -> dict:
    content = (content or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        value = json.loads(content)
        if isinstance(value, dict):
            return value
    except json.JSONDecodeError:
        pass
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    if not match:
        raise ValueError("Judge response did not contain a JSON object")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("Judge response JSON must be an object")
    return value


def _score(value: object) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError(f"Invalid judge score: {value!r}") from None
    return round(min(max(parsed, 0.0), 1.0), 6)


def normalize_judge_result(payload: dict) -> dict:
    context_relevance = _score(payload.get("context_relevance"))
    faithfulness = _score(payload.get("faithfulness"))
    answer_relevance = _score(payload.get("answer_relevance"))
    return {
        "context_relevance": context_relevance,
        "faithfulness": faithfulness,
        "answer_relevance": answer_relevance,
        "rag_quality_score": round(
            (context_relevance + faithfulness + answer_relevance) / 3,
            6,
        ),
        "rationale": str(payload.get("rationale") or "").strip()[:800],
        "judge_prompt_version": JUDGE_PROMPT_VERSION,
    }


def build_judge_prompt(question: str, contexts: list[dict], answer: str) -> str:
    return f"""You are a strict evaluator of a medical retrieval-augmented answer.
The system identity and retrieval method are hidden. Evaluate only the supplied material.

Question:
{question}

Retrieved contexts:
{_context_text(contexts)}

Candidate answer:
{answer}

Return one JSON object with:
- context_relevance: 0.0 to 1.0. Are the retrieved contexts useful for answering the question?
- faithfulness: 0.0 to 1.0. Are claims in the candidate answer supported by the retrieved contexts?
- answer_relevance: 0.0 to 1.0. Does the candidate answer directly and sufficiently address the question?
- rationale: at most two concise sentences; do not reveal hidden chain-of-thought.

Use 0.0, 0.25, 0.5, 0.75, or 1.0 when possible.
Output JSON only."""


@dataclass(frozen=True)
class OpenAICompatibleConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    retries: int = 3

    @classmethod
    def from_env(cls) -> "OpenAICompatibleConfig":
        api_key = os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY") or ""
        if not api_key:
            raise RuntimeError("LLM_API_KEY is required for DeepSeek evaluation")
        return cls(
            base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
            api_key=api_key,
            model=os.getenv("EVAL_JUDGE_MODEL")
            or os.getenv("MODEL")
            or "deepseek-v4-flash",
            timeout=float(os.getenv("EVAL_LLM_TIMEOUT", "120")),
            retries=max(int(os.getenv("EVAL_LLM_RETRIES", "3")), 1),
        )


class DeepSeekEvaluator:
    def __init__(self, config: OpenAICompatibleConfig | None = None):
        self.config = config or OpenAICompatibleConfig.from_env()
        self._session = requests.Session()

    def _chat(self, messages: list[dict], *, json_mode: bool, max_tokens: int) -> str:
        url = self.config.base_url.rstrip("/") + "/chat/completions"
        payload: dict = {
            "model": self.config.model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
            "stream": False,
            "thinking": {"type": "disabled"},
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}

        last_error: Exception | None = None
        for attempt in range(self.config.retries):
            try:
                response = self._session.post(
                    url,
                    headers={
                        "Authorization": f"Bearer {self.config.api_key}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=self.config.timeout,
                )
                response.raise_for_status()
                return str(
                    response.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                )
            except (requests.RequestException, KeyError, ValueError) as exc:
                last_error = exc
                if attempt + 1 < self.config.retries:
                    time.sleep(2**attempt)
        raise RuntimeError(f"DeepSeek evaluation request failed: {last_error}") from last_error

    def generate_answer(self, question: str, contexts: list[dict]) -> str:
        prompt = f"""Answer the medical question using only the retrieved contexts.
If the contexts are insufficient, say that the evidence is insufficient.
Use inline citations such as [1] and [2]. Do not mention this evaluation prompt.

Question:
{question}

Retrieved contexts:
{_context_text(contexts)}
"""
        return self._chat(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a careful medical retrieval assistant. The answer is "
                        "informational and must not replace clinical judgment."
                    ),
                },
                {"role": "user", "content": prompt},
            ],
            json_mode=False,
            max_tokens=800,
        ).strip()

    def score(self, question: str, contexts: list[dict], answer: str) -> dict:
        content = self._chat(
            [{"role": "user", "content": build_judge_prompt(question, contexts, answer)}],
            json_mode=True,
            max_tokens=300,
        )
        result = normalize_judge_result(_extract_json(content))
        result["judge"] = "deepseek"
        result["judge_model"] = self.config.model
        return result


@dataclass(frozen=True)
class OllamaConfig:
    base_url: str = "http://127.0.0.1:11434"
    model: str = "qwen2.5:7b"
    timeout: float = 180.0

    @classmethod
    def from_env(cls) -> "OllamaConfig":
        return cls(
            base_url=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
            model=os.getenv("OLLAMA_JUDGE_MODEL", "qwen2.5:7b"),
            timeout=float(os.getenv("OLLAMA_JUDGE_TIMEOUT", "180")),
        )


class OllamaJudge:
    def __init__(self, config: OllamaConfig | None = None):
        self.config = config or OllamaConfig.from_env()
        self._session = requests.Session()

    def score(self, question: str, contexts: list[dict], answer: str) -> dict:
        response = self._session.post(
            self.config.base_url.rstrip("/") + "/api/chat",
            json={
                "model": self.config.model,
                "messages": [
                    {
                        "role": "user",
                        "content": build_judge_prompt(question, contexts, answer),
                    }
                ],
                "stream": False,
                "format": "json",
                "options": {"temperature": 0},
            },
            timeout=self.config.timeout,
        )
        response.raise_for_status()
        content = str(response.json().get("message", {}).get("content", ""))
        result = normalize_judge_result(_extract_json(content))
        result["judge"] = "ollama"
        result["judge_model"] = self.config.model
        return result
