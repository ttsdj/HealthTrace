"""Optional MinerU open-api CLI integration with timeout-safe fallback."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from langchain_core.documents import Document

from backend.env import load_env

load_env()


class MinerUUnavailableError(RuntimeError):
    """Raised when MinerU is disabled, uninstalled, or not configured."""


class MinerUParseError(RuntimeError):
    """Raised when MinerU is available but parsing fails."""


def mineru_enabled() -> bool:
    return os.getenv("MINERU_ENABLED", "true").strip().lower() in {"1", "true", "yes", "on"}


def mineru_timeout_seconds() -> float:
    return max(1.0, float(os.getenv("MINERU_TIMEOUT_SECONDS", "10")))


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _extract_text_from_json(payload: Any) -> str:
    parts: list[str] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key in ("text", "content", "md", "markdown", "html"):
                item = value.get(key)
                if isinstance(item, str) and item.strip():
                    parts.append(item.strip())
            for item in value.values():
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)

    visit(payload)
    return "\n\n".join(dict.fromkeys(parts))


def _read_best_output(output_dir: Path) -> str:
    markdown_files = sorted(output_dir.rglob("*.md"), key=lambda p: p.stat().st_size, reverse=True)
    if markdown_files:
        return markdown_files[0].read_text(encoding="utf-8", errors="ignore")

    json_files = sorted(output_dir.rglob("*.json"), key=lambda p: p.stat().st_size, reverse=True)
    for json_file in json_files:
        try:
            payload = json.loads(json_file.read_text(encoding="utf-8", errors="ignore"))
        except json.JSONDecodeError:
            continue
        text = _extract_text_from_json(payload)
        if text.strip():
            return text
    return ""


def _mineru_command() -> str:
    command = os.getenv("MINERU_COMMAND", "mineru-open-api").strip() or "mineru-open-api"
    command_path = Path(command)
    if command_path.exists():
        return str(command_path)
    executable = shutil.which(command)
    if executable is None:
        raise MinerUUnavailableError(f"MinerU CLI not found: {command}")
    return executable


def _mineru_mode(file_path: str) -> str:
    mode = os.getenv("MINERU_MODE", "auto").strip().lower()
    if mode in {"extract", "flash"}:
        return mode
    if os.getenv("MINERU_TOKEN", "").strip():
        return "extract"

    path = Path(file_path)
    try:
        file_mb = path.stat().st_size / 1024 / 1024
    except OSError:
        file_mb = 999
    return "flash" if file_mb <= 10 else "extract"


def _build_command(executable: str, file_path: str, output_dir: Path, timeout: float) -> list[str]:
    mode = _mineru_mode(file_path)
    language = os.getenv("MINERU_LANGUAGE", "ch").strip() or "ch"
    cli_timeout = str(max(1, int(timeout)))

    if mode == "flash":
        return [
            executable,
            "flash-extract",
            file_path,
            "-o",
            str(output_dir),
            "--language",
            language,
            "--timeout",
            cli_timeout,
        ]

    args = [
        executable,
        "extract",
        file_path,
        "-o",
        str(output_dir),
        "-f",
        os.getenv("MINERU_OUTPUT_FORMAT", "md"),
        "--model",
        os.getenv("MINERU_MODEL", "pipeline"),
        "--language",
        language,
        "--timeout",
        cli_timeout,
    ]
    if _env_bool("MINERU_OCR", True):
        args.append("--ocr")
    if _env_bool("MINERU_TABLE", True):
        args.append("--table")
    if _env_bool("MINERU_FORMULA", False):
        args.append("--formula")
    return args


def parse_pdf_with_mineru(file_path: str, filename: str) -> list[Document]:
    if not mineru_enabled():
        raise MinerUUnavailableError("MinerU is disabled")

    executable = _mineru_command()
    timeout = mineru_timeout_seconds()
    with tempfile.TemporaryDirectory(prefix="medretrieve_mineru_") as tmp:
        output_dir = Path(tmp)
        args = _build_command(executable, file_path, output_dir, timeout)
        env = os.environ.copy()
        token = env.get("MINERU_TOKEN", "").strip()
        if token:
            env["MINERU_TOKEN"] = token
        try:
            completed = subprocess.run(
                args,
                cwd=str(Path(file_path).parent),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="ignore",
                timeout=timeout + 2,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise TimeoutError(f"MinerU timed out after {timeout:.1f}s") from exc

        if completed.returncode != 0:
            detail = (completed.stderr or completed.stdout or "").strip()[:800]
            raise MinerUParseError(f"MinerU CLI failed: {detail}")

        text = _read_best_output(output_dir)
        if not text.strip():
            text = (completed.stdout or "").strip()
        if not text.strip():
            raise MinerUParseError("MinerU produced no readable text")

        return [
            Document(
                page_content=text,
                metadata={
                    "page": 0,
                    "extraction_method": f"mineru_open_api_{_mineru_mode(file_path)}",
                    "source_filename": filename,
                },
            )
        ]
