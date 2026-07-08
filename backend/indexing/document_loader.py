"""文档加载和分片服务"""
import os
import re
import unicodedata
from typing import Dict, List

from langchain_core.documents import Document
from langchain_community.document_loaders import Docx2txtLoader, PyPDFLoader, UnstructuredExcelLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter

from backend.indexing.ocr import (
    IMAGE_EXTENSIONS,
    OCRBlock,
    OCRUnavailableError,
    get_ocr_engine,
)
from backend.indexing.mineru_parser import (
    MinerUParseError,
    MinerUUnavailableError,
    parse_pdf_with_mineru,
)
from backend.indexing.structure_parser import StructuredTextBlock, parse_structured_blocks

# 编译非打印 C0/C1 控制字符的正则（保留常规排版字：\t, \n, \r）
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
# 编译零宽字符和不可见格式化控制字符（零宽空白、BOM 标记、左右强排标志等）
_INVISIBLE_CHAR_RE = re.compile(r"[\u200b-\u200d\ufeff\u200f\u202a-\u202e]")


def sanitize_text(text: str) -> str:
    """
    企业级标准文本净化器 (Text Sanitizer)。
    1. 规范化 (Normalization)：统一转换为标准 NFC 格式，合并分离变音符和音调，确保多端字符表示合一。
    2. 剔除/替换不合法及不可见字节：过滤 NUL (0x00) 空字节、零宽字符、BOM 标签和不可见强排标记。
    3. 清洗非打印字符及乱码：剔除 C0/C1 控制符号，剥离 Unicode PUA 私有使用区乱码符号。
    4. 编码收敛防爆：利用 utf-8 ignore 安全剥离任何不完整的、孤立的 UTF-16 代理项（Surrogates）。
    """
    if not text:
        return ""
    
    # 1. 规范化为 Unicode NFC 格式
    text = unicodedata.normalize("NFC", text)
    
    # 2. 清除不可见零宽字符、BOM 及格式控制符
    text = _INVISIBLE_CHAR_RE.sub("", text)
    
    # 3. 清洗非打印控制符及 PUA 乱码框区字符
    text = _CONTROL_CHAR_RE.sub("", text)
    text = re.sub(r"[\ue000-\uf8ff]", "", text)
    
    # 4. 彻底擦除孤立代理项 (Surrogates)，收敛至 100% 合规的 UTF-8 (对应 PostgreSQL 的 utf8mb4 标准)
    try:
        cleaned = text.encode("utf-8", "ignore").decode("utf-8", "ignore")
    except Exception:
        chars = []
        for char in text:
            if 0xD800 <= ord(char) <= 0xDFFF:
                continue
            chars.append(char)
        cleaned = "".join(chars)
        
    return cleaned


