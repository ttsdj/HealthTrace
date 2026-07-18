"""MIRAGE benchmark execution and output writers."""
from __future__ import annotations

import csv
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable

import requests

from backend.evaluation.mirage_metrics import score_record, summarize_records

MIRAGE_PROMPT_VERSION = "mirage-choice-v1"
SUPPORTED_MODES = ("llm_only", "rag_agent")


def _context_text(contexts: list[dict]) -> str:
    blocks = []
    for index, item in enumerate(contexts, 1):
        text = str(item.get("text") or "").strip()
        source = str(item.get("filename") or item.get("chunk_id") or "").strip()
        header = f"[{index}] {source}" if source else f"[{index}]"
        blocks.append(f"{header}\n{text}")
    return "\n\n".join(blocks)


def build_choice_prompt(case: dict, contexts: list[dict] | None = None) -> str:
    options = "\n".join(
        f"{label}. {text}" for label, text in (case.get("options") or {}).items()
    )
    context_block = ""
    if contexts is not None:
        context_block = (
            "Retrieved medical context. Use it when relevant, but do not ignore the "
            "question stem and options:\n"
            f"{_context_text(contexts) if contexts else '[No retrieved context]'}\n\n"
        )
    return f"""You are answering a medical benchmark multiple-choice question.
Return JSON only, with exactly these keys:
{{"answer": "A", "confidence": 0.0}}

{context_block}Question:
{case["question"]}

Options:
{options}

Choose the single best option label from: {", ".join(case["options"])}."""


@dataclass(frozen=True)
class OpenAICompatibleChoiceConfig:
    base_url: str
    api_key: str
    model: str
    timeout: float = 120.0
    retries: int = 2

    @classmethod
    def from_env(cls) -> "OpenAICompatibleChoiceConfig":
        api_key = os.getenv("LLM_API_KEY") or os.getenv("ARK_API_KEY") or ""
        if not api_key:
            raise RuntimeError("LLM_API_KEY or ARK_API_KEY is required for MIRAGE")
        return cls(
            base_url=os.getenv("BASE_URL", "").strip()
            or "https://api.deepseek.com/v1",
            api_key=api_key,
            model=os.getenv("MIRAGE_MODEL")
            or os.getenv("MODEL")
            or "deepseek-v4-flash",
            timeout=float(os.getenv("MIRAGE_LLM_TIMEOUT", "120")),
            retries=max(int(os.getenv("MIRAGE_LLM_RETRIES", "2")), 1),
        )


class ChoiceLLMClient:
    def __init__(self, config: OpenAICompatibleChoiceConfig | None = None):
        self.config = config or OpenAICompatibleChoiceConfig.from_env()
        self._session = requests.Session()

    def answer(self, case: dict, contexts: list[dict] | None = None) -> str:
        payload: dict = {
            "model": self.config.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are a careful medical exam assistant. "
                        "Answer with the requested JSON object only."
                    ),
                },
                {"role": "user", "content": build_choice_prompt(case, contexts)},
            ],
            "temperature": 0,
            "max_tokens": int(os.getenv("MIRAGE_MAX_TOKENS", "300")),
            "stream": False,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
        }
        last_error: Exception | None = None
        url = self.config.base_url.rstrip("/") + "/chat/completions"
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
                if response.status_code >= 400 and "response_format" in response.text:
                    payload.pop("response_format", None)
                    response = self._session.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {self.config.api_key}",
                            "Content-Type": "application/json",
                        },
                        json=payload,
                        timeout=self.config.timeout,
                    )
                if response.status_code >= 400:
                    body = response.text.replace("\n", " ")[:500]
                    raise RuntimeError(f"HTTP {response.status_code}: {body}")
                content = str(
                    response.json()
                    .get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                ).strip()
                if content:
                    return content
                raise RuntimeError("empty_model_content")
            except Exception as exc:  # noqa: BLE001 - benchmark records the failure.
                last_error = exc
                if attempt + 1 < self.config.retries:
                    time.sleep(2**attempt)
        raise RuntimeError(f"MIRAGE LLM request failed: {last_error}") from last_error


def retrieve_contexts(question: str, *, top_k: int) -> tuple[list[dict], dict]:
    from backend.rag.utils import retrieve_documents

    result = retrieve_documents(question, top_k=top_k)
    return list(result.get("docs") or []), dict(result.get("meta") or {})


