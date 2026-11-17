"""Zotero / Obsidian 导入器。

解析 CSV / BibTeX / RIS / Zotero RDF 与 Obsidian vault，把结果统一交给
``services.ingest.create_paper_from_metadata`` 落库（去重、PDF 复制入库都在 ingest 里）。

所有 ``import_*`` 函数返回 ``{"imported": [paper...], "skipped": int, "errors": [str...]}``；
单条记录失败只记进 errors，不中断整批。
"""
from __future__ import annotations

import csv
import io
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any
from urllib.parse import unquote
from xml.etree import ElementTree as ET

from .. import db
from . import imported_notes
from .metadata import (
    DOI_PATTERN,
    YEAR_PATTERN,
    clean_meta,
    normalize_arxiv_id,
    normalize_doi,
)

try:  # PyYAML 不是硬依赖，缺了就走简易 frontmatter 解析
    import yaml as _yaml
except Exception:  # noqa: BLE001
    _yaml = None

SUPPORTED_SUFFIXES = (".csv", ".bib", ".ris", ".rdf")

_MAX_SCAN_PDFS = 3000
_MAX_MD_FILES = 2000
_MAX_ERRORS = 60

_SKIP_DIR_NAMES = {
    ".obsidian",
    ".trash",
    ".git",
    ".stfolder",
    "templates",
    "template",
    "_templates",
}

_RDF_NS = "http://www.w3.org/1999/02/22-rdf-syntax-ns#"
_DC_NS = "http://purl.org/dc/elements/1.1/"
_DCTERMS_NS = "http://purl.org/dc/terms/"
_ZOTERO_NS = "http://www.zotero.org/namespaces/export#"
_FOAF_NS = "http://xmlns.com/foaf/0.1/"
_PRISM_NS = "http://prismstandard.org/namespaces/1.2/basic/"

# Zotero RDF 对象里不算文献的类型：附件、笔记、批注，以及分类/搜索器/设置这类结构节点。
_RDF_NON_BIBLIOGRAPHIC_TYPES = {
    "attachment",
    "note",
    "annotation",
    "collection",
    "search",
    "savedsearch",
    "setting",
    "settings",
    "tag",
    "creator",
    "author",
    "subject",
    "journal",
    "publication",
    "publisher",
}


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _q(namespace: str, tag: str) -> str:
    return "{%s}%s" % (namespace, tag)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def _clean(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u00a0", " ").replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _clean_bib(value: Any) -> str:
    """BibTeX 字段：去掉 {} 保护符与多余空白。"""
    text = str(value or "").replace("{", "").replace("}", "")
    return _clean(text)


def _new_result() -> dict:
    return {"imported": [], "skipped": 0, "skipped_no_pdf": 0, "skipped_no_pdf_examples": [], "errors": [],
            "notes_imported": 0, "notes_duplicates": 0, "notes_updated_papers": 0, "warnings": []}


def _notes(*pairs: tuple[str, str]) -> list[dict]:
    return [entry for label, raw in pairs if (entry := imported_notes.note(raw, label))]


def _note_xml(node: ET.Element | None) -> str:
    if node is None:
        return ''
    # EndNote's <style face="bold"> is text formatting, not an HTML stylesheet.
    def render(element):
        local = _local(element.tag).lower()
        value = element.text or ''
        for child in element:
            value += render(child) + (child.tail or '')
        if local == 'style':
            face = element.get('face', '').lower()
            if 'bold' in face: value = '<b>' + value + '</b>'
            if 'italic' in face: value = '<i>' + value + '</i>'
            return value
        if local in {'p','div','br','li','h1','h2','h3','h4','b','strong','i','em','ul','ol','table','tr','td','th'}:
            return '<' + local + '>' + value + '</' + local + '>'
        return value
    return render(node)


def _add_error(result: dict, message: str) -> None:
    if len(result["errors"]) < _MAX_ERRORS:
        result["errors"].append(_clean(message)[:400])


def _read_text(path: Path, encodings: tuple[str, ...] = ("utf-8-sig", "utf-8", "gb18030", "latin-1")) -> str:
    raw = Path(path).read_bytes()
    for encoding in encodings:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _split_tags(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, (list, tuple, set)):
        pieces = [str(item) for item in raw]
    else:
        pieces = re.split(r"[;,，；]", str(raw))
    tags: list[str] = []
    for piece in pieces:
        text = _clean(piece).lstrip("#").strip()
        if text and text.lower() not in {tag.lower() for tag in tags}:
            tags.append(text[:60])
    return tags[:20]


def _format_person(name: str) -> str:
    """'Doe, John' -> 'John Doe'，已经是 'John Doe' 就保持。"""
    text = _clean(name).strip("{}\"' ")
    if not text:
        return ""
    if "," in text:
        last, _, first = text.partition(",")
        first = _clean(first)
        last = _clean(last)
        if first and last:
            return f"{first} {last}"
        return last or first
    return text


def _authors_from_raw(value: Any) -> str:
    """解析作者串：Zotero CSV 的 'Doe, John; Smith, Jane'、BibTeX 的 'A and B'。"""
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        names: list[str] = []
        for item in value:
            if isinstance(item, dict):
                item = " ".join(
                    str(item.get(key) or "") for key in ("given", "first", "firstName", "givenName")
                ) + " " + " ".join(
                    str(item.get(key) or "") for key in ("family", "last", "lastName", "surname")
                )
            text = _format_person(str(item))
            if text:
                names.append(text)
        return "; ".join(names)
    text = _clean(value)
    if not text:
        return ""
    pieces = re.split(r"\s*;\s*", text)
    if len(pieces) == 1:
        pieces = re.split(r"\s+and\s+", text)
    names = [_format_person(piece) for piece in pieces if piece.strip()]
    return "; ".join(name for name in names if name)


def _arxiv_from_text(value: Any) -> str:
    text = _clean(value)
    if not text:
        return ""
    if not re.search(r"arxiv", text, re.IGNORECASE):
        return ""
    return normalize_arxiv_id(text)


def _year_from_raw(value: Any) -> str:
    match = YEAR_PATTERN.search(_clean(value))
    return match.group(0) if match else ""


# ---------------------------------------------------------------------------
# PDF 索引 / 附件路径解析
# ---------------------------------------------------------------------------

def looks_like_pdf(path: Path) -> bool:
    """文件名像 PDF 不够，内容必须是真 PDF（以 %PDF 开头），且不是空壳文件。"""
    try:
        candidate = Path(path)
        if not candidate.is_file():
            return False
        if candidate.stat().st_size < 512:
            return False
        with candidate.open("rb") as handle:
            return handle.read(5) == b"%PDF-"
    except OSError:
        return False


def _scan_pdfs(root: Path, limit: int = _MAX_SCAN_PDFS) -> list[Path]:
    pdfs: list[Path] = []
    try:
        for path in Path(root).rglob("*.pdf"):
            try:
                if path.is_file() and looks_like_pdf(path):
                    pdfs.append(path)
            except OSError:
                continue
            if len(pdfs) >= limit:
                break
    except OSError:
        pass
    return pdfs


def _normalize_for_match(text: str) -> str:
    value = _clean(text).lower()
    value = re.sub(r"\.pdf$", "", value)
    value = re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)
    return value


