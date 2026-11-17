"""导入 API：Zotero 文件/文件夹、Obsidian vault、DOI / arXiv / URL 标识符。"""
from __future__ import annotations

import re
import shutil
import tempfile
import os
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile

from .. import config, db
from ..models import EndnoteIn, IdentifierIn, PathIn, PdfFolderIn
from ..services import discover as discover_service
from ..services import importers, metadata
from ..services.metadata import DOI_PATTERN, clean_meta, normalize_arxiv_id, normalize_doi
from ..services.pdf_fetch import manager as pdf_fetch_manager

router = APIRouter(prefix="/api", tags=["import"])


def _schedule_pdf_fetch(result: dict) -> dict:
    """导入完成后把没有 PDF 的条目丢进后台抓取队列。"""
    try:
        imported = result.get("imported") or []
        ids = [
            paper.get("id")
            for paper in imported
            if isinstance(paper, dict) and paper.get("id") and not paper.get("has_pdf")
        ]
        if ids:
            result["pdf_fetch_queued"] = pdf_fetch_manager.start(ids)
    except Exception:  # noqa: BLE001 - 不影响导入结果
        pass
    return result

_ALLOWED_ZOTERO_SUFFIXES = (".csv", ".bib", ".ris", ".rdf")
_ARXIV_BARE_PATTERN = re.compile(
    r"^(?:\d{4}\.\d{4,5}|[a-z][a-z\-]*(?:\.[a-z\-]+)?/\d{7})(?:v\d+)?$",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# 公共小工具
# ---------------------------------------------------------------------------

def _translate_exception(exc: Exception, action: str = "导入") -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    status = getattr(exc, "status", None)
    if isinstance(exc, (metadata.MetadataError, discover_service.DiscoverError)) and isinstance(status, int):
        return HTTPException(status, str(exc))
    if isinstance(exc, (ValueError, FileNotFoundError, NotADirectoryError, PermissionError, OSError)):
        return HTTPException(400, f"{action}失败：{exc}")
    return HTTPException(500, f"{action}失败：{type(exc).__name__}: {exc}")


def _validate_folder(raw_path: str) -> Path:
    text = (raw_path or "").strip().strip("\"'")
    if not text:
        raise HTTPException(400, "路径不能为空")
    path = Path(text).expanduser()
    if not path.exists():
        raise HTTPException(400, f"文件夹不存在：{text}")
    if not path.is_dir():
        raise HTTPException(400, f"不是文件夹：{text}")
    return path


def _create_paper(meta: dict, pdf_path: Path | None = None, source: str = "manual") -> dict:
    payload = clean_meta(meta)
    if not payload.get("title"):
        raise HTTPException(400, "没有解析到论文标题，无法创建条目")
    try:
        from ..services.ingest import create_paper_from_metadata

        paper_id = create_paper_from_metadata(payload, pdf_path=pdf_path, source=source)
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"保存论文失败：{exc}") from exc
    paper = db.get_paper(paper_id)
    if not paper:
        paper = {"id": paper_id, **payload}
    return paper


def _parse_identifier(raw: str) -> tuple[str, str]:
    """识别标识符 → (kind, value)，kind ∈ {doi, arxiv, url}。"""
    text = (raw or "").strip().strip("<>").strip()
    if not text:
        raise HTTPException(400, "请输入 DOI、arXiv ID 或论文网址")

    match = re.search(r"https?://(?:dx\.)?doi\.org/(10\..+)$", text, re.IGNORECASE)
    if match:
        doi = normalize_doi(match.group(1))
        if doi:
            return "doi", doi
    match = re.search(r"https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/([^\s?#]+)", text, re.IGNORECASE)
    if match:
        arxiv_id = normalize_arxiv_id(match.group(1))
        if arxiv_id:
            return "arxiv", arxiv_id
    match = re.search(r"arxiv[:\s/]+([^\s?#]+)", text, re.IGNORECASE)
    if match:
        arxiv_id = normalize_arxiv_id(match.group(1))
        if arxiv_id:
            return "arxiv", arxiv_id
    if _ARXIV_BARE_PATTERN.match(text):
        arxiv_id = normalize_arxiv_id(text)
        if arxiv_id:
            return "arxiv", arxiv_id
    if re.match(r"^https?://", text, re.IGNORECASE):
        return "url", text
    match = DOI_PATTERN.search(text)
    if match:
        doi = normalize_doi(match.group(0))
        if doi:
            return "doi", doi
    raise HTTPException(400, f"无法识别的标识符：{raw}（支持 DOI / arXiv ID / 论文网址）")


