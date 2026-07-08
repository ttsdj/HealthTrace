"""Import medical QA data into the med_medical_qa Milvus collection.

Supported inputs:
- JSONL/loose JSON lines with disease records from RAGQnASystem medical_new_2.json
- JSONL with question/answer fields
- CSV with question/content and optional answer fields
"""

from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path
from typing import Iterable

from backend.env import load_env
from backend.indexing import MilvusWriter, embedding_service
from backend.indexing.milvus_client import get_milvus_store

load_env()


FIELD_MAP = {
    "desc": ("{name}是什么？", "疾病简介"),
    "cause": ("{name}的病因是什么？", "疾病病因"),
    "prevent": ("如何预防{name}？", "预防措施"),
    "symptom": ("{name}有哪些常见症状？", "常见症状"),
    "check": ("{name}通常需要做哪些检查？", "检查项目"),
    "cure_department": ("{name}应该看什么科室？", "就诊科室"),
    "cure_way": ("{name}有哪些治疗方式？", "治疗方式"),
    "cure_lasttime": ("{name}治疗周期通常多久？", "治疗周期"),
    "cured_prob": ("{name}治愈概率如何？", "治愈概率"),
    "common_drug": ("{name}常用药有哪些？", "常用药"),
    "recommand_drug": ("{name}推荐药品有哪些？", "推荐药品"),
    "do_eat": ("{name}宜吃哪些食物？", "宜吃食物"),
    "not_eat": ("{name}不宜吃哪些食物？", "忌吃食物"),
    "recommand_eat": ("{name}推荐食谱有哪些？", "推荐食谱"),
    "acompany": ("{name}可能有哪些并发疾病？", "并发疾病"),
}


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return "、".join(str(item) for item in value if item)
    return str(value).strip()


def _iter_json_lines(path: Path) -> Iterable[dict]:
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        item = line.strip().rstrip(",")
        if not item or item in {"[", "]"}:
            continue
        try:
            value = json.loads(item)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            yield value


def _iter_json_array(path: Path) -> Iterable[dict]:
    data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    if isinstance(data, list):
        for item in data:
            if isinstance(item, dict):
                yield item


def _iter_csv(path: Path) -> Iterable[dict]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        for row in reader:
            yield dict(row)


def _iter_rows(path: Path) -> Iterable[dict]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        yield from _iter_csv(path)
        return
    try:
        yield from _iter_json_array(path)
    except json.JSONDecodeError:
        yield from _iter_json_lines(path)


def _split_text(text: str, max_chars: int = 1800, overlap: int = 120) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + max_chars, len(text))
        chunks.append(text[start:end].strip())
        if end >= len(text):
            break
        start = max(0, end - overlap)
    return [item for item in chunks if item]


def _qa_docs_from_disease(row: dict, source_name: str) -> Iterable[dict]:
    name = _as_text(row.get("name"))
    if not name:
        return
    categories = _as_text(row.get("category"))
    for field, (question_template, title) in FIELD_MAP.items():
        answer = _as_text(row.get(field))
        if not answer:
            continue
        question = question_template.format(name=name)
        text = f"问题：{question}\n答案：{answer}"
        if categories:
            text += f"\n分类：{categories}"
        for idx, chunk in enumerate(_split_text(text)):
            chunk_id = f"{source_name}::{name}::{field}::{idx}"
            yield {
                "text": chunk,
                "filename": source_name,
                "file_type": "MedicalQA",
                "file_path": "",
                "page_number": 0,
                "chunk_idx": idx,
                "chunk_id": chunk_id,
                "parent_chunk_id": "",
                "root_chunk_id": chunk_id,
                "chunk_level": 3,
                "chunk_kind": "medical_qa",
                "structure_type": "qa_pair",
                "section_path": f"{name} > {title}",
                "source_dataset": source_name,
                "qa_question": question,
                "qa_answer": answer[:1500],
            }


def _qa_docs_from_generic(row: dict, source_name: str, index: int) -> Iterable[dict]:
    question = _as_text(row.get("question") or row.get("content") or row.get("query"))
    answer = _as_text(row.get("answer") or row.get("response") or row.get("label") or row.get("text"))
    if not question and not answer:
        return
    text = f"问题：{question}\n答案：{answer}" if answer else question
    for idx, chunk in enumerate(_split_text(text)):
        chunk_id = f"{source_name}::qa::{index}::{idx}"
        yield {
            "text": chunk,
            "filename": source_name,
            "file_type": "MedicalQA",
            "file_path": "",
            "page_number": 0,
            "chunk_idx": index,
            "chunk_id": chunk_id,
            "parent_chunk_id": "",
            "root_chunk_id": chunk_id,
            "chunk_level": 3,
            "chunk_kind": "medical_qa",
            "structure_type": "qa_pair",
            "section_path": "medical qa",
            "source_dataset": source_name,
            "qa_question": question,
            "qa_answer": answer[:1500],
        }


def build_docs(path: Path, source_name: str, limit: int = 0) -> list[dict]:
    docs: list[dict] = []
    for row_index, row in enumerate(_iter_rows(path), 1):
        if limit and row_index > limit:
            break
        if "name" in row and any(field in row for field in FIELD_MAP):
            docs.extend(_qa_docs_from_disease(row, source_name) or [])
        else:
            docs.extend(_qa_docs_from_generic(row, source_name, row_index) or [])
    return docs


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Path to CSV/JSON/JSONL medical QA data")
    parser.add_argument("--source-name", default="", help="Source label stored in Milvus metadata")
    parser.add_argument("--limit", type=int, default=0, help="Limit source records before expansion")
    parser.add_argument("--batch-size", type=int, default=32)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    input_path = Path(args.input)
    source_name = args.source_name or input_path.stem
    docs = build_docs(input_path, source_name, limit=args.limit)
    print(f"Prepared {len(docs)} QA chunks from {input_path}")
    if args.dry_run:
        for sample in docs[:3]:
            print(json.dumps(sample, ensure_ascii=False)[:600])
        return

    store = get_milvus_store("medical_qa")
    writer = MilvusWriter(embedding_service=embedding_service, milvus_manager=store)
    writer.write_documents(docs, batch_size=args.batch_size)
    print(f"Imported {len(docs)} chunks into {store.collection_name}")


if __name__ == "__main__":
    main()
