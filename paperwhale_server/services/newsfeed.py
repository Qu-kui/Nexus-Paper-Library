"""PaperWhale 资讯流：AI 前沿 + 官方媒体 + 任意关注方向。

设计目标：
- 默认带回 Hacker News / GitHub / AIHOT / OpenAI / DeepMind / Simon Willison 六个 AI 源；
- 默认带回六个官方媒体源；
- 用户输入任意方向后，自动用百度 / 360 / GDELT 检索最近新闻；
- 从文章域名反查 RSS/Atom，验证后作为该方向的渠道；
- 结果全部落盘到 DATA_DIR/newsfeed.json，前端只读缓存，不阻塞界面。
"""
from __future__ import annotations

import hashlib
import html as html_lib
import ipaddress
import json
import re
import socket
import threading
import time
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

import httpx

from .. import config
from . import newsfeed_ai

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
DATA_PATH = config.DATA_DIR / "newsfeed.json"
MAX_ITEMS = 800
# 资讯保留窗口：超过这个天数的条目不再展示，避免旧的高分新闻长期霸榜。
# 只做过期清理，不每次全量重新调用模型打分。
MAX_ITEM_AGE_DAYS = 30
MAX_TOPIC_ITEMS = 40
MAX_MEDIA_ITEMS = 20
GDELT_MIN_INTERVAL = 6.5
_GDELT_LAST = 0.0
_GDELT_LOCK = threading.Lock()
_GDELT_CACHE: dict[str, tuple[float, list[dict]]] = {}
_LOCK = threading.RLock()
_REFRESH_THREAD: threading.Thread | None = None
_REFRESHING = False
_ENRICH_LOCK = threading.Lock()
_ACTIVE_TOPICS: set[str] = set()
_TOPIC_JOBS: set[str] = set()

OFFICIAL_SOURCES = [
    {
        "id": "media-people",
        "name": "人民日报",
        "homepage": "http://www.people.com.cn/",
        "search": "人民日报",
        "rss": ["http://www.people.com.cn/rss/politics.xml"],
    },
    {
        "id": "media-xinhua",
        "name": "新华网",
        "homepage": "https://www.news.cn/",
        "search": "新华网",
        "rss": ["http://www.xinhuanet.com/politics/news_politics.xml"],
    },
    {
        "id": "media-cctv",
        "name": "新闻联播",
        "homepage": "https://tv.cctv.com/lm/xwlb/",
        "search": "新闻联播",
        "rss": [],
    },
    {
        "id": "media-thepaper",
        "name": "澎湃新闻",
        "homepage": "https://www.thepaper.cn/",
        "search": "澎湃新闻",
        "rss": [],
    },
    {
        "id": "media-qq",
        "name": "腾讯新闻",
        "homepage": "https://news.qq.com/",
        "search": "腾讯新闻",
        "rss": [],
    },
    {
        "id": "media-huanqiu",
        "name": "环球网",
        "homepage": "https://www.huanqiu.com/",
        "search": "环球网",
        "rss": [],
    },
]

_OFFICIAL_BY_ID = {row["id"]: row for row in OFFICIAL_SOURCES}

# 原始 AI 情报源：Hacker News / GitHub Releases / AIHOT / OpenAI /
# DeepMind / Simon Willison。沿用 DSH 插件的 id，方便识别与迁移。
AI_SOURCES = [
    {
        "id": "hn",
        "name": "Hacker News",
        "homepage": "https://news.ycombinator.com/",
        "kind": "hn",
        "keywords": ["AI", "LLM", "Agent"],
        "max": 20,
    },
    {
        "id": "github",
        "name": "GitHub",
        "homepage": "https://github.com/",
        "kind": "github",
        "repos": [
            "deepseek-ai/DeepSeek-V3",
            "vllm-project/vllm",
            "huggingface/transformers",
            "anthropics/anthropic-sdk-python",
        ],
        "max": 15,
    },
    {
        "id": "aihot",
        "name": "AIHOT",
        "homepage": "https://aihot.news/",
        "kind": "rss",
        "url": "https://aihot.news/feed/daily.xml",
        "max": 2,
    },
    {
        "id": "rss-openai",
        "name": "OpenAI",
        "homepage": "https://openai.com/news/",
        "kind": "rss",
        "url": "https://openai.com/news/rss.xml",
        "max": 20,
    },
    {
        "id": "rss-deepmind",
        "name": "DeepMind",
        "homepage": "https://deepmind.google/blog/",
        "kind": "rss",
        "url": "https://deepmind.google/blog/rss.xml",
        "max": 20,
    },
    {
        "id": "rss-simonw",
        "name": "Simon Willison",
        "homepage": "https://simonwillison.net/",
        "kind": "atom",
        "url": "https://simonwillison.net/atom/everything/",
        "max": 20,
    },
]

_AI_BY_ID = {row["id"]: row for row in AI_SOURCES}

_AI_IDS = {row["id"] for row in AI_SOURCES}
_MEDIA_IDS = {row["id"] for row in OFFICIAL_SOURCES}

MODEL_TOPIC_ID = "model-release"
POLITICS_TOPIC_ID = "cn-politics"
DEFAULT_TOPICS = [
    {
        "id": MODEL_TOPIC_ID,
        "label": "大模型发布",
        "builtin": True,
        "status": "ready",
    },
    {
        "id": POLITICS_TOPIC_ID,
        "label": "国内时政新闻",
        "builtin": True,
        "status": "ready",
    },
]


def _group_for_source(source: str) -> str:
    source = str(source or "")
    if source in _AI_IDS:
        return "ai"
    if source in _MEDIA_IDS or source.startswith("media-"):
        return "media"
    if source.startswith("topic-"):
        return "topic"
    return ""

_TAG_RE = re.compile(r"<[^>]+>")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]{2,}")
_ASCII_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9+.#_-]{2,}")
_STOP = {
    "the", "and", "for", "with", "from", "that", "this", "into", "over", "new",
    "news", "latest", "about", "after", "before", "will", "has", "have", "are",
    "人工智能", "新闻", "最新", "消息", "报道",
}


# ---------------------------------------------------------------------------
# 存储
# ---------------------------------------------------------------------------
def _empty_state() -> dict:
    return {
        "schema": 2,
        "updated_at": "",
        "refreshed_at": "",
        "refreshing": False,
        "last_error": "",
        "topics_seeded": False,
        "topics": [],
        "sources": [],
        "items": [],
        "enrich_error": "",
    }


def _make_default_topic(spec: dict) -> dict:
    return {
        "id": spec["id"],
        "label": spec["label"],
        "builtin": True,
        "status": "ready",
        "error": "",
        "created_at": _now(),
        "last_discovery_at": "",
        "articles": [],
        "channels": [],
    }


def _ensure_default_topics(state: dict) -> None:
    topics = state.setdefault("topics", [])
    specs = {str(spec["id"]): spec for spec in DEFAULT_TOPICS}
    # 默认方向只在第一次初始化时创建。用户删掉后不再自动补回，
    # 否则「移除」按钮会被下一次加载悄悄撤销。
    if not state.get("topics_seeded"):
        if not topics:
            topics.extend(_make_default_topic(spec) for spec in DEFAULT_TOPICS)
        state["topics_seeded"] = True
    for topic in topics:
        topic["builtin"] = str(topic.get("id") or "") in specs
        if topic["builtin"]:
            spec = specs[str(topic["id"])]
            topic["label"] = spec["label"]
            if not topic.get("status"):
                topic["status"] = "ready"
    # 默认方向排在自定义方向前面；已删除的默认方向不再出现。
    order = {spec["id"]: i for i, spec in enumerate(DEFAULT_TOPICS)}
    topics.sort(key=lambda t: (0 if t.get("builtin") else 1, order.get(str(t.get("id")), 99)))


def _source_topic(source_id: str) -> tuple[str, str]:
    if source_id in _AI_IDS:
        return MODEL_TOPIC_ID, "大模型发布"
    if source_id in _MEDIA_IDS or str(source_id).startswith("media-"):
        return POLITICS_TOPIC_ID, "国内时政新闻"
    return "", ""


def _site_id(url: str) -> str:
    return "site-" + hashlib.sha1(str(url).encode("utf-8")).hexdigest()[:10]


def _publisher_for(state: dict, url: str, topic_id: str, topic_label: str) -> tuple[str, str]:
    host = _domain(url)
    if not host:
        return "", ""
    quality = _site_quality(host)[0]
    # 检索结果域名不直接当站点：官方/已知媒体才收录。
    # 未知域名和聚合器只作为文章出处显示，不进入「站点源」。
    if quality not in {"official", "media"}:
        return "", host
    site = _register_site(
        state,
        host,
        f"https://{host}/",
        f"https://{host}/",
        "search-result",
        topic_id,
        topic_label,
        quality=quality,
    )
    return (str(site.get("id") or ""), host) if site else ("", host)


def _ensure_item_sites(state: dict) -> None:
    """旧的自定义方向条目只有一个 topic 假来源，按文章域名补成真实站点。"""
    topics = {str(t.get("id") or ""): t for t in state.get("topics", [])}
    for item in state.get("items", []):
        if not isinstance(item, dict):
            continue
        if not (item.get("source_group") == "topic" or str(item.get("source") or "").startswith("topic-")):
            continue
        topic_id = str(item.get("topic_id") or "")
        topic = topics.get(topic_id) or {}
        sid, host = _publisher_for(
            state, str(item.get("url") or ""), topic_id, str(topic.get("label") or item.get("topic_label") or "")
        )
        if host:
            item["source_name"] = host
        if sid:
            item["source"] = sid


