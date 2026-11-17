"""浏览器扩展「网页一键入库」接口。"""
from __future__ import annotations

import base64
import tempfile
from pathlib import Path

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config, db
from ..services import analysis_queue, ingest, metadata, pdf_fetch, pdf_download, pdf_trace

router = APIRouter(prefix="/api", tags=["webimport"])

MAX_PDF_BYTES = 80 * 1024 * 1024
UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36 PaperWhale/1.0"
)


class WebImportIn(BaseModel):
    title: str = ""
    authors: str | list[str] | None = None
    year: str = ""
    venue: str = ""
    volume: str = ""
    issue: str = ""
    pages: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    abstract: str = ""
    tags: str | list[str] | None = None
    item_type: str = "article"
    pdf_url: str = ""
    pdf_base64: str = ""
    pdf_name: str = ""
    pdf_cookies: str = ""
    pdf_referer: str = ""
    pdf_user_agent: str = ""
    pdf_candidates: list[str] = Field(default_factory=list)
    pdf_download_path: str = ""
    page_html: str = ""
    category_path: list[str] = Field(default_factory=list)
    pdf_attempts: list[dict] = Field(default_factory=list)
    download_pdf: bool = True
    analyze: bool = False


def _text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _normalize_pdf_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    if "arxiv.org/abs/" in url:
        return url.replace("/abs/", "/pdf/")
    return url


def _download_pdf(url: str, referer: str='', cookies: str='', user_agent: str='') -> Path | None:
    headers={}
    if referer:
        headers['Referer']=pdf_trace.request_referer(referer,url)
    if user_agent and len(user_agent)<=512 and all(ord(char)>=32 and ord(char)!=127 for char in user_agent):
        headers['User-Agent']=user_agent
    if cookies:
        headers['Cookie']=cookies
    try:
        return pdf_download.download(_normalize_pdf_url(url),headers=headers,route='browser-extension').path
    except pdf_download.PdfDownloadError:
        return None


def _download_candidates(urls: list[str], referer: str='', cookies: str='', user_agent: str='') -> Path | None:
    seen: set[str] = set()
    for raw in urls:
        url = _normalize_pdf_url(str(raw or '').strip())
        if not url.startswith(('http://', 'https://')) or url in seen:
            continue
        seen.add(url)
        path = _download_pdf(url, referer, cookies, user_agent)
        if path:
            return path
        if len(seen) >= 8:
            break
    return None


def _pdf_from_path(raw_path: str) -> Path | None:
    """浏览器下载到本机的 PDF 路径（只读源文件，复制到临时文件后入库）。"""
    import shutil

    try:
        source = Path(str(raw_path or '').strip())
        if not source.is_absolute() or not source.is_file():
            return None
        size = source.stat().st_size
        if size < 1024 or size > MAX_PDF_BYTES:
            return None
        with source.open('rb') as handle:
            if b'%PDF' not in handle.read(1024):
                return None
        fd, tmp_name = tempfile.mkstemp(suffix='.pdf')
        import os
        os.close(fd)
        target = Path(tmp_name)
        shutil.copyfile(source, target)
        pdf_download.validate_pdf(target)
        return target
    except (OSError, pdf_download.PdfDownloadError):
        return None


def _pdf_from_base64(data: str, name: str = "") -> Path | None:
    """浏览器插件用网页登录态抓到的 PDF（比后端下载更容易过出版社）。"""
    raw = (data or "").strip()
    if not raw or len(raw)>MAX_PDF_BYTES*4//3+1024:
        return None
    if "," in raw[:100] and raw.lstrip().startswith("data:"):
        raw = raw.split(",", 1)[1]
    try:
        blob = base64.b64decode(raw, validate=True)
    except Exception:  # noqa: BLE001
        return None
    if len(blob) < 1024 or len(blob) > MAX_PDF_BYTES:
        return None
    if b"%PDF" not in blob[:1024]:
        return None
    fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
    import os

    os.close(fd)
    path = Path(tmp_name)
    path.write_bytes(blob)
    try:
        pdf_download.validate_pdf(path)
    except pdf_download.PdfDownloadError:
        path.unlink(missing_ok=True)
        return None
    return path


