"""元数据服务。

- ``extract_pdf_metadata``：从 PDF 元数据 + 首页排版启发式抽取 title/authors/year/doi/abstract
- ``crossref_by_doi``：Crossref works API 查询 DOI
- ``arxiv_meta``：arXiv Atom API 查询单篇
- ``parse_arxiv_feed`` / ``metadata_from_html`` / ``openalex_by_doi``：辅助（供 discover 与 identifier 导入复用）

网络错误统一抛 ``MetadataError``，其 ``status`` 给出建议的 HTTP 状态码（400/404/502）。
"""
from __future__ import annotations

import html as _html
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx

from . import pdftext
from . import pdf_trace
from .activity import tracked

CROSSREF_API = "https://api.crossref.org/works"
OPENALEX_API = "https://api.openalex.org/works/doi:"
ARXIV_API = "https://export.arxiv.org/api/query"

DEFAULT_TIMEOUT = 10.0
ARXIV_TIMEOUT = 20.0

ATOM_NS = "http://www.w3.org/2005/Atom"
ARXIV_NS = "http://arxiv.org/schemas/atom"
_A = "{%s}" % ATOM_NS
_X = "{%s}" % ARXIV_NS

DOI_PATTERN = re.compile(r"10\.\d{4,9}/[-._;()/:<>a-zA-Z0-9]+", re.IGNORECASE)
YEAR_PATTERN = re.compile(r"(?:19|20)\d{2}")
ARXIV_CORE_PATTERN = re.compile(
    r"(?P<new>\d{4}\.\d{4,5})|(?P<old>[a-z][a-z\-]*(?:\.[a-z\-]+)?/\d{7})",
    re.IGNORECASE,
)

CONTRACT_FIELDS = (
    "title",
    "authors",
    "year",
    "venue",
    "volume",
    "issue",
    "pages",
    "doi",
    "arxiv_id",
    "url",
    "abstract",
    "tags",
    "category",
)

_JUNK_TITLE_PATTERN = re.compile(
    r"(microsoft word|untitled|^document\d*$|^powerpoint|^slide|\.(docx?|dvi|tex|pdf|qxd|indd)\s*$)",
    re.IGNORECASE,
)
_INSTITUTION_PATTERN = re.compile(
    r"(universit|department|institut|college|laborator|school of|academy|@|https?://)",
    re.IGNORECASE,
)
#: PDF 内嵌 Title 经常是期刊名（如 Scientific Reports），不能当论文标题
_JOURNAL_WORDS = re.compile(
    r"\b(reports?|journal|proceedings|transactions|letters|communications|reviews?|"
    r"nature|science|cell|plos|frontiers|bmj|jama|lancet|ieee|acm)\b",
    re.IGNORECASE,
)


def looks_like_journal_title(title: str) -> bool:
    text = _clean(title)
    if not text or len(text) > 40:
        return False
    words = re.findall(r"[A-Za-z\u4e00-\u9fff]+", text)
    if not words:
        return False
    if len(words) > 5:
        return False
    return bool(_JOURNAL_WORDS.search(text))


class MetadataError(Exception):
    """元数据获取失败；``status`` 为建议的 HTTP 状态码。"""

    def __init__(self, message: str, status: int = 502) -> None:
        super().__init__(message)
        self.status = status


def _contact_email() -> str:
    """礼貌池联系邮箱：环境变量优先，其次设置里的 Unpaywall / 学术邮箱。"""
    mailto = os.environ.get("PAPERWHALE_MAILTO", "").strip()
    if mailto:
        return mailto
    try:
        from .. import config

        settings = config.get_settings()
        return str(settings.get("unpaywall_email") or settings.get("scholar_email") or "").strip()
    except Exception:  # noqa: BLE001 - 没初始化配置时按无邮箱处理
        return ""


def _user_agent() -> str:
    mailto = _contact_email()
    agent = "PaperWhale/0.1 (local literature workbench)"
    if mailto:
        agent += f" mailto:{mailto}"
    return agent


USER_AGENT = "PaperWhale/0.1 (local literature workbench)"


# ---------------------------------------------------------------------------
# 通用小工具
# ---------------------------------------------------------------------------