def _title_match_score(title: str, filename: str) -> float:
    normalized_title = _normalize_for_match(title)
    if len(normalized_title) < 4:
        return 0.0
    stem = Path(filename).stem
    candidates = [_normalize_for_match(stem)]
    pieces = [piece.strip() for piece in re.split(r"\s+-\s+", stem) if piece.strip()]
    if len(pieces) >= 2:
        candidates.append(_normalize_for_match(pieces[-1]))
    best = 0.0
    for candidate in candidates:
        if not candidate:
            continue
        if normalized_title in candidate or candidate in normalized_title:
            shorter = min(len(normalized_title), len(candidate))
            longer = max(len(normalized_title), len(candidate))
            best = max(best, 0.75 + 0.25 * shorter / longer)
        else:
            best = max(best, SequenceMatcher(None, normalized_title, candidate).ratio())
    return best


def _best_pdf(meta: dict, pdfs: list[Path], used: set[Path] | None = None) -> Path | None:
    title = _clean(meta.get("title") or "")
    if not title or not pdfs:
        return None
    used = used or set()
    best: Path | None = None
    best_score = 0.0
    for path in pdfs:
        if path in used:
            continue
        score = _title_match_score(title, path.name)
        if score > best_score:
            best, best_score = path, score
    return best if best is not None and best_score >= 0.65 else None


def _resolve_attachment(
    hint: str,
    base_dir: Path | None,
    pdfs: list[Path],
    extra_bases: list[Path] | None = None,
) -> Path | None:
    """把附件路径（相对/绝对/Zotero 导出格式）解析成真实 PDF 路径。"""
    raw = _clean(str(hint or ""))
    if not raw:
        return None
    raw = re.sub(
        r"^(?:full\s*text\s*pdf|pdf|file|attachment|附件|link\s*to\s*pdf)\s*[:：]\s*",
        "",
        raw,
        flags=re.IGNORECASE,
    )
    raw = raw.strip().strip("{}").strip().strip("\"'")
    # 'files/1234/x.pdf:application/pdf' 这类 Zotero 后缀
    if not raw.lower().startswith(("http://", "https://")) and not re.match(r"^[A-Za-z]:[\\/]", raw):
        parts = [piece.strip() for piece in raw.split(":") if piece.strip()]
        pdf_like = [piece for piece in parts if piece.lower().endswith(".pdf")]
        if pdf_like:
            raw = pdf_like[0]
    raw = raw.strip()
    if not raw or raw.lower() in {"pdf", "full text pdf", "link to pdf", "attachment"}:
        return None

    bases: list[Path] = []
    for base in [base_dir, *(extra_bases or [])]:
        if base is None:
            continue
        try:
            base_path = Path(base)
        except TypeError:
            continue
        if base_path not in bases:
            bases.append(base_path)

    try:
        absolute = Path(raw)
        if absolute.is_absolute() and absolute.suffix.lower() == ".pdf" and absolute.is_file():
            return absolute
    except OSError:
        pass

    normalized = raw.replace("\\", "/").strip("/")
    name = normalized.rsplit("/", 1)[-1]
    candidates = [normalized]
    if name and name != normalized:
        candidates.append(name)
    for base in bases:
        for candidate in candidates:
            try:
                target = base / candidate
                if target.suffix.lower() == ".pdf" and target.is_file():
                    return target
            except OSError:
                continue
    if name:
        target_name = name.lower()
        for path in pdfs:
            if path.name.lower() == target_name:
                return path
        normalized_name = _normalize_for_match(name)
        if normalized_name:
            for path in pdfs:
                if _normalize_for_match(path.name) == normalized_name:
                    return path
    return None


# ---------------------------------------------------------------------------
# 落库
# ---------------------------------------------------------------------------

def _find_existing(meta: dict) -> str | None:
    try:
        return db.find_duplicate(
            meta.get("title") or "",
            meta.get("doi") or "",
            meta.get("arxiv_id") or "",
        )
    except Exception:  # noqa: BLE001 - 数据库没初始化时当作没有重复
        return None


def _save_item(
    item: dict,
    *,
    source: str,
    base_dir: Path | None,
    pdfs: list[Path],
    used: set[Path],
    result: dict,
    require_pdf: bool = False,
) -> None:
    meta = clean_meta(item.get("meta") or {})
    for warning in item.get('warnings', []):
        if warning not in result.setdefault('warnings', []): result['warnings'].append(warning)
    if not meta.get("title"):
        result["skipped"] += 1
        _add_error(result, "跳过一条没有标题的记录")
        return

    pdf_path: Path | None = None
    explicit = item.get("pdf")
    if explicit:
        try:
            candidate = Path(explicit)
            if not candidate.is_absolute() and base_dir is not None:
                candidate = Path(base_dir) / candidate
            if candidate.suffix.lower() == ".pdf" and candidate.is_file():
                pdf_path = candidate
        except OSError:
            pdf_path = None

    hints = item.get("pdf_hints")
    if isinstance(item.get("pdf_hint"), str):
        hints = [item["pdf_hint"]] + list(hints or [])
    if pdf_path is None and hints:
        for hint in hints:
            pdf_path = _resolve_attachment(str(hint), base_dir, pdfs)
            if pdf_path is not None:
                break
    if pdf_path is None and pdfs:
        pdf_path = _best_pdf(meta, pdfs, used)
    if pdf_path is not None and not looks_like_pdf(pdf_path):
        pdf_path = None

    # 用户定的规矩：必须先定位到真实的 PDF 文件，才允许往库里建条目。
    # 否则 Zotero 导出里的分类名、期刊名、空壳记录都会被当成论文塞进库里。
    existing_id = _find_existing(meta)
    if require_pdf and pdf_path is None and not (existing_id and item.get('notes')):
        result["skipped"] += 1
        result["skipped_no_pdf"] = int(result.get("skipped_no_pdf") or 0) + 1
        examples = result.setdefault("skipped_no_pdf_examples", [])
        if len(examples) < 10:
            examples.append(str(meta.get("title"))[:60])
        return

    try:
        from .ingest import create_paper_from_metadata

        # 去重、补全空字段、给已有条目补 PDF 都由 ingest 负责；
        # 这里只根据导入前的状态决定计入 imported 还是 skipped。
        paper_id = create_paper_from_metadata(meta, pdf_path=pdf_path, source=source)
    except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
        _add_error(result, f"{str(meta.get('title'))[:60]}：{exc}")
        return
    try:
        added, duplicate = imported_notes.merge(paper_id, item.get('notes') or [], source=source)
        result['notes_imported'] = result.get('notes_imported', 0) + added
        result['notes_duplicates'] = result.get('notes_duplicates', 0) + duplicate
        if added and existing_id: result['notes_updated_papers'] = result.get('notes_updated_papers', 0) + 1
    except Exception as exc:  # noqa: BLE001 - 单条失败不中断整批
        _add_error(result, f"{str(meta.get('title'))[:60]}：文献已保留，笔记导入失败：{exc}")

    if pdf_path is not None:
        used.add(pdf_path)
    if existing_id:
        result["skipped"] += 1
        return
    paper = None
    try:
        paper = db.get_paper(paper_id)
    except Exception:  # noqa: BLE001
        paper = None
    result["imported"].append(paper or {"id": paper_id, **meta})


