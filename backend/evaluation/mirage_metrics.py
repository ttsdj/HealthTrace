"""Deterministic metrics and answer extraction for MIRAGE."""
from __future__ import annotations

import json
import re
from collections import defaultdict
from typing import Iterable


def _normalize_text(value: str) -> str:
    value = (value or "").strip().lower()
    value = re.sub(r"\s+", " ", value)
    return value


def _json_answer(text: str) -> str:
    content = (text or "").strip()
    if content.startswith("```"):
        content = re.sub(r"^```(?:json)?\s*", "", content)
        content = re.sub(r"\s*```$", "", content)
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", content, flags=re.DOTALL)
        if not match:
            return ""
        try:
            payload = json.loads(match.group(0))
        except json.JSONDecodeError:
            return ""
    if not isinstance(payload, dict):
        return ""
    for key in ("answer", "choice", "option", "label"):
        value = str(payload.get(key) or "").strip().upper()
        if value:
            return value[:1]
    return ""


def extract_choice(response_text: str, options: dict[str, str]) -> str | None:
    labels = set(options)
    json_choice = _json_answer(response_text)
    if json_choice in labels:
        return json_choice

    text = response_text or ""
    patterns = (
        r"(?:answer|choice|option|答案|选项)\s*(?:is|:|为|是)?\s*[\(\[]?\s*([A-Z])\s*[\)\]]?",
        r"^\s*[\(\[]?\s*([A-Z])\s*[\)\]]?[\s\.:：、-]",
        r"\b([A-Z])\b",
    )
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            label = match.group(1).upper()
            if label in labels:
                return label

    lowered = _normalize_text(text)
    yes_no_map = {
        "yes": "yes",
        "no": "no",
        "maybe": "maybe",
    }
    option_by_text = {
        _normalize_text(option_text): label for label, option_text in options.items()
    }
    for word in yes_no_map:
        if re.search(rf"\b{word}\b", lowered):
            label = option_by_text.get(word)
            if label:
                return label
    for option_text, label in option_by_text.items():
        if option_text and option_text in lowered:
            return label
    return None


def score_record(record: dict) -> dict:
    extracted = extract_choice(record.get("raw_response", ""), record.get("options") or {})
    gold = str(record.get("gold_answer") or "").strip().upper()
    return {
        "predicted_answer": extracted,
        "is_valid": extracted is not None,
        "is_correct": bool(extracted and extracted == gold),
    }


def summarize_records(records: Iterable[dict]) -> dict:
    rows = list(records)
    by_dataset: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_dataset[str(row.get("dataset") or "unknown")].append(row)

    def _summary(selected: list[dict]) -> dict:
        total = len(selected)
        correct = sum(1 for row in selected if row.get("is_correct"))
        invalid = sum(1 for row in selected if not row.get("is_valid"))
        failures = sum(1 for row in selected if row.get("error"))
        latencies = [
            float(row["latency_seconds"])
            for row in selected
            if row.get("latency_seconds") is not None
        ]
        return {
            "cases": total,
            "correct": correct,
            "accuracy": round(correct / total, 6) if total else None,
            "invalid_answers": invalid,
            "invalid_answer_rate": round(invalid / total, 6) if total else None,
            "failures": failures,
            "failure_rate": round(failures / total, 6) if total else None,
            "mean_latency_seconds": (
                round(sum(latencies) / len(latencies), 6) if latencies else None
            ),
        }

    datasets = {
        dataset: _summary(selected)
        for dataset, selected in sorted(by_dataset.items())
    }
    dataset_accuracies = [
        value["accuracy"] for value in datasets.values() if value["accuracy"] is not None
    ]
    return {
        "overall": _summary(rows),
        "macro_accuracy": (
            round(sum(dataset_accuracies) / len(dataset_accuracies), 6)
            if dataset_accuracies
            else None
        ),
        "datasets": datasets,
    }
