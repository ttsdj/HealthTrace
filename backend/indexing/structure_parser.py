"""Lightweight structure parsing for document chunks.

The goal is not to replace OCR/layout engines. It gives the current RAG
pipeline stable metadata for headings, lists, tables and paragraphs so
retrieval traces can explain where a chunk came from.
"""

from __future__ import annotations

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class StructuredTextBlock:
    text: str
    block_type: str
    section_path: str


_HEADING_KEYWORDS = {
    "摘要",
    "引言",
    "背景",
    "目的",
    "方法",
    "结果",
    "结论",
    "讨论",
    "诊断",
    "治疗",
    "症状",
    "病因",
    "检查",
    "用药",
    "预防",
    "参考文献",
}

_HEADING_RE = re.compile(
    r"^(#{1,6}\s+|第[一二三四五六七八九十百千0-9]+[章节篇部分]\s*|"
    r"(\d+|[一二三四五六七八九十]+)([.．、]\d+)*[.．、]\s*)\S+"
)
_LIST_RE = re.compile(r"^(\d+[.)、]|[-*•·])\s*\S+")


def _normalise_line(line: str) -> str:
    return re.sub(r"\s+", " ", line.strip())


def _looks_like_table(line: str) -> bool:
    stripped = line.strip()
    if stripped.count("|") >= 2:
        return True
    if "\t" in stripped:
        return True
    return bool(re.search(r"\S\s{2,}\S\s{2,}\S", stripped))


def _looks_like_heading(line: str) -> bool:
    stripped = _normalise_line(line)
    if not stripped:
        return False
    if _HEADING_RE.match(stripped):
        return True
    trimmed = stripped.strip(":：")
    if trimmed in _HEADING_KEYWORDS:
        return True
    if len(stripped) <= 36 and stripped[-1:] not in "。！？；，、,.!?;":
        return any(word in stripped for word in _HEADING_KEYWORDS)
    return False


def _detect_block_type(line: str) -> str:
    stripped = line.strip()
    if _looks_like_table(stripped):
        return "table"
    if _LIST_RE.match(stripped):
        return "list"
    return "paragraph"


def _update_section_path(current: list[str], heading: str) -> list[str]:
    cleaned = _normalise_line(heading).lstrip("#").strip()
    if not cleaned:
        return current
    # Keep the latest short hierarchy. PDF extraction usually loses true
    # heading levels, so a compact rolling path is more useful than guessing.
    if len(current) >= 2:
        current = current[-1:]
    return [*current, cleaned]


def parse_structured_blocks(text: str) -> list[StructuredTextBlock]:
    """Split extracted page text into structure-aware blocks."""

    blocks: list[StructuredTextBlock] = []
    section_path: list[str] = []
    current_lines: list[str] = []
    current_type = "paragraph"
    in_code = False

    def flush() -> None:
        nonlocal current_lines, current_type
        content = "\n".join(line.rstrip() for line in current_lines).strip()
        if content:
            blocks.append(
                StructuredTextBlock(
                    text=content,
                    block_type=current_type,
                    section_path=" > ".join(section_path),
                )
            )
        current_lines = []
        current_type = "paragraph"

    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()

        if not stripped:
            flush()
            continue

        if stripped.startswith("```"):
            if not in_code:
                flush()
                in_code = True
                current_type = "code"
                current_lines = [line]
            else:
                current_lines.append(line)
                flush()
                in_code = False
            continue

        if in_code:
            current_lines.append(line)
            continue

        if _looks_like_heading(stripped):
            flush()
            section_path = _update_section_path(section_path, stripped)
            blocks.append(
                StructuredTextBlock(
                    text=_normalise_line(stripped),
                    block_type="heading",
                    section_path=" > ".join(section_path),
                )
            )
            continue

        block_type = _detect_block_type(stripped)
        if current_lines and block_type != current_type:
            flush()
        current_type = block_type
        current_lines.append(line)

    flush()
    return blocks