def _save_items(
    items: list[dict],
    *,
    source: str,
    base_dir: Path | None,
    pdfs: list[Path],
    result: dict | None = None,
    require_pdf: bool = False,
) -> dict:
    result = result if result is not None else _new_result()
    used: set[Path] = set()
    for item in items:
        _save_item(
            item,
            source=source,
            base_dir=base_dir,
            pdfs=pdfs,
            used=used,
            result=result,
            require_pdf=require_pdf,
        )
    return result


# ---------------------------------------------------------------------------
# Zotero CSV
# ---------------------------------------------------------------------------

_CSV_FIELDS: dict[str, tuple[str, ...]] = {
    "title": ("title", "paper title", "论文标题", "标题", "名称"),
    "authors": ("author", "authors", "creator", "creators", "作者"),
    "year": ("publication year", "year", "publication date", "date", "年份"),
    "venue": (
        "publication title",
        "publication",
        "journal",
        "journal title",
        "book title",
        "conference name",
        "proceedings title",
        "publisher",
        "期刊",
    ),
    "doi": ("doi", "doi号"),
    "volume": ("volume", "卷", "卷号"),
    "issue": ("issue", "number", "期", "期号"),
    "pages": ("pages", "page", "页码", "页"),
    "url": ("url", "link", "网址"),
    "abstract": ("abstract note", "abstract", "abstractnote", "摘要"),
    "attachments": ("file attachments", "attachments", "attachment", "files", "附件"),
    "tags": ("tags", "manual tags", "automatic tags", "keywords", "keyword", "标签"),
    "arxiv": ("arxiv id", "arxiv", "eprint", "arxiv_id"),
    "extra": ("extra",),
    "notes": ("notes", "note", "笔记", "research notes"),
    "item_type": ("item type", "itemtype"),
}


def _norm_header(value: str) -> str:
    text = _clean(value).lower().replace("_", " ").replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip(" :")