# ---------------------------------------------------------------------------
# Zotero / Obsidian
# ---------------------------------------------------------------------------

@router.post("/import/zotero")
def import_zotero(file: UploadFile = File(...)) -> dict:
    filename = file.filename or "zotero-export"
    suffix = Path(filename).suffix.lower()
    if suffix not in _ALLOWED_ZOTERO_SUFFIXES:
        raise HTTPException(
            400,
            f"不支持的文件格式：{suffix or '无后缀'}（支持 .csv / .bib / .ris / .rdf）",
        )
    config.ensure_dirs()
    file_descriptor, temp_name = tempfile.mkstemp(
        prefix="paperwhale-import-", suffix=suffix, dir=str(config.TMP_DIR)
    )
    temp_path = Path(temp_name)
    try:
        with open(file_descriptor, "wb") as handle:
            shutil.copyfileobj(file.file, handle)
        try:
            return _schedule_pdf_fetch(importers.import_zotero_file(temp_path))
        except Exception as exc:  # noqa: BLE001
            raise _translate_exception(exc) from exc
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


@router.post("/import/endnote")
def import_endnote(body: EndnoteIn) -> dict:
    """从 EndNote 迁移：读 XML 导出（含 .Data/PDF 附件），或给一个库所在文件夹。"""
    text = (body.path or "").strip().strip('"\'')
    if not text:
        raise HTTPException(400, "路径不能为空")
    target = Path(os.path.expandvars(os.path.expanduser(text)))
    try:
        result = importers.import_endnote_path(target, include_without_pdf=body.include_without_pdf)
        return _schedule_pdf_fetch(result)
    except Exception as exc:  # noqa: BLE001
        raise _translate_exception(exc, "EndNote 迁移") from exc


@router.post("/import/zotero-folder")
def import_zotero_folder(body: PathIn) -> dict:
    folder = _validate_folder(body.path)
    try:
        return _schedule_pdf_fetch(importers.import_zotero_folder(folder))
    except Exception as exc:  # noqa: BLE001
        raise _translate_exception(exc) from exc


@router.post("/import/pdf-folder")
def import_pdf_folder(body: PdfFolderIn) -> dict:
    """扫描一个本地文件夹里的 PDF：能对上已有论文就补 PDF，对不上就新建论文条目。"""
    from ..services import ingest

    folder = _validate_folder(body.path)
    pattern = "**/*.pdf" if body.recursive else "*.pdf"
    skipped_not_pdf: list[str] = []
    try:
        candidates = sorted(path for path in folder.glob(pattern) if path.is_file())
    except OSError as exc:
        raise _translate_exception(exc, "扫描文件夹") from exc
    files = []
    for candidate in candidates:
        if ingest.is_real_pdf(candidate):
            files.append(candidate)
        else:
            # 后缀是 .pdf 但内容不是 PDF（下载残片、改名文件），绝不能建条目
            skipped_not_pdf.append(candidate.name)
        if len(files) >= 500:
            break
    if not files:
        raise HTTPException(400, "这个文件夹里没有找到有效的 PDF 文件")

    created: list[dict] = []
    matched: list[dict] = []
    already: list[dict] = []
    errors: list[str] = []
    for path in files:
        try:
            result = ingest.import_pdf_file(path, source="folder", category_path=body.category_path)
        except Exception as exc:  # noqa: BLE001 - 单个文件失败不中断整批
            errors.append(f"{path.name}: {exc}"[:300])
            continue
        entry = {"id": result["id"], "title": result["title"], "path": str(path)}
        if result["action"] == "created":
            created.append(entry)
        elif result["action"] == "matched":
            matched.append(entry)
        else:
            already.append(entry)
    return {
        "folder": str(folder),
        "scanned": len(files),
        "created": created,
        "matched": matched,
        "already_has_pdf": already,
        "skipped_not_pdf": skipped_not_pdf[:50],
        "errors": errors,
    }