def _ensure_topic_sites(state: dict) -> None:
    """合并旧缓存里同一站点的主页、订阅和检索结果记录。"""
    original = state.get("sources", [])
    state["sources"] = []
    aliases = {}
    for site in original:
        if site.get("type") == "search":
            continue
        for tid in site.get("topic_ids") or [""]:
            saved = _register_site(
                state, site.get("name", ""), site.get("url", ""),
                site.get("homepage", ""), site.get("type", "rss"), tid,
                (site.get("topic_labels") or {}).get(tid, ""),
                quality=str(site.get("quality") or ""),
                shared=bool(site.get("shared")),
            )
            if saved:
                aliases[site.get("id")] = saved["id"]
                for key in ("last_ok_at", "last_error", "added_at"):
                    if site.get(key):
                        saved[key] = site[key]
    for item in state.get("items", []):
        item["source"] = aliases.get(item.get("source"), item.get("source"))
    for topic in state.get("topics", []):
        if topic.get("builtin"):
            continue
        for channel in topic.get("channels") or []:
            if not isinstance(channel, dict) or channel.get("type") == "search":
                continue
            url = str(channel.get("url") or "")
            if _site_quality(_domain(url))[0] == "blocked":
                continue
            _register_site(
                state, channel.get("title", ""), url, f"https://{_domain(url)}/",
                channel.get("type", "rss"), topic.get("id", ""), topic.get("label", ""),
                quality="verified" if channel.get("type") in {"rss", "atom"} else "",
            )


def _cleanup_sources(state: dict) -> None:
    """移除旧缓存里的检索入口、聚合器和未验证的检索结果站点。"""
    removed: dict[str, dict] = {}
    kept: list[dict] = []
    for site in state.get("sources", []):
        quality = str(site.get("quality") or _site_quality(_domain(site.get("homepage") or site.get("url", "")))[0])
        if (
            site.get("type") == "search"
            or quality == "blocked"
            or (site.get("type") == "search-result" and quality == "unknown")
        ):
            removed[str(site.get("id") or "")] = site
            continue
        site["quality"] = quality
        kept.append(site)
    state["sources"] = kept
    for topic in state.get("topics", []):
        topic["channels"] = [
            channel for channel in topic.get("channels") or []
            if isinstance(channel, dict)
            and channel.get("type") != "search"
            and _site_quality(_domain(str(channel.get("url") or "")))[0] != "blocked"
        ]
    if not removed:
        return
    for item in state.get("items", []):
        original_source = str(item.get("source") or "")
        if original_source not in removed:
            continue
        topic_id = str(item.get("topic_id") or "")
        item["source"] = f"topic-{topic_id}" if topic_id else "topic"
        item["source_group"] = "topic"
        if not item.get("source_name"):
            item["source_name"] = str(removed[original_source].get("name") or "")


def _load() -> dict:
    with _LOCK:
        try:
            raw = json.loads(DATA_PATH.read_text(encoding="utf-8"))
        except Exception:
            raw = {}
        state = _empty_state()
        if isinstance(raw, dict):
            state.update({k: raw.get(k) for k in state if k in raw})
        if not isinstance(state.get("topics"), list):
            state["topics"] = []
        if not isinstance(state.get("sources"), list):
            state["sources"] = []
        if not isinstance(state.get("items"), list):
            state["items"] = []
        for key in ("topics", "sources", "items"):
            state[key] = [row for row in state[key] if isinstance(row, dict)]
        _ensure_default_topics(state)
        for topic in state["topics"]:
            if topic.get("status") == "discovering" and topic.get("id") not in _TOPIC_JOBS and topic.get("id") not in _ACTIVE_TOPICS:
                topic["status"] = "error"
                topic["error"] = "上次发现任务中断，请刷新重试"
        for item in state["items"]:
            item["score"] = newsfeed_ai._clamp_score(item.get("score"))
            if not item.get("source_group"):
                item["source_group"] = _group_for_source(item.get("source", ""))
            topic_id, topic_label = _source_topic(str(item.get("source") or ""))
            if not item.get("topic_id") and topic_id:
                item["topic_id"] = topic_id
                item["topic_label"] = topic_label
        _cleanup_sources(state)
        _ensure_topic_sites(state)
        _ensure_item_sites(state)
        _prune_items(state)
        return state


def _save(state: dict) -> None:
    with _LOCK:
        DATA_PATH.parent.mkdir(parents=True, exist_ok=True)
        state["updated_at"] = _now()
        tmp = DATA_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(DATA_PATH)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


CN_TZ = timezone(timedelta(hours=8))


def _iso_timestamp(value: Any, *, millis: bool = False) -> str:
    """把秒/毫秒时间戳转成 UTC ISO；非法值返回空串。"""
    try:
        number = float(str(value).strip())
    except (TypeError, ValueError):
        return ""
    if not (0 < number < 10**13):
        return ""
    if millis or number > 10**11:
        number /= 1000
    try:
        return datetime.fromtimestamp(number, tz=timezone.utc).isoformat(timespec="seconds")
    except (OverflowError, OSError, ValueError):
        return ""


def _iso_cn_datetime(value: Any) -> str:
    """解析带/不带时区的北京时间字符串，统一转 UTC ISO。"""
    text = str(value or "").strip().replace(" ", "T")
    if not text:
        return ""
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=CN_TZ)
    return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")


