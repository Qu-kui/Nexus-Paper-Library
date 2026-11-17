"""在线发现：arXiv 检索、PDF 下载、DOI → 开放获取 PDF 兜底。"""
from __future__ import annotations

import re
import uuid
from difflib import SequenceMatcher
from pathlib import Path

import httpx

from .. import config
from . import metadata as metadata_service

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}.pdf"
MAX_RESULTS = 50
DOWNLOAD_TIMEOUT = 60.0


class DiscoverError(Exception):
    """在线发现失败；``status`` 为建议的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _clamp_limit(limit: int | None) -> int:
    try:
        value = int(limit or 10)
    except (TypeError, ValueError):
        value = 10
    return max(1, min(value, MAX_RESULTS))


def arxiv_search(query: str, limit: int = 10) -> list[dict]:
    """按 arXiv API 检索，返回契约字段列表（arxiv_id 已去版本号）。"""
    keyword = (query or "").strip()
    if not keyword:
        return []
    count = _clamp_limit(limit)
    params = {
        "search_query": f"all:{keyword}",
        "start": 0,
        "max_results": count,
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    try:
        response = metadata_service._get(
            ARXIV_API,
            params=params,
            timeout=metadata_service.ARXIV_TIMEOUT,
            headers={
                "User-Agent": metadata_service.USER_AGENT,
                "Accept": "application/atom+xml",
            },
        )
    except (httpx.HTTPError,metadata_service.MetadataError) as exc:
        raise DiscoverError(f"arXiv 请求失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise DiscoverError(f"arXiv 返回 HTTP {response.status_code}", 502)
    try:
        items = metadata_service.parse_arxiv_feed(response.text)
    except ValueError as exc:
        raise DiscoverError(f"arXiv 返回内容无法解析：{exc}", 502) from exc
    return items[:count]


def fetch_pdf(url: str, filename: str | None = None, headers: dict | None = None) -> Path:
    """下载 URL 指向的 PDF 到 config.TMP_DIR；不是 PDF 或下载失败抛 DiscoverError。"""
    from . import pdf_download

    try:
        return pdf_download.download((url or '').strip(), filename=filename, headers=headers).path
    except pdf_download.PdfDownloadError as exc:
        raise DiscoverError(str(exc),exc.status or 502) from exc



def fetch_text(url: str) -> str:
    """抓网页 HTML 文本（identifier 导入里按标题建条目用）。"""
    target_url = (url or "").strip()
    if not re.match(r"^https?://", target_url, re.IGNORECASE):
        raise DiscoverError(f"不是合法的 http(s) 地址：{target_url}", 400)
    try:
        response = httpx.get(
            target_url,
            timeout=metadata_service.DEFAULT_TIMEOUT,
            follow_redirects=True,
            headers={"User-Agent": metadata_service.USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
        )
    except httpx.HTTPError as exc:
        raise DiscoverError(f"访问网页失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise DiscoverError(f"访问网页失败：HTTP {response.status_code}", 502)
    content_type = str(response.headers.get("content-type") or "").lower()
    if "pdf" in content_type:
        raise DiscoverError("该链接是 PDF 而不是网页", 400)
    return response.text


def download_arxiv(arxiv_id: str) -> tuple[Path, dict]:
    """下载 arXiv PDF 到临时目录，返回 (path, meta)。"""
    normalized = metadata_service.normalize_arxiv_id(arxiv_id)
    if not normalized:
        raise DiscoverError(f"无法识别的 arXiv ID：{arxiv_id}", 400)
    meta = metadata_service.arxiv_meta(normalized)
    pdf_url = meta.get("pdf_url") or ARXIV_PDF_URL.format(arxiv_id=normalized)
    safe_id = normalized.replace("/", "-")
    path = fetch_pdf(pdf_url, filename=f"arxiv-{safe_id}.pdf")
    return path, meta


def _normalized_title(text: str) -> str:
    value = (text or "").lower()
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", value)


def _title_similarity(left: str, right: str) -> float:
    normalized_left = _normalized_title(left)
    normalized_right = _normalized_title(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def find_pdf_for_doi(doi: str, title: str = "", pdf_url: str = "") -> Path | None:
    """为 DOI 找一份可下载的 PDF：Crossref link → OpenAlex OA → arXiv 同名检索。

    全部走不通返回 None（调用方只建元数据条目）。
    """
    from . import pdf_download

    candidates=[{'url':pdf_url,'source':'provided'}] if pdf_url else []
    try:
        candidates.extend(metadata_service.openalex_candidates(metadata_service.openalex_by_doi(doi)))
    except Exception:
        pass
    for candidate in candidates:
        result=None
        try:
            headers=metadata_service.openalex_content_headers(candidate['url']) if candidate.get('hosted') else {}
            result=pdf_download.download(candidate['url'],headers=headers,route=candidate.get('source','doi'))
            pdf_download.verify_identity(result.path,{'doi':doi,'title':title})
            return result.path
        except pdf_download.PdfDownloadError:
            if result:
                result.path.unlink(missing_ok=True)
    if not title:
        return None
    try:
        results = arxiv_search(title, limit=5)
    except DiscoverError:
        return None
    best: dict | None = None
    best_score = 0.0
    for item in results:
        score = _title_similarity(title, item.get("title") or "")
        if score > best_score:
            best, best_score = item, score
    if best is None or best_score < 0.86:
        return None
    arxiv_id = best.get("arxiv_id") or ""
    if not arxiv_id:
        return None
    try:
        path, _meta = download_arxiv(arxiv_id)
    except (DiscoverError, metadata_service.MetadataError):
        return None
    return path