def _csv_mapping(fieldnames: list[str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name in fieldnames or []:
        if not name:
            continue
        key = _norm_header(name)
        if key and key not in normalized:
            normalized[key] = name
    mapping: dict[str, str] = {}
    for target, aliases in _CSV_FIELDS.items():
        for alias in aliases:
            if alias in normalized:
                mapping[target] = normalized[alias]
                break
    return mapping


def _csv_attachment_hints(raw: Any) -> list[str]:
    text = _clean(raw)
    if not text:
        return []
    if text.startswith("["):
        try:
            data = json.loads(text)
            if isinstance(data, list):
                return [str(item) for item in data if str(item).strip()]
        except (ValueError, TypeError):
            pass
    pieces = re.split(r";|\r?\n", str(raw))
    return [piece.strip() for piece in pieces if piece.strip()]


def _csv_record_to_item(record: dict[str, str], mapping: dict[str, str]) -> dict:
    def value(field: str) -> str:
        key = mapping.get(field)
        return record.get(key, "") if key else ""

    title = _clean(value("title"))
    url = _clean(value("url"))
    extra = _clean(value("extra"))
    doi = normalize_doi(value("doi"))
    if not doi:
        doi = normalize_doi(url) or normalize_doi(extra)
    arxiv_id = normalize_arxiv_id(value("arxiv")) if value("arxiv") else ""
    if not arxiv_id:
        for candidate in (url, extra, doi, title):
            arxiv_id = _arxiv_from_text(candidate)
            if arxiv_id:
                break
    meta = clean_meta(
        {
            "title": title,
            "authors": _authors_from_raw(value("authors")),
            "year": _year_from_raw(value("year")),
            "venue": _clean(value("venue")),
            "volume": _clean(value("volume")),
            "issue": _clean(value("issue")),
            "pages": _clean(value("pages")),
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": url,
            "abstract": _clean(value("abstract")),
            "tags": _split_tags(value("tags")),
            "category": "",
        }
    )
    return {"meta": meta, "pdf_hints": _csv_attachment_hints(value("attachments")),
            "notes": _notes(('CSV 笔记', value('notes')))}


def parse_zotero_csv(path: Path) -> list[dict]:
    """解析 Zotero CSV 导出（表头大小写/别名容错）。返回 [{meta, pdf_hints}]。"""
    text = _read_text(path)
    try:
        rows = list(csv.reader(io.StringIO(text)))
    except csv.Error as exc:
        raise ValueError(f"CSV 解析失败：{exc}") from exc
    if not rows:
        return []
    header_index = 0
    header: list[str] = rows[0]
    for index, row in enumerate(rows[:15]):
        keys = {_norm_header(cell) for cell in row if cell}
        if "title" in keys or "item type" in keys:
            header_index = index
            header = row
            break
    mapping = _csv_mapping(header)
    if "title" not in mapping:
        raise ValueError("CSV 缺少标题列（Title），不像是 Zotero 导出")
    items: list[dict] = []
    for row in rows[header_index + 1:]:
        if not any(str(cell).strip() for cell in row):
            continue
        record: dict[str, str] = {}
        for index, name in enumerate(header):
            if not name:
                continue
            record[name] = row[index] if index < len(row) else ""
        if record.get(mapping.get('item_type'), '').lower() in {'note','annotation','attachment'}:
            continue
        items.append(_csv_record_to_item(record, mapping))
    return items


# ---------------------------------------------------------------------------
# BibTeX
# ---------------------------------------------------------------------------

def _bib_read_value(body: str, start: int) -> tuple[str | None, int]:
    index = start
    while index < len(body) and body[index].isspace():
        index += 1
    if index >= len(body):
        return None, index
    char = body[index]
    if char == "{":
        depth = 1
        cursor = index + 1
        while cursor < len(body) and depth:
            if body[cursor] == "{":
                depth += 1
            elif body[cursor] == "}":
                depth -= 1
            cursor += 1
        return body[index + 1: cursor - 1], cursor
    if char == '"':
        cursor = index + 1
        while cursor < len(body) and body[cursor] != '"':
            cursor += 1
        return body[index + 1: cursor], cursor + 1
    cursor = index
    while cursor < len(body) and body[cursor] not in ",\n}":
        cursor += 1
    return body[index:cursor].strip(), cursor


def _parse_bibtex_manual(text: str) -> list[dict]:
    """极简 BibTeX 解析（只给 bibtexparser 不可用时兜底）。"""
    entries: list[dict] = []
    for match in re.finditer(r"@\s*(\w+)\s*[{(]\s*([^,\s]*)\s*,", text):
        depth = 1
        cursor = match.end()
        while cursor < len(text) and depth:
            if text[cursor] == "{":
                depth += 1
            elif text[cursor] == "}":
                depth -= 1
            cursor += 1
        body = text[match.end(): cursor - 1]
        fields: dict[str, str] = {}
        for field_match in re.finditer(r"([A-Za-z][\w\-]*)\s*=\s*", body):
            key = field_match.group(1).lower()
            if key in fields:
                continue
            value, _ = _bib_read_value(body, field_match.end())
            if value is not None:
                fields[key] = value.strip()
        if fields:
            entries.append(fields)
    return entries


def _load_bibtex_entries(text: str) -> list[dict]:
    try:
        import bibtexparser
    except ImportError:
        return _parse_bibtex_manual(text)

    # bibtexparser 2.x
    if hasattr(bibtexparser, "parse_string"):
        try:
            library = bibtexparser.parse_string(text)
            entries: list[dict] = []
            for entry in getattr(library, "entries", []) or []:
                data: dict[str, str] = {}
                try:
                    for key, field in entry.fields.items():
                        value = getattr(field, "value", field)
                        data[str(key).lower()] = str(value)
                except Exception:  # noqa: BLE001
                    continue
                if data:
                    entries.append(data)
            if entries:
                return entries
        except Exception:  # noqa: BLE001 - 解析失败再试 v1 / 手工
            pass

    # bibtexparser 1.x
    if hasattr(bibtexparser, "loads"):
        try:
            library = bibtexparser.loads(text)
            entries = [dict(entry) for entry in getattr(library, "entries", []) or []]
            if entries:
                return entries
        except Exception:  # noqa: BLE001
            pass
    return _parse_bibtex_manual(text)


def _bib_value(fields: dict, *keys: str) -> str:
    for key in keys:
        value = fields.get(key)
        if value:
            text = _clean_bib(value)
            if text:
                return text
    return ""


def _bib_attachment_hints(raw: Any) -> list[str]:
    text = _clean_bib(raw)
    if not text:
        return []
    hints: list[str] = []
    for chunk in re.split(r";", text):
        chunk = chunk.strip().strip("{}").strip()
        if not chunk:
            continue
        if re.match(r"^[A-Za-z]:[\\/].*\.pdf$", chunk, re.IGNORECASE) or chunk.lower().endswith(".pdf"):
            hints.append(chunk)
            continue
        for piece in chunk.split(":"):
            piece = piece.strip()
            if piece.lower().endswith(".pdf"):
                hints.append(piece)
                break
    return hints


def _bib_fields_to_item(fields: dict) -> dict:
    title = _bib_value(fields, "title")
    url = _bib_value(fields, "url")
    doi = normalize_doi(_bib_value(fields, "doi"))
    abstract = _bib_value(fields, "abstract")
    keywords = _bib_value(fields, "keywords") or _bib_value(fields, "keyword")
    entry_type = _clean(fields.get("ENTRYTYPE") or fields.get("entrytype") or "")
    eprint = _bib_value(fields, "eprint")
    archive_prefix = _bib_value(fields, "archiveprefix")
    arxiv_id = ""
    if eprint and (
        re.search(r"arxiv", archive_prefix, re.IGNORECASE)
        or re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?", eprint)
        or "/" in eprint
    ):
        arxiv_id = normalize_arxiv_id(eprint)
    if not arxiv_id:
        for candidate in (url, doi, _bib_value(fields, "note"), _bib_value(fields, "howpublished")):
            arxiv_id = _arxiv_from_text(candidate)
            if arxiv_id:
                break
    year = _year_from_raw(_bib_value(fields, "year") or _bib_value(fields, "date"))
    meta = clean_meta(
        {
            "title": title,
            "authors": _authors_from_raw(_bib_value(fields, "author") or _bib_value(fields, "editor")),
            "year": year,
            "venue": _bib_value(
                fields,
                "journal",
                "journaltitle",
                "booktitle",
                "publisher",
                "series",
                "howpublished",
                "organization",
                "school",
                "institution",
            ),
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": url,
            "abstract": abstract,
            "tags": _split_tags(keywords),
            "category": "",
            "volume": _bib_value(fields, "volume"),
            "issue": _bib_value(fields, "number", "issue"),
            "pages": _bib_value(fields, "pages"),
        }
    )
    hints = _bib_attachment_hints(fields.get("file") or fields.get("attachment"))
    return {"meta": meta, "pdf_hints": hints, "entry_type": entry_type,
            "notes": _notes(('BibTeX 备注', fields.get('note', '')), ('BibTeX 批注', (fields.get('annote') or fields.get('annotation', ''))))}


def parse_bibtex(path: Path) -> list[dict]:
    text = _read_text(path)
    return [_bib_fields_to_item(fields) for fields in _load_bibtex_entries(text)]


# ---------------------------------------------------------------------------
# RIS
# ---------------------------------------------------------------------------

def _split_ris_records(text: str) -> list[dict[str, list[str]]]:
    records: list[dict[str, list[str]]] = []
    current: dict[str, list[str]] = {}
    last_tag = ""
    for line in (text or "").splitlines():
        if not line.strip():
            continue
        match = re.match(r"^([A-Z][A-Z0-9]) {1,2}- ?(.*)$", line)
        if not match:
            if last_tag and current.get(last_tag):
                current[last_tag][-1] = (current[last_tag][-1] + ("\n" if last_tag == "N1" else " ") + line.strip()).strip()
            continue
        tag = match.group(1).upper()
        value = match.group(2).strip()
        if tag == "TY":
            if current:
                records.append(current)
                current = {}
            current[tag] = [value] if value else []
        elif tag == "ER":
            if current:
                records.append(current)
                current = {}
            last_tag = ""
            continue
        else:
            current.setdefault(tag, []).append(value)
        last_tag = tag
    if current:
        records.append(current)
    return records


def _ris_first(record: dict[str, list[str]], *tags: str) -> str:
    for tag in tags:
        for value in record.get(tag, []):
            text = _clean(value)
            if text:
                return text
    return ""


def _ris_all(record: dict[str, list[str]], *tags: str) -> list[str]:
    values: list[str] = []
    for tag in tags:
        for value in record.get(tag, []):
            text = _clean(value)
            if text:
                values.append(text)
    return values


def _ris_record_to_item(record: dict[str, list[str]]) -> dict:
    title = _ris_first(record, "TI", "T1", "CT")
    authors = "; ".join(
        name for name in (_format_person(value) for value in _ris_all(record, "AU", "A1")) if name
    )
    year = _year_from_raw(_ris_first(record, "PY", "Y1", "DA"))
    venue = _ris_first(record, "JO", "JF", "T2", "BT", "JA", "J2", "PB")
    doi = normalize_doi(_ris_first(record, "DO"))
    url = _ris_first(record, "UR")
    abstract = _ris_first(record, "AB", "N2")
    tags = _split_tags("; ".join(_ris_all(record, "KW")))
    arxiv_id = _arxiv_from_text(url) or _arxiv_from_text(doi) or normalize_arxiv_id(_ris_first(record, "AN"))
    start_page = _ris_first(record, "SP")
    end_page = _ris_first(record, "EP")
    pages = f"{start_page}–{end_page}" if start_page and end_page and end_page != start_page else (start_page or end_page)
    meta = clean_meta(
        {
            "title": title,
            "authors": authors,
            "year": year,
            "venue": venue,
            "volume": _ris_first(record, "VL"),
            "issue": _ris_first(record, "IS"),
            "pages": pages,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": url,
            "abstract": abstract,
            "tags": tags,
            "category": "",
        }
    )
    hints: list[str] = []
    for value in _ris_all(record, "L1", "L2", "L4", "LK"):
        if ".pdf" in value.lower():
            hints.append(value)
    return {"meta": meta, "pdf_hints": hints,
            "notes": _notes(*(('RIS 备注', value) for value in record.get('N1', [])))}


def parse_ris(path: Path) -> list[dict]:
    text = _read_text(path)
    return [_ris_record_to_item(record) for record in _split_ris_records(text)]


# ---------------------------------------------------------------------------
# Zotero RDF
# ---------------------------------------------------------------------------

def _rdf_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return _clean(" ".join(element.itertext()))


def _rdf_attachment(element: ET.Element) -> dict:
    path = ""
    for tag in (_q(_ZOTERO_NS, "path"), _q(_ZOTERO_NS, "attachmentFilename")):
        child = element.find(tag)
        if child is not None:
            text = child.get(_q(_RDF_NS, 'resource')) or _rdf_text(child)
            if text:
                path = text
                break
    if not path:
        for child in element.iter():
            if _local(child.tag) not in ("URI", "value"):
                continue
            text = _rdf_text(child)
            if text and (".pdf" in text.lower() or "\\" in text or "/" in text):
                path = text
                break
    if not path:
        match = re.search(r"([^\s<>\"']+\.pdf)", _rdf_text(element), re.IGNORECASE)
        if match:
            path = match.group(1)
    parent = ""
    for child in element.iter(_q(_DCTERMS_NS, "isReferencedBy")):
        resource = child.get(_q(_RDF_NS, "resource")) or ""
        if resource:
            parent = resource.lstrip("#")
            break
    if not parent:
        for child in element.iter(_q(_DCTERMS_NS, "references")):
            resource = child.get(_q(_RDF_NS, "resource")) or ""
            if resource:
                parent = resource.lstrip("#")
                break
    return {"path": path, "parent": parent}


def _rdf_item_meta(element: ET.Element) -> dict:
    def first(*tags: str) -> str:
        for tag in tags:
            child = element.find(tag)
            if child is None:
                continue
            text = _rdf_text(child)
            if text:
                return text
        return ""

    title = first(_q(_DC_NS, "title"))
    authors: list[str] = []
    for creator in element.findall(_q(_DC_NS, "creator")):
        names: list[str] = []
        for person in creator.iter():
            if _local(person.tag) != "Person":
                continue
            surname = _rdf_text(person.find(_q(_FOAF_NS, "surname")))
            given = _rdf_text(person.find(_q(_FOAF_NS, "givenName")))
            name = _clean(f"{given} {surname}") or _rdf_text(person)
            if name:
                names.append(name)
        if not names:
            name = _rdf_text(creator)
            if name:
                names.append(name)
        for name in names:
            if name not in authors:
                authors.append(name)

    date_text = first(_q(_DCTERMS_NS, "date"), _q(_DC_NS, "date"))
    year = _year_from_raw(date_text)
    venue = first(_q(_PRISM_NS, "publicationName"), _q(_DC_NS, "source"))
    if not venue:
        for child in element.findall(_q(_DCTERMS_NS, "isPartOf")):
            text = _rdf_text(child)
            if text:
                venue = text
                break
    abstract = first(_q(_DCTERMS_NS, "abstract"), _q(_DC_NS, "description"))
    volume = first(_q(_PRISM_NS, "volume"))
    issue = first(_q(_PRISM_NS, "number"))
    page_range = first(_q(_PRISM_NS, "pageRange"))
    start_page = first(_q(_PRISM_NS, "startingPage"))
    end_page = first(_q(_PRISM_NS, "endingPage"))
    if page_range:
        pages = page_range
    elif start_page and end_page and end_page != start_page:
        pages = f"{start_page}–{end_page}"
    else:
        pages = start_page

    identifier_texts: list[str] = []
    for child in element.iter():
        if _local(child.tag) in ("identifier", "URI", "value"):
            text = _rdf_text(child)
            if text:
                identifier_texts.append(text)
    identifiers = " ".join(identifier_texts)
    doi = normalize_doi(identifiers)
    url = ""
    for text in identifier_texts:
        match = re.search(r"https?://\S+", text)
        if match:
            url = match.group(0).rstrip(".,);")
            break
    tags: list[str] = []
    for subject in element.findall(_q(_DC_NS, "subject")):
        text = _rdf_text(subject)
        if text and text not in tags:
            tags.append(text)
    arxiv_id = _arxiv_from_text(url) or _arxiv_from_text(doi) or normalize_arxiv_id(identifiers)

    return clean_meta(
        {
            "title": title,
            "authors": "; ".join(authors),
            "year": year,
            "venue": venue,
            "volume": volume,
            "issue": issue,
            "pages": pages,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": url,
            "abstract": abstract,
            "tags": tags,
            "category": "",
        }
    )


def _rdf_attachment_hints(element: ET.Element, attachments: dict[str, dict]) -> list[str]:
    hints: list[str] = []
    references: list[str] = []
    for child in element.iter():
        if _local(child.tag) in ("references", "hasPart", "isReferencedBy", "link"):
            resource = child.get(_q(_RDF_NS, "resource")) or ""
            if resource:
                references.append(resource.lstrip("#"))
    for reference in references:
        attachment = attachments.get(reference)
        if not attachment:
            continue
        path = attachment.get("path") or ""
        if path.lower().endswith(".pdf") and path not in hints:
            hints.append(path)
    key = (element.get(_q(_RDF_NS, "about")) or "").lstrip("#")
    if key:
        for attachment in attachments.values():
            path = attachment.get("path") or ""
            if attachment.get("parent") == key and path.lower().endswith(".pdf") and path not in hints:
                hints.append(path)
    return hints


def parse_zotero_rdf(path: Path) -> list[dict]:
    try:
        root = ET.fromstring(Path(path).read_bytes())
    except ET.ParseError as exc:
        raise ValueError(f"RDF XML 解析失败：{exc}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取 RDF 文件：{exc}") from exc

    objects: dict[str, ET.Element] = {}
    for element in root.iter():
        about = element.get(_q(_RDF_NS, "about")) or element.get(_q(_RDF_NS, 'nodeID'))
        if not about:
            continue
        key = about.lstrip("#").strip()
        if key and key not in objects:
            objects[key] = element
    if not objects:  # 少数导出把对象嵌套在别的节点里
        for element in root.iter():
            about = element.get(_q(_RDF_NS, "about"))
            if not about:
                continue
            key = about.lstrip("#").strip()
            if key and key not in objects:
                objects[key] = element

    attachments: dict[str, dict] = {}
    memo_nodes: dict[str, ET.Element] = {}
    items: list[tuple[str, ET.Element]] = []
    for key, element in objects.items():
        local = _local(element.tag).lower()
        item_type = _rdf_text(element.find(_q(_ZOTERO_NS, "itemType"))).lower()
        rdf_type = element.find(_q(_RDF_NS, 'type'))
        declared_type = (rdf_type.get(_q(_RDF_NS, 'resource'), '') if rdf_type is not None else '').rsplit('#', 1)[-1].lower()
        if item_type in {'note','annotation'} or local == 'memo' or declared_type == 'memo':
            memo_nodes[key] = element
            continue
        if local == "attachment" or item_type == "attachment":
            attachments[key] = _rdf_attachment(element)
            continue
        # Zotero RDF 里除了文献条目，还有 <z:Collection>/<z:Search> 这类结构节点：
        # 它们只有 dc:title（就是分类名、搜索器名字），没有 itemType。把它们当论文导入，
        # 用户库里就会冒出「散射成像」「衍射光学」这种假条目——必须挡掉。
        if not item_type:
            continue
        if item_type in _RDF_NON_BIBLIOGRAPHIC_TYPES:
            continue
        items.append((key, element))

    parsed: list[dict] = []
    used_notes: set[str] = set()
    def references(element):
        for child in element.iter():
            if _local(child.tag) in {'isReferencedBy','hasPart','link'}:
                resource = child.get(_q(_RDF_NS, 'resource')) or child.get(_q(_RDF_NS, 'nodeID'))
                if resource: yield resource.lstrip('#')

    def attached_notes(element, seen):
        notes = []
        for reference in references(element):
            if reference in seen: continue
            seen.add(reference)
            memo = memo_nodes.get(reference)
            if memo is not None:
                raw = _note_xml(memo.find(_q(_RDF_NS, 'value')))
                if not raw:
                    raw = _note_xml(memo.find(_q(_ZOTERO_NS, 'annotationText')))
                    comment = _note_xml(memo.find(_q(_ZOTERO_NS, 'annotationComment')))
                    if comment: raw += '\n\n' + comment
                notes.extend(_notes(('Zotero 笔记 / 批注文本', raw)))
                used_notes.add(reference)
            elif reference in attachments:
                attachment = objects[reference]
                notes.extend(_notes(('Zotero 附件备注', _note_xml(attachment.find(_q(_DC_NS, 'description'))))))
                notes.extend(attached_notes(attachment, seen))
        # Some exports nest the memo rather than referring to an external node.
        for child in element:
            if _local(child.tag) == 'isReferencedBy':
                for memo in child:
                    if _local(memo.tag).lower() == 'memo' and not memo.get(_q(_RDF_NS, 'about')):
                        notes.extend(_notes(('Zotero 笔记', _note_xml(memo.find(_q(_RDF_NS, 'value'))))))
        return notes

    for _key, element in items:
        meta = _rdf_item_meta(element)
        if not meta.get("title"):
            continue
        parsed.append({"meta": meta, "pdf_hints": _rdf_attachment_hints(element, attachments),
                       "notes": attached_notes(element, {_key})})
    if parsed and len(memo_nodes) > len(used_notes):
        parsed[0]['warnings'] = [f'{len(memo_nodes)-len(used_notes)} 条笔记未关联到论文，未擅自归入其他条目；请在 Zotero 中挂到对应论文后重新导出。']
    return parsed


# ---------------------------------------------------------------------------
# 分发 / 导入入口
# ---------------------------------------------------------------------------

def _parse_any(path: Path) -> list[dict]:
    suffix = Path(path).suffix.lower()
    if suffix == ".csv":
        return parse_zotero_csv(path)
    if suffix == ".bib":
        return parse_bibtex(path)
    if suffix == ".ris":
        return parse_ris(path)
    if suffix == ".rdf":
        return parse_zotero_rdf(path)
    raise ValueError(f"不支持的文件格式：{suffix or '无后缀'}（支持 .csv / .bib / .ris / .rdf）")


def import_zotero_file(path: Path) -> dict:
    """导入单个 Zotero 导出文件（.csv/.bib/.ris/.rdf）。"""
    source_path = Path(path).expanduser()
    if not source_path.exists():
        raise FileNotFoundError(f"文件不存在：{source_path}")
    if not source_path.is_file():
        raise IsADirectoryError(f"不是文件：{source_path}")
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"不支持的文件格式：{suffix or '无后缀'}（支持 .csv / .bib / .ris / .rdf）")
    items = _parse_any(source_path)
    pdfs = _scan_pdfs(source_path.parent)
    return _save_items(items, source="zotero", base_dir=source_path.parent, pdfs=pdfs, require_pdf=True)


def _find_zotero_sources(folder: Path) -> list[Path]:
    for suffix in (".rdf", ".csv", ".bib", ".ris"):
        found = sorted(path for path in folder.glob(f"*{suffix}") if path.is_file())
        if found:
            return found[:20]
    return []


def _import_pdfs_only(pdfs: list[Path], result: dict) -> None:
    from .metadata import extract_pdf_metadata

    for pdf_path in pdfs[:200]:
        try:
            meta = extract_pdf_metadata(pdf_path)
        except Exception:  # noqa: BLE001 - 单篇失败不中断
            meta = {"title": pdf_path.stem}
        if not meta.get("title"):
            meta["title"] = pdf_path.stem
        _save_item(
            {"meta": meta, "pdf": pdf_path},
            source="zotero",
            base_dir=pdf_path.parent,
            pdfs=pdfs,
            used=set(),
            result=result,
        )


def import_zotero_folder(folder: Path) -> dict:
    """导入 Zotero 导出文件夹：优先读元数据文件，再按文件名/标题把 files/ 里的 PDF 附上。"""
    root = Path(folder).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"文件夹不存在：{root}")
    if not root.is_dir():
        raise NotADirectoryError(f"不是文件夹：{root}")
    pdfs = _scan_pdfs(root)
    result = _new_result()
    sources = _find_zotero_sources(root)
    if not sources:
        _import_pdfs_only(pdfs, result)
        return result
    used: set[Path] = set()
    for source in sources:
        try:
            items = _parse_any(source)
        except Exception as exc:  # noqa: BLE001
            _add_error(result, f"{source.name}：{exc}")
            continue
        for item in items:
            _save_item(
                item,
                source="zotero",
                base_dir=source.parent,
                pdfs=pdfs,
                used=used,
                result=result,
                require_pdf=True,
            )
    return result