def _date_from_url(url: str) -> str:
    """从新闻 URL 提取 YYYY-MM-DD；拿不到完整日期返回空串。"""
    text = str(url or "")
    patterns = (
        r"/(20\d{2})/(\d{2})/(\d{2})(?:/|$)",
        r"/(20\d{2})(\d{2})(\d{2})/",
        r"/(20\d{2})/(\d{2})(\d{2})/",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        try:
            year, month, day = (int(part) for part in match.groups())
            if 2000 <= year <= 2100 and 1 <= month <= 12 and 1 <= day <= 31:
                return f"{year:04d}-{month:02d}-{day:02d}"
        except (TypeError, ValueError):
            continue
    return ""


def _cctv_published(url: str, title: str) -> str:
    """新闻联播：URL 给日期，标题里的 19:00 给时钟。"""
    day = _date_from_url(url)
    if not day:
        return ""
    clock = re.search(r"(?<!\d)([01]?\d|2[0-3])[:：]([0-5]\d)(?!\d)", str(title or ""))
    if not clock:
        return day
    try:
        return datetime.strptime(f"{day} {int(clock.group(1)):02d}:{clock.group(2)}", "%Y-%m-%d %H:%M").replace(tzinfo=CN_TZ).astimezone(timezone.utc).isoformat(timespec="seconds")
    except ValueError:
        return day


def _combine_cn_day_clock(day: str, clock: str = "") -> str:
    day = str(day or "").strip()
    clock = str(clock or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", day):
        return ""
    try:
        if clock and re.fullmatch(r"\d{1,2}:\d{2}(?::\d{2})?", clock):
            fmt = "%Y-%m-%d %H:%M:%S" if clock.count(":") == 2 else "%Y-%m-%d %H:%M"
            return datetime.strptime(f"{day} {clock}", fmt).replace(tzinfo=CN_TZ).astimezone(timezone.utc).isoformat(timespec="seconds")
        return day
    except ValueError:
        return day


def _extract_article_time(markup: str) -> str:
    """从文章页提取发布时间；兼容腾讯/澎湃/环球/新华的常见写法。"""
    text = str(markup or "")
    for pattern, kind in (
        (r'"pubtime"\s*:\s*"([^"]{8,40})"', "cn"),
        (r'"publishTime"\s*:\s*(\d{10,13})', "ms"),
        (r'"pubTime"\s*:\s*"([^"]{8,40})"', "cn"),
        (r'class=["\']article-time["\'][^>]*>(\d{10,13})<', "ms"),
    ):
        match = re.search(pattern, text, re.I)
        if match:
            stamp = _iso_cn_datetime(match.group(1)) if kind == "cn" else _iso_timestamp(match.group(1), millis=True)
            if stamp:
                return stamp
    cn_datetime = re.search(r"(20\d{2})年(\d{1,2})月(\d{1,2})日\s*([0-2]?\d:[0-5]\d)", text)
    if cn_datetime:
        stamp = _combine_cn_day_clock(
            f"{int(cn_datetime.group(1)):04d}-{int(cn_datetime.group(2)):02d}-{int(cn_datetime.group(3)):02d}",
            cn_datetime.group(4),
        )
        if stamp:
            return stamp
    meta = re.search(r'<meta[^>]+name=["\']publishdate["\'][^>]+content=["\']([^"\']+)', text, re.I)
    if meta:
        clock = re.search(r'class=["\']time["\'][^>]*>([0-2]?\d:[0-5]\d(?::[0-5]\d)?)<', text, re.I)
        stamp = _combine_cn_day_clock(meta.group(1), clock.group(1) if clock else "")
        if stamp:
            return stamp
    return ""


def _backfill_item_times(rows: list[dict], limit: int = 80) -> int:
    """旧条目/搜索结果缺发布时间时，从文章页补一次；返回补全数量。"""
    targets = [
        row for row in rows
        if (not row.get("published_at") or len(str(row.get("published_at"))) == 10)
        and str(row.get("url") or "").startswith("http")
    ][:limit]
    if not targets:
        return 0

    def one(client: httpx.Client, row: dict) -> str:
        url = str(row.get("url") or "")
        try:
            response = client.get(url)
            stamp = _extract_article_time(response.text) if response.status_code < 400 else ""
        except Exception:
            stamp = ""
        row["time_checked_at"] = _now()
        return stamp or _date_from_url(url)

    filled = 0
    try:
        with _client(10) as client, ThreadPoolExecutor(max_workers=8) as pool:
            for row, stamp in zip(targets, pool.map(lambda item: one(client, item), targets)):
                if stamp:
                    row["published_at"] = stamp
                    filled += 1
    except Exception:
        return filled
    return filled


def _fetch_huanqiu_times(rows: list[dict]) -> None:
    """环球网列表不带时间，从文章页的 article-time 补毫秒时间戳。"""
    targets = [row for row in rows if not row.get("published_at")][:20]
    urls = [str(row.get("url") or "") for row in targets]
    if not urls:
        return

    def one(client: httpx.Client, url: str) -> str:
        try:
            response = client.get(url)
            if response.status_code >= 400:
                return ""
            match = re.search(r'<textarea[^>]*class="article-time"[^>]*>(\d{10,13})</textarea>', response.text, re.I)
            return _iso_timestamp(match.group(1)) if match else ""
        except Exception:
            return ""

    try:
        with _client(10) as client, ThreadPoolExecutor(max_workers=6) as pool:
            for row, stamp in zip(targets, pool.map(lambda url: one(client, url), urls)):
                if stamp:
                    row["published_at"] = stamp
    except Exception:
        return


def _clean(value: Any, limit: int = 400) -> str:
    text = html_lib.unescape(_TAG_RE.sub(" ", str(value or "")))
    return re.sub(r"\s+", " ", text).strip()[:limit]


def _item_id(url: str, title: str) -> str:
    return hashlib.sha1(f"{url}|{title}".encode("utf-8", "ignore")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 相关性
# ---------------------------------------------------------------------------
def topic_tokens(label: str) -> list[str]:
    label = str(label or "").strip()
    tokens = [t for t in _CJK_RE.findall(label) if len(t) >= 2]
    for word in _ASCII_RE.findall(label):
        low = word.lower()
        if low not in _STOP and len(low) >= 3:
            tokens.append(low)
    # 去重保序
    seen: set[str] = set()
    out = []
    for token in tokens:
        if token not in seen:
            seen.add(token)
            out.append(token)
    return out


def is_relevant(text: str, label: str) -> bool:
    text = str(text or "")
    if not text:
        return False
    tokens = topic_tokens(label)
    if not tokens:
        return True
    lowered = text.lower()
    hits = 0
    for token in tokens:
        if token in lowered:
            hits += 1
    need = 1 if len(tokens) == 1 else max(1, int(len(tokens) * 0.6 + 0.999))
    return hits >= need


def _filter_relevant(rows: Iterable[dict], label: str) -> list[dict]:
    return [row for row in rows if is_relevant(f"{row.get('title','')} {row.get('summary','')}", label)]


# ---------------------------------------------------------------------------
# HTTP / 安全
# ---------------------------------------------------------------------------
def _client(timeout: float = 8) -> httpx.Client:
    return httpx.Client(
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        event_hooks={"request": [_check_request_url]},
        headers={"User-Agent": UA, "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"},
    )


def _check_request_url(request: httpx.Request) -> None:
    if not _safe_public_url(str(request.url)):
        raise ValueError("资讯源地址必须是公开的 HTTP(S) 地址")


def _safe_public_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            return False
        host = parsed.hostname
        if host in {"localhost", "127.0.0.1", "::1"}:
            return False
        for info in socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP):
            ip = ipaddress.ip_address(info[4][0])
            if not ip.is_global:
                return False
        return True
    except Exception:
        return False


def _domain(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").replace("www.", "", 1).lower()
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# 检索：百度 / 360 / GDELT
# ---------------------------------------------------------------------------
def search_baidu_news(query: str, limit: int = 20) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        return []
    limit = max(5, min(50, int(limit or 20)))
    url = "https://news.baidu.com/ns"
    try:
        with _client(15) as client:
            response = client.get(
                url,
                params={"word": query, "tn": "news", "from": "news", "cl": 2, "rn": limit, "ct": 1},
                headers={"Referer": "https://news.baidu.com/"},
            )
            if response.status_code >= 400:
                return []
            page = response.text
    except Exception:
        return []
    pattern = re.compile(
        r'class="news-title[^"]*"\s*>\s*<a[^>]+href="([^"]+)"[^>]*aria-label="标题：([^"]+)"',
        re.IGNORECASE,
    )
    rows: list[dict] = []
    seen: set[str] = set()
    for match in pattern.finditer(page):
        href = html_lib.unescape(match.group(1)).strip()
        title = _clean(html_lib.unescape(match.group(2)), 200)
        if not href.startswith("http") or not title or href in seen:
            continue
        seen.add(href)
        rows.append({"title": title, "url": href, "summary": "", "published_at": "", "domain": _domain(href)})
        if len(rows) >= limit:
            break
    return rows


def search_360_news(query: str, limit: int = 20) -> list[dict]:
    query = str(query or "").strip()
    if not query:
        return []
    limit = max(5, min(50, int(limit or 20)))
    try:
        with _client(15) as client:
            response = client.get(
                "https://news.so.com/ns",
                params={"q": query, "src": "sort_time"},
                headers={"Referer": "https://news.so.com/"},
            )
            if response.status_code >= 400:
                return []
            page = response.text
    except Exception:
        return []
    blocks = re.findall(r'<li[^>]*class="[^"]*res-list[^"]*"[^>]*>(.*?)</li>', page, re.S | re.I)
    rows: list[dict] = []
    seen: set[str] = set()
    for block in blocks[: max(limit * 2, 12)]:
        match = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*title="([^"]+)"', block, re.S | re.I)
        if not match:
            match = re.search(r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>', block, re.S | re.I)
        if not match:
            continue
        href = html_lib.unescape(match.group(1)).strip()
        title = _clean(html_lib.unescape(match.group(2) if match.lastindex and match.lastindex >= 2 else ""), 200)
        title = _clean(re.sub(r"<[^>]+>", " ", title), 200)
        if not href.startswith("http") or not title or href in seen:
            continue
        summary_match = re.search(r'<p[^>]*class="[^"]*summary[^"]*"[^>]*>(.*?)</p>', block, re.S | re.I)
        seen.add(href)
        rows.append({
            "title": title,
            "url": href,
            "summary": _clean(summary_match.group(1), 240) if summary_match else "",
            "published_at": "",
            "domain": _domain(href),
        })
        if len(rows) >= limit:
            break
    return rows


def _parse_gdelt_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def search_gdelt(query: str, limit: int = 30) -> tuple[list[dict], str]:
    query = str(query or "").strip()
    if not query:
        return [], "empty_query"
    if any(ch.isspace() for ch in query) or '"' in query:
        query = f'"{query.replace(chr(34), "")}"'
    cache_key = query.lower()
    cached = _GDELT_CACHE.get(cache_key)
    if cached and time.time() - cached[0] < 600:
        return [dict(row) for row in cached[1]], ""
    global _GDELT_LAST
    with _GDELT_LOCK:
        delay = _GDELT_LAST + GDELT_MIN_INTERVAL - time.monotonic()
        if delay > 0:
            time.sleep(delay)
        _GDELT_LAST = time.monotonic()
    url = "https://api.gdeltproject.org/api/v2/doc/doc"
    try:
        with _client(20) as client:
            response = client.get(
                url,
                params={
                    "query": query,
                    "mode": "artlist",
                    "maxrecords": max(5, min(50, limit)),
                    "format": "json",
                    "sort": "datedesc",
                    "timespan": "7d",
                },
            )
            if response.status_code == 429:
                return [], "GDELT 限流（每 5 秒一次），请稍后重试"
            if response.status_code >= 400:
                return [], f"GDELT HTTP {response.status_code}"
            data = response.json()
    except Exception as exc:
        return [], f"GDELT 连接失败：{str(exc)[:100]}"
    raw = data.get("articles") if isinstance(data, dict) else None
    rows: list[dict] = []
    seen: set[str] = set()
    for item in raw or []:
        item = item or {}
        href = str(item.get("url") or "").strip()
        title = _clean(item.get("title"), 200)
        if not href.startswith("http") or not title or href in seen:
            continue
        if not is_relevant(title, query.strip('"')):
            continue
        seen.add(href)
        rows.append({
            "title": title,
            "url": href,
            "summary": "",
            "published_at": _parse_gdelt_date(str(item.get("seendate") or "")),
            "domain": _clean(item.get("domain"), 120) or _domain(href),
        })
    _GDELT_CACHE[cache_key] = (time.time(), [dict(row) for row in rows])
    return rows, ""


def _has_cjk(value: str) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", value or ""))


# ---------------------------------------------------------------------------
# RSS 自动发现
# ---------------------------------------------------------------------------
def _feed_links(page: str, base: str) -> list[str]:
    links = re.findall(r"<link\b[^>]+>", page or "", re.I)
    found: list[str] = []
    for tag in links:
        if not re.search(r'rel\s*=\s*["\']alternate["\']', tag, re.I):
            continue
        if not re.search(r'type\s*=\s*["\']application/(?:rss|atom)\+xml["\']', tag, re.I):
            continue
        href = re.search(r'href\s*=\s*["\']([^"\']+)["\']', tag, re.I)
        if not href:
            continue
        url = urljoin(base, html_lib.unescape(href.group(1)))
        if url not in found:
            found.append(url)
    return found


def _parse_feed_date(value: str) -> str:
    value = str(value or "").strip()
    if not value:
        return ""
    try:
        return parsedate_to_datetime(value).astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        pass
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).isoformat(timespec="seconds")
    except Exception:
        return ""


def _parse_feed_titles(payload: bytes) -> list[dict]:
    try:
        root = ET.fromstring(payload[:2_000_000])
    except Exception:
        return []
    titles: list[dict] = []
    for node in root.iter():
        tag = node.tag.rsplit("}", 1)[-1].lower()
        if tag not in {"item", "entry"}:
            continue
        title = ""
        link = ""
        summary = ""
        published = ""
        for child in list(node):
            name = child.tag.rsplit("}", 1)[-1].lower()
            if name == "title":
                title = _clean(child.text)
            elif name == "link":
                href = (child.get("href") or child.text or "").strip()
                # Atom 里可能有 rel=self / rel=replies，优先 alternate。
                if not link or (child.get("rel") or "").lower() == "alternate":
                    link = href
            elif name in {"description", "summary", "content", "encoded"}:
                summary = _clean(child.text, 300)
            elif name in {"pubdate", "published", "updated", "date"}:
                published = published or _parse_feed_date(child.text or "")
        if title:
            titles.append({
                "title": title,
                "url": link,
                "summary": summary,
                "published_at": published,
            })
    return titles


def _validate_feed(url: str, label: str) -> bool:
    if not _safe_public_url(url):
        return False
    try:
        with _client(6) as client:
            response = client.get(url)
            if response.status_code >= 400:
                return False
            items = _parse_feed_titles(response.content)
    except Exception:
        return False
    return any(is_relevant(f"{row['title']} {row.get('summary','')}", label) for row in items[:20])


def discover_channels(articles: list[dict], label: str, max_channels: int = 3) -> list[dict]:
    channels: list[dict] = []
    seen_domains: set[str] = set()
    used_urls: set[str] = set()
    max_domains = 4
    for article in articles:
        if len(seen_domains) >= max_domains:
            break
        if len(channels) >= max_channels:
            break
        host = str(article.get("domain") or _domain(article.get("url", "")))
        host = host.replace("www.", "", 1).lower()
        if not host or host in seen_domains or _site_quality(host)[0] == "blocked":
            continue
        seen_domains.add(host)
        page_url = f"https://{host}/"
        if not _safe_public_url(page_url):
            continue
        try:
            with _client(6) as client:
                response = client.get(page_url)
                if response.status_code >= 400:
                    continue
                candidates = _feed_links(response.text, str(response.url))
        except Exception:
            continue
        if not candidates:
            candidates = [f"{page_url}feed", f"{page_url}rss.xml"]
        for candidate in candidates[:2]:
            if candidate in used_urls:
                continue
            used_urls.add(candidate)
            if _validate_feed(candidate, label):
                channels.append({
                    "id": hashlib.sha1(candidate.encode()).hexdigest()[:8],
                    "title": host,
                    "url": candidate,
                    "type": "rss",
                })
                break
    return channels


# ---------------------------------------------------------------------------
# AI 前沿源
# ---------------------------------------------------------------------------
def _stamp_ai_rows(rows: list[dict], source: dict) -> list[dict]:
    for row in rows:
        row["source"] = source["id"]
        row["source_name"] = source["name"]
        row["source_group"] = "ai"
        row["topic_id"] = MODEL_TOPIC_ID
        row["topic_label"] = "大模型发布"
        row.setdefault("domain", _domain(row.get("url", "")))
    return rows[: int(source.get("max") or 20)]


def _fetch_feed_source(source: dict) -> list[dict]:
    try:
        with _client(18) as client:
            response = client.get(
                source["url"],
                headers={
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
                    "User-Agent": UA,
                },
            )
            if response.status_code >= 400:
                return []
            rows = _parse_feed_titles(response.content)
    except Exception:
        return []
    return _stamp_ai_rows(rows, source)


def _fetch_hn(source: dict) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    max_items = int(source.get("max") or 20)
    since = int(time.time()) - 48 * 3600
    for keyword in source.get("keywords") or ["AI"]:
        if len(rows) >= max_items:
            break
        url = (
            "https://hn.algolia.com/api/v1/search"
            f"?tags=story&query={httpx.QueryParams({'q': keyword})['q']}"
            f"&hitsPerPage={max_items}&numericFilters=created_at_i>{since}"
        )
        data: dict = {}
        for attempt in range(2):
            try:
                with _client(15) as client:
                    response = client.get(url, headers={"Accept": "application/json"})
                if response.status_code == 429 or response.status_code >= 500:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                if response.status_code >= 400:
                    break
                data = response.json()
                break
            except Exception:
                if attempt == 0:
                    time.sleep(0.8)
        for hit in (data.get("hits") or []) if isinstance(data, dict) else []:
            if len(rows) >= max_items:
                break
            href = str(hit.get("url") or "").strip()
            if not href:
                href = f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
            if href in seen:
                continue
            title = _clean(hit.get("title"), 200)
            if not title:
                continue
            seen.add(href)
            points = hit.get("points") or 0
            comments = hit.get("num_comments") or 0
            summary = _clean(hit.get("story_text"), 300) or f"Hacker News · {points} 分 · {comments} 条评论"
            rows.append({
                "title": title,
                "url": href,
                "summary": summary,
                "published_at": _parse_feed_date(str(hit.get("created_at") or "")),
                "domain": _domain(href),
            })
    return _stamp_ai_rows(rows, source)


def _fetch_github(source: dict) -> list[dict]:
    rows: list[dict] = []
    max_items = int(source.get("max") or 15)
    token = ""
    try:
        import os

        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or ""
    except Exception:
        token = ""
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    for repo in source.get("repos") or []:
        if len(rows) >= max_items:
            break
        try:
            with _client(15) as client:
                response = client.get(
                    f"https://api.github.com/repos/{repo}/releases",
                    params={"per_page": 3},
                    headers=headers,
                )
            if response.status_code in {403, 429}:
                break
            if response.status_code >= 400:
                continue
            releases = response.json()
        except Exception:
            continue
        if not isinstance(releases, list):
            continue
        for release in releases:
            if len(rows) >= max_items:
                break
            href = str((release or {}).get("html_url") or "").strip()
            tag = str((release or {}).get("tag_name") or (release or {}).get("name") or "").strip()
            if not href or not tag:
                continue
            body = _clean((release or {}).get("body"), 300)
            rows.append({
                "title": f"[Release] {repo} {tag}",
                "url": href,
                "summary": body or f"{repo} 发布 {tag}",
                "published_at": _parse_feed_date(str((release or {}).get("published_at") or "")),
                "domain": "github.com",
            })
    return _stamp_ai_rows(rows, source)


def _fetch_ai_source(source: dict) -> list[dict]:
    kind = source.get("kind")
    if kind == "hn":
        return _fetch_hn(source)
    if kind == "github":
        return _fetch_github(source)
    return _fetch_feed_source(source)


def _fetch_ai_sources() -> list[dict]:
    rows: list[dict] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {pool.submit(_fetch_ai_source, source): source for source in AI_SOURCES}
        for future in as_completed(futures):
            try:
                rows.extend(future.result() or [])
            except Exception:
                continue
    return rows[: sum(int(row.get("max") or 20) for row in AI_SOURCES)]


# ---------------------------------------------------------------------------
# 官方媒体
# ---------------------------------------------------------------------------
def _fetch_cctv() -> list[dict]:
    try:
        with _client(15) as client:
            response = client.get("https://tv.cctv.com/lm/xwlb/")
            response.encoding = response.encoding or "gb18030"
            page = response.text
    except Exception:
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href="(https?://tv\.cctv\.com/20\d{2}/\d{2}/\d{2}/[^"]+\.shtml)"[^>]*>(.*?)</a>', page, re.S | re.I):
        href = html_lib.unescape(match.group(1))
        title = _clean(match.group(2), 160)
        if title and href not in seen:
            seen.add(href)
            rows.append({"title": title, "url": href, "summary": "", "published_at": _cctv_published(href, title), "domain": "tv.cctv.com"})
        if len(rows) >= MAX_MEDIA_ITEMS:
            break
    return rows


