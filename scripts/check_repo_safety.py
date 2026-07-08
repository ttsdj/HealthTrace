"""Pre-commit safety checks for MedRetrieveV2.0.

The script intentionally checks only files tracked or staged by Git. Local
private files may exist, but they must not enter the repository.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

FORBIDDEN_PATH_PREFIXES = (
    "docs/",
    "data/",
    "volumes/",
    "logs/",
    "ablation_results/",
    "frontend/dist/",
)

FORBIDDEN_FILENAMES = {
    ".env",
}

FORBIDDEN_SUFFIXES = (
    ".docx",
    ".log",
    ".sqlite",
    ".db",
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".safetensors",
    ".bin",
)

SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9_.\-]{16,}"),
    re.compile(r"sk-ws-[A-Za-z0-9_.\-]{16,}"),
    re.compile(r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}"),
    re.compile(
        r"(?i)(api[_-]?key|token|secret|password)[\"']?\s*[:=]\s*[\"']"
        r"(?!replace|your_|local_|example|postgres\b)[^\"']{12,}[\"']"
    ),
]

TEXT_SUFFIXES = {
    ".bat",
    ".cmd",
    ".css",
    ".env",
    ".example",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".toml",
    ".ts",
    ".tsx",
    ".vue",
    ".yaml",
    ".yml",
}


def git_lines(*args: str) -> list[str]:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def normalize(path: str) -> str:
    return path.replace("\\", "/")


def is_text_candidate(path: Path) -> bool:
    if path.name == ".env.example":
        return True
    return path.suffix.lower() in TEXT_SUFFIXES


def main() -> int:
    failures: list[str] = []
    tracked = {normalize(path) for path in git_lines("ls-files")}
    staged = {
        normalize(path)
        for path in git_lines("diff", "--cached", "--name-only", "--diff-filter=ACMR")
    }
    paths = sorted(tracked | staged)

    for rel in paths:
        path = Path(rel)
        if path.name in FORBIDDEN_FILENAMES:
            failures.append(f"Forbidden private file is tracked/staged: {rel}")
        if rel.startswith(FORBIDDEN_PATH_PREFIXES):
            failures.append(f"Forbidden local artifact path is tracked/staged: {rel}")
        if path.suffix.lower() in FORBIDDEN_SUFFIXES:
            failures.append(f"Forbidden binary/private suffix is tracked/staged: {rel}")

        full_path = ROOT / rel
        if not full_path.exists() or not is_text_candidate(full_path):
            continue
        try:
            text = full_path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                failures.append(f"Possible secret detected in {rel}: {pattern.pattern}")

    if failures:
        print("Repository safety check failed:\n")
        for item in failures:
            print(f"- {item}")
        return 1

    print("Repository safety check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