# ---------------------------------------------------------------------------
# Obsidian vault
# ---------------------------------------------------------------------------

def _parse_simple_yaml(block: str) -> dict:
    """无 PyYAML 时的 frontmatter 兜底解析（仅 key: value / 列表）。"""
    data: dict[str, Any] = {}
    current_key: str | None = None
    for raw_line in block.splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        stripped = raw_line.strip()
        if raw_line[:1] in (" ", "\t") and current_key:
            if stripped.startswith("- "):
                existing = data.get(current_key)
                if not isinstance(existing, list):
                    data[current_key] = []
                data[current_key].append(_unquote_yaml(stripped[2:].strip()))
            continue
        match = re.match(r"^([^:]+):\s*(.*)$", raw_line)
        if not match:
            continue
        key = match.group(1).strip().lower()
        value = match.group(2).strip()
        current_key = key
        if not value:
            data[key] = []
        elif value.startswith("[") and value.endswith("]"):
            data[key] = [
                _unquote_yaml(piece.strip())
                for piece in value[1:-1].split(",")
                if piece.strip()
            ]
        else:
            data[key] = _unquote_yaml(value)
    return data


def _unquote_yaml(value: str) -> str:
    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'":
        text = text[1:-1]
    return text


def _parse_frontmatter_block(block: str) -> dict:
    if _yaml is not None:
        try:
            data = _yaml.safe_load(block)
            if isinstance(data, dict):
                return {str(key).strip().lower(): value for key, value in data.items()}
        except Exception:  # noqa: BLE001 - 坏 YAML 走兜底
            pass
    return _parse_simple_yaml(block)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    if not text:
        return {}, ""
    stripped = text.lstrip("\ufeff")
    lines = stripped.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, stripped
    for index in range(1, min(len(lines), 500)):
        if lines[index].strip() in ("---", "..."):
            block = "\n".join(lines[1:index])
            body = "\n".join(lines[index + 1:])
            return _parse_frontmatter_block(block), body
    return {}, stripped


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return _clean(value)
    if isinstance(value, bool):
        return "true" if value else ""
    if isinstance(value, (int, float)):
        return _clean(str(value))
    if isinstance(value, (list, tuple, set)):
        return "; ".join(_to_text(item) for item in value if _to_text(item))
    if isinstance(value, dict):
        return "; ".join(_to_text(item) for item in value.values() if _to_text(item))
    return _clean(str(value))