def _fetch_thepaper() -> list[dict]:
    try:
        with _client(15) as client:
            response = client.get("https://cache.thepaper.cn/contentapi/wwwIndex/rightSidebar")
            data = response.json()
    except Exception:
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    payload = data.get("data") if isinstance(data, dict) else data
    groups = payload.get("hotNews") if isinstance(payload, dict) else None
    all_groups = []
    if isinstance(payload, dict):
        for key in ("hotNews", "editorHandpicked", "financialInformationNews", "podcastNews", "studioNews"):
            value = payload.get(key)
            if isinstance(value, list):
                all_groups.extend(value)
    for item in all_groups:
        item = item or {}
        cont_id = item.get("contId") or item.get("id")
        title = _clean(item.get("name") or item.get("title"), 180)
        if not cont_id or not title:
            continue
        href = f"https://www.thepaper.cn/newsDetail_forward_{cont_id}"
        if href in seen:
            continue
        seen.add(href)
        published = _iso_timestamp(item.get("pubTimeLong"), millis=True) or _iso_cn_datetime(item.get("pubTime"))
        rows.append({"title": title, "url": href, "summary": "", "published_at": published, "domain": "thepaper.cn"})
        if len(rows) >= MAX_MEDIA_ITEMS:
            break
    return rows


def _fetch_qq() -> list[dict]:
    try:
        with _client(10) as client:
            response = client.get(
                "https://r.inews.qq.com/gw/event/hot_ranking_list",
                params={"page_size": 20},
                headers={"Referer": "https://news.qq.com/"},
            )
            data = response.json()
    except Exception:
        return []
    groups = data.get("idlist") if isinstance(data, dict) else None
    if not groups:
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    for group in groups[:1]:
        for item in (group or {}).get("newslist") or []:
            title = _clean(item.get("title"), 180)
            href = str(item.get("url") or "").strip()
            if not title or not href.startswith("http") or href in seen:
                continue
            if "每10分钟更新" in title:
                continue
            seen.add(href)
            rows.append({
                "title": title,
                "url": href,
                "summary": _clean(item.get("source"), 80),
                "published_at": _iso_timestamp(item.get("timestamp")) or _iso_cn_datetime(item.get("time")),
                "domain": "qq.com",
            })
            if len(rows) >= MAX_MEDIA_ITEMS:
                break
    return rows


def _fetch_huanqiu() -> list[dict]:
    try:
        with _client(10) as client:
            response = client.get("https://m.huanqiu.com/")
            if response.status_code >= 400:
                return []
            page = response.text
    except Exception:
        return []
    rows: list[dict] = []
    seen: set[str] = set()
    for block in re.findall(r'<li[^>]*class="[^"]*item[^"]*"[^>]*>(.*?)</li>', page, re.S | re.I):
        href_match = re.search(r'<textarea[^>]*class="href"[^>]*>([^<]+)</textarea>', block)
        title_match = re.search(r'<textarea[^>]*class="title"[^>]*>([^<]+)</textarea>', block)
        if not href_match or not title_match:
            continue
        href = urljoin("https://m.huanqiu.com/", html_lib.unescape(href_match.group(1)).strip())
        title = _clean(html_lib.unescape(title_match.group(1)), 180)
        if not title or href in seen:
            continue
        seen.add(href)
        rows.append({"title": title, "url": href, "summary": "", "published_at": "", "domain": "huanqiu.com"})
        if len(rows) >= MAX_MEDIA_ITEMS:
            break
    _fetch_huanqiu_times(rows)
    return rows


def _fetch_homepage(source: dict) -> list[dict]:
    homepage = str(source.get("homepage") or "").strip()
    if not homepage:
        return []
    try:
        with _client(15) as client:
            response = client.get(homepage)
            if response.status_code >= 400:
                return []
            response.encoding = response.encoding or "utf-8"
            page = response.text
    except Exception:
        return []
    year = datetime.now().year
    rows: list[dict] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        href = html_lib.unescape(match.group(1)).strip()
        title = _clean(match.group(2), 200)
        if len(title) < 8 or not href:
            continue
        href = urljoin(homepage, href)
        if not href.startswith("http") or href in seen:
            continue
        if not re.search(rf"/(?:{year}|{year - 1})\d{{4}}/", href) and not re.search(rf"/{year}/{year - 1}/", href):
            continue
        if not _safe_public_url(href):
            continue
        seen.add(href)
        rows.append({"title": title, "url": href, "summary": "", "published_at": _date_from_url(href), "domain": _domain(href)})
        if len(rows) >= MAX_MEDIA_ITEMS:
            break
    return rows


