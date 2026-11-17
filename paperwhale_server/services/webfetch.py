"""抓取用户给出的网页/PDF 链接，供画像助手阅读。"""
from __future__ import annotations

import html as html_lib
import ipaddress
import re
import socket
import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import pdftext

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"(?is)<(script|style|noscript|svg|head)[^>]*>.*?</\1>")


def _safe_url(url: str) -> str:
    parsed = urlparse(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("不是有效的网页链接")
    try:
        address = ipaddress.ip_address(socket.gethostbyname(parsed.hostname))
        if address.is_private or address.is_loopback or address.is_link_local:
            raise ValueError("不能读取本机/内网地址")
    except socket.gaierror as exc:
        raise ValueError("域名解析失败") from exc
    return url.strip()


def _html_text(raw: str) -> tuple[str, str]:
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", raw)
    title = re.sub(r"\s+", " ", html_lib.unescape(_TAG_RE.sub("", title_match.group(1)))).strip() if title_match else ""
    cleaned = _SCRIPT_RE.sub(" ", raw)
    cleaned = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</h[1-6]>|</li>|</tr>", "\n", cleaned)
    text = html_lib.unescape(_TAG_RE.sub(" ", cleaned)).replace("\xa0", " ")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return title, "\n".join(lines)


def fetch(url: str, max_chars: int = 20000) -> dict:
    url = _safe_url(url)
    try:
        response = httpx.get(
            url,
            timeout=20,
            follow_redirects=True,
            trust_env=False,
            headers={"User-Agent": USER_AGENT, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
        )
    except Exception as exc:  # noqa: BLE001
        raise ValueError(f"打开链接失败：{exc}") from exc
    if response.status_code >= 400:
        raise ValueError(f"网站返回 HTTP {response.status_code}")
    content_type = (response.headers.get("content-type") or "").lower()
    if "application/pdf" in content_type or url.lower().endswith(".pdf"):
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as handle:
            handle.write(response.content)
            temp_path = Path(handle.name)
        try:
            pages = pdftext.page_texts(temp_path)
            text = "\n".join(f"[第 {page} 页]\n{content}" for page, content in pages)
        finally:
            temp_path.unlink(missing_ok=True)
        title = ""
    elif "html" in content_type or not content_type:
        title, text = _html_text(response.text)
    else:
        title = ""
        text = response.text
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if not text:
        raise ValueError("页面没有可读文字（可能需要登录或由 JS 动态加载）")
    return {
        "url": str(response.url),
        "title": (title or str(response.url))[:200],
        "text": text[:max_chars],
        "chars": len(text),
    }