def _to_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [_to_text(item) for item in value if _to_text(item)]
    text = _to_text(value)
    if not text:
        return []
    return [piece.strip() for piece in re.split(r"[;,，；]", text) if piece.strip()]


def _authors_from_frontmatter(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple, set)):
        names = [_format_person(_to_text(item)) for item in value if _to_text(item)]
        return "; ".join(name for name in names if name)
    text = _to_text(value)
    if ";" in text:
        names = [_format_person(piece) for piece in text.split(";") if piece.strip()]
        return "; ".join(name for name in names if name)
    return text


def _clean_obsidian_link(value: Any) -> str:
    text = _clean(str(value or ""))
    if not text:
        return ""
    text = re.sub(r"^!?\[\[", "", text)
    text = re.sub(r"\]\]$", "", text)
    text = text.split("|")[0].strip()
    text = text.split("#")[0].strip()
    return text


def _obsidian_pdf(
    md_path: Path,
    frontmatter: dict,
    body: str,
    vault: Path | None,
    pdfs: list[Path],
    used: set[Path] | None = None,
) -> Path | None:
    vault = vault or md_path.parent
    hints: list[str] = []
    for key in ("pdf", "pdfs", "file", "files", "attachment", "attachments", "paper"):
        value = frontmatter.get(key)
        if not value:
            continue
        for piece in _to_list(value) if not isinstance(value, (list, tuple, set)) else list(value):
            cleaned = _clean_obsidian_link(piece)
            if cleaned:
                hints.append(cleaned)
    for match in re.findall(r"!?\[\[([^\]\n]+?\.pdf)(?:\|[^\]\n]*)?\]\]", body or "", re.IGNORECASE):
        cleaned = _clean_obsidian_link(match)
        if cleaned:
            hints.append(cleaned)
    for hint in hints:
        found = _resolve_attachment(hint, md_path.parent, pdfs, extra_bases=[vault])
        if found is not None:
            return found

    same_name = md_path.with_suffix(".pdf")
    if same_name.is_file():
        return same_name
    stem_normalized = _normalize_for_match(md_path.stem)
    if stem_normalized:
        for path in pdfs:
            if (path not in (used or set())) and _normalize_for_match(path.stem) == stem_normalized:
                return path
    title = _clean(frontmatter.get("title") or "") or _clean(md_path.stem)
    return _best_pdf({"title": title}, pdfs, used)