OFFICIAL_DOMAINS = {
    "media-people": ("people.com.cn",),
    "media-xinhua": ("news.cn", "xinhuanet.com"),
    "media-cctv": ("cctv.com",),
    "media-thepaper": ("thepaper.cn",),
    "media-qq": ("qq.com",),
    "media-huanqiu": ("huanqiu.com",),
}


def _official_domain_ok(source_id: str, url: str) -> bool:
    host = _domain(url)
    return any(host == suffix or host.endswith("." + suffix) for suffix in OFFICIAL_DOMAINS.get(source_id, ()))


def _fetch_media(source: dict) -> list[dict]:
    sid = source["id"]
    if sid == "media-cctv":
        rows = _fetch_cctv()
    elif sid == "media-thepaper":
        rows = _fetch_thepaper()
    elif sid == "media-qq":
        rows = _fetch_qq()
    elif sid == "media-huanqiu":
        rows = _fetch_huanqiu()
    else:
        rows = []
    if not rows:
        rows = _fetch_homepage(source)
    if not rows:
        rows = search_360_news(source.get("search") or source["name"], MAX_MEDIA_ITEMS)
    if not rows:
        rows = search_baidu_news(source.get("search") or source["name"], MAX_MEDIA_ITEMS)
    rows = [row for row in rows if _official_domain_ok(sid, str(row.get("url") or ""))]
    rows = _filter_relevant(rows, source["name"]) or rows
    for row in rows:
        if not row.get("published_at"):
            row["published_at"] = _date_from_url(str(row.get("url") or ""))
        row["source"] = sid
        row["source_name"] = source["name"]
        row["source_group"] = "media"
        row["topic_id"] = POLITICS_TOPIC_ID
        row["topic_label"] = "国内时政新闻"
    return rows[:MAX_MEDIA_ITEMS]


# ---------------------------------------------------------------------------
# 站点源
# ---------------------------------------------------------------------------
_SKIP_SITE_HOSTS = {
    "twitter.com", "x.com", "facebook.com", "instagram.com", "youtube.com",
    "bilibili.com", "weibo.com", "zhihu.com", "github.com", "arxiv.org",
    "baidu.com", "so.com", "google.com", "bing.com", "news.google.com",
}

# 站点质量：官方站点 > 已知媒体 > 已验证 RSS/首页 > 未验证的检索结果。
# 聚合器/自媒体平台不进入“站点源”，避免把 360kuai、百家号当成新闻源。
_OFFICIAL_SITE_HOSTS = {
    "gov.cn", "people.com.cn", "xinhuanet.com", "news.cn", "cctv.com",
    "thepaper.cn", "chinanews.com.cn", "openai.com", "deepmind.google",
}
_TRUSTED_SITE_HOSTS = {
    "caixin.com", "infzm.com", "yicai.com", "jiemian.com", "stcn.com",
    "cls.cn", "21jingji.com", "nbd.com.cn", "bjnews.com.cn", "ftchinese.com",
    "zaobao.com", "ifeng.com", "sina.com.cn", "163.com", "qq.com", "huanqiu.com",
    "sohu.com", "36kr.com", "huxiu.com", "tmtpost.com", "geekpark.net",
    "jiqizhixin.com", "qbitai.com", "reuters.com", "bbc.com", "apnews.com",
    "nytimes.com", "bloomberg.com", "wsj.com", "economist.com", "techcrunch.com",
    "theverge.com", "wired.com", "arstechnica.com", "nature.com", "science.org",
    "mit.edu", "stanford.edu", "news.ycombinator.com", "aihot.news",
    "simonwillison.net",
}
_BLOCKED_SITE_HOSTS = {
    "360kuai.com", "baijiahao.baidu.com", "toutiao.com", "weibo.com",
    "zhihu.com", "douyin.com", "kuaishou.com", "x.com", "twitter.com",
    "facebook.com", "instagram.com", "youtube.com", "bilibili.com",
    "so.com", "baidu.com", "google.com", "bing.com", "news.google.com",
    "mp.weixin.qq.com", "github.com", "arxiv.org",
}
_SITE_QUALITY_LABELS = {
    "official": "官方",
    "media": "媒体",
    "verified": "已校验",
    "unknown": "未验证",
}
_SITE_QUALITY_RANK = {"blocked": 0, "unknown": 1, "verified": 2, "media": 3, "official": 4}

# 没有可用模型推荐时的兜底：只放少量明确高质量的站，不碰聚合器。
_CURATED_TOPIC_SITES = {
    "时政": [
        {"name": "中国政府网", "homepage": "https://www.gov.cn/", "rss": ""},
        {"name": "财新网", "homepage": "https://www.caixin.com/", "rss": ""},
        {"name": "南方周末", "homepage": "https://www.infzm.com/", "rss": ""},
    ],
}


def _host_matches(host: str, domains: set[str]) -> bool:
    host = str(host or "").lower().replace("www.", "", 1)
    return any(host == domain or host.endswith("." + domain) for domain in domains)


def _site_quality(host: str) -> tuple[str, str]:
    host = str(host or "").lower().replace("www.", "", 1)
    if not host:
        return "unknown", _SITE_QUALITY_LABELS["unknown"]
    if _host_matches(host, _BLOCKED_SITE_HOSTS):
        return "blocked", "已屏蔽"
    if _host_matches(host, _OFFICIAL_SITE_HOSTS):
        return "official", _SITE_QUALITY_LABELS["official"]
    if _host_matches(host, _TRUSTED_SITE_HOSTS):
        return "media", _SITE_QUALITY_LABELS["media"]
    return "unknown", _SITE_QUALITY_LABELS["unknown"]


def _register_site(
    state: dict,
    name: str,
    url: str,
    homepage: str,
    kind: str,
    topic_id: str,
    topic_label: str,
    quality: str = "",
    shared: bool = False,
) -> dict | None:
    if kind == "search":
        return None
    url = str(url or "").strip()
    homepage = str(homepage or "").strip()
    if not url and not homepage:
        return None
    if not (url or homepage).startswith(("http://", "https://")):
        return None
    target = url or homepage
    host = _domain(homepage or target)
    if quality not in _SITE_QUALITY_RANK:
        quality = _site_quality(host)[0]
    if quality == "blocked":
        return None
    sites = state.setdefault("sources", [])
    for site in sites:
        if not isinstance(site, dict):
            continue
        same_url = site.get("url") == target
        same_home = _domain(site.get("homepage") or site.get("url", "")) == _domain(homepage or target)
        if same_url or same_home:
            if site.get("type") in {"search-result", "homepage"} and kind in {"rss", "atom"}:
                site.update({"url": target, "homepage": homepage, "type": kind, "name": _clean(name, 80)})
            if shared:
                site["shared"] = True
            old_quality = str(site.get("quality") or "unknown")
            if _SITE_QUALITY_RANK.get(quality, 0) > _SITE_QUALITY_RANK.get(old_quality, 0):
                site["quality"] = quality
            ids = site.setdefault("topic_ids", [])
            if topic_id and topic_id not in ids:
                ids.append(topic_id)
            labels = site.setdefault("topic_labels", {})
            if topic_id:
                labels[topic_id] = topic_label
            return site
    site = {
        "id": _site_id(target),
        "name": _clean(name, 80) or _domain(homepage or url) or "站点",
        "homepage": homepage or (f"https://{_domain(url)}/" if _domain(url) else url),
        "url": url,
        "type": kind,
        "quality": quality,
        "shared": bool(shared),
        "topic_ids": [topic_id] if topic_id else [],
        "topic_labels": {topic_id: topic_label} if topic_id else {},
        "added_at": _now(),
        "last_ok_at": "",
        "last_error": "",
    }
    sites.append(site)
    return site


def _fetch_generic_homepage(site: dict, label: str) -> list[dict]:
    homepage = str(site.get("homepage") or site.get("url") or "").strip()
    if not homepage or not _safe_public_url(homepage):
        return []
    try:
        with _client(15) as client:
            response = client.get(homepage)
            if response.status_code >= 400:
                return []
            response.encoding = response.encoding or "utf-8"
            page = response.text
    except Exception:
        return []
    host = _domain(homepage)
    rows: list[dict] = []
    seen: set[str] = set()
    for match in re.finditer(r'<a[^>]+href="([^"]+)"[^>]*>(.*?)</a>', page, re.S | re.I):
        href = urljoin(homepage, html_lib.unescape(match.group(1)).strip())
        title = _clean(match.group(2), 200)
        if len(title) < 8 or href in seen:
            continue
        if not href.startswith("http") or _domain(href) != host:
            continue
        if re.search(r"/(?:login|signin|register|privacy|terms|about|contact)(?:/|$)", href, re.I):
            continue
        if not is_relevant(title, label):
            continue
        if not _safe_public_url(href):
            continue
        seen.add(href)
        rows.append({
            "title": title,
            "url": href,
            "summary": "",
            "published_at": "",
            "domain": host,
        })
        if len(rows) >= 12:
            break
    return rows


def _fetch_dynamic_site(site: dict) -> list[dict]:
    if site.get("shared"):
        return []
    kind = str(site.get("type") or "rss").lower()
    topic_ids = site.get("topic_ids") or []
    topic_id = str(topic_ids[0]) if topic_ids else ""
    topic_label = str((site.get("topic_labels") or {}).get(topic_id) or "")
    if kind == "search":
        return []
    rows: list[dict] = []
    if kind in {"rss", "atom"} and site.get("url"):
        try:
            with _client(18) as client:
                response = client.get(
                    site["url"],
                    headers={
                        "Accept": "application/rss+xml, application/atom+xml, "
                                  "application/xml, text/xml, */*",
                        "User-Agent": UA,
                    },
                )
                if response.status_code < 400:
                    rows = _parse_feed_titles(response.content)
        except Exception:
            rows = []
    else:
        rows = _fetch_generic_homepage(site, "" if len(topic_ids) > 1 else topic_label)
    if not rows:
        return []
    out: list[dict] = []
    labels = site.get("topic_labels") or {}
    for tid in topic_ids or [""]:
        label = str(labels.get(tid) or "")
        matched = _filter_relevant(rows, label) if label else rows
        for original in matched[:20]:
            row = dict(original)
            row["source"] = site["id"]
            row["source_name"] = site.get("name") or _domain(row.get("url", ""))
            row["source_group"] = "custom"
            if tid:
                row["topic_id"] = tid
                row["topic_label"] = label
            row.setdefault("domain", _domain(row.get("url", "")))
            out.append(row)
    return out


