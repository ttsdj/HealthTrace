"""RAGAS 0.4 metric adapters for persisted RAGCare experiment records."""
from __future__ import annotations

import asyncio
import importlib.util
import math
import os
import sys
import time
import types
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from backend.env import load_env

load_env()

RAGAS_METRIC_NAMES = (
    "context_precision",
    "context_recall",
    "faithfulness",
    "answer_relevancy",
    "factual_correctness",
)


def _install_ragas_043_vertex_compatibility() -> None:
    """Work around RAGAS 0.4.3 importing a removed optional Vertex module."""
    module_name = "langchain_community.chat_models.vertexai"
    try:
        if importlib.util.find_spec(module_name) is not None:
            return
    except (ImportError, ModuleNotFoundError):
        pass

    module = types.ModuleType(module_name)
    module.ChatVertexAI = type("ChatVertexAI", (), {})
    sys.modules[module_name] = module


def _ragas_components():
    _install_ragas_043_vertex_compatibility()

    import ragas
    from openai import AsyncOpenAI
    from ragas.cache import DiskCacheBackend
    from ragas.embeddings import HuggingFaceEmbeddings
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        FactualCorrectness,
        Faithfulness,
    )

    return {
        "version": ragas.__version__,
        "AsyncOpenAI": AsyncOpenAI,
        "DiskCacheBackend": DiskCacheBackend,
        "HuggingFaceEmbeddings": HuggingFaceEmbeddings,
        "llm_factory": llm_factory,
        "AnswerRelevancy": AnswerRelevancy,
        "ContextPrecision": ContextPrecision,
        "ContextRecall": ContextRecall,
        "FactualCorrectness": FactualCorrectness,
        "Faithfulness": Faithfulness,
    }


@dataclass(frozen=True)
class RagasSettings:
    base_url: str
    api_key: str
    judge_model: str
    embedding_model: str
    embedding_device: str
    embedding_cache: str | None
    cache_dir: Path
    timeout: float
    max_retries: int

    @classmethod
    def from_env(cls, *, cache_dir: Path | None = None) -> "RagasSettings":
        api_key = os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY") or ""
        if not api_key:
            raise RuntimeError("LLM_API_KEY or ARK_API_KEY is required for RAGAS")
        return cls(
            base_url=os.getenv("BASE_URL", "https://api.deepseek.com"),
            api_key=api_key,
            judge_model=os.getenv("RAGAS_JUDGE_MODEL")
            or os.getenv("EVAL_JUDGE_MODEL")
            or os.getenv("MODEL", "deepseek-v4-flash"),
            embedding_model=os.getenv("RAGAS_EMBEDDING_MODEL")
            or os.getenv("EMBEDDING_MODEL", "BAAI/bge-m3"),
            embedding_device=os.getenv("RAGAS_EMBEDDING_DEVICE")
            or os.getenv("EMBEDDING_DEVICE", "cpu"),
            embedding_cache=os.getenv("HF_HOME") or None,
            cache_dir=cache_dir
            or Path(os.getenv("RAGAS_CACHE_DIR", "data/ragcare/.ragas_cache")),
            timeout=float(os.getenv("RAGAS_TIMEOUT", "180")),
            max_retries=max(int(os.getenv("RAGAS_MAX_RETRIES", "2")), 0),
        )


def _score_value(result) -> float:
    value = float(result.value)
    if not math.isfinite(value):
        raise ValueError(f"RAGAS returned a non-finite score: {value}")
    return round(value, 6)