def parse_obsidian_note(
    md_path: Path,
    *,
    vault: Path | None = None,
    pdfs: list[Path] | None = None,
    used: set[Path] | None = None,
) -> dict | None:
    """解析一篇 Obsidian 笔记 → {meta, pdf}；不像论文笔记时返回 None。"""
    path = Path(md_path)
    text = _read_text(path)
    frontmatter, body = _split_frontmatter(text)

    note_type = _to_text(frontmatter.get("type") or frontmatter.get("note_type")).lower()
    if note_type in {"daily", "daily-note", "dailynote", "template", "journal"}:
        return None

    heading_match = re.search(r"^#\s+(.+?)\s*$", body or "", re.MULTILINE)
    heading = _clean(heading_match.group(1)) if heading_match else ""
    if not frontmatter and not heading:
        return None

    title = _to_text(frontmatter.get("title") or frontmatter.get("name")) or heading or _clean(path.stem)
    if not title:
        return None

    url = _to_text(frontmatter.get("url") or frontmatter.get("source") or frontmatter.get("link"))
    doi = normalize_doi(frontmatter.get("doi"))
    if not doi:
        doi = normalize_doi(url) or normalize_doi(_to_text(frontmatter.get("DOI")))
    if not doi:
        doi_match = DOI_PATTERN.search(body[:4000] if body else "")
        if doi_match:
            doi = normalize_doi(doi_match.group(0))
    arxiv_id = normalize_arxiv_id(
        frontmatter.get("arxiv_id") or frontmatter.get("arxiv") or frontmatter.get("eprint")
    )
    if not arxiv_id:
        arxiv_id = _arxiv_from_text(url) or _arxiv_from_text(doi)
    if not arxiv_id:
        arxiv_match = re.search(r"arxiv[:\s]+([A-Za-z.\-/\d]+)", body[:4000] if body else "", re.IGNORECASE)
        if arxiv_match:
            arxiv_id = normalize_arxiv_id(arxiv_match.group(1))

    tags: list[str] = []
    for tag in _split_tags(frontmatter.get("tags") or frontmatter.get("tag") or frontmatter.get("keywords")):
        if tag not in tags:
            tags.append(tag)
    for tag in re.findall(r"(?:^|\s)#([^\s#][\w\u4e00-\u9fff/\-]*)", body[:8000] if body else "", re.MULTILINE):
        cleaned = _clean(tag).lstrip("#")
        if cleaned and cleaned.lower() not in {existing.lower() for existing in tags}:
            tags.append(cleaned[:60])

    meta = clean_meta(
        {
            "title": title,
            "authors": _authors_from_frontmatter(
                frontmatter.get("authors") or frontmatter.get("author") or frontmatter.get("creators")
            ),
            "year": _year_from_raw(
                frontmatter.get("year")
                or frontmatter.get("date")
                or frontmatter.get("published")
                or frontmatter.get("created")
            ),
            "venue": _to_text(
                frontmatter.get("venue")
                or frontmatter.get("journal")
                or frontmatter.get("publication")
                or frontmatter.get("conference")
            ),
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": url,
            "abstract": _to_text(frontmatter.get("abstract")),
            "tags": tags,
            "category": _to_text(frontmatter.get("category")),
        }
    )
    pdf_path = _obsidian_pdf(path, frontmatter, body, vault, list(pdfs or []), used)
    return {"meta": meta, "pdf": pdf_path}


def _iter_markdown_files(vault: Path) -> list[Path]:
    files: list[Path] = []
    for path in vault.rglob("*.md"):
        try:
            relative = path.relative_to(vault)
        except ValueError:
            continue
        parts = relative.parts
        if any(part.startswith(".") for part in parts):
            continue
        if any(part.lower() in _SKIP_DIR_NAMES for part in parts[:-1]):
            continue
        files.append(path)
        if len(files) >= _MAX_MD_FILES:
            break
    return sorted(files)


def import_obsidian_vault(folder: Path) -> dict:
    """递归导入 Obsidian vault 里的论文笔记，并关联 vault 内的 PDF。"""
    root = Path(folder).expanduser()
    if not root.exists():
        raise FileNotFoundError(f"文件夹不存在：{root}")
    if not root.is_dir():
        raise NotADirectoryError(f"不是文件夹：{root}")
    pdfs = _scan_pdfs(root)
    result = _new_result()
    used: set[Path] = set()
    for md_path in _iter_markdown_files(root):
        try:
            item = parse_obsidian_note(md_path, vault=root, pdfs=pdfs, used=used)
        except Exception as exc:  # noqa: BLE001
            _add_error(result, f"{md_path.name}：{exc}")
            continue
        if not item:
            result["skipped"] += 1
            continue
        _save_item(
            item,
            source="obsidian",
            base_dir=md_path.parent,
            pdfs=pdfs,
            used=used,
            result=result,
        )
    return result

