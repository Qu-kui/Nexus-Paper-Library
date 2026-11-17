"""从联网检索来源里抓取配图，缓存到本地后随回答返回。"""
from __future__ import annotations

import contextlib
import hashlib
import html as html_module
import json
import threading
import time
import ipaddress
import re
import socket
from urllib.parse import quote_plus
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from PIL import Image

from .. import config

_DNS_LOCK = threading.Lock()
_PRUNE_LOCK = threading.Lock()
_LAST_PRUNE = 0.0

MAX_PAGES = 4
MAX_IMAGES = 4
MAX_BYTES = 8 * 1024 * 1024
_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36'
_META_RE = re.compile(r'<meta[^>]+(?:property|name)\s*=\s*["\'](?:og:image(?::url)?|twitter:image(?::src)?)["\'][^>]*>', re.I)
_CONTENT_RE = re.compile(r'content\s*=\s*["\']([^"\']+)["\']', re.I)
_IMG_RE = re.compile(r'<img[^>]+src\s*=\s*["\']([^"\']+)["\']', re.I)
_TITLE_RE = re.compile(r'<title[^>]*>(.*?)</title>', re.I | re.S)
_BAD_IMAGE_RE = re.compile(r'(logo|icon|sprite|avatar|badge|banner|favicon|placeholder)', re.I)
_BOT_RE = re.compile(r'not a bot|captcha|just a moment|checking your browser|access denied|forbidden|cloudflare|cf-chl|验证码|人机验证', re.I)


def _prune(folder: Path, days: int = 7) -> None:
    cutoff = time.time() - days * 86400
    try:
        for item in folder.iterdir():
            if item.is_file() and item.stat().st_mtime < cutoff:
                item.unlink(missing_ok=True)
    except OSError:
        pass


def cache_dir() -> Path:
    folder = config.DATA_DIR / 'web-images'
    folder.mkdir(parents=True, exist_ok=True)
    _prune(folder)
    return folder


def _public_ips(host: str) -> list[str]:
    try:
        infos = socket.getaddrinfo(host, None)
    except OSError:
        return []
    ips: list[str] = []
    for info in infos:
        try:
            address = ipaddress.ip_address(info[4][0])
        except ValueError:
            continue
        if address.is_private or address.is_loopback or address.is_link_local or address.is_reserved:
            return []
        ips.append(str(address))
    return list(dict.fromkeys(ips))


def _public_url(url: str) -> bool:
    parsed = urlparse(url or '')
    if parsed.scheme not in {'http', 'https'} or not parsed.hostname:
        return False
    host = parsed.hostname.lower()
    if host in {'localhost'} or host.endswith('.local'):
        return False
    return bool(_public_ips(host))


@contextlib.contextmanager
def _pinned_dns(host: str):
    """把域名固定到校验过的 IP，避免 DNS rebinding 在连接时换成内网地址。"""
    ips = _public_ips(host)
    if not ips:
        yield
        return
    target = ips[0]
    original = socket.getaddrinfo
    with _DNS_LOCK:
        def pinned(hostname, port, *args, **kwargs):
            if str(hostname).lower() == host.lower():
                return original(target, port, *args, **kwargs)
            return original(hostname, port, *args, **kwargs)

        socket.getaddrinfo = pinned
        try:
            yield
        finally:
            socket.getaddrinfo = original


def _prune(folder: Path, days: int = 7, max_bytes: int = 256 * 1024 * 1024) -> None:
    """按时间+容量清理缓存，最多每 10 分钟检查一次。"""
    global _LAST_PRUNE
    now = time.time()
    with _PRUNE_LOCK:
        if now - _LAST_PRUNE < 600:
            return
        _LAST_PRUNE = now
    cutoff = now - days * 86400
    try:
        files = [(item.stat().st_mtime, item.stat().st_size, item) for item in folder.iterdir() if item.is_file()]
    except OSError:
        return
    files.sort()
    total = sum(size for _, size, _ in files)
    for mtime, size, item in files:
        if mtime < cutoff or total > max_bytes:
            try:
                item.unlink(missing_ok=True)
                total -= size
            except OSError:
                pass