class RagasMetricSuite:
    """Reusable RAGAS scorers sharing one judge, embedding model, and cache."""

    def __init__(
        self,
        settings: RagasSettings | None = None,
        *,
        metric_names: Iterable[str] = RAGAS_METRIC_NAMES,
    ):
        self.settings = settings or RagasSettings.from_env()
        self.metric_names = tuple(metric_names)
        unknown = set(self.metric_names) - set(RAGAS_METRIC_NAMES)
        if unknown:
            raise ValueError(f"Unsupported RAGAS metrics: {sorted(unknown)}")

        components = _ragas_components()
        self.ragas_version = components["version"]
        self.settings.cache_dir.mkdir(parents=True, exist_ok=True)
        cache = components["DiskCacheBackend"](str(self.settings.cache_dir))
        client = components["AsyncOpenAI"](
            api_key=self.settings.api_key,
            base_url=self.settings.base_url,
            timeout=self.settings.timeout,
            max_retries=self.settings.max_retries,
        )
        self._client = client
        llm = components["llm_factory"](
            self.settings.judge_model,
            provider="openai",
            client=client,
            cache=cache,
            temperature=0,
            max_tokens=4096,
            extra_body={"thinking": {"type": "disabled"}},
            system_prompt=(
                "You are a strict medical RAG evaluator. Score only from the "
                "provided question, evidence, response, and reference."
            ),
        )

        embeddings = None
        if "answer_relevancy" in self.metric_names:
            embeddings = components["HuggingFaceEmbeddings"](
                model=self.settings.embedding_model,
                device=self.settings.embedding_device,
                normalize_embeddings=True,
                batch_size=16,
                cache=cache,
                cache_folder=self.settings.embedding_cache,
                local_files_only=bool(
                    os.getenv("RAGAS_EMBEDDING_LOCAL_ONLY", "true").lower()
                    in {"1", "true", "yes"}
                ),
            )

        factories = {
            "context_precision": lambda: components["ContextPrecision"](llm=llm),
            "context_recall": lambda: components["ContextRecall"](llm=llm),
            "faithfulness": lambda: components["Faithfulness"](llm=llm),
            "answer_relevancy": lambda: components["AnswerRelevancy"](
                llm=llm,
                embeddings=embeddings,
            ),
            "factual_correctness": lambda: components["FactualCorrectness"](
                llm=llm,
                mode="f1",
                atomicity="low",
                coverage="low",
            ),
        }
        self.metrics = {name: factories[name]() for name in self.metric_names}

    async def evaluate_case(self, record: dict) -> dict:
        question = str(record.get("question") or "").strip()
        response = str(record.get("generated_answer") or "").strip()
        reference = str(
            record.get("gold_text_answer") or record.get("gold_answer") or ""
        ).strip()
        contexts = [
            str(item.get("text") or "").strip()
            for item in (record.get("retrieved_docs") or [])
            if str(item.get("text") or "").strip()
        ]
        if not question or not response or not reference or not contexts:
            raise ValueError(
                "RAGAS requires question, generated_answer, reference, and contexts"
            )

        kwargs = {
            "context_precision": {
                "user_input": question,
                "reference": reference,
                "retrieved_contexts": contexts,
            },
            "context_recall": {
                "user_input": question,
                "reference": reference,
                "retrieved_contexts": contexts,
            },
            "faithfulness": {
                "user_input": question,
                "response": response,
                "retrieved_contexts": contexts,
            },
            "answer_relevancy": {
                "user_input": question,
                "response": response,
            },
            "factual_correctness": {
                "response": response,
                "reference": reference,
            },
        }

        scores: dict[str, float] = {}
        reasons: dict[str, str] = {}
        errors: dict[str, str] = {}
        durations: dict[str, float] = {}
        for name in self.metric_names:
            started = time.perf_counter()
            try:
                result = await self.metrics[name].ascore(**kwargs[name])
                scores[name] = _score_value(result)
                if result.reason:
                    reasons[name] = str(result.reason)
            except Exception as exc:
                errors[name] = f"{type(exc).__name__}: {exc}"[:1000]
            durations[name] = round(time.perf_counter() - started, 4)

        valid = [scores[name] for name in self.metric_names if name in scores]
        return {
            "scores": scores,
            "ragas_quality_score": round(sum(valid) / len(valid), 6)
            if valid
            else None,
            "reasons": reasons,
            "errors": errors,
            "durations": durations,
        }

    def evaluate_case_sync(self, record: dict) -> dict:
        return asyncio.run(self.evaluate_case(record))

    async def aclose(self) -> None:
        await self._client.close()