@router.post("/import/obsidian")
def import_obsidian(body: PathIn) -> dict:
    folder = _validate_folder(body.path)
    try:
        return _schedule_pdf_fetch(importers.import_obsidian_vault(folder))
    except Exception as exc:  # noqa: BLE001
        raise _translate_exception(exc) from exc


# ---------------------------------------------------------------------------
# 标识符导入
# ---------------------------------------------------------------------------

@router.post("/import/identifier")
def import_identifier(body: IdentifierIn) -> dict:
    kind, value = _parse_identifier(body.identifier or "")
    if kind == "doi":
        paper = _import_by_doi(value)
    elif kind == "arxiv":
        paper = _import_by_arxiv(value)
    else:
        paper = _import_by_url(value)
    result = {"paper": paper}
    if paper and not paper.get("has_pdf"):
        try:
            result["pdf_fetch_queued"] = pdf_fetch_manager.start([paper["id"]])
        except Exception:  # noqa: BLE001
            pass
    return result


def _import_by_doi(doi: str) -> dict:
    try:
        meta = metadata.crossref_by_doi(doi)
    except metadata.MetadataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    pdf_url = meta.pop("pdf_url", "")
    pdf_path: Path | None = None
    try:
        pdf_path = discover_service.find_pdf_for_doi(doi, meta.get("title") or "", pdf_url=pdf_url)
    except Exception:  # noqa: BLE001 - PDF 只是加分项
        pdf_path = None
    try:
        return _create_paper(meta, pdf_path=pdf_path, source="doi")
    finally:
        if pdf_path is not None:
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass


def _import_by_arxiv(arxiv_id: str) -> dict:
    try:
        meta = metadata.arxiv_meta(arxiv_id)
    except metadata.MetadataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    pdf_path: Path | None = None
    try:
        pdf_path, _downloaded_meta = discover_service.download_arxiv(arxiv_id)
    except discover_service.DiscoverError:
        pdf_path = None
    except metadata.MetadataError:
        pdf_path = None
    except Exception:  # noqa: BLE001
        pdf_path = None
    try:
        return _create_paper(meta, pdf_path=pdf_path, source="arxiv")
    finally:
        if pdf_path is not None:
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass


def _title_from_url(url: str) -> str:
    try:
        name = Path(url.split("?")[0].split("#")[0]).name
    except (TypeError, ValueError):
        name = ""
    name = re.sub(r"\.(pdf|html?)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"[-_+]+", " ", name).strip()
    return name[:300]


def _import_by_url(url: str) -> dict:
    # 1) 直接当 PDF 下载
    pdf_path: Path | None = None
    try:
        pdf_path = discover_service.fetch_pdf(url)
    except Exception:  # noqa: BLE001 - 不是 PDF 就走网页解析
        pdf_path = None
    if pdf_path is not None:
        try:
            try:
                meta = metadata.extract_pdf_metadata(pdf_path)
            except metadata.MetadataError:
                meta = {}
            meta.setdefault("title", "")
            meta["title"] = (meta.get("title") or "").strip() or _title_from_url(url)
            meta["url"] = meta.get("url") or url
            return _create_paper(meta, pdf_path=pdf_path, source="manual")
        finally:
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass
    # 2) 抓网页 meta 标签
    try:
        html_text = discover_service.fetch_text(url)
    except discover_service.DiscoverError as exc:
        raise HTTPException(exc.status, f"无法访问该网址：{exc}") from exc
    meta = metadata.metadata_from_html(html_text, url)
    if not meta.get("title"):
        raise HTTPException(400, "无法从该网址识别论文信息（没有标题），请改用手动上传 PDF")
    return _create_paper(meta, pdf_path=None, source="manual")