# ---------------------------------------------------------------------------
# EndNote 迁移（EndNote XML 导出 + .Data/PDF 附件）
# ---------------------------------------------------------------------------

SUPPORTED_ENDNOTE_SUFFIXES = (".xml", ".enl", ".ris", ".bib", ".csv")


def _endnote_text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return _clean(" ".join("".join(element.itertext()).split()))


def _endnote_author(element: ET.Element) -> str:
    text = " ".join("".join(element.itertext()).split())
    if not text:
        return ""
    # EndNote XML 把姓名写成 "Family, Given"；库里统一用 "Given Family"
    if "," in text and text.count(",") == 1:
        family, given = [piece.strip() for piece in text.split(",", 1)]
        if family and given:
            return f"{given} {family}"
    return text


def _endnote_data_dir(source: Path) -> Path | None:
    """EndNote 库的附件目录：<库名>.Data（PDF 在 <库名>.Data/PDF 下）。"""
    for candidate in (source.with_suffix(".Data"), source.parent / f"{source.stem}.Data"):
        try:
            if candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _endnote_pdf_file(hint: str, data_dir: Path | None) -> Path | None:
    """把 EndNote 的附件标记解析成本机真实 PDF。

    形如 internal-pdf://1234567890/Some Paper.pdf 或 file:///C:/x/paper.pdf。
    """
    raw = _clean(str(hint or ""))
    if not raw:
        return None
    if raw.lower().startswith("internal-pdf://"):
        name = raw.split("//", 1)[-1]
        name = name.split("/", 1)[-1] if "/" in name else name
        name = unquote(name)
        if not name or not data_dir:
            return None
        target = data_dir / "PDF"
        if not target.is_dir():
            return None
        wanted = name.lower()
        try:
            candidates = sorted(target.rglob("*.pdf"))[:4000]
        except OSError:
            return None
        for candidate in candidates:
            if candidate.name.lower() == wanted or candidate.stem.lower() == Path(wanted).stem:
                return candidate
        return None
    if raw.lower().startswith("file:///"):
        text = unquote(raw[8:]).replace("/", "\\")
        candidate = Path(text)
        return candidate if candidate.suffix.lower() == ".pdf" and candidate.is_file() else None
    return None


def parse_endnote_xml(path: Path) -> list[dict]:
    """解析 EndNote「File → Export → XML」导出的文件。"""
    source = Path(path)
    try:
        root = ET.fromstring(source.read_bytes())
    except ET.ParseError as exc:
        raise ValueError(f"EndNote XML 解析失败：{exc}") from exc
    except OSError as exc:
        raise ValueError(f"无法读取 EndNote XML：{exc}") from exc
    data_dir = _endnote_data_dir(source)
    records: list[dict] = []
    for record in root.iter("record"):
        titles = record.find("titles")
        title = _endnote_text(titles.find("title")) if titles is not None else ""
        if not title:
            continue
        authors: list[str] = []
        for author in record.findall("./contributors/authors/author"):
            name = _endnote_author(author)
            if name and name not in authors:
                authors.append(name)
        venue = ""
        if titles is not None:
            venue = _endnote_text(titles.find("secondary-title")) or _endnote_text(titles.find("tertiary-title"))
        year = ""
        dates = record.find("dates")
        if dates is not None:
            year = _year_from_raw(_endnote_text(dates.find("year")) or _endnote_text(dates.find("date")))
        doi = normalize_doi(_endnote_text(record.find("electronic-resource-num")))
        urls: list[str] = []
        pdf_hints: list[str] = []
        for url_element in record.findall("./urls/related-urls/url"):
            text = _endnote_text(url_element)
            if text:
                urls.append(text)
        for url_element in record.findall("./urls/pdf-urls/url"):
            text = _endnote_text(url_element)
            if text:
                pdf_hints.append(text)
        url = next((item for item in urls if item.lower().startswith(("http://", "https://"))), "")
        if not doi and url:
            doi = normalize_doi(url)
        abstract_parts = [
            _clean(" ".join("".join(node.itertext()).split()))
            for node in record.findall("./abstract/p")
        ]
        abstract = " ".join(part for part in abstract_parts if part)
        if not abstract:
            abstract = _endnote_text(record.find("abstract"))
        tags = [
            _clean(" ".join("".join(node.itertext()).split()))
            for node in record.findall("./keyword/keyword")
        ]
        tags = [tag for tag in tags if tag]
        resolved: list[str] = []
        for hint in pdf_hints:
            found = _endnote_pdf_file(hint, data_dir)
            if found is not None:
                resolved.append(str(found))
        meta = clean_meta(
            {
                "title": title,
                "authors": "; ".join(authors),
                "year": year,
                "venue": venue,
                "volume": _endnote_text(record.find("volume")),
                "issue": _endnote_text(record.find("number")),
                "pages": _endnote_text(record.find("pages")),
                "doi": doi,
                "url": url,
                "abstract": abstract,
                "tags": tags,
                "category": "",
            }
        )
        records.append({"meta": meta, "pdf_hints": resolved,
                        "notes": _notes(('EndNote Notes', _note_xml(record.find('notes'))),
                                        ('EndNote Research Notes', _note_xml(record.find('research-notes'))))})
    return records


def find_endnote_source(folder: Path) -> Path | None:
    """在用户选的文件夹里找 EndNote 库/导出文件：优先 XML，其次 .enl，再退 RIS/BibTeX。"""
    root = Path(folder)
    for pattern in ("*.xml", "*.enl", "*.ris", "*.bib"):
        try:
            candidates = sorted(path for path in root.glob(pattern) if path.is_file())
        except OSError:
            candidates = []
        if candidates:
            return candidates[0]
    return None


def import_endnote_path(path: Path, *, include_without_pdf: bool = True) -> dict:
    """迁移一个 EndNote 库：接受 .xml 导出文件、.enl 库文件或所在的文件夹。"""
    source = Path(path).expanduser()
    if not source.exists():
        raise FileNotFoundError(f"路径不存在：{source}")
    if source.is_dir():
        found = find_endnote_source(source)
        if found is None:
            raise ValueError("这个文件夹里没找到 EndNote 的 .xml/.enl/.ris/.bib 文件")
        source = found
    suffix = source.suffix.lower()
    if suffix == ".enl":
        raise ValueError(
            "EndNote 的 .enl 是它的专用数据库，当前版本只读 XML 导出："
            "在 EndNote 里 File → Export → 格式选 XML，勾选 Export File Attachments，"
            "导出到库旁边再选那个 .xml 文件即可（PDF 会自动一起迁入）。"
        )
    if suffix == ".xml":
        items = parse_endnote_xml(source)
        data_dir = _endnote_data_dir(source)
        pdfs = _scan_pdfs(data_dir) if data_dir is not None else _scan_pdfs(source.parent)
    elif suffix in (".ris", ".bib", ".csv"):
        items = _parse_any(source)
        pdfs = _scan_pdfs(source.parent)
    else:
        raise ValueError(f"不支持的文件类型：{suffix}（支持 .xml / .ris / .bib / .csv）")
    result = _save_items(
        items,
        source="endnote",
        base_dir=source.parent,
        pdfs=pdfs,
        require_pdf=not include_without_pdf,
    )
    result["source_file"] = str(source)
    result["pdf_files"] = len(pdfs)
    return result