def _discover_ai_sites(state: dict, topic: dict, allow_llm: bool = True) -> list[dict]:
    """让模型推荐新站点；验证 RSS/首页后收录，并立即抓一轮。"""
    label = str(topic.get("label") or "")
    topic_id = str(topic.get("id") or "")
    if not label or not topic_id:
        return []
    existing = [str(site.get("homepage") or site.get("url") or "") for site in state.get("sources", [])]
    existing += [str(row.get("homepage") or "") for row in AI_SOURCES]
    existing += [str(row.get("homepage") or "") for row in OFFICIAL_SOURCES]
    candidates = newsfeed_ai.suggest_sites(label, existing) if allow_llm else []
    if not candidates:
        # 模型不可用或没有给出候选时，用少量明确高质量的站兜底；
        # 仍然走下面的质量门槛，不会把聚合器塞进站点面板。
        key = _topic_key(label)
        for curated_key, curated in _CURATED_TOPIC_SITES.items():
            if curated_key == key or curated_key in label:
                candidates = curated
                break
    if not candidates:
        return []
    known_hosts = {_domain(url) for url in existing if _domain(url)}
    new_rows: list[dict] = []
    added = 0
    for candidate in candidates:
        if added >= 3:
            break
        homepage = str(candidate.get("homepage") or "")
        host = _domain(homepage)
        if not host or host in known_hosts or host in _SKIP_SITE_HOSTS:
            continue
        if not _safe_public_url(homepage):
            continue
        quality = _site_quality(host)[0]
        if quality == "blocked":
            continue
        rss_url = str(candidate.get("rss") or "")
        if rss_url and _validate_feed(rss_url, label):
            site = _register_site(
                state, candidate["name"], rss_url, homepage, "rss", topic_id, label,
                quality="official" if quality == "official" else "verified",
            )
        else:
            feed = ""
            homepage_ok = False
            try:
                with _client(12) as client:
                    response = client.get(homepage)
                    if response.status_code < 400:
                        homepage_ok = True
                        for link in _feed_links(response.text, str(response.url))[:3]:
                            if _validate_feed(link, label):
                                feed = link
                                break
            except Exception:
                pass
            if feed:
                site = _register_site(
                    state, candidate["name"], feed, homepage, "rss", topic_id, label,
                    quality="official" if quality == "official" else "verified",
                )
            elif homepage_ok and quality in {"official", "media"}:
                # 只对已知官方/媒体收录首页；未知站点必须有可验证的 RSS。
                site = _register_site(
                    state, candidate["name"], homepage, homepage, "homepage", topic_id, label,
                    quality=quality,
                )
            else:
                continue
        if not site:
            continue
        added += 1
        known_hosts.add(host)
        new_rows.extend(_fetch_dynamic_site(site))
    return new_rows


# ---------------------------------------------------------------------------
# 自定义方向
# ---------------------------------------------------------------------------
def _topic_slug(label: str) -> str:
    base = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "-", label.lower()).strip("-")
    if base and not _has_cjk(label):
        return base[:40]
    return "t-" + hashlib.sha1(label.encode("utf-8")).hexdigest()[:8]


def _find_topic(state: dict, topic_id: str) -> dict | None:
    for topic in state.get("topics", []):
        if topic.get("id") == topic_id:
            return topic
    return None


def _search_topic(label: str) -> tuple[list[dict], str, list[str]]:
    """返回 (文章, 使用的 provider, 错误集合)。"""
    prefer_cn = _has_cjk(label)
    errors: list[str] = []
    if prefer_cn:
        for name, fn in (("baidu", search_baidu_news), ("so360", search_360_news)):
            rows = fn(label, 30)
            rows = _filter_relevant(rows, label)
            rows = [row for row in rows if _site_quality(_domain(str(row.get("url") or "")))[0] != "blocked"]
            if rows:
                return rows, name, errors
            errors.append(f"{name}: no relevant")
        rows, err = search_gdelt(label, 30)
        rows = [row for row in (rows or []) if _site_quality(_domain(str(row.get("url") or "")))[0] != "blocked"]
        if rows:
            return rows, "gdelt", errors
        errors.append(err or "gdelt: no relevant")
    else:
        rows, err = search_gdelt(label, 30)
        rows = [row for row in (rows or []) if _site_quality(_domain(str(row.get("url") or "")))[0] != "blocked"]
        if rows:
            return rows, "gdelt", errors
        errors.append(err or "gdelt: no relevant")
        for name, fn in (("so360", search_360_news), ("baidu", search_baidu_news)):
            rows = fn(label, 30)
            rows = _filter_relevant(rows, label)
            rows = [row for row in rows if _site_quality(_domain(str(row.get("url") or "")))[0] != "blocked"]
            if rows:
                return rows, name, errors
            errors.append(f"{name}: no relevant")
    return [], "", errors


_TOPIC_NOISE = ("国内", "国际", "最新", "今日", "热点", "新闻", "资讯", "消息", "报道")


def _topic_key(label: str) -> str:
    text = re.sub(r"[\s·|｜/]+", "", str(label or "").lower())
    for word in _TOPIC_NOISE:
        text = text.replace(word, "")
    return text


def _topic_site_specs(state: dict, topic_id: str) -> list[dict]:
    """内置方向用固定源定义，自定义方向用已收录的站点。"""
    if topic_id == MODEL_TOPIC_ID:
        qualities = {
            "hn": "media", "github": "official", "aihot": "media",
            "rss-openai": "official", "rss-deepmind": "official", "rss-simonw": "media",
        }
        return [
            {
                "name": row["name"],
                "url": row.get("url") or row.get("homepage", ""),
                "homepage": row.get("homepage", ""),
                "type": "rss" if row.get("url") else "homepage",
                "quality": qualities.get(row["id"], "media"),
            }
            for row in AI_SOURCES
        ]
    if topic_id == POLITICS_TOPIC_ID:
        return [
            {
                "name": row["name"],
                "url": row.get("homepage", ""),
                "homepage": row.get("homepage", ""),
                "type": "homepage",
                "quality": "official",
            }
            for row in OFFICIAL_SOURCES
        ]
    specs: list[dict] = []
    for site in state.get("sources", []):
        if topic_id not in (site.get("topic_ids") or []):
            continue
        quality = str(site.get("quality") or _site_quality(_domain(site.get("homepage") or site.get("url", "")))[0])
        if quality not in {"official", "media", "verified"}:
            continue
        specs.append({
            "name": site.get("name") or _domain(site.get("homepage", "")),
            "url": site.get("url") or site.get("homepage", ""),
            "homepage": site.get("homepage") or site.get("url", ""),
            "type": site.get("type") or "rss",
            "quality": quality,
        })
    return specs


def _share_related_sources(state: dict, topic: dict) -> int:
    """把语义相近方向的高质量站点共享给新方向，不复制文章。"""
    topic_id = str(topic.get("id") or "")
    key = _topic_key(topic.get("label", ""))
    if not topic_id or len(key) < 2:
        return 0
    shared_sites = 0
    shared_from: list[str] = []
    for other in state.get("topics", []):
        other_id = str(other.get("id") or "")
        if not other_id or other_id == topic_id:
            continue
        other_key = _topic_key(other.get("label", ""))
        if not other_key or not (
            key == other_key
            or (len(other_key) >= 2 and (key in other_key or other_key in key))
        ):
            continue
        specs = _topic_site_specs(state, other_id)
        if not specs:
            continue
        shared_from.append(str(other.get("label") or other_id))
        for spec in specs:
            host = _domain(str(spec.get("homepage") or spec.get("url") or ""))
            quality = str(spec.get("quality") or _site_quality(host)[0])
            if quality not in {"official", "media", "verified"}:
                continue
            site = _register_site(
                state,
                str(spec.get("name") or host),
                str(spec.get("url") or spec.get("homepage") or ""),
                str(spec.get("homepage") or spec.get("url") or ""),
                str(spec.get("type") or "homepage"),
                topic_id,
                str(topic.get("label") or ""),
                quality=quality,
                shared=True,
            )
            if site:
                shared_sites += 1
        # 共享已有文章：只追加 topic id，保留原 topic_id/来源标签。
        for item in state.get("items", []):
            item_topics = list(item.get("topic_ids") or [])
            if other_id not in item_topics and item.get("topic_id") != other_id:
                continue
            if not item_topics:
                item_topics = [other_id]
            if topic_id not in item_topics:
                item_topics.append(topic_id)
            item["topic_ids"] = item_topics
    if shared_from:
        topic["shared_from"] = sorted(set(shared_from))
    return shared_sites


def _discover_topic_rows(state: dict, topic_id: str) -> None:
    with _LOCK:
        if topic_id in _ACTIVE_TOPICS:
            return
        _ACTIVE_TOPICS.add(topic_id)
    try:
        _discover_topic_rows_impl(state, topic_id)
    finally:
        with _LOCK:
            _ACTIVE_TOPICS.discard(topic_id)


