from __future__ import annotations

import unicodedata

from langchain_core.documents import Document

from backend.indexing.document_loader import DocumentLoader, sanitize_text
from backend.indexing.ocr import (
    OCRBlock,
    PaddleStructureEngine,
    parse_ppstructure_results,
)
from backend.memory.service import MemoryService, is_semantic_memory_candidate


def test_parse_ppstructure_results_preserves_reading_order_and_metadata():
    result = {
        "res": {
            "parsing_res_list": [
                {
                    "block_content": "| 药品 | 禁忌 |\n|---|---|\n| A | 孕妇 |",
                    "block_label": "table",
                    "block_bbox": [10, 80, 500, 300],
                    "block_order": 2,
                },
                {
                    "block_content": "用药注意事项",
                    "block_label": "paragraph_title",
                    "block_bbox": [10, 10, 500, 60],
                    "block_order": 1,
                },
            ]
        }
    }

    blocks = parse_ppstructure_results([result], page_number=4)

    assert [item.block_type for item in blocks] == ["paragraph_title", "table"]
    assert blocks[0].page_number == 4
    assert blocks[1].bbox == (10.0, 80.0, 500.0, 300.0)


def test_pdf_auto_mode_keeps_native_page_without_images(monkeypatch):
    loader = DocumentLoader()
    native = Document(
        page_content="这是具有足够正文内容的数字 PDF 页面。" * 10,
        metadata={"page": 0},
    )
    monkeypatch.setenv("DOCUMENT_OCR_MODE", "auto")
    monkeypatch.setattr(loader, "_pdf_page_has_images", lambda *_: False)

    routed = loader._route_pdf_pages("unused.pdf", [native])

    assert routed == [native]
    assert routed[0].metadata["extraction_method"] == "native_text"


def test_pdf_auto_mode_routes_sparse_page_to_structured_ocr(monkeypatch):
    loader = DocumentLoader()
    native = Document(page_content="", metadata={"page": 0})
    monkeypatch.setenv("DOCUMENT_OCR_MODE", "auto")
    monkeypatch.setattr(loader, "_pdf_page_has_images", lambda *_: False)
    monkeypatch.setattr(loader, "_render_pdf_page", lambda *_: object())

    class StubEngine:
        def parse(self, image, page_number):
            return [
                OCRBlock("检查结果", "paragraph_title", page_number, 0, (0, 0, 200, 40)),
                OCRBlock(
                    "| 项目 | 结果 |\n|---|---|\n| 血糖 | 5.2 |",
                    "table",
                    page_number,
                    1,
                    (0, 50, 400, 300),
                ),
            ]

    monkeypatch.setattr(
        "backend.indexing.document_loader.get_ocr_engine",
        lambda: StubEngine(),
    )

    routed = loader._route_pdf_pages("unused.pdf", [native])
    chunks = loader._load_from_langchain_docs(routed, "unused.pdf", "report.pdf", "PDF")

    assert len(routed) == 2
    assert {doc.metadata["structure_type"] for doc in routed} == {
        "paragraph_title",
        "table",
    }
    assert all(chunk["extraction_method"] == "paddleocr_ppstructurev3" for chunk in chunks)
    assert any(chunk["structure_type"] == "table" for chunk in chunks)
    assert len({chunk["chunk_id"] for chunk in chunks}) == len(chunks)


def test_sanitize_text_normalises_unicode_to_nfc():
    decomposed = "Cafe\u0301"

    cleaned = sanitize_text(decomposed)

    assert cleaned == "Café"
    assert unicodedata.is_normalized("NFC", cleaned)


def test_memory_metadata_and_semantic_policy_are_explicit():
    doc = MemoryService._memory_doc(
        text="我对青霉素过敏",
        user_id="user-1",
        session_id="session-2",
        memory_type="semantic_memory",
    )

    assert is_semantic_memory_candidate("我对青霉素过敏")
    assert not is_semantic_memory_candidate("今天天气怎么样")
    assert doc["user_id"] == "user-1"
    assert doc["session_id"] == "session-2"
    assert doc["memory_type"] == "semantic_memory"
    assert doc["created_at"]


def test_table_failure_retries_without_table_reconstruction():
    class StubPipeline:
        def __init__(self):
            self.calls = []

        def predict(self, image, **kwargs):
            self.calls.append(kwargs)
            if not kwargs:
                raise ValueError("KMeans n_clusters must be >= 1")
            return [
                {
                    "res": {
                        "parsing_res_list": [
                            {
                                "block_content": "",
                                "block_label": "table",
                                "block_bbox": [0, 0, 300, 100],
                                "block_order": 0,
                            }
                        ],
                        "overall_ocr_res": {
                            "rec_texts": ["白细胞", "6.2"],
                            "rec_boxes": [[10, 20, 90, 45], [180, 20, 230, 45]],
                            "rec_scores": [0.98, 0.96],
                        },
                    }
                }
            ]

    engine = object.__new__(PaddleStructureEngine)
    engine._pipeline = StubPipeline()
    engine._tables_enabled = True

    blocks = engine.parse(object(), page_number=2)

    assert engine._pipeline.calls == [{}, {"use_table_recognition": False}]
    assert blocks[0].text == "白细胞 | 6.2"
    assert blocks[0].block_type == "table_ocr"
    assert blocks[0].fallback_reason == "table_recognition_failed"