def _clean(value: Any) -> str:
    """去 HTML/JATS 标签、反转义、压空白。"""
    if value is None:
        return ""
    text = _html.unescape(str(value))
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("\u00a0", " ").replace("\u2009", " ").replace("\ufeff", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _first_text(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        for item in value:
            text = _clean(item)
            if text:
                return text
        return ""
    return _clean(value)


def normalize_doi(raw: Any) -> str:
    """把 DOI / doi.org 链接 / ``doi:`` 前缀统一成裸 DOI。"""
    text = _clean(str(raw or ""))
    if not text:
        return ""
    text = re.sub(r"^(?:https?://(?:dx\.)?doi\.org/|doi:\s*)", "", text, flags=re.IGNORECASE)
    match = DOI_PATTERN.search(text)
    if not match:
        return ""
    return match.group(0).rstrip(".,;)]}>\"'").strip()


def normalize_arxiv_id(raw: Any) -> str:
    """把 arXiv ID / abs 链接 / ``arXiv:`` 前缀统一成不带版本号的 ID。"""
    text = _clean(str(raw or ""))
    if not text:
        return ""
    text = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\.pdf$", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^arxiv[:\s/]+", "", text, flags=re.IGNORECASE)
    text = re.sub(r"^arxiv\s*/\s*", "", text, flags=re.IGNORECASE)
    text = text.strip().strip("<>\"'").strip()
    text = re.sub(r"v\d+$", "", text, flags=re.IGNORECASE)
    match = ARXIV_CORE_PATTERN.fullmatch(text)
    if not match:
        match = ARXIV_CORE_PATTERN.search(text)
    if not match:
        return ""
    return match.group(0)


def normalize_volume(raw: Any) -> str:
    """卷号归一：去 ``Vol.`` / ``volume`` 前缀和「第…卷」包装，压空白。"""
    text = _clean(str(raw or ""))
    if not text:
        return ""
    text = re.sub(r"^第\s*", "", text)
    text = re.sub(r"\s*卷$", "", text)
    text = re.sub(r"^(?:vol(?:ume)?\.?|v\.)\s*", "", text, flags=re.IGNORECASE)
    return text.strip(" .,;:()（）[]")


def normalize_issue(raw: Any) -> str:
    """期号归一：去外层括号、``No.``/``issue``/「第…期」包装。"""
    text = _clean(str(raw or ""))
    if not text:
        return ""
    for _ in range(3):
        if len(text) >= 2 and text[0] in "(（[" and text[-1] in ")）]":
            text = text[1:-1].strip()
        else:
            break
    text = re.sub(r"^(?:no\.?|number|issue|第)\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*期$", "", text)
    return text.strip(" .,;:")


def normalize_pages(raw: Any) -> str:
    """页码归一：去 ``pp.`` 前缀，各种连字符统一成 en dash（45-67 → 45–67）。"""
    text = _clean(str(raw or ""))
    if not text:
        return ""
    text = re.sub(
        r"^(?:pages?|pp?\.?|页码[:：]?)\s*", "", text, flags=re.IGNORECASE
    )
    for dash in ("—", "−", "~", "〜", "～", "－"):
        text = text.replace(dash, "–")
    text = re.sub(r"-+", "–", text)
    text = re.sub(r"–+", "–", text)
    text = re.sub(r"\s*–\s*", "–", text)
    text = re.sub(r"\s+", " ", text).strip(" .,;")
    return text


def clean_meta(meta: dict | None) -> dict:
    """把任意来源的 meta 收敛成 ingest 认识的契约字段（去除空值）。"""
    from .bibliography import clean_venue_fields
    source = clean_venue_fields(meta or {})
    result: dict[str, Any] = {}
    for key in CONTRACT_FIELDS:
        value = source.get(key)
        if key == "tags":
            if isinstance(value, (list, tuple, set)):
                raw_tags = list(value)
            else:
                raw_tags = re.split(r"[;,，；/]", str(value or ""))
            tags: list[str] = []
            seen: set[str] = set()
            for tag in raw_tags:
                text = _clean(str(tag)).lstrip("#").strip()
                if not text:
                    continue
                lowered = text.lower()
                if lowered in seen:
                    continue
                seen.add(lowered)
                tags.append(text[:60])
            if tags:
                result["tags"] = tags[:20]
            continue
        if isinstance(value, (list, tuple)):
            value = "; ".join(_clean(item) for item in value if _clean(item))
        text = _clean(value) if value is not None else ""
        if text:
            result[key] = text
    for key, normalize in (
        ("volume", normalize_volume),
        ("issue", normalize_issue),
        ("pages", normalize_pages),
    ):
        if key in result:
            normalized = normalize(result.get(key))
            if normalized:
                result[key] = normalized
            else:
                result.pop(key, None)
    return result


def _get(url: str, *, params: dict | None = None, timeout: float = DEFAULT_TIMEOUT,
         headers: dict | None = None) -> httpx.Response:
    request_headers = {"User-Agent": _user_agent()}
    request_headers.update(headers or {})
    params = dict(params or {})
    from .. import config
    from urllib.parse import urlsplit

    email = _contact_email()
    if email and urlsplit(url).hostname in {"api.openalex.org", "api.crossref.org"} and "mailto" not in params:
        # 这两个公开接口带 mailto 会进礼貌池，批量查询时更不容易被限流。
        params["mailto"] = email

    key = str(config.get_settings().get('openalex_api_key') or os.environ.get('OPENALEX_API_KEY') or '').strip()
    if urlsplit(url).hostname == 'api.openalex.org' and key:
        request_headers['Authorization'] = 'Bearer '+key
    trace=pdf_trace.active.get()
    if trace and getattr(trace,'deadline',None):
        import time

        remaining=trace.deadline-time.monotonic()
        if remaining<=0:
            pdf_trace.record('metadata',url,code='deadline')
            raise MetadataError('本轮获取达到时间上限',504)
        timeout=min(timeout,remaining)
    try:
        response = httpx.get(
            url,
            params=params,
            timeout=timeout,
            follow_redirects=True,
            headers=request_headers,
        )
        pdf_trace.record('metadata',url,code=pdf_trace.http_code(response.status_code),status=response.status_code)
        return response
    except httpx.HTTPError as exc:  # 连接失败 / 超时 / 证书等
        pdf_trace.record('metadata',url,code='timeout' if isinstance(exc,httpx.TimeoutException) else 'network_error')
        raise MetadataError(f"网络请求失败：{exc}", 502) from exc


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

def _arxiv_entry_to_meta(entry: ET.Element) -> dict:
    entry_id = _clean(entry.findtext(f"{_A}id") or "")
    arxiv_id = ""
    match = re.search(r"/abs/(.+)$", entry_id)
    if match:
        arxiv_id = normalize_arxiv_id(match.group(1))
    if not arxiv_id:
        arxiv_id = normalize_arxiv_id(entry_id)

    title = _clean(entry.findtext(f"{_A}title") or "")
    abstract = _clean(entry.findtext(f"{_A}summary") or "")
    authors = []
    for author in entry.findall(f"{_A}author"):
        name = _clean(author.findtext(f"{_A}name") or "")
        if name:
            authors.append(name)

    published = _clean(entry.findtext(f"{_A}published") or "")
    updated = _clean(entry.findtext(f"{_A}updated") or "")
    year_match = YEAR_PATTERN.search(published)
    year = year_match.group(0) if year_match else ""

    # 契约：arxiv_id 去版本号，pdf_url 固定为 https://arxiv.org/pdf/<id>.pdf
    abs_url = f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else entry_id
    pdf_url = f"https://arxiv.org/pdf/{arxiv_id}.pdf" if arxiv_id else ""
    if not arxiv_id:
        for link in entry.findall(f"{_A}link"):
            href = str(link.get("href") or "").strip()
            if not href:
                continue
            link_title = str(link.get("title") or "").strip().lower()
            link_type = str(link.get("type") or "").strip().lower()
            if link_title == "pdf" or link_type == "application/pdf":
                pdf_url = href

    primary = entry.find(f"{_X}primary_category")
    category = _clean(primary.get("term")) if primary is not None and primary.get("term") else ""
    tags: list[str] = []
    for cat in entry.findall(f"{_X}category"):
        term = _clean(cat.get("term") or "")
        if term and term not in tags:
            tags.append(term)

    doi = normalize_doi(entry.findtext(f"{_X}doi") or "")
    journal_ref = _clean(entry.findtext(f"{_X}journal_ref") or "")
    venue = journal_ref or ("arXiv preprint" if arxiv_id else "")

    meta = clean_meta(
        {
            "arxiv_id": arxiv_id,
            "title": title,
            "authors": "; ".join(authors),
            "abstract": abstract,
            "year": year,
            "venue": venue,
            "url": abs_url,
            "doi": doi,
            "tags": tags,
            "category": category,
        }
    )
    meta["published"] = published
    meta["pdf_url"] = pdf_url
    meta["abs_url"] = abs_url
    if updated:
        meta["updated"] = updated
    return meta


def parse_arxiv_feed(xml_text: str) -> list[dict]:
    """解析 arXiv Atom feed；解析失败抛 ``ValueError``。"""
    if not xml_text or not xml_text.strip():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ValueError(f"Atom XML 解析失败：{exc}") from exc
    items: list[dict] = []
    for entry in root.findall(f"{_A}entry"):
        meta = _arxiv_entry_to_meta(entry)
        title = meta.get("title", "")
        # arXiv 查不到时也会返回一个 title 为 Error 的 entry
        if not meta.get("arxiv_id") or not title:
            continue
        if title.lower().startswith("error"):
            continue
        items.append(meta)
    return items


def arxiv_meta(arxiv_id: str) -> dict:
    """查询单篇 arXiv 论文元数据；返回契约字段 + pdf_url/abs_url/published。"""
    normalized = normalize_arxiv_id(arxiv_id)
    if not normalized:
        raise MetadataError(f"无法识别的 arXiv ID：{arxiv_id}", 400)
    response = _get(
        ARXIV_API,
        params={"id_list": normalized},
        timeout=ARXIV_TIMEOUT,
        headers={"Accept": "application/atom+xml"},
    )
    if response.status_code >= 400:
        raise MetadataError(f"arXiv 返回 HTTP {response.status_code}", 502)
    try:
        entries = parse_arxiv_feed(response.text)
    except ValueError as exc:
        raise MetadataError(f"arXiv 返回内容无法解析：{exc}", 502) from exc
    if not entries:
        raise MetadataError(f"arXiv 上找不到这篇论文：{normalized}", 404)
    meta = entries[0]
    meta["arxiv_id"] = normalized
    if not meta.get("pdf_url"):
        meta["pdf_url"] = f"https://arxiv.org/pdf/{normalized}.pdf"
    if not meta.get("abs_url"):
        meta["abs_url"] = f"https://arxiv.org/abs/{normalized}"
    return meta


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------

def _crossref_authors(items: Any) -> str:
    names: list[str] = []
    for item in items or []:
        if not isinstance(item, dict):
            continue
        given = _clean(item.get("given") or "")
        family = _clean(item.get("family") or "")
        name = _clean(f"{given} {family}")
        if not name:
            name = _clean(item.get("name") or "")
        if name:
            names.append(name)
    return "; ".join(names)


def _crossref_year(message: dict) -> str:
    for key in ("published-print", "published-online", "issued", "published", "created", "posted"):
        section = message.get(key)
        if not isinstance(section, dict):
            continue
        parts = section.get("date-parts")
        if (
            isinstance(parts, list)
            and parts
            and isinstance(parts[0], list)
            and parts[0]
            and YEAR_PATTERN.fullmatch(str(parts[0][0]))
        ):
            return str(parts[0][0])
    for key in ("published", "issued", "published-print", "published-online"):
        match = YEAR_PATTERN.search(str(message.get(key) or ""))
        if match:
            return match.group(0)
    return ""


def _crossref_pdf_url(message: dict) -> str:
    for link in message.get("link") or []:
        if not isinstance(link, dict):
            continue
        url = str(link.get("URL") or "").strip()
        content_type = str(link.get("content-type") or "").strip().lower()
        if url and ("pdf" in content_type or url.lower().split("?")[0].endswith(".pdf")):
            return url
    return ""


def _crossref_message_to_meta(message: dict) -> dict:
    doi = normalize_doi(message.get("DOI") or "")
    meta = clean_meta(
        {
            "title": _first_text(message.get("title")),
            "authors": _crossref_authors(message.get("author")),
            "year": _crossref_year(message),
            "venue": _first_text(message.get("container-title")) or _first_text(message.get("publisher")),
            "volume": message.get("volume") or "",
            "issue": message.get("issue") or "",
            "pages": message.get("page") or "",
            "doi": doi,
            "url": str(message.get("URL") or "").strip() or (f"https://doi.org/{doi}" if doi else ""),
            "abstract": _clean(message.get("abstract") or ""),
            "tags": [str(subject) for subject in (message.get("subject") or []) if str(subject).strip()][:10],
        }
    )
    pdf_url = _crossref_pdf_url(message)
    if pdf_url:
        meta["pdf_url"] = pdf_url
    return meta


def crossref_by_doi(doi: str) -> dict:
    """按 DOI 查 Crossref；404 表示 DOI 不存在，网络问题为 502。"""
    normalized = normalize_doi(doi)
    if not normalized:
        raise MetadataError(f"不是有效的 DOI：{doi}", 400)
    response = _get(
        f"{CROSSREF_API}/{quote(normalized, safe='/')}",
        headers={"Accept": "application/json"},
    )
    if response.status_code == 404:
        raise MetadataError(f"Crossref 查不到这个 DOI：{normalized}", 404)
    if response.status_code >= 400:
        raise MetadataError(f"Crossref 返回 HTTP {response.status_code}", 502)
    try:
        payload = response.json()
    except ValueError as exc:
        raise MetadataError("Crossref 返回内容不是合法 JSON", 502) from exc
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, dict) or not message:
        raise MetadataError("Crossref 返回内容为空", 502)
    return _crossref_message_to_meta(message)