def cache_dir() -> Path:
    folder = config.DATA_DIR / 'web-images'
    folder.mkdir(parents=True, exist_ok=True)
    _prune(folder)
    return folder


def _page_images(page_url: str, html: str) -> list[str]:
    found: list[str] = []
    for tag in _META_RE.findall(html):
        match = _CONTENT_RE.search(tag)
        if match:
            found.append(urljoin(page_url, match.group(1).strip()))
    if not found:
        for src in _IMG_RE.findall(html)[:8]:
            src = src.strip()
            if src.startswith('data:'):
                continue
            found.append(urljoin(page_url, src))
    return [url for url in dict.fromkeys(found) if _public_url(url)]


def _get_bytes(url: str, limit: int, content_prefix: str = '') -> bytes | None:
    """带总字节上限、手动重定向和 DNS 固定的 GET。"""
    headers = {'User-Agent': _UA}
    with httpx.Client(timeout=8, follow_redirects=False, headers=headers) as client:
        current = url
        for _ in range(4):
            if not _public_url(current):
                return None
            host = urlparse(current).hostname or ''
            with _pinned_dns(host):
                with client.stream('GET', current) as response:
                    if response.is_redirect and response.headers.get('location'):
                        current = urljoin(current, response.headers['location'])
                        continue
                    if response.status_code >= 400:
                        return None
                    ctype = response.headers.get('content-type', '').lower()
                    if content_prefix and not ctype.startswith(content_prefix):
                        return None
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > limit:
                            return None
                    return bytes(data)
    return None


def _fetch_page(url: str) -> tuple[str, list[str]]:
    if not _public_url(url):
        return '', []
    data = _get_bytes(url, 600_000, 'text/html')
    if not data:
        return '', []
    html = data.decode('utf-8', errors='ignore')[:600_000]
    title = ''
    match = _TITLE_RE.search(html)
    if match:
        title = ' '.join(re.sub(r'<[^>]+>', '', match.group(1)).split())[:120]
    if _BOT_RE.search(title) or _BOT_RE.search(html[:20_000]):
        return '', []
    return title, _page_images(url, html)


def _download(image_url: str) -> tuple[bytes, int, int, str] | None:
    if not _public_url(image_url) or _BAD_IMAGE_RE.search(image_url):
        return None
    data = _get_bytes(image_url, MAX_BYTES, 'image/')
    if not data:
        return None
    try:
        from io import BytesIO

        with Image.open(BytesIO(data)) as image:
            width, height = image.size
            fmt = (image.format or 'PNG').lower()
            if width < 320 or height < 200:
                return None
            ratio = width / max(1, height)
            if ratio < 0.35 or ratio > 3.5:
                return None
        suffix = {'jpeg': 'jpg', 'jpg': 'jpg', 'png': 'png', 'webp': 'webp', 'gif': 'gif'}.get(fmt, 'png')
        return data, width, height, suffix
    except Exception:  # noqa: BLE001
        return None


_STOP_TOKENS = {'the', 'and', 'for', 'with', 'structure', 'diagram', 'schematic',
                'what', 'how', 'are', 'image', 'images', 'photos', 'picture',
                '示意图', '结构图', '图片', '资料', '背景', '什么', '怎么', '如何', '相关',
                '结构', '原理', '图', '研究', '进展', '最新', '分析', '应用', '介绍'}


def _query_tokens(query: str) -> list[str]:
    tokens = re.findall(r'[A-Za-z]{3,}|[\u4e00-\u9fff]{2,}', query or '')
    return [token.lower() for token in tokens if token.lower() not in _STOP_TOKENS][:8]


