"""参考文献外扩：以库里一篇论文为种子，拉它引用 / 被引的文献。

数据源是 OpenAlex：
- ``references``：种子论文的 ``referenced_works``（它引用了谁）
- ``citations``：``filter=cites:<work_id>``（谁引用了它）

只做候选列表，入不入库由用户勾选；入库走与导入相同的 ``create_paper_from_metadata``，
所以判重（DOI / 标题归一化）是复用的。
"""
from __future__ import annotations

from typing import Any

import httpx

from .. import db
from . import ingest, metadata

OPENALEX_API = "https://api.openalex.org"

#: 一次外扩最多返回多少条（OpenAlex 单页上限 50）
MAX_LIMIT = 50

#: 一次最多入库多少条，防止误点把整页灌进库
MAX_IMPORT = 100


def _json(path: str, params: dict[str, Any] | None = None) -> dict:
    url = path if path.startswith("http") else f"{OPENALEX_API}{path}"
    try:
        response = metadata._get(url, params=params or {}, timeout=15.0)
    except metadata.MetadataError:
        raise
    except httpx.HTTPError as exc:
        raise metadata.MetadataError(f"OpenAlex 请求失败：{exc}", 502) from exc
    if response.status_code >= 400:
        raise metadata.MetadataError(f"OpenAlex 返回 {response.status_code}", 502)
    try:
        data = response.json()
    except ValueError as exc:
        raise metadata.MetadataError("OpenAlex 返回的不是 JSON", 502) from exc
    if not isinstance(data, dict):
        raise metadata.MetadataError("OpenAlex 返回结构异常", 502)
    return data


def _short_id(work_id: str) -> str:
    return str(work_id or "").rstrip("/").rsplit("/", 1)[-1]


def _looks_same_title(left: str, right: str) -> bool:
    a, b = db.normalize_title(left), db.normalize_title(right)
    if not a or not b:
        return False
    return a == b or db._title_prefix_match(a, b)


def find_seed_work(paper: dict) -> dict | None:
    """按 DOI（优先）或标题找到种子论文的 OpenAlex 记录。"""
    doi = metadata.normalize_doi((paper or {}).get("doi") or "")
    if doi:
        try:
            return _json(f"/works/doi:{doi}")
        except metadata.MetadataError:
            pass
    title = str((paper or {}).get("title") or "").strip()
    if len(title) < 12:
        return None
    data = _json("/works", {"search": title, "per-page": 5})
    for work in data.get("results") or []:
        if _looks_same_title(title, str(work.get("display_name") or "")):
            return work
    return None


def _to_item(work: dict) -> dict:
    meta = metadata._openalex_work_to_meta(work)
    doi = meta.get("doi") or ""
    title = str(meta.get("title") or "").strip()
    existing = db.find_duplicate(title=title, doi=doi) if title else None
    return {
        "title": title,
        "authors": meta.get("authors") or "",
        "year": meta.get("year") or "",
        "venue": meta.get("venue") or "",
        "doi": doi,
        "url": meta.get("url") or "",
        "abstract": meta.get("abstract") or "",
        "arxiv_id": "",
        "openalex_id": str(work.get("id") or ""),
        "cited_by_count": int(work.get("cited_by_count") or 0),
        "existing_paper_id": existing or "",
    }


def expand(paper_id: str, direction: str = "references", limit: int = 40) -> dict:
    """返回 {seed, direction, items, truncated}；失败抛 MetadataError / LookupError。"""
    paper = db.get_paper(paper_id)
    if not paper:
        raise LookupError("论文不存在")
    work = find_seed_work(paper)
    if not work:
        raise LookupError("OpenAlex 里找不到这篇论文（可能没有 DOI 或标题对不上）")
    capped = max(1, min(int(limit or 40), MAX_LIMIT))
    direction = "citations" if str(direction) == "citations" else "references"
    items: list[dict] = []
    truncated = False

    if direction == "citations":
        data = _json(
            "/works",
            {
                "filter": f"cites:{_short_id(work.get('id') or '')}",
                "per-page": capped,
                "sort": "cited_by_count:desc",
            },
        )
        results = data.get("results") or []
        truncated = len(results) >= capped
        items = [_to_item(raw) for raw in results]
    else:
        references = [str(item) for item in (work.get("referenced_works") or []) if item]
        truncated = len(references) > capped
        references = references[:capped]
        if references:
            ids = "|".join(_short_id(item) for item in references)
            data = _json("/works", {"filter": f"openalex_id:{ids}", "per-page": capped})
            by_id = {str(raw.get("id") or "").rstrip("/"): raw for raw in data.get("results") or []}
            for reference in references:
                raw = by_id.get(reference.rstrip("/"))
                if raw:
                    items.append(_to_item(raw))

    seed_meta = metadata._openalex_work_to_meta(work)
    return {
        "seed": {
            "paper_id": paper_id,
            "title": paper.get("title") or seed_meta.get("title") or "",
            "openalex_id": str(work.get("id") or ""),
            "referenced_works": len(work.get("referenced_works") or []),
            "cited_by_count": int(work.get("cited_by_count") or 0),
        },
        "direction": direction,
        "items": items,
        "truncated": truncated,
    }


def import_items(items: list[dict], fetch_pdf: bool = True) -> dict:
    """把勾选的候选入库；判重复用 create_paper_from_metadata。"""
    created: list[str] = []
    skipped = 0
    failed: list[dict] = []
    for raw in list(items or [])[:MAX_IMPORT]:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        doi = metadata.normalize_doi(raw.get("doi") or "")
        arxiv_id = metadata.normalize_arxiv_id(raw.get("arxiv_id") or "")
        if db.find_duplicate(title=title, doi=doi, arxiv_id=arxiv_id):
            skipped += 1
            continue
        meta = {
            "title": title[:500],
            "authors": str(raw.get("authors") or "")[:1000],
            "year": str(raw.get("year") or "")[:16],
            "venue": str(raw.get("venue") or "")[:300],
            "doi": doi,
            "arxiv_id": arxiv_id,
            "url": str(raw.get("url") or "")[:1000],
            "abstract": str(raw.get("abstract") or "")[:4000],
        }
        try:
            created.append(ingest.create_paper_from_metadata(meta, source="references"))
        except Exception as exc:  # noqa: BLE001 - 单条失败不影响整批
            failed.append({"title": title, "error": f"{type(exc).__name__}: {exc}"[:200]})

    queued = 0
    if fetch_pdf and created:
        try:
            from .pdf_fetch import manager as pdf_fetch_manager

            queued = pdf_fetch_manager.start(created)
        except Exception:  # noqa: BLE001 - 抓取排队失败不影响入库
            queued = 0
    return {
        "created": len(created),
        "skipped": skipped,
        "failed": failed,
        "queued": queued,
        "paper_ids": created,
    }