def run_case(
    case: dict,
    *,
    mode: str,
    llm_client: ChoiceLLMClient,
    top_k: int = 5,
    retriever: Callable[[str], tuple[list[dict], dict]] | None = None,
) -> dict:
    started = time.perf_counter()
    contexts: list[dict] | None = None
    retrieval_meta: dict = {}
    error = ""
    try:
        if mode == "rag_agent":
            if retriever is None:
                contexts, retrieval_meta = retrieve_contexts(case["question"], top_k=top_k)
            else:
                contexts, retrieval_meta = retriever(case["question"])
        raw_response = llm_client.answer(case, contexts if mode == "rag_agent" else None)
    except Exception as exc:  # noqa: BLE001 - keep the batch alive.
        raw_response = ""
        error = str(exc)[:1000]

    record = {
        **case,
        "mode": mode,
        "raw_response": raw_response,
        "retrieved_contexts": contexts or [],
        "retrieval_meta": retrieval_meta,
        "error": error,
        "latency_seconds": round(time.perf_counter() - started, 4),
    }
    record.update(score_record(record))
    return record


def run_benchmark(
    cases: list[dict],
    *,
    mode: str,
    llm_client: ChoiceLLMClient,
    top_k: int = 5,
    workers: int = 2,
    retriever: Callable[[str], tuple[list[dict], dict]] | None = None,
    progress_callback: Callable[[int, int, dict, dict], None] | None = None,
) -> list[dict]:
    if mode not in SUPPORTED_MODES:
        raise ValueError(f"Unsupported MIRAGE mode: {mode}")
    if not cases:
        raise ValueError("No MIRAGE cases selected")

    records: list[dict] = []
    totals = {"correct": 0, "invalid": 0, "failures": 0}
    with ThreadPoolExecutor(max_workers=max(workers, 1)) as executor:
        futures = [
            executor.submit(
                run_case,
                case,
                mode=mode,
                llm_client=llm_client,
                top_k=top_k,
                retriever=retriever,
            )
            for case in cases
        ]
        for future in as_completed(futures):
            record = future.result()
            records.append(record)
            if record.get("is_correct"):
                totals["correct"] += 1
            if not record.get("is_valid"):
                totals["invalid"] += 1
            if record.get("error"):
                totals["failures"] += 1
            if progress_callback:
                progress_callback(len(records), len(cases), dict(totals), record)
    records.sort(key=lambda item: (item.get("dataset", ""), item.get("raw_id", "")))
    return records


def write_outputs(output_dir: Path, *, config: dict, records: list[dict]) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    summary = summarize_records(records)
    (output_dir / "config.json").write_text(
        json.dumps(config, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (output_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    with (output_dir / "per_case.jsonl").open("w", encoding="utf-8", newline="\n") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")

    csv_fields = [
        "dataset",
        "raw_id",
        "mode",
        "gold_answer",
        "predicted_answer",
        "is_correct",
        "is_valid",
        "latency_seconds",
        "error",
    ]
    with (output_dir / "per_case.csv").open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=csv_fields)
        writer.writeheader()
        for record in records:
            writer.writerow({key: record.get(key) for key in csv_fields})

    report_lines = [
        "# MIRAGE Benchmark Report",
        "",
        f"- Experiment: `{config.get('experiment_id')}`",
        f"- Mode: `{config.get('mode')}`",
        f"- Model: `{config.get('model')}`",
        f"- Cases: `{summary['overall']['cases']}`",
        f"- Overall accuracy: `{summary['overall']['accuracy']}`",
        f"- Macro accuracy: `{summary['macro_accuracy']}`",
        f"- Invalid answer rate: `{summary['overall']['invalid_answer_rate']}`",
        f"- Failure rate: `{summary['overall']['failure_rate']}`",
        "",
        "## Dataset Accuracy",
        "",
        "| Dataset | Cases | Accuracy | Invalid rate | Failure rate | Mean latency(s) |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for dataset, row in summary["datasets"].items():
        report_lines.append(
            f"| {dataset} | {row['cases']} | {row['accuracy']} | "
            f"{row['invalid_answer_rate']} | {row['failure_rate']} | "
            f"{row['mean_latency_seconds']} |"
        )
    report_lines.extend(
        [
            "",
            "## Notes",
            "",
            "- MIRAGE retrieval follows QOR: retrieval receives only the question text; options are used only by the answer generator.",
            "- Accuracy is deterministic after extracting the predicted option label from the model response.",
        ]
    )
    (output_dir / "report.md").write_text("\n".join(report_lines) + "\n", encoding="utf-8")
    return summary
