"""Optional PaddleOCR-backed document image parsing.

The module deliberately imports PaddleOCR lazily. Digital documents keep using
their native text layer, while scanned or image-heavy pages can be routed to
PP-StructureV3 without making the large OCR runtime a mandatory dependency.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import os
from pathlib import Path
from typing import Any, Iterable


IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff")


class OCRUnavailableError(RuntimeError):
    """Raised when OCR is required but its optional runtime is unavailable."""


@dataclass(frozen=True)
class OCRBlock:
    text: str
    block_type: str
    page_number: int
    block_order: int
    bbox: tuple[float, float, float, float] | None = None
    confidence: float | None = None
    fallback_reason: str | None = None


def _as_dict(result: Any) -> dict[str, Any]:
    payload = getattr(result, "json", result)
    if callable(payload):
        payload = payload()
    if not isinstance(payload, dict):
        return {}
    nested = payload.get("res")
    return nested if isinstance(nested, dict) else payload


def _normalise_bbox(value: Any) -> tuple[float, float, float, float] | None:
    if value is None:
        return None
    if hasattr(value, "tolist"):
        value = value.tolist()
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        return None
    try:
        return tuple(round(float(item), 2) for item in value)
    except (TypeError, ValueError):
        return None


def _table_text_from_ocr(
    overall_ocr: Any,
    table_bbox: tuple[float, float, float, float] | None,
) -> tuple[str, float | None]:
    """Recover table text lines when structural table reconstruction fails."""

    if not isinstance(overall_ocr, dict) or table_bbox is None:
        return "", None
    texts = overall_ocr.get("rec_texts")
    boxes = overall_ocr.get("rec_boxes")
    scores = overall_ocr.get("rec_scores")
    texts = [] if texts is None else texts
    boxes = [] if boxes is None else boxes
    scores = [] if scores is None else scores
    if hasattr(boxes, "tolist"):
        boxes = boxes.tolist()
    if hasattr(scores, "tolist"):
        scores = scores.tolist()

    left, top, right, bottom = table_bbox
    cells: list[tuple[float, float, float, str, float | None]] = []
    for index, raw_text in enumerate(texts):
        text = str(raw_text or "").strip()
        if not text or index >= len(boxes):
            continue
        box = _normalise_bbox(boxes[index])
        if box is None:
            continue
        x0, y0, x1, y1 = box
        center_x = (x0 + x1) / 2
        center_y = (y0 + y1) / 2
        if not (left <= center_x <= right and top <= center_y <= bottom):
            continue
        try:
            score = float(scores[index]) if index < len(scores) else None
        except (TypeError, ValueError):
            score = None
        cells.append((center_y, x0, max(y1 - y0, 1.0), text, score))

    if not cells:
        return "", None
    cells.sort(key=lambda item: (item[0], item[1]))
    rows: list[list[tuple[float, float, float, str, float | None]]] = []
    for cell in cells:
        if not rows:
            rows.append([cell])
            continue
        row_center = sum(item[0] for item in rows[-1]) / len(rows[-1])
        tolerance = max(cell[2], max(item[2] for item in rows[-1])) * 0.65
        if abs(cell[0] - row_center) <= tolerance:
            rows[-1].append(cell)
        else:
            rows.append([cell])

    text_rows = [
        " | ".join(item[3] for item in sorted(row, key=lambda cell: cell[1]))
        for row in rows
    ]
    valid_scores = [
        item[4]
        for row in rows
        for item in row
        if item[4] is not None
    ]
    confidence = (
        sum(valid_scores) / len(valid_scores)
        if valid_scores
        else None
    )
    return "\n".join(text_rows), confidence


def parse_ppstructure_results(
    results: Iterable[Any],
    page_number: int,
    fallback_reason: str | None = None,
) -> list[OCRBlock]:
    """Convert PP-StructureV3 output into a stable internal block model."""

    blocks: list[OCRBlock] = []
    fallback_order = 0
    for result in results:
        payload = _as_dict(result)
        parsing_items = payload.get("parsing_res_list") or []
        for item in parsing_items:
            if not isinstance(item, dict):
                continue
            text = str(item.get("block_content") or "").strip()
            block_type = str(item.get("block_label") or "text")
            bbox = _normalise_bbox(item.get("block_bbox"))
            recovered_confidence = None
            if not text and block_type == "table":
                text, recovered_confidence = _table_text_from_ocr(
                    payload.get("overall_ocr_res"),
                    bbox,
                )
                if text:
                    block_type = "table_ocr"
            if not text:
                continue
            raw_order = item.get("block_order")
            if raw_order is None:
                raw_order = item.get("block_id", fallback_order)
            try:
                order = int(raw_order)
            except (TypeError, ValueError):
                order = fallback_order
            fallback_order += 1
            score = item.get("score")
            try:
                confidence = float(score) if score is not None else None
            except (TypeError, ValueError):
                confidence = None
            if confidence is None:
                confidence = recovered_confidence
            blocks.append(
                OCRBlock(
                    text=text,
                    block_type=block_type,
                    page_number=page_number,
                    block_order=order,
                    bbox=bbox,
                    confidence=confidence,
                    fallback_reason=fallback_reason,
                )
            )
    return sorted(blocks, key=lambda block: block.block_order)


class PaddleStructureEngine:
    """Lazy local PP-StructureV3 wrapper."""

    def __init__(self) -> None:
        project_root = Path(__file__).resolve().parents[2]
        default_cache = project_root / "data" / "model_cache" / "paddlex"
        configured_cache = Path(
            os.getenv("DOCUMENT_OCR_CACHE_DIR", str(default_cache))
        )
        if not configured_cache.is_absolute():
            configured_cache = project_root / configured_cache
        os.environ.setdefault(
            "PADDLE_PDX_CACHE_HOME",
            str(configured_cache),
        )
        os.environ.setdefault(
            "PADDLE_PDX_MODEL_SOURCE",
            os.getenv("DOCUMENT_OCR_MODEL_SOURCE", "BOS"),
        )
        try:
            from paddleocr import PPStructureV3
        except ImportError as exc:
            raise OCRUnavailableError(
                "需要 OCR，但尚未安装 PaddleOCR。请执行 "
                "`python -m pip install -e \".[ocr]\"`，模型首次运行还会下载。"
            ) from exc

        device = os.getenv("DOCUMENT_OCR_DEVICE", "cpu").strip() or "cpu"
        language = os.getenv("DOCUMENT_OCR_LANG", "ch").strip() or "ch"
        engine = os.getenv("DOCUMENT_OCR_ENGINE", "transformers").strip() or "transformers"
        self._tables_enabled = _env_bool("DOCUMENT_OCR_TABLES", True)
        self._pipeline = PPStructureV3(
            lang=language,
            device=device,
            engine=engine,
            use_doc_orientation_classify=True,
            use_textline_orientation=True,
            use_table_recognition=self._tables_enabled,
            use_formula_recognition=_env_bool("DOCUMENT_OCR_FORMULAS", False),
            use_chart_recognition=_env_bool("DOCUMENT_OCR_CHARTS", False),
        )

    def parse(self, image: Any, page_number: int = 0) -> list[OCRBlock]:
        try:
            results = self._pipeline.predict(image)
            return parse_ppstructure_results(results, page_number)
        except Exception as exc:
            if self._tables_enabled:
                try:
                    results = self._pipeline.predict(
                        image,
                        use_table_recognition=False,
                    )
                    blocks = parse_ppstructure_results(
                        results,
                        page_number,
                        fallback_reason="table_recognition_failed",
                    )
                    if blocks:
                        return blocks
                except Exception as fallback_exc:
                    raise RuntimeError(
                        "PaddleOCR 页面解析失败，禁用表格重建后仍失败: "
                        f"primary={exc}; fallback={fallback_exc}"
                    ) from fallback_exc
            raise RuntimeError(f"PaddleOCR 页面解析失败: {exc}") from exc


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@lru_cache(maxsize=1)
def get_ocr_engine() -> PaddleStructureEngine:
    return PaddleStructureEngine()