def _discover_topic_rows_impl(state: dict, topic_id: str) -> None:
    with _LOCK:
        state = _load()
        topic = _find_topic(state, topic_id)
        if not topic:
            return
        topic["status"] = "discovering"
        topic["error"] = ""
        _save(state)
        # 先沿用相似方向已验证的高质量站点，再补充检索到的新来源。
        if _share_related_sources(state, topic):
            _save(state)
    label = topic["label"]
    needs_ai_sites = True
    last_ai = str(topic.get("last_ai_discovery_at") or "")
    if last_ai:
        try:
            needs_ai_sites = datetime.now(timezone.utc) - datetime.fromisoformat(last_ai) > timedelta(hours=24)
        except Exception:
            needs_ai_sites = True
    rows, provider, errors = _search_topic(label)
    rows = [
        row for row in rows[:MAX_TOPIC_ITEMS]
        if _site_quality(_domain(str(row.get("url") or "")))[0] != "blocked"
    ]
    for row in rows:
        row["topic_id"] = topic_id
        row["topic_label"] = label
        row["source"] = f"topic-{topic_id}"
        row["source_name"] = label
        row["source_group"] = "topic"
        sid, host = _publisher_for(state, str(row.get("url") or ""), topic_id, label)
        if host:
            row["source_name"] = host
        if sid:
            row["source"] = sid
    channels = discover_channels(rows, label)
    channel_list = channels
    with _LOCK:
        topic = _find_topic(state, topic_id)
        if topic:
            for channel in channels:
                host = _domain(str(channel.get("url") or "")) or str(channel.get("title") or "")
                _register_site(
                    state,
                    str(channel.get("title") or host),
                    str(channel.get("url") or ""),
                    f"https://{host}/" if host and "." in host else str(channel.get("url") or ""),
                    str(channel.get("type") or "rss"),
                    topic_id,
                    label,
                    quality="verified",
                )
    ai_rows: list[dict] = []
    try:
        # 24 小时内不重复调用模型，但 curated 高质量兜底仍然可以补。
        ai_rows = _discover_ai_sites(state, topic, allow_llm=needs_ai_sites)
    except Exception:
        ai_rows = []
    rows.extend(ai_rows)
    with _LOCK:
        current = _load()
        topic = _find_topic(current, topic_id)
        if topic:
            source_map = {}
            for site in state.get("sources", []):
                if topic_id not in (site.get("topic_ids") or []):
                    continue
                saved = _register_site(
                    current, site.get("name", ""), site.get("url", ""),
                    site.get("homepage", ""), site.get("type", "rss"), topic_id, label,
                    quality=str(site.get("quality") or ""),
                    shared=bool(site.get("shared")),
                )
                if saved:
                    source_map[site["id"]] = saved["id"]
            for row in rows:
                row["source"] = source_map.get(row.get("source"), row.get("source"))
            shared_sources = [
                site for site in current.get("sources", [])
                if topic_id in (site.get("topic_ids") or []) and site.get("shared")
            ]
            has_content = bool(rows or shared_sources)
            topic.update({
                "articles": rows[:MAX_TOPIC_ITEMS + 12],
                "channels": channel_list,
                "status": "discovering" if topic_id in _TOPIC_JOBS else ("ready" if has_content else "error"),
                "error": "" if has_content else (" / ".join(errors)[-300:] or "未发现相关资讯"),
                "last_discovery_at": _now(),
            })
            if needs_ai_sites and newsfeed_ai.llm.is_configured():
                topic["last_ai_discovery_at"] = _now()
            _merge_items(current, rows)
            _save(current)


# ---------------------------------------------------------------------------
# 合并 / 刷新
# ---------------------------------------------------------------------------
def _item_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        try:
            parsed = datetime.strptime(text[:10], "%Y-%m-%d")
        except ValueError:
            return None
        parsed = parsed.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _retention_cutoff(days: int = MAX_ITEM_AGE_DAYS) -> datetime:
    return datetime.now(timezone.utc) - timedelta(days=days)


def _is_older_than(row: dict, cutoff: datetime) -> bool:
    stamp = _item_datetime(row.get("published_at") or row.get("fetched_at"))
    return bool(stamp and stamp < cutoff)


def _prune_items(state: dict, days: int = MAX_ITEM_AGE_DAYS) -> int:
    """删除超过保留窗口的条目；没有可用日期的条目保留。"""
    cutoff = _retention_cutoff(days)
    kept = []
    removed = 0
    for row in state.get("items", []):
        if _is_older_than(row, cutoff):
            removed += 1
            continue
        kept.append(row)
    if removed:
        state["items"] = kept
        state["pruned_at"] = _now()
        state["pruned_count"] = int(state.get("pruned_count") or 0) + removed
    state["retention_days"] = days
    return removed


def _merge_items(state: dict, rows: list[dict]) -> None:
    existing = {row.get("url"): row for row in state.get("items", []) if row.get("url")}
    cutoff = _retention_cutoff()
    for row in rows:
        url = str(row.get("url") or "").strip()
        if not url.lower().startswith(("http://", "https://")) or not _domain(url):
            continue
        if _is_older_than(row, cutoff):
            continue
        if row.get("topic_id") and not _find_topic(state, row["topic_id"]):
            continue
        item = dict(row)
        item["id"] = _item_id(url, item.get("title", ""))
        item["fetched_at"] = _now()
        item.setdefault("score", None)
        if url in existing:
            current = existing[url]
            tids = list(dict.fromkeys((current.get("topic_ids") or [current.get("topic_id")]) +
                                     (item.get("topic_ids") or [item.get("topic_id")])))
            item["topic_ids"] = [tid for tid in tids if tid]
            if current.get("title") != item.get("title") or current.get("summary", "") != item.get("summary", ""):
                for field in ("title_zh", "summary_zh", "score", "score_reason", "scored_at", "score_version"):
                    current.pop(field, None)
            # 保留已有 AI 分数/摘要，补上新信息
            current.update({k: v for k, v in item.items() if v not in (None, "")})
        else:
            existing[url] = item
            state.setdefault("items", []).insert(0, item)
    state["items"] = sorted(state["items"], key=lambda row: str(row.get("fetched_at") or ""), reverse=True)[:MAX_ITEMS]


def _sort_items(items: list[dict]) -> list[dict]:
    def key(row: dict):
        score = newsfeed_ai._clamp_score(row.get("score"))
        return (
            1 if isinstance(score, (int, float)) else 0,
            float(score or 0),
            str(row.get("published_at") or row.get("fetched_at") or ""),
        )
    return sorted(items, key=key, reverse=True)


def _enrich_state(state: dict, limit: int = 60, topic_id: str = "") -> int:
    """在后台把条目翻译成中文并打分；每批落盘，页面轮询能看到进度。"""
    if not state.get("items"):
        return 0
    items = state["items"]
    if topic_id:
        # 新方向刚抓到的条目排在最前，避免被旧条目占满本轮额度。
        items = [row for row in items if topic_id in (row.get("topic_ids") or [row.get("topic_id")])]
        items += [row for row in state["items"] if topic_id not in (row.get("topic_ids") or [row.get("topic_id")])]

    fields = ("title_zh", "summary_zh", "score", "score_reason", "scored_at", "score_version")
    def persist(error: str | None = None) -> None:
        with _LOCK:
            current = _load()
            patches = {row.get("url"): row for row in items}
            for row in current["items"]:
                patch = patches.get(row.get("url"))
                if patch and row.get("title") == patch.get("title") and row.get("summary", "") == patch.get("summary", ""):
                    row.update({key: patch[key] for key in fields if key in patch})
            if error is not None:
                current["enrich_error"] = error
            _save(current)

    with _ENRICH_LOCK:
        latest = {row.get("url"): row for row in _load()["items"]}
        items = [latest[row.get("url")] for row in items if row.get("url") in latest]
        count, error = newsfeed_ai.enrich_items(items, on_batch=persist, limit=limit)
        persist(error)
    return count


def refresh_all(_reserved: bool = False) -> None:
    global _REFRESHING
    with _LOCK:
        if _REFRESHING and not _reserved:
            return
        _REFRESHING = True
        state = _load()
        state["refreshing"] = True
        _save(state)
    try:
        with _LOCK:
            active_topics = {str(t.get("id") or "") for t in _load().get("topics", [])}
        official_sources = OFFICIAL_SOURCES if POLITICS_TOPIC_ID in active_topics else []
        ai_sources = AI_SOURCES if MODEL_TOPIC_ID in active_topics else []
        media_rows: list[dict] = []
        with ThreadPoolExecutor(max_workers=6) as pool:
            futures = {pool.submit(_fetch_media, source): source for source in official_sources}
            futures.update({pool.submit(_fetch_ai_source, source): source for source in ai_sources})
            for future in as_completed(futures):
                source = futures[future]
                try:
                    rows = future.result()
                except Exception:
                    rows = []
                media_rows.extend(rows or [])
        with _LOCK:
            state = _load()
            _merge_items(state, media_rows)
            _save(state)
            dynamic_sites = [
                dict(site) for site in state.get("sources", [])
                if site.get("type") in {"rss", "atom", "homepage"}
                and not site.get("shared")
                and not str(site.get("id") or "").startswith("search-")
            ]
        dynamic_rows: list[dict] = []
        if dynamic_sites:
            with ThreadPoolExecutor(max_workers=4) as pool:
                futures = {pool.submit(_fetch_dynamic_site, site): site for site in dynamic_sites}
                for future in as_completed(futures):
                    site = futures[future]
                    try:
                        rows = future.result()
                    except Exception:
                        rows = []
                    dynamic_rows.extend(rows or [])
                    with _LOCK:
                        current = _load()
                        for saved in current.get("sources", []):
                            if saved.get("id") == site.get("id"):
                                saved["last_ok_at"] = _now() if rows else saved.get("last_ok_at", "")
                                saved["last_error"] = "" if rows else "本次没有抓到内容"
                                break
                        _save(current)
            with _LOCK:
                state = _load()
                _merge_items(state, dynamic_rows)
                _save(state)
        with _LOCK:
            state = _load()
            time_rows = list(state.get("items", []))
        if _backfill_item_times(time_rows):
            with _LOCK:
                current = _load()
                by_url = {row.get("url"): row for row in current.get("items", []) if row.get("url")}
                for row in time_rows:
                    target = by_url.get(row.get("url"))
                    if target and row.get("published_at"):
                        target["published_at"] = row["published_at"]
                        target["time_checked_at"] = row.get("time_checked_at", "")
                _save(current)
        for topic in list(state.get("topics", [])):
            if topic.get("builtin") or topic.get("id") in _TOPIC_JOBS:
                continue
            try:
                state = _load()
                _discover_topic_rows(state, topic["id"])
            except Exception as exc:
                with _LOCK:
                    state = _load()
                    item = _find_topic(state, topic["id"])
                    if item:
                        item["status"] = "error"
                        item["error"] = str(exc)[:300]
                        _save(state)
        with _LOCK:
            state = _load()
            _prune_items(state)
            state["items"] = _sort_items(state.get("items", []))[:MAX_ITEMS]
        _enrich_state(state, 300)
        with _LOCK:
            state = _load()
            _prune_items(state)
            state["items"] = _sort_items(state.get("items", []))[:MAX_ITEMS]
            state["refreshed_at"] = _now()
            state["refreshing"] = False
            state["last_error"] = ""
            _save(state)
    except Exception as exc:  # noqa: BLE001
        with _LOCK:
            state = _load()
            state["refreshing"] = False
            state["last_error"] = str(exc)[:300]
            _save(state)
    finally:
        with _LOCK:
            _REFRESHING = False


