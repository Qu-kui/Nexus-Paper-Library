"""轻量联网搜索（Bing HTML，无需 API Key）。用于研究画像助手。"""
from __future__ import annotations

import base64
import html as html_lib
import re
from urllib.parse import parse_qs, unquote, urlparse

import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")
_RESULT_RE = re.compile(
    r'<li[^>]*class="[^"]*b_algo[^"]*"[^>]*>(.*?)</li>', re.IGNORECASE | re.DOTALL
)
_TITLE_RE = re.compile(r"<h2[^>]*>\s*<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", re.IGNORECASE | re.DOTALL)
_SNIPPET_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.IGNORECASE | re.DOTALL)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub(" ", fragment or ""))).strip()


def _decode_bing_url(href: str) -> str:
    href = html_lib.unescape(href or "").strip()
    if "bing.com/ck/a" not in href:
        return href
    try:
        query = parse_qs(urlparse(href).query)
        encoded = (query.get("u") or [""])[0]
        if encoded.startswith("a1"):
            encoded = encoded[2:]
        encoded += "=" * (-len(encoded) % 4)
        url = base64.urlsafe_b64decode(encoded.encode()).decode("utf-8", errors="ignore")
        return url or href
    except Exception:  # noqa: BLE001
        return href


def search(query: str, limit: int = 5) -> list[dict]:
    query = (query or "").strip()
    if not query:
        return []
    # 首选 ddgs（DuckDuckGo），比直接抓 Bing 稳定；失败再退回 Bing HTML
    try:
        from ddgs import DDGS

        results = DDGS().text(query, max_results=max(3, limit))
        items = []
        for row in results or []:
            title = str(row.get("title") or "").strip()
            url = str(row.get("href") or row.get("url") or "").strip()
            snippet = str(row.get("body") or row.get("snippet") or "").strip()
            if title and url.startswith("http"):
                items.append({"title": title[:200], "url": url[:500], "snippet": snippet[:400]})
            if len(items) >= limit:
                break
        if items:
            return items
    except Exception:  # noqa: BLE001
        pass
    return _bing_search(query, limit)


def _bing_search(query: str, limit: int) -> list[dict]:
    try:
        response = httpx.get(
            "https://www.bing.com/search",
            params={"q": query, "count": max(8, limit)},
            timeout=15,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
        if response.status_code >= 400:
            return []
        html_text = response.text[:900000]
    except Exception:  # noqa: BLE001
        return []
    items: list[dict] = []
    seen: set[str] = set()
    for block in _RESULT_RE.findall(html_text):
        title_match = _TITLE_RE.search(block)
        if not title_match:
            continue
        url = _decode_bing_url(title_match.group(1))
        title = _text(title_match.group(2))
        if not title or not url.startswith("http") or url in seen:
            continue
        snippet_match = _SNIPPET_RE.search(block)
        snippet = _text(snippet_match.group(1)) if snippet_match else ""
        seen.add(url)
        items.append({"title": title[:200], "url": url[:500], "snippet": snippet[:400]})
        if len(items) >= limit:
            break
    return items