def _search_360_urls(query: str, pages: int = 3) -> list[tuple[str, str]]:
    tokens = _query_tokens(query) if re.search(r'[\u4e00-\u9fff]', query) else []
    found: list[tuple[str, str]] = []
    for index in range(pages):
        pn = 12 + index * 12
        from urllib.parse import urlencode

        raw = _get_bytes('https://image.so.com/j?' + urlencode({'q': query, 'pn': pn, 'sn': 12}),
                         200_000)
        if not raw:
            continue
        try:
            data = json.loads(raw.decode('utf-8', errors='ignore'))
        except ValueError:
            continue
        for item in data.get('list') or []:
            title = str(item.get('title') or '')
            for key in ('img', 'thumb'):
                url = str(item.get(key) or '').strip()
                if not url.startswith('http'):
                    continue
                haystack = (title + ' ' + url).lower()
                if tokens and not any(token in haystack for token in tokens):
                    continue
                found.append((url, title or query))
    return list(dict.fromkeys(found))


def _search_image_urls(query: str, count: int = 12) -> list[tuple[str, str]]:
    return _search_360_urls(query)


def collect(sources: list[dict] | None, limit: int = MAX_IMAGES, query: str = '') -> list[dict]:
    """给来源网址列表收集配图；返回本地缓存后的图片信息。"""
    urls: list[str] = []
    for row in sources or []:
        url = str((row or {}).get('url') or '').strip()
        if url and url not in urls:
            urls.append(url)
    urls = [url for url in urls if _public_url(url)][:MAX_PAGES]
    folder = cache_dir()
    results: list[dict] = []
    with ThreadPoolExecutor(max_workers=max(1, min(4, len(urls)))) as pool:
        page_jobs = {pool.submit(_fetch_page, url): url for url in urls}
        pages: list[tuple[str, str, list[str]]] = []
        for job in as_completed(page_jobs):
            page_url = page_jobs[job]
            try:
                title, images = job.result()
            except Exception:  # noqa: BLE001
                continue
            pages.append((page_url, title, images))
    candidates: list[tuple[str, str, str]] = []  # image_url, page_url, title
    for page_url, title, images in pages:
        for image_url in images[:2]:
            candidates.append((image_url, page_url, title))
    search_page = 'https://image.so.com/i?q=' + quote_plus(query)
    has_tokens = bool(_query_tokens(query))
    if not candidates and query and has_tokens:
        for image_url, title in _search_image_urls(query, count=16)[:8]:
            candidates.append((image_url, search_page, title))
    if candidates:
        with ThreadPoolExecutor(max_workers=min(4, len(candidates))) as pool:
                jobs = {pool.submit(_download, image_url): (image_url, page_url, title)
                        for image_url, page_url, title in candidates}
                for job in as_completed(jobs):
                    image_url, page_url, title = jobs[job]
                    if len(results) >= limit:
                        break
                    try:
                        downloaded = job.result()
                    except Exception:  # noqa: BLE001
                        continue
                    if not downloaded:
                        continue
                    data, width, height, suffix = downloaded
                    name = hashlib.sha256(image_url.encode('utf-8')).hexdigest()[:20] + '.' + suffix
                    target = folder / name
                    if not target.exists():
                        try:
                            target.write_bytes(data)
                        except OSError:
                            continue
                    results.append({
                        'url': f'/api/web-images/{name}',
                        'source_url': page_url,
                        'title': title or urlparse(page_url).hostname or '',
                        'width': width,
                        'height': height,
                    })
    if len(results) < 2 and query and has_tokens:
        for image_url, title in _search_image_urls(query, count=16):
            if len(results) >= limit:
                break
            downloaded = _download(image_url)
            if not downloaded:
                continue
            data, width, height, suffix = downloaded
            name = hashlib.sha256(image_url.encode('utf-8')).hexdigest()[:20] + '.' + suffix
            target = folder / name
            if not target.exists():
                try:
                    target.write_bytes(data)
                except OSError:
                    continue
            results.append({
                'url': f'/api/web-images/{name}',
                'source_url': search_page,
                'title': title or query,
                'width': width,
                'height': height,
            })
    return results
