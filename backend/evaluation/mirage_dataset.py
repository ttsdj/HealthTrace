"""MIRAGE benchmark download and normalization utilities."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

import requests

DEFAULT_MIRAGE_URLS = (
    "https://raw.githubusercontent.com/gzxiong/MIRAGE/main/benchmark.json",
    "https://raw.githubusercontent.com/Teddy-XiongGZ/MIRAGE/main/benchmark.json",
)
DEFAULT_DATASET_ORDER = ("medqa", "medmcqa", "pubmedqa", "bioasq", "mmlu")


def candidate_benchmark_paths(project_root: Path) -> list[Path]:
    return [
        project_root / "data" / "mirage" / "raw" / "benchmark.json",
        Path("F:/aicoding/MIRAGE/benchmark.json"),
        Path("F:/aicoding/mirage/benchmark.json"),
        Path("F:/aicoding/benchmark.json"),
    ]


def download_benchmark(
    output_path: Path,
    *,
    force: bool = False,
    timeout: float = 120.0,
    urls: Iterable[str] = DEFAULT_MIRAGE_URLS,
) -> Path:
    if output_path.exists() and not force:
        return output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    errors: list[str] = []
    for url in urls:
        try:
            response = requests.get(url, timeout=timeout)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict) or not payload:
                raise ValueError("downloaded payload is not a non-empty JSON object")
            output_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            return output_path
        except Exception as exc:  # noqa: BLE001 - preserve every mirror error.
            errors.append(f"{url}: {exc}")
    raise RuntimeError("Unable to download MIRAGE benchmark: " + " | ".join(errors))


def resolve_benchmark_path(
    project_root: Path,
    data_path: str | Path | None = None,
    *,
    download: bool = False,
    force_download: bool = False,
) -> Path:
    if data_path:
        path = Path(data_path)
        if path.exists() and not force_download:
            return path
        if download:
            return download_benchmark(path, force=force_download)
        raise FileNotFoundError(f"MIRAGE benchmark file not found: {path}")

    for path in candidate_benchmark_paths(project_root):
        if path.exists():
            return path

    default_path = candidate_benchmark_paths(project_root)[0]
    if download:
        return download_benchmark(default_path, force=force_download)
    raise FileNotFoundError(
        "MIRAGE benchmark.json was not found. Place it under "
        f"{default_path} or rerun with --download."
    )


def load_raw_benchmark(path: Path) -> dict:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("MIRAGE benchmark root must be a JSON object")
    return payload


def _normalize_options(options: object) -> dict[str, str]:
    if not isinstance(options, dict) or not options:
        raise ValueError("case options must be a non-empty object")
    normalized: dict[str, str] = {}
    for key, value in options.items():
        label = str(key).strip().upper()
        text = str(value or "").strip()
        if not label or not text:
            continue
        normalized[label] = text
    if not normalized:
        raise ValueError("case options are empty after normalization")
    return dict(sorted(normalized.items()))


def normalize_benchmark(raw: dict) -> list[dict]:
    cases: list[dict] = []
    dataset_names = [
        name for name in DEFAULT_DATASET_ORDER if name in raw
    ] + sorted(name for name in raw if name not in DEFAULT_DATASET_ORDER)
    for dataset in dataset_names:
        rows = raw.get(dataset)
        if not isinstance(rows, dict):
            continue
        for raw_id in sorted(rows):
            item = rows[raw_id]
            if not isinstance(item, dict):
                continue
            question = str(item.get("question") or "").strip()
            options = _normalize_options(item.get("options"))
            answer = str(item.get("answer") or "").strip().upper()
            if not question or answer not in options:
                continue
            case = {
                "query_id": f"mirage-{dataset}-{raw_id}",
                "dataset": dataset,
                "raw_id": str(raw_id),
                "question": question,
                "options": options,
                "gold_answer": answer,
                "gold_answer_text": options[answer],
            }
            if item.get("PMID") is not None:
                case["pmid"] = str(item.get("PMID"))
            cases.append(case)
    if not cases:
        raise ValueError("No valid MIRAGE cases were loaded")
    return cases


def load_mirage_cases(path: Path) -> list[dict]:
    return normalize_benchmark(load_raw_benchmark(path))


def select_cases(
    cases: list[dict],
    *,
    datasets: Iterable[str] | None = None,
    max_cases: int = 0,
    max_cases_per_dataset: int = 0,
) -> list[dict]:
    wanted = {item.strip().lower() for item in datasets or [] if item.strip()}
    selected = [
        case for case in cases if not wanted or str(case["dataset"]).lower() in wanted
    ]
    if max_cases_per_dataset > 0:
        counts: dict[str, int] = {}
        limited: list[dict] = []
        for case in selected:
            dataset = case["dataset"]
            count = counts.get(dataset, 0)
            if count >= max_cases_per_dataset:
                continue
            counts[dataset] = count + 1
            limited.append(case)
        selected = limited
    if max_cases > 0:
        selected = selected[:max_cases]
    return selected
