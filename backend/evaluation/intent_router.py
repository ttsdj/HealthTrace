"""Reproducible acceptance metrics for the frozen intent-routing dataset."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from statistics import fmean
from time import perf_counter

from backend.agent.intent_router import Intent, route_intent


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_frozen_intent_cases(path: str | Path, manifest_path: str | Path | None = None) -> list[dict]:
    dataset_path = Path(path)
    if manifest_path:
        manifest = json.loads(Path(manifest_path).read_text(encoding="utf-8"))
        actual = sha256_file(dataset_path)
        if actual != manifest["dataset_sha256"]:
            raise ValueError("frozen intent dataset hash does not match its manifest")
    cases = [
        json.loads(line)
        for line in dataset_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    if len(cases) != 100:
        raise ValueError(f"intent acceptance dataset must contain exactly 100 cases, got {len(cases)}")
    required = {"case_id", "stratum", "query", "expected_intent", "expected_high_risk", "expected_write_requested"}
    for case in cases:
        missing = required - set(case)
        if missing:
            raise ValueError(f"intent case {case.get('case_id', '<unknown>')} missing {sorted(missing)}")
        Intent(case["expected_intent"])
    return cases


def _macro_f1(expected: list[str], predicted: list[str]) -> tuple[float, dict[str, dict[str, float]]]:
    labels = [item.value for item in Intent]
    details: dict[str, dict[str, float]] = {}
    values: list[float] = []
    for label in labels:
        tp = sum(e == label and p == label for e, p in zip(expected, predicted))
        fp = sum(e != label and p == label for e, p in zip(expected, predicted))
        fn = sum(e == label and p != label for e, p in zip(expected, predicted))
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        details[label] = {
            "precision": round(precision, 4),
            "recall": round(recall, 4),
            "f1": round(f1, 4),
            "support": sum(e == label for e in expected),
        }
        values.append(f1)
    return round(fmean(values), 4), details


def evaluate_intent_router(cases: list[dict]) -> dict:
    expected: list[str] = []
    predicted: list[str] = []
    latencies: list[float] = []
    rows: list[dict] = []
    high_risk_total = high_risk_true_positive = 0
    false_write_triggers = 0

    for case in cases:
        started = perf_counter()
        route = route_intent(case["query"])
        elapsed_ms = (perf_counter() - started) * 1000
        actual_intent = route.primary_intent.value
        expected_intent = case["expected_intent"]
        expected.append(expected_intent)
        predicted.append(actual_intent)
        latencies.append(elapsed_ms)
        is_high_risk = bool(case["expected_high_risk"])
        if is_high_risk:
            high_risk_total += 1
            high_risk_true_positive += int(route.primary_intent == Intent.URGENT_CARE)
        if not case["expected_write_requested"] and route.requires_write_confirmation:
            false_write_triggers += 1
        rows.append(
            {
                "case_id": case["case_id"],
                "stratum": case["stratum"],
                "expected_intent": expected_intent,
                "actual_intent": actual_intent,
                "intent_correct": actual_intent == expected_intent,
                "expected_high_risk": is_high_risk,
                "actual_high_risk": route.primary_intent == Intent.URGENT_CARE,
                "expected_write_requested": bool(case["expected_write_requested"]),
                "actual_write_requested": route.requires_write_confirmation,
                "route_source": route.route_source,
                "latency_ms": round(elapsed_ms, 3),
            }
        )
    macro_f1, per_intent = _macro_f1(expected, predicted)
    ordered = sorted(latencies)
    p95 = ordered[max(0, int(len(ordered) * 0.95 + 0.999999) - 1)]
    correct = sum(e == p for e, p in zip(expected, predicted))
    report = {
        "case_count": len(cases),
        "accuracy": round(correct / len(cases), 4),
        "macro_f1": macro_f1,
        "high_risk_recall": round(high_risk_true_positive / high_risk_total, 4) if high_risk_total else None,
        "false_write_trigger_rate": round(false_write_triggers / len(cases), 4),
        "route_latency_ms": {
            "mean": round(fmean(latencies), 3),
            "p95": round(p95, 3),
            "max": round(max(latencies), 3),
        },
        "per_intent": per_intent,
        "route_sources": dict(Counter(row["route_source"] for row in rows)),
        "failed_case_ids": [row["case_id"] for row in rows if not row["intent_correct"]],
        "rows": rows,
    }
    return report


def acceptance_passed(report: dict) -> bool:
    return (
        report["case_count"] == 100
        and report["accuracy"] >= 0.97
        and report["macro_f1"] >= 0.97
        and report["high_risk_recall"] == 1.0
        and report["false_write_trigger_rate"] == 0.0
    )
