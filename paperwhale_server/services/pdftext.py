"""PDF 文本抽取（pypdfium2 / PDFium）。

许可：pypdfium2 为 Apache-2.0 OR BSD-3-Clause，避免 PyMuPDF 的 AGPL 约束，
方便把 PaperWhale 分发给其他人使用。
"""
from __future__ import annotations

from pathlib import Path

import pypdfium2 as pdfium
from pypdfium2 import raw as pdfium_c


class PdfTextError(Exception):
    """PDF 打不开 / 加密 / 解析失败。"""


def open_document(path: Path | str) -> pdfium.PdfDocument:
    try:
        return pdfium.PdfDocument(str(path))
    except Exception as exc:  # noqa: BLE001 - pdfium 异常类型不统一
        raise PdfTextError(f"PDF 解析失败：{exc}") from exc


def page_count(path: Path | str) -> int:
    doc = open_document(path)
    try:
        return len(doc)
    finally:
        doc.close()


def _read_page_text(doc: pdfium.PdfDocument, index: int) -> str:
    page = doc[index]
    try:
        textpage = page.get_textpage()
        try:
            return textpage.get_text_range() or ""
        finally:
            textpage.close()
    finally:
        page.close()


def page_texts(path: Path | str, limit: int | None=None) -> list[tuple[int, str]]:
    """按页抽取文本，返回 [(1-based 页码, 文本)]；空白页也保留请调用方过滤。"""
    doc = open_document(path)
    try:
        result: list[tuple[int, str]] = []
        for index in range(len(doc) if limit is None else min(len(doc),max(0,limit))):
            try:
                result.append((index + 1, _read_page_text(doc, index)))
            except Exception:  # noqa: BLE001 - 单页失败不影响整篇
                result.append((index + 1, ""))
        return result
    finally:
        doc.close()


def first_pages_text(path: Path | str, count: int = 2) -> str:
    pages = page_texts(path,limit=max(1,count))
    return "\n".join(text for _, text in pages)


def document_info(path: Path | str) -> dict:
    """PDF 内嵌元数据（Title/Author/Subject/Keywords/CreationDate）。"""
    doc = open_document(path)
    try:
        info = doc.get_metadata_dict() or {}
    except Exception:  # noqa: BLE001
        info = {}
    finally:
        doc.close()
    return {
        "title": (info.get("Title") or "").strip(),
        "author": (info.get("Author") or "").strip(),
        "subject": (info.get("Subject") or "").strip(),
        "keywords": (info.get("Keywords") or "").strip(),
        "creation_date": (info.get("CreationDate") or "").strip(),
    }


def page_size(path: Path | str, index: int = 0) -> tuple[float, float]:
    doc = open_document(path)
    try:
        width, height = doc[index].get_size()
    finally:
        doc.close()
    return float(width), float(height)


def page_lines(path: Path | str, index: int = 0) -> list[tuple[float, float, str]]:
    """首页排版行：[(上边缘 y, 最大字号, 文本)]，按 PDF 内文字顺序（通常自上而下）。

    逐字符定位、按行合并，再用 ``get_text_range`` 取整行文本以保留 PDFium 插入的空格。
    """
    doc = open_document(path)
    try:
        page = doc[index]
        try:
            textpage = page.get_textpage()
            try:
                total = textpage.count_chars()
                lines: list[tuple[float, float, str]] = []
                line_start: int | None = None
                line_end: int | None = None
                current_y = 0.0
                current_size = 0.0
                current_top = 0.0

                def flush() -> None:
                    nonlocal line_start, line_end, current_y, current_size, current_top
                    if line_start is not None and line_end is not None:
                        text = textpage.get_text_range(line_start, line_end - line_start + 1)
                        text = text.replace("\r", " ").replace("\n", " ").strip()
                        if text:
                            lines.append((current_top, current_size, text))
                    line_start = None
                    line_end = None
                    current_y = 0.0
                    current_size = 0.0
                    current_top = 0.0

                for char_index in range(total):
                    char = textpage.get_text_range(char_index, 1)
                    if "\r" in char or "\n" in char:
                        flush()
                        continue
                    left, bottom, right, top = textpage.get_charbox(char_index)
                    is_blank = not char.strip()
                    if right <= left and top <= bottom:
                        # 零宽字符（通常是 PDFium 补的空格）：只扩展当前行范围
                        if is_blank:
                            if line_start is None:
                                line_start = char_index
                            line_end = char_index
                        continue
                    try:
                        size = float(pdfium_c.FPDFText_GetFontSize(textpage.raw, char_index))
                    except Exception:  # noqa: BLE001
                        size = 0.0
                    # 部分 PDF（尤其 Type3/矢量期刊模板）拿到的字号恒为 1，
                    # 用字符框高度兜底，否则标题启发式会退化成“取最早出现的几行”。
                    box_height = abs(top - bottom)
                    if size <= 1.5 and box_height > size:
                        size = box_height
                    center = (bottom + top) / 2
                    if line_start is not None and current_y:
                        tolerance = max(2.0, min(size or current_size or 1.0, current_size or 1.0) * 0.6)
                        if abs(center - current_y) > tolerance:
                            flush()
                    if line_start is None:
                        line_start = char_index
                        current_y = center
                        current_size = size
                        current_top = top
                    else:
                        current_size = max(current_size, size)
                        current_top = max(current_top, top)
                    line_end = char_index
                flush()
                return lines
            finally:
                textpage.close()
        finally:
            page.close()
    finally:
        doc.close()