class DocumentLoader:
    """文档加载和分片服务"""

    def __init__(self, chunk_size: int = 800, chunk_overlap: int = 100):
        level_1_size = max(2000, chunk_size * 3)
        level_1_overlap = max(400, chunk_overlap * 3)
        level_2_size = max(1000, chunk_size * 2)
        level_2_overlap = max(200, chunk_overlap * 2)
        level_3_size = max(600, chunk_size)
        level_3_overlap = max(100, chunk_overlap)
        self._paragraph_group_size = level_1_size

        self._splitter_level_1 = RecursiveCharacterTextSplitter(
            chunk_size=level_1_size,
            chunk_overlap=level_1_overlap,
            add_start_index=True,
            separators=["\n\n", "。", "！", "？", "\n", "，", "、", " ", ""],
        )
        self._splitter_level_2 = RecursiveCharacterTextSplitter(
            chunk_size=level_2_size,
            chunk_overlap=level_2_overlap,
            add_start_index=True,
            separators=["\n\n", "。", "！", "？", "\n", "，", "、", " ", ""],
        )
        self._splitter_level_3 = RecursiveCharacterTextSplitter(
            chunk_size=level_3_size,
            chunk_overlap=level_3_overlap,
            add_start_index=True,
            separators=["\n\n", "。", "！", "？", "\n", "，", "、", " ", ""],
        )

    @staticmethod
    def _build_chunk_id(
        filename: str,
        page_number: int,
        level: int,
        index: int,
        scope: str = "",
    ) -> str:
        scope_part = f"::{scope}" if scope else ""
        return f"{filename}::p{page_number}{scope_part}::l{level}::{index}"

    def _paragraph_groups(self, text: str) -> list[str]:
        paragraphs = [item.strip() for item in text.splitlines() if item.strip()]
        if not paragraphs:
            return [text.strip()] if text.strip() else []

        groups: list[str] = []
        current: list[str] = []
        current_len = 0
        for para in paragraphs:
            para_len = len(para)
            if current and current_len + para_len > self._paragraph_group_size:
                groups.append("\n".join(current))
                current = []
                current_len = 0
            current.append(para)
            current_len += para_len
        if current:
            groups.append("\n".join(current))
        return groups

    def _split_page_to_three_levels(
        self,
        text: str,
        base_doc: Dict,
        page_global_chunk_idx: int,
        structured_blocks: list[StructuredTextBlock] | None = None,
        chunk_scope: str = "",
    ) -> List[Dict]:
        if not text:
            return []

        root_chunks: List[Dict] = []
        page_number = int(base_doc.get("page_number", 0))
        filename = base_doc["filename"]
        structured_blocks = structured_blocks or parse_structured_blocks(text)
        if not structured_blocks:
            structured_blocks = [
                StructuredTextBlock(
                    text=text,
                    block_type="paragraph",
                    section_path="",
                )
            ]
        level_1_counter = 0
        level_2_counter = 0
        level_3_counter = 0

        for block in structured_blocks:
            block_doc = {
                **base_doc,
                "section_path": block.section_path,
                "chunk_kind": block.block_type,
                "structure_type": block.block_type,
            }
            paragraph_groups = self._paragraph_groups(block.text)
            for paragraph_group_idx, paragraph_group in enumerate(paragraph_groups):
                paragraph_doc = {
                    **block_doc,
                    "paragraph_group_idx": paragraph_group_idx,
                    "chunk_strategy": "paragraph_then_semantic_recursive",
                }
                level_1_docs = self._splitter_level_1.create_documents([paragraph_group], [paragraph_doc])

                for level_1_doc in level_1_docs:
                    level_1_text = (level_1_doc.page_content or "").strip()
                    if not level_1_text:
                        continue
                    level_1_id = self._build_chunk_id(
                        filename, page_number, 1, level_1_counter, chunk_scope
                    )
                    level_1_counter += 1

                    level_1_chunk = {
                        **paragraph_doc,
                        "text": level_1_text,
                        "chunk_id": level_1_id,
                        "parent_chunk_id": "",
                        "root_chunk_id": level_1_id,
                        "chunk_level": 1,
                        "chunk_idx": page_global_chunk_idx,
                    }
                    page_global_chunk_idx += 1
                    root_chunks.append(level_1_chunk)

                    level_2_docs = self._splitter_level_2.create_documents([level_1_text], [paragraph_doc])
                    for level_2_doc in level_2_docs:
                        level_2_text = (level_2_doc.page_content or "").strip()
                        if not level_2_text:
                            continue
                        level_2_id = self._build_chunk_id(
                            filename, page_number, 2, level_2_counter, chunk_scope
                        )
                        level_2_counter += 1

                        level_2_chunk = {
                            **paragraph_doc,
                            "text": level_2_text,
                            "chunk_id": level_2_id,
                            "parent_chunk_id": level_1_id,
                            "root_chunk_id": level_1_id,
                            "chunk_level": 2,
                            "chunk_idx": page_global_chunk_idx,
                        }
                        page_global_chunk_idx += 1
                        root_chunks.append(level_2_chunk)

                        level_3_docs = self._splitter_level_3.create_documents([level_2_text], [paragraph_doc])
                        for level_3_doc in level_3_docs:
                            level_3_text = (level_3_doc.page_content or "").strip()
                            if not level_3_text:
                                continue
                            level_3_id = self._build_chunk_id(
                                filename, page_number, 3, level_3_counter, chunk_scope
                            )
                            level_3_counter += 1
                            root_chunks.append({
                                **paragraph_doc,
                                "text": level_3_text,
                                "chunk_id": level_3_id,
                                "parent_chunk_id": level_2_id,
                                "root_chunk_id": level_1_id,
                                "chunk_level": 3,
                                "chunk_idx": page_global_chunk_idx,
                            })
                            page_global_chunk_idx += 1

        return root_chunks

    def _load_from_langchain_docs(
        self,
        raw_docs: list,
        file_path: str,
        filename: str,
        doc_type: str,
    ) -> list[dict]:
        documents: list[dict] = []
        page_global_chunk_idx = 0
        for doc in raw_docs:
            meta = getattr(doc, "metadata", None) or {}
            page_num = meta.get("page", 0)
            if page_num is None:
                page_num = 0
            try:
                page_num = int(page_num)
            except (TypeError, ValueError):
                page_num = 0
            base_doc = {
                "filename": sanitize_text(filename),
                "file_path": sanitize_text(file_path),
                "file_type": sanitize_text(doc_type),
                "page_number": page_num,
            }
            extraction_method = str(meta.get("extraction_method") or "native_text")
            source_block_index = meta.get("source_block_index")
            base_doc["extraction_method"] = extraction_method
            for key in (
                "block_bbox",
                "block_order",
                "ocr_confidence",
                "ocr_fallback_reason",
                "mineru_fallback_reason",
                "pypdf_fallback_reason",
            ):
                if meta.get(key) is not None:
                    base_doc[key] = meta[key]

            structured_blocks = None
            chunk_scope = ""
            if meta.get("structure_type"):
                block_type = str(meta["structure_type"])
                structured_blocks = [
                    StructuredTextBlock(
                        text=sanitize_text((doc.page_content or "").strip()),
                        block_type=block_type,
                        section_path=str(meta.get("section_path") or ""),
                    )
                ]
                chunk_scope = f"b{int(source_block_index or 0)}"
            page_chunks = self._split_page_to_three_levels(
                text=sanitize_text((doc.page_content or "").strip()),
                base_doc=base_doc,
                page_global_chunk_idx=page_global_chunk_idx,
                structured_blocks=structured_blocks,
                chunk_scope=chunk_scope,
            )
            page_global_chunk_idx += len(page_chunks)
            documents.extend(page_chunks)
        return documents

    def load_document(self, file_path: str, filename: str) -> list[dict]:
        file_lower = filename.lower()

        if file_lower.endswith(".pdf"):
            doc_type = "PDF"
            try:
                raw_docs = parse_pdf_with_mineru(file_path, filename)
                return self._load_from_langchain_docs(raw_docs, file_path, filename, doc_type)
            except (TimeoutError, MinerUUnavailableError, MinerUParseError) as mineru_error:
                mineru_fallback_reason = str(mineru_error)[:300]
            except Exception as mineru_error:
                mineru_fallback_reason = f"unexpected_mineru_error: {str(mineru_error)[:260]}"
            loader = PyPDFLoader(file_path)
        elif file_lower.endswith((".docx", ".doc")):
            doc_type = "Word"
            loader = Docx2txtLoader(file_path)
        elif file_lower.endswith((".xlsx", ".xls")):
            doc_type = "Excel"
            loader = UnstructuredExcelLoader(file_path)
        elif file_lower.endswith((".html", ".htm")):
            doc_type = "HTML"
            from backend.indexing.html_processor import load_html_for_document_loader

            raw_docs = load_html_for_document_loader(file_path, filename)
            return self._load_from_langchain_docs(raw_docs, file_path, filename, doc_type)
        elif file_lower.endswith(IMAGE_EXTENSIONS):
            doc_type = "Image"
            raw_docs = self._load_image_with_ocr(file_path)
            return self._load_from_langchain_docs(raw_docs, file_path, filename, doc_type)
        else:
            raise ValueError(f"不支持的文件类型: {filename}")

        try:
            raw_docs = loader.load()
            if file_lower.endswith(".pdf"):
                for doc in raw_docs:
                    metadata = getattr(doc, "metadata", None) or {}
                    doc.metadata = {
                        **metadata,
                        "mineru_fallback_reason": mineru_fallback_reason,
                    }
                raw_docs = self._route_pdf_pages(file_path, raw_docs)
            return self._load_from_langchain_docs(raw_docs, file_path, filename, doc_type)
        except Exception as e:
            if file_lower.endswith(".pdf"):
                try:
                    raw_docs = self._load_pdf_with_pdfium(file_path)
                    for doc in raw_docs:
                        metadata = getattr(doc, "metadata", None) or {}
                        doc.metadata = {
                            **metadata,
                            "mineru_fallback_reason": mineru_fallback_reason,
                            "pypdf_fallback_reason": str(e)[:300],
                        }
                    raw_docs = self._route_pdf_pages(file_path, raw_docs)
                    return self._load_from_langchain_docs(raw_docs, file_path, filename, doc_type)
                except Exception as fallback_error:
                    raise Exception(
                        f"PDF parse failed: PyPDFLoader={e}; pypdfium2={fallback_error}"
                    ) from fallback_error
            raise Exception(f"处理文档失败: {str(e)}") from e

    def _load_pdf_with_pdfium(self, file_path: str) -> list[Document]:
        import pypdfium2 as pdfium

        raw_docs: list[Document] = []
        pdf = pdfium.PdfDocument(file_path)
        try:
            for page_index in range(len(pdf)):
                page = pdf[page_index]
                textpage = page.get_textpage()
                try:
                    text = textpage.get_text_range() or ""
                finally:
                    textpage.close()
                    page.close()
                raw_docs.append(Document(page_content=text, metadata={"page": page_index}))
        finally:
            pdf.close()
        return raw_docs

    @staticmethod
    def _ocr_mode() -> str:
        mode = os.getenv("DOCUMENT_OCR_MODE", "fallback").strip().lower()
        return mode if mode in {"off", "fallback", "auto", "always"} else "fallback"

    @staticmethod
    def _native_text_is_sparse(text: str) -> bool:
        min_chars = int(os.getenv("DOCUMENT_OCR_MIN_TEXT_CHARS", "80"))
        meaningful = re.sub(r"\s+", "", text or "")
        return len(meaningful) < min_chars

    @staticmethod
    def _ocr_max_pages() -> int:
        return max(0, int(os.getenv("DOCUMENT_OCR_MAX_PAGES", "8")))

    @staticmethod
    def _ocr_skip_document(page_index: int) -> Document:
        return Document(
            page_content=(
                "本页原生文本提取为空，系统为避免大文档解析长时间阻塞，"
                "已跳过重型 OCR。若需要解析扫描件，请设置 DOCUMENT_OCR_MODE=auto "
                "并适当调高 DOCUMENT_OCR_MAX_PAGES 后重新导入。"
            ),
            metadata={
                "page": page_index,
                "extraction_method": "ocr_skipped_runtime_guard",
                "ocr_fallback_reason": "ocr_page_limit_reached",
            },
        )

    @staticmethod
    def _pdf_page_has_images(file_path: str, page_index: int) -> bool:
        try:
            from pypdf import PdfReader

            page = PdfReader(file_path).pages[page_index]
            resources = page.get("/Resources") or {}
            resources = (
                resources.get_object()
                if hasattr(resources, "get_object")
                else resources
            )
            xobjects = resources.get("/XObject") or {}
            xobjects = xobjects.get_object() if hasattr(xobjects, "get_object") else xobjects
            for item in xobjects.values():
                obj = item.get_object() if hasattr(item, "get_object") else item
                if obj.get("/Subtype") == "/Image":
                    return True
        except Exception:
            return False
        return False

    @staticmethod
    def _render_pdf_page(file_path: str, page_index: int):
        import pypdfium2 as pdfium

        dpi = max(96, int(os.getenv("DOCUMENT_OCR_DPI", "220")))
        pdf = pdfium.PdfDocument(file_path)
        try:
            page = pdf[page_index]
            try:
                bitmap = page.render(scale=dpi / 72)
                try:
                    return bitmap.to_numpy()
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            pdf.close()

    @staticmethod
    def _ocr_blocks_to_documents(blocks: list[OCRBlock]) -> list[Document]:
        documents: list[Document] = []
        for block_index, block in enumerate(blocks):
            metadata = {
                "page": block.page_number,
                "extraction_method": "paddleocr_ppstructurev3",
                "structure_type": block.block_type,
                "source_block_index": block_index,
                "block_order": block.block_order,
            }
            if block.bbox is not None:
                metadata["block_bbox"] = list(block.bbox)
            if block.confidence is not None:
                metadata["ocr_confidence"] = block.confidence
            if block.fallback_reason:
                metadata["ocr_fallback_reason"] = block.fallback_reason
            documents.append(Document(page_content=block.text, metadata=metadata))
        return documents

    def _route_pdf_pages(self, file_path: str, native_docs: list[Document]) -> list[Document]:
        mode = self._ocr_mode()
        if mode == "off":
            return native_docs

        routed: list[Document] = []
        ocr_attempts = 0
        max_ocr_pages = self._ocr_max_pages()
        for fallback_index, native_doc in enumerate(native_docs):
            meta = native_doc.metadata or {}
            try:
                page_index = int(meta.get("page", fallback_index))
            except (TypeError, ValueError):
                page_index = fallback_index
            native_text = native_doc.page_content or ""
            sparse = self._native_text_is_sparse(native_text)
            has_images = mode == "auto" and self._pdf_page_has_images(file_path, page_index)
            needs_ocr = mode == "always" or sparse or has_images
            if not needs_ocr:
                native_doc.metadata = {**meta, "extraction_method": "native_text"}
                routed.append(native_doc)
                continue
            if max_ocr_pages and ocr_attempts >= max_ocr_pages:
                if native_text.strip():
                    native_doc.metadata = {
                        **meta,
                        "extraction_method": "native_text",
                        "ocr_fallback_reason": "ocr_page_limit_reached",
                    }
                    routed.append(native_doc)
                else:
                    routed.append(self._ocr_skip_document(page_index))
                continue

            try:
                ocr_attempts += 1
                image = self._render_pdf_page(file_path, page_index)
                blocks = get_ocr_engine().parse(image, page_index)
            except OCRUnavailableError:
                if native_text.strip():
                    native_doc.metadata = {
                        **meta,
                        "extraction_method": "native_text",
                        "ocr_fallback_reason": "paddleocr_unavailable",
                    }
                    routed.append(native_doc)
                    continue
                raise
            except Exception:
                if native_text.strip():
                    native_doc.metadata = {
                        **meta,
                        "extraction_method": "native_text",
                        "ocr_fallback_reason": "paddleocr_failed",
                    }
                    routed.append(native_doc)
                    continue
                raise

            ocr_docs = self._ocr_blocks_to_documents(blocks)
            if ocr_docs:
                routed.extend(ocr_docs)
            elif native_text.strip():
                native_doc.metadata = {
                    **meta,
                    "extraction_method": "native_text",
                    "ocr_fallback_reason": "paddleocr_empty",
                }
                routed.append(native_doc)
        return routed

    def _load_image_with_ocr(self, file_path: str) -> list[Document]:
        try:
            import numpy as np
            from PIL import Image, ImageOps
        except ImportError as exc:
            raise OCRUnavailableError(
                "图片识别需要 Pillow 和 NumPy，请安装项目的 OCR 可选依赖。"
            ) from exc

        with Image.open(file_path) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
            blocks = get_ocr_engine().parse(np.asarray(image), 0)
        if not blocks:
            raise ValueError("OCR 未从图片中识别出可入库文本")
        return self._ocr_blocks_to_documents(blocks)

    def load_documents_from_folder(self, folder_path: str) -> list[dict]:
        all_documents = []

        for filename in os.listdir(folder_path):
            file_lower = filename.lower()
            if not (
                file_lower.endswith(".pdf")
                or file_lower.endswith((".docx", ".doc"))
                or file_lower.endswith((".xlsx", ".xls"))
                or file_lower.endswith((".html", ".htm"))
                or file_lower.endswith(IMAGE_EXTENSIONS)
            ):
                continue

            file_path = os.path.join(folder_path, filename)
            try:
                documents = self.load_document(file_path, filename)
                all_documents.extend(documents)
            except Exception:
                continue

        return all_documents