def enrich_from_doi(doi: str) -> dict:
    """给已有条目按 DOI 补全元数据；Crossref 失败时返回空 dict。"""
    try:
        meta = crossref_by_doi(doi)
    except Exception:  # noqa: BLE001 - 补全失败不影响主流程
        return {}
    return meta if isinstance(meta, dict) else {}


def _title_key(text: str) -> str:
    cleaned = re.sub(r"[^\w\u4e00-\u9fff]+", " ", (text or "").lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def _title_score(left: str, right: str) -> float:
    import difflib

    a, b = _title_key(left), _title_key(right)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


def _openalex_work_to_meta(work: dict) -> dict:
    authors = []
    for authorship in work.get("authorships") or []:
        author = authorship.get("author") or {}
        name = str(author.get("display_name") or "").strip()
        if name:
            authors.append(name)
    source = ((work.get("primary_location") or {}).get("source") or {})
    best_oa = work.get("best_oa_location") or {}
    pdf_url = str(best_oa.get("pdf_url") or "").strip()
    biblio = work.get("biblio") or {}
    first_page = str(biblio.get("first_page") or "").strip()
    last_page = str(biblio.get("last_page") or "").strip()
    pages = ""
    if first_page and last_page and last_page != first_page:
        pages = f"{first_page}–{last_page}"
    elif first_page:
        pages = first_page
    meta = clean_meta(
        {
            "title": work.get("display_name") or "",
            "authors": "; ".join(authors),
            "year": work.get("publication_year") or "",
            "venue": source.get("display_name") or "",
            "volume": biblio.get("volume") or "",
            "issue": biblio.get("issue") or "",
            "pages": pages,
            "doi": normalize_doi(work.get("doi") or ""),
            "url": (f"https://doi.org/{normalize_doi(work.get('doi') or '')}" if work.get("doi") else ""),
            "abstract": "",
        }
    )
    if pdf_url:
        meta["pdf_url"] = pdf_url
    return meta


def find_metadata_by_title(title: str) -> dict:
    """用论文标题在 OpenAlex / Crossref 里找最可信条目（优先高引用、标题完全匹配）。"""
    query = _clean(title)
    if len(query) < 12 or not re.search(r"[A-Za-z]", query):
        return {}
    best: dict = {}
    best_score = 0.0
    best_cites = -1

    def consider(meta: dict, score: float, cites: int) -> None:
        nonlocal best, best_score, best_cites
        if not meta or not score:
            return
        if score > best_score + 0.015 or (
            abs(score - best_score) <= 0.015 and score > 0 and cites > best_cites
        ):
            best = meta
            best_score = score
            best_cites = cites

    try:
        response = _get(
            "https://api.openalex.org/works",
            params={"search": query, "per-page": 10},
        )
        if response.status_code < 400:
            for work in ((response.json() or {}).get("results") or []):
                score = _title_score(query, str(work.get("display_name") or ""))
                if score >= 0.9:
                    consider(
                        _openalex_work_to_meta(work),
                        score,
                        int(work.get("cited_by_count") or 0),
                    )
    except Exception:  # noqa: BLE001
        pass

    if best_score < 0.94:
        try:
            response = _get(
                CROSSREF_API,
                params={
                    "query.bibliographic": query,
                    "rows": 8,
                    "select": "DOI,title,author,container-title,issued,link,abstract,volume,page,is-referenced-by-count",
                },
            )
            if response.status_code < 400:
                items = ((response.json() or {}).get("message") or {}).get("items") or []
                for item in items:
                    candidate = _first_text(item.get("title"))
                    score = _title_score(query, candidate)
                    if score >= 0.86:
                        consider(
                            _crossref_message_to_meta(item),
                            score,
                            int(item.get("is-referenced-by-count") or 0),
                        )
        except Exception:  # noqa: BLE001
            pass

    if not best or best_score < 0.86:
        return {}
    best["_score"] = round(best_score, 3)
    pdf_urls = []
    pdf_value = str(best.pop("pdf_url", "") or "")
    if pdf_value:
        pdf_urls.append(pdf_value)
    best["_pdf_urls"] = pdf_urls
    return best


def openalex_by_doi(doi: str) -> dict:
    """尽力从 OpenAlex 找开放获取 PDF；失败返回空 dict（不抛异常）。"""
    normalized = normalize_doi(doi)
    if not normalized:
        return {}
    try:
        response = _get(
            f"{OPENALEX_API}{quote(normalized, safe='/')}",
            headers={"Accept": "application/json"},
        )
        if response.status_code >= 400:
            return {}
        payload = response.json()
    except Exception:  # noqa: BLE001 - 纯辅助信息，失败就当没有
        return {}
    if not isinstance(payload, dict):
        return {}
    candidates=[]
    seen=set()
    for location in [payload.get('best_oa_location'),payload.get('primary_location'),*(payload.get('locations') or [])]:
        if not isinstance(location,dict):
            continue
        for field,kind in [('pdf_url','pdf'),('landing_page_url','landing')]:
            url=str(location.get(field) or '').strip()
            if url.startswith(('http://','https://')) and url not in seen:
                seen.add(url)
                candidates.append({'url':url,'kind':kind,'source':'openalex',
                                   'version':str(location.get('version') or ''),
                                   'license':str(location.get('license') or '')})
    oa_url=str((payload.get('open_access') or {}).get('oa_url') or '')
    if oa_url.startswith(('http://','https://')) and oa_url not in seen:
        candidates.append({'url':oa_url,'kind':'landing','source':'openalex','version':''})
    pdf_urls=[c['url'] for c in candidates if c['kind']=='pdf']
    content_url=str((payload.get('content_urls') or {}).get('pdf') or '')
    if not (payload.get('has_content') or {}).get('pdf') or not re.fullmatch(r'https://content\.openalex\.org/works/W\d+\.pdf',content_url):
        content_url=''
    return {'pdf_url':pdf_urls[0] if pdf_urls else '', 'pdf_urls':pdf_urls,
            'candidates':candidates,'content_url':content_url,'work_id':str(payload.get('id') or '')}


def openalex_candidates(result: dict) -> list[dict]:
    from .. import config

    candidates=list(result.get('candidates') or [])
    key=str(config.get_settings().get('openalex_api_key') or os.environ.get('OPENALEX_API_KEY') or '').strip()
    content=result.get('content_url')
    if content:
        if config.get_settings().get('openalex_content_enabled') and key:
            candidates.insert(0,{'url':content,'source':'openalex-content','kind':'pdf','version':'','hosted':True})
        else:
            pdf_trace.record('openalex-content',content,code='api_key_required')
    return candidates


def openalex_content_headers(url: str) -> dict:
    from .. import config

    if not re.fullmatch(r'https://content\.openalex\.org/works/W\d+\.pdf',url):
        return {}
    key=str(config.get_settings().get('openalex_api_key') or os.environ.get('OPENALEX_API_KEY') or '').strip()
    return {'Authorization':'Bearer '+key} if key else {}


# ---------------------------------------------------------------------------
# HTML / PDF
# ---------------------------------------------------------------------------

_META_TAG_PATTERN = re.compile(r"<meta\b[^>]*>", re.IGNORECASE | re.DOTALL)


def _meta_attr(tag: str, name: str) -> str:
    match = re.search(rf"{name}\s*=\s*[\"']([^\"']*)[\"']", tag, re.IGNORECASE)
    return match.group(1) if match else ""


def metadata_from_html(html_text: str, url: str = "") -> dict:
    """从网页的 Highwire / Dublin Core / OpenGraph meta 标签抽论文元数据。"""
    pairs: list[tuple[str, str]] = []
    for tag in _META_TAG_PATTERN.findall(html_text or ""):
        key = (_meta_attr(tag, "name") or _meta_attr(tag, "property") or _meta_attr(tag, "http-equiv")).strip().lower()
        content = _meta_attr(tag, "content")
        if key and content:
            pairs.append((key, _html.unescape(content)))

    def first(*keys: str) -> str:
        for key in keys:
            for pair_key, value in pairs:
                if pair_key == key:
                    return _clean(value)
        return ""

    def every(key: str) -> list[str]:
        return [_clean(value) for pair_key, value in pairs if pair_key == key and _clean(value)]

    title = first(
        "citation_title",
        "dc.title",
        "dcterms.title",
        "og:title",
        "twitter:title",
    )
    if not title:
        match = re.search(r"<title[^>]*>(.*?)</title>", html_text or "", re.IGNORECASE | re.DOTALL)
        if match:
            title = _clean(match.group(1))

    author_list = every("citation_author")
    if not author_list:
        author_list = every("dc.creator") or every("author")
    date_text = first("citation_publication_date", "citation_date", "citation_online_date", "dc.date", "dcterms.issued")
    year_match = YEAR_PATTERN.search(date_text)
    doi = normalize_doi(
        first("citation_doi", "dc.identifier", "dcterms.identifier", "prism.doi")
    )
    if not doi:
        doi = normalize_doi(url)
    arxiv_id = normalize_arxiv_id(first("citation_arxiv_id")) or normalize_arxiv_id(url)
    tags = every("citation_keywords") or every("dc.subject")
    split_tags: list[str] = []
    for tag in tags:
        split_tags.extend(re.split(r"[;,，；]", tag))

    first_page = first("citation_firstpage", "prism.startingpage")
    last_page = first("citation_lastpage", "prism.endingpage")
    pages = ""
    if first_page and last_page and last_page != first_page:
        pages = f"{first_page}–{last_page}"
    elif first_page:
        pages = first_page

    return clean_meta(
        {
            "title": title,
            "authors": "; ".join(author_list),
            "year": year_match.group(0) if year_match else "",
            "venue": first(
                "citation_journal_title",
                "citation_conference_title",
                "citation_inbook_title",
                "prism.publicationname",
                "dc.publisher",
                "og:site_name",
            ),
            "volume": first("citation_volume", "prism.volume"),
            "issue": first("citation_issue", "prism.number"),
            "pages": pages,
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": url,
            "abstract": first("citation_abstract", "dc.description", "og:description", "description"),
            "tags": split_tags,
            "category": "",
        }
    )


def _find_doi(text: str) -> str:
    if not text:
        return ""
    match = DOI_PATTERN.search(text)
    if not match:
        return ""
    return normalize_doi(match.group(0))


def _looks_like_junk_title(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return True
    if len(stripped) < 6:
        return True
    return bool(_JUNK_TITLE_PATTERN.search(stripped))


def _title_from_lines(lines: list[tuple[float, float, str]], page_height: float) -> str:
    """首页最大字号的行聚成标题（尽力而为）。lines: [(上边缘 y, 字号, 文本)]。"""
    if not lines:
        return ""
    top = [line for line in lines if page_height and line[0] > page_height * 0.4] or lines
    # 旋转水印（如 arXiv 侧边栏）会被拆成 1-2 字的竖排行，先把它们排除再取最大字号
    candidates = [line for line in top if len(line[2]) >= 3]
    if not candidates:
        return ""
    max_size = max(line[1] for line in candidates)
    if max_size <= 0:
        return ""
    chosen = [line for line in candidates if line[1] >= max_size - 0.6]
    chosen.sort(key=lambda item: -item[0])
    cluster: list[tuple[float, float, str]] = []
    for line in chosen:
        if not cluster:
            cluster.append(line)
            continue
        gap = abs(cluster[-1][0] - line[0])
        if gap <= max(12.0, cluster[-1][1] * 2.6):
            cluster.append(line)
        else:
            break
    title = re.sub(r"\s+", " ", " ".join(line[2] for line in cluster)).strip()
    if len(title) < 6:
        return ""
    return title[:300]


def _heading_from_text(text: str) -> str:
    for raw_line in (text or "").splitlines():
        line = _clean(raw_line)
        if len(line) < 10:
            continue
        if re.search(r"arxiv:\d|doi\.org|^https?://|@|©", line, re.IGNORECASE):
            continue
        return line[:300]
    return ""


def _authors_from_text(text: str) -> str:
    """从首页前几行猜作者（仅在 PDF 元数据无作者时兜底）。"""
    lines = [_clean(line) for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    for line in lines[:25]:
        if re.search(r"\babstract\b", line, re.IGNORECASE):
            break
        if _INSTITUTION_PATTERN.search(line):
            continue
        if len(line) > 300:
            continue
        pieces = [piece.strip() for piece in re.split(r"\s*(?:;|,|\band\b)\s*", line) if piece.strip()]
        names = [
            piece for piece in pieces
            if re.fullmatch(r"[A-Z][\w.\-'’]*(?:\s+[A-Z][\w.\-'’]*){0,4}", piece)
        ]
        if len(names) >= 2 and sum(len(name.split()) for name in names) >= 4:
            return "; ".join(names)
    return ""


def _abstract_from_text(text: str) -> str:
    if not text:
        return ""
    match = re.search(r"\babstract\b\s*[:：.\-–—]?\s*", text, re.IGNORECASE)
    if not match:
        return ""
    rest = text[match.end():]
    stop = re.search(
        r"\n\s*(?:\d+\s*[.、]?\s*introduction\b|introduction\b|keywords?\b|index terms\b|ccs concepts\b)",
        rest,
        re.IGNORECASE,
    )
    if stop:
        rest = rest[: stop.start()]
    rest = re.sub(r"\s+", " ", rest).strip()
    if len(rest) < 80:
        return ""
    return rest[:2000]


def extract_pdf_metadata(pdf_path: Path | str) -> dict:
    """尽力抽取 PDF 的 title/authors/year/doi/abstract。

    优先用 PDF 内嵌元数据，缺失时用首页字号/版式启发式兜底。
    """
    path = Path(pdf_path)
    if not path.exists():
        raise MetadataError(f"PDF 文件不存在：{path}", 400)
    try:
        info = pdftext.document_info(path)
        pages = pdftext.page_texts(path,limit=2)
    except pdftext.PdfTextError as exc:
        raise MetadataError(str(exc), 400) from exc

    try:
        title = _clean(info.get("title") or "")
        if _looks_like_junk_title(title):
            title = ""
        authors = _clean(info.get("author") or "")
        subject = _clean(info.get("subject") or "")
        keywords = _clean(info.get("keywords") or "")
        creation_date = str(info.get("creation_date") or "")

        first_text = pages[0][1] if pages else ""
        second_text = pages[1][1] if len(pages) > 1 else ""

        heuristic_title = ""
        if pages:
            try:
                _, page_height = pdftext.page_size(path, 0)
                heuristic_title = _title_from_lines(pdftext.page_lines(path, 0), page_height)
            except Exception:  # noqa: BLE001 - 启发式失败就算了
                heuristic_title = ""

        # PDF 的 /Title 常常是期刊名、投稿系统名，遇到这种情况以首页版式为准
        if title and looks_like_journal_title(title):
            title = heuristic_title or ""
        if not title:
            title = heuristic_title
        if not title:
            title = _heading_from_text(first_text)
        if not authors:
            authors = _authors_from_text(first_text)
        if not authors:
            authors = _authors_from_text(second_text)

        combined = "\n".join([title, authors, subject, keywords, first_text, second_text])
        year = ""
        year_match = YEAR_PATTERN.search(first_text) or YEAR_PATTERN.search(creation_date)
        if year_match:
            year = year_match.group(0)
        doi = _find_doi(combined)
        abstract = _abstract_from_text(first_text) or _abstract_from_text(second_text)

        return {
            "title": _clean(title)[:500],
            "authors": _clean(authors)[:500],
            "year": year,
            "doi": doi,
            "abstract": abstract,
        }
    finally:
        pass


@tracked
def repair_bad_titles(limit: int = 40) -> int:
    """修复「标题是期刊名 / 标题乱码 / 作者缺失」的历史数据。

    优先用 DOI 查 Crossref 补全；没有 DOI 再用 PDF 首页版式。
    """
    from .. import db

    changed = 0
    rows = db.query(
        "SELECT id, title, authors, doi, file_path FROM papers "
        "WHERE length(COALESCE(title,'')) <= 40 OR COALESCE(authors,'') = ''"
    )
    for row in rows:
        if changed >= limit:
            break
        old_title = _clean(row["title"])
        old_authors = _clean(row["authors"])
        suspicious = (
            not old_title
            or len(old_title) <= 12
            or looks_like_journal_title(old_title)
        )
        if not suspicious:
            continue
        updates: dict[str, str] = {}
        doi = _clean(row["doi"])
        if doi:
            meta = enrich_from_doi(doi)
            new_title = _clean(meta.get("title"))
            new_authors = _clean(meta.get("authors"))
            if new_title and not looks_like_journal_title(new_title) and new_title.lower() != old_title.lower():
                updates["title"] = new_title[:500]
            if new_authors and (not old_authors or len(new_authors) > len(old_authors)):
                updates["authors"] = new_authors[:500]
            if _clean(meta.get("venue")):
                updates["venue"] = _clean(meta.get("venue"))[:200]
            if _clean(meta.get("year")):
                updates["year"] = _clean(meta.get("year"))[:10]
            if _clean(meta.get("abstract")):
                existing = db.get_paper(row["id"]) or {}
                if not existing.get("abstract"):
                    updates["abstract"] = _clean(meta.get("abstract"))[:4000]
        if not updates and row["file_path"]:
            path = Path(str(row["file_path"]))
            if path.exists():
                try:
                    meta = extract_pdf_metadata(path)
                except Exception:  # noqa: BLE001
                    meta = {}
                new_title = _clean(meta.get("title"))
                if new_title and new_title.lower() != old_title.lower() and not looks_like_journal_title(new_title):
                    updates["title"] = new_title[:500]
                if not old_authors and _clean(meta.get("authors")):
                    updates["authors"] = _clean(meta.get("authors"))[:500]
        if updates:
            try:
                db.update_paper(row["id"], updates)
                changed += 1
            except Exception:  # noqa: BLE001
                continue
    return changed


def ensure_metadata(paper_id: str, allow_web: bool = True) -> dict:
    """精读后兜底：缺作者/期刊/年份/DOI 时按 DOI 或标题联网补全。"""
    from .. import db

    paper = db.get_paper(paper_id)
    if not paper:
        return {}
    title = str(paper.get("title") or "").strip()
    if not title or looks_like_journal_title(title):
        return paper

    def missing(value: Any) -> bool:
        text = str(value or "").strip()
        return text in {"", "未知作者", "未知", "Unknown", "unknown", "N/A", "n/a", "—", "-"}

    fields: dict = {}
    candidates: list[dict] = []
    doi = normalize_doi(paper.get("doi") or "")
    if doi:
        try:
            candidates.append(enrich_from_doi(doi))
        except Exception:  # noqa: BLE001
            pass
    if allow_web and (
        missing(paper.get("authors"))
        or missing(paper.get("venue"))
        or missing(paper.get("year"))
        or missing(paper.get("doi"))
    ):
        try:
            found = find_metadata_by_title(title)
            if found:
                candidates.append(found)
        except Exception:  # noqa: BLE001
            pass

    def bad_authors(value: Any) -> bool:
        text = str(value or "").strip()
        if missing(text):
            return True
        return bool(re.search(r"\b(USA|China|CA|University|Department|Institute of Technology)\b", text))

    for candidate in candidates:
        if not isinstance(candidate, dict) or not candidate:
            continue
        if bad_authors(paper.get("authors")) and candidate.get("authors"):
            fields["authors"] = str(candidate.get("authors")).strip()[:500]
        if missing(paper.get("venue")) and candidate.get("venue"):
            venue = str(candidate.get("venue")).strip()
            if venue:
                fields["venue"] = venue[:200]
        elif str(paper.get("venue") or "").strip().lower() in {"arxiv", "arxiv preprint"} and candidate.get("venue"):
            venue = str(candidate.get("venue")).strip()
            if venue and "arxiv" not in venue.lower():
                fields["venue"] = venue[:200]
        if missing(paper.get("year")) and candidate.get("year"):
            fields["year"] = str(candidate.get("year")).strip()[:4]
        if missing(paper.get("doi")) and candidate.get("doi"):
            fields["doi"] = normalize_doi(candidate.get("doi"))
        for key in ("volume", "issue", "pages"):
            if missing(paper.get(key)) and candidate.get(key):
                fields[key] = str(candidate.get(key)).strip()[:50]
        if not paper.get("url") and candidate.get("url"):
            fields["url"] = str(candidate.get("url")).strip()[:1000]
        if fields:
            break
    if fields:
        db.update_paper(paper_id, fields)
        return db.get_paper(paper_id) or paper
    return paper