def start_refresh() -> bool:
    global _REFRESHING
    with _LOCK:
        if _REFRESHING:
            return False
        current = _load()
        _REFRESHING = True
        current["refreshing"] = True
        try:
            _save(current)
            threading.Thread(target=refresh_all, kwargs={"_reserved": True}, name="pw-newsfeed", daemon=True).start()
        except Exception:
            _REFRESHING = False
            current["refreshing"] = False
            _save(current)
            raise
        return True


def start_topic(label: str) -> dict:
    label = str(label or "").strip()
    if not label:
        raise ValueError("方向不能为空")
    if len(label) > 40:
        raise ValueError("方向名称最多 40 个字")
    with _LOCK:
        state = _load()
        existing = next((t for t in state["topics"] if t.get("label") == label), None)
        if existing:
            return existing
        default_spec = next((spec for spec in DEFAULT_TOPICS if spec["label"] == label), None)
        restored = default_spec is not None
        if restored:
            topic = _make_default_topic(default_spec)
            state["topics"].insert(0, topic)
            state["topics_seeded"] = True
        else:
            topic = {
                "id": _topic_slug(label),
                "label": label,
                "status": "pending",
                "error": "",
                "created_at": _now(),
                "last_discovery_at": "",
                "articles": [],
                "channels": [],
            }
            # 避免 slug 撞车
            while _find_topic(state, topic["id"]) or topic["id"] in _TOPIC_JOBS or topic["id"] in _ACTIVE_TOPICS:
                topic["id"] = topic["id"] + "-2"
            state["topics"].append(topic)
        _save(state)

    if restored:
        start_refresh()
        return {**topic, "restored": True, "status": "ready"}
    start_topic_refresh(topic["id"])
    return {**topic, "status": "discovering"}


def start_topic_refresh(topic_id: str) -> bool:
    with _LOCK:
        state = _load()
        topic = _find_topic(state, topic_id)
        if not topic:
            raise ValueError("方向不存在")
        if topic.get("builtin"):
            return start_refresh()
        if topic_id in _TOPIC_JOBS or topic_id in _ACTIVE_TOPICS:
            return False
        _TOPIC_JOBS.add(topic_id)
        topic["status"] = "discovering"
        topic["error"] = ""
        _save(state)

    def worker():
        error = ""
        try:
            _discover_topic_rows(_load(), topic_id)
            _enrich_state(_load(), 60, topic_id=topic_id)
        except Exception as exc:
            error = str(exc)[:300]
        finally:
            with _LOCK:
                state = _load()
                _TOPIC_JOBS.discard(topic_id)
                row = _find_topic(state, topic_id)
                if row:
                    has_shared = any(
                        topic_id in (item.get("topic_ids") or [item.get("topic_id")])
                        for item in state.get("items", [])
                    ) or any(
                        topic_id in (site.get("topic_ids") or []) and site.get("shared")
                        for site in state.get("sources", [])
                    )
                    has_content = bool(row.get("articles")) or has_shared
                    row["status"] = "error" if error else ("ready" if has_content else "error")
                    row["error"] = error or ("" if has_content else (row.get("error", "") or "未发现相关资讯"))
                    _save(state)

    try:
        threading.Thread(target=worker, name=f"pw-news-topic-{topic_id}", daemon=True).start()
    except Exception:
        with _LOCK:
            _TOPIC_JOBS.discard(topic_id)
        raise
    return True


def remove_topic(topic_id: str) -> bool:
    with _LOCK:
        state = _load()
        topic = _find_topic(state, topic_id)
        if not topic:
            return False
        before = len(state["topics"])
        state["topics"] = [t for t in state["topics"] if t.get("id") != topic_id]
        if len(state["topics"]) == before:
            return False
        kept_items = []
        for item in state["items"]:
            ids = [tid for tid in (item.get("topic_ids") or [item.get("topic_id")]) if tid and tid != topic_id]
            if not ids and item.get("topic_id") == topic_id:
                continue
            item["topic_ids"] = ids
            if item.get("topic_id") == topic_id:
                item["topic_id"] = ids[0]
                item["topic_label"] = (_find_topic(state, ids[0]) or {}).get("label", "")
            kept_items.append(item)
        state["items"] = kept_items
        kept_sources = []
        for site in state.get("sources", []):
            ids = [tid for tid in (site.get("topic_ids") or []) if tid != topic_id]
            if not ids:
                continue
            site["topic_ids"] = ids
            labels = site.get("topic_labels") or {}
            labels.pop(topic_id, None)
            site["topic_labels"] = labels
            kept_sources.append(site)
        state["sources"] = kept_sources
        _save(state)
        return True


def public_snapshot() -> dict:
    state = _load()
    if state.get("refreshing") and not _REFRESHING:
        state["refreshing"] = False
    items = _sort_items(state.get("items", []))
    item_counts: dict[str, int] = {}
    topic_counts: dict[str, int] = {}
    for item in items:
        sid = str(item.get("source") or "")
        if sid:
            item_counts[sid] = item_counts.get(sid, 0) + 1
        for tid in item.get("topic_ids") or [item.get("topic_id")]:
            if tid:
                topic_counts[tid] = topic_counts.get(tid, 0) + 1
    sources: list[dict] = []
    active_topic_ids = {str(t.get("id") or "") for t in state.get("topics", [])}
    for row in (AI_SOURCES if MODEL_TOPIC_ID in active_topic_ids else []):
        sources.append({
            "id": row["id"],
            "name": row["name"],
            "homepage": row.get("homepage", ""),
            "group": "ai",
            "kind": "API" if row.get("kind") in {"hn", "github"} else "RSS",
            "type": row.get("kind") or "rss",
            "topic_ids": [MODEL_TOPIC_ID],
            "quality": "official" if row["id"] in {"github", "rss-openai", "rss-deepmind"} else "media",
            "quality_label": "官方" if row["id"] in {"github", "rss-openai", "rss-deepmind"} else "媒体",
            "article_count": item_counts.get(row["id"], 0),
            "last_ok_at": "",
            "last_error": "",
        })
    for row in (OFFICIAL_SOURCES if POLITICS_TOPIC_ID in active_topic_ids else []):
        sources.append({
            "id": row["id"],
            "name": row["name"],
            "homepage": row.get("homepage", ""),
            "group": "media",
            "kind": "API" if row.get("id") == "media-qq" else "主页",
            "type": "media",
            "topic_ids": [POLITICS_TOPIC_ID],
            "quality": "official",
            "quality_label": "官方",
            "article_count": item_counts.get(row["id"], 0),
            "last_ok_at": "",
            "last_error": "",
        })
    for site in state.get("sources", []):
        if not isinstance(site, dict) or site.get("type") == "search":
            continue
        sid = str(site.get("id") or "")
        quality = str(site.get("quality") or _site_quality(_domain(site.get("homepage") or site.get("url", "")))[0])
        sources.append({
            "id": sid,
            "name": site.get("name") or _domain(site.get("homepage", "")) or "站点",
            "homepage": site.get("homepage", ""),
            "group": "custom",
            "kind": (
                "检索入口" if site.get("type") == "search"
                else "检索结果" if site.get("type") == "search-result"
                else "自动发现"
            ),
            "type": site.get("type") or "rss",
            "topic_ids": site.get("topic_ids") or [],
            "quality": quality,
            "quality_label": _SITE_QUALITY_LABELS.get(quality, ""),
            "article_count": item_counts.get(sid, 0),
            "last_ok_at": site.get("last_ok_at") or "",
            "last_error": site.get("last_error") or "",
        })
    unique_sources: dict[str, dict] = {}
    aliases: dict[str, str] = {}
    for source in sources:
        host = _domain(source.get("homepage", ""))
        key = host or source["id"]
        canonical = unique_sources.get(key)
        if canonical is None:
            unique_sources[key] = source
            canonical = source
        elif canonical is not source:
            canonical["topic_ids"] = list(dict.fromkeys(canonical["topic_ids"] + source["topic_ids"]))
        aliases[source["id"]] = canonical["id"]
    sources = list(unique_sources.values())
    counts: dict[str, int] = {}
    for item in items:
        item["source"] = aliases.get(item.get("source"), item.get("source"))
        sid = item.get("source")
        counts[sid] = counts.get(sid, 0) + 1
    for source in sources:
        source["article_count"] = counts.get(source["id"], 0)
    source_counts: dict[str, int] = {}
    for source in sources:
        for tid in source.get("topic_ids") or []:
            source_counts[tid] = source_counts.get(tid, 0) + 1
    topics = []
    for topic in state.get("topics", []):
        tid = str(topic.get("id") or "")
        topics.append({
            "id": tid,
            "label": topic.get("label"),
            "builtin": bool(topic.get("builtin")),
            "status": topic.get("status"),
            "error": topic.get("error") or "",
            "article_count": topic_counts.get(tid, 0),
            "source_count": source_counts.get(tid, 0),
            "channels": topic.get("channels") or [],
            "last_discovery_at": topic.get("last_discovery_at") or "",
        })
    return {
        "items": items,
        "total": len(items),
        "topics": topics,
        "sources": sources,
        "source_total": len(sources),
        "retention_days": int(state.get("retention_days") or MAX_ITEM_AGE_DAYS),
        "refreshing": bool(state.get("refreshing")),
        "scored": sum(1 for item in items if isinstance(item.get("score"), (int, float))),
        "translated": sum(1 for item in items if item.get("title_zh")),
        "updated_at": state.get("updated_at", ""),
        "refreshed_at": state.get("refreshed_at", ""),
        "last_error": state.get("last_error", ""),
        "enrich_error": state.get("enrich_error", ""),
    }