@router.post("/import/web")
def import_web(body: WebImportIn) -> dict:
    title = body.title.strip()
    if body.page_html:
        try:
            (config.DATA_DIR / 'sd-debug-page.html').write_text(body.page_html[:2_000_000], encoding='utf-8')
        except OSError:
            pass
    if not title:
        raise HTTPException(400, "网页没有提取到标题，无法入库")
    meta = {
        "title": title,
        "authors": _text(body.authors),
        "year": body.year.strip(),
        "venue": body.venue.strip(),
        "volume": body.volume.strip(),
        "issue": body.issue.strip(),
        "pages": body.pages.strip(),
        "doi": body.doi.strip().lower().removeprefix("https://doi.org/").removeprefix("doi:"),
        "arxiv_id": body.arxiv_id.strip(),
        "url": body.url.strip(),
        "abstract": body.abstract.strip(),
        "tags": body.tags,
        "source": "browser",
        "category_path": list(body.category_path or []),
    }
    # 旧版扩展只发 page_html：服务端从网页 meta 标签兜底补卷期页（只补空值）。
    # page_html 上限 2MB，meta 标签都在 <head>，截前 50 万字符解析即可；失败静默忽略。
    if body.page_html and not all(
        str(part or "").strip() for part in (meta["volume"], meta["issue"], meta["pages"])
    ):
        try:
            parsed = metadata.metadata_from_html(body.page_html[:500_000])
        except Exception:  # noqa: BLE001 - 网页解析失败不能挡住导入
            parsed = {}
        if isinstance(parsed, dict):
            for key in ("volume", "issue", "pages"):
                if not str(meta.get(key) or "").strip() and parsed.get(key):
                    meta[key] = parsed[key]
    meta = {key: value for key, value in meta.items() if value not in (None, "", [])}

    browser_attempts=pdf_fetch.sanitize_browser_attempts(body.pdf_attempts)
    pdf_warning=''
    def validation_failure(code):
        nonlocal browser_attempts,pdf_warning
        pdf_warning=pdf_trace.MESSAGES[code]
        browser_attempts=browser_attempts[:23]+pdf_fetch.sanitize_browser_attempts([
            {'url':body.pdf_url or body.url,'code':code,'route':'browser-validation'}],allow_validation=True)

    pdf_path: Path | None = None
    if body.pdf_base64:
        pdf_path = _pdf_from_base64(body.pdf_base64, body.pdf_name)
        if pdf_path is None:
            validation_failure('invalid_pdf')
    if pdf_path is None and body.pdf_download_path:
        pdf_path = _pdf_from_path(body.pdf_download_path)
    if pdf_path is None and body.download_pdf:
        urls = [body.pdf_url, *(body.pdf_candidates if isinstance(body.pdf_candidates, list) else [])]
        pdf_path = _download_candidates(
            [str(item) for item in urls if str(item or '').strip()],
            body.pdf_referer or body.url,
            body.pdf_cookies,
            body.pdf_user_agent,
        )
    identity={}
    if pdf_path:
        try:
            identity=pdf_download.verify_identity(pdf_path,meta)
        except pdf_download.PdfDownloadError as exc:
            pdf_path.unlink(missing_ok=True)
            pdf_path=None
            validation_failure(exc.code if exc.code in pdf_trace.MESSAGES else 'invalid_pdf')
    try:
        before = db.find_duplicate(
            title=meta["title"], doi=meta.get("doi", ""), arxiv_id=meta.get("arxiv_id", "")
        )
        previous=db.get_paper(before) if before else None
        paper_id = ingest.create_paper_from_metadata(meta, pdf_path=pdf_path, source="browser")
        if pdf_path and not (previous or {}).get('file_path'):
            import hashlib
            import json

            with pdf_path.open('rb') as handle:
                digest=hashlib.file_digest(handle,'sha256').hexdigest()
            provenance={'source':'browser-extension','url':pdf_trace.safe_url(body.pdf_url),'version':'',
                        'identity':identity,'sha256':digest,'fetched_at':db.now()}
            db.update_paper(paper_id,{'pdf_acquisition':json.dumps(provenance,ensure_ascii=False),'pdf_error':''})
    finally:
        if pdf_path is not None:
            try:
                pdf_path.unlink(missing_ok=True)
            except OSError:
                pass

    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(500, "入库失败")

    pdf_state = "attached" if paper.get("file_path") else "none"
    # Persist before start(): the worker may save its first report immediately.
    pdf_fetch.manager.record_browser_attempts(paper_id,browser_attempts,has_pdf=bool(paper.get('has_pdf')))
    if not paper.get("file_path") and body.download_pdf:
        # 网页没给 PDF 直链时，用 DOI / arXiv / 标题去后台找
        queued = pdf_fetch.manager.start([paper_id])
        if queued:
            pdf_state = "queued"

    if body.analyze and paper.get("file_path"):
        analysis_queue.queue_manager.start([paper_id])

    return {
        "paper": paper,
        "duplicate": bool(before and before == paper_id),
        "pdf": pdf_state,
        "pdf_warning":pdf_warning,
        "browser_attempts":browser_attempts,
    }
