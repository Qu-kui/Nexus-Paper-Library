"""谷歌学术邮件订阅（Google Scholar Alerts）→ 发现页阅读 / 入库。"""
from __future__ import annotations

import email
import html as html_lib
import imaplib
import json
import re
import threading
import time
from datetime import datetime, timedelta
from email.header import decode_header
from typing import Any

from .. import config, db, llm
from . import ai_context, ingest, pdf_fetch
from .activity import tracked

SCHOLAR_SENDER = "scholaralerts-noreply@google.com"
_ANCHOR_RE = re.compile(
    r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>', re.IGNORECASE | re.DOTALL
)
_TAG_RE = re.compile(r"<[^>]+>")


def _decode(value: Any) -> str:
    if not value:
        return ""
    parts = decode_header(str(value))
    output = []
    for text, charset in parts:
        if isinstance(text, bytes):
            output.append(text.decode(charset or "utf-8", errors="ignore"))
        else:
            output.append(str(text))
    return html_lib.unescape("".join(output)).strip()


def _plain_text(fragment: str) -> str:
    text = _TAG_RE.sub(" ", fragment or "")
    text = html_lib.unescape(text).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def _settings() -> dict:
    settings = config.get_settings()
    return {
        "email": str(settings.get("scholar_email") or "").strip(),
        "host": str(settings.get("scholar_imap_host") or "imap.gmail.com").strip(),
        "password": str(settings.get("scholar_password") or ""),
        "folder": str(settings.get("scholar_folder") or "INBOX").strip() or "INBOX",
        "auto": bool(settings.get("scholar_auto_sync", True)),
        "last_sync": str(settings.get("scholar_last_sync") or ""),
    }


def configured() -> bool:
    current = _settings()
    return bool(current["email"] and current["password"])


def _connect():
    current = _settings()
    if not configured():
        raise ValueError("还没有配置谷歌学术订阅邮箱")
    conn = imaplib.IMAP4_SSL(current["host"], timeout=30)
    conn.login(current["email"], current["password"])
    # QQ/163 等国内邮箱要求客户端先发 IMAP ID，否则可能拒绝登录
    try:
        if "ID" not in imaplib.Commands:
            imaplib.Commands["ID"] = ("AUTH",)
        conn._simple_command(
            "ID", '("name" "PaperWhale" "version" "1.0" "vendor" "PaperWhale")'
        )
    except Exception:  # noqa: BLE001
        pass
    conn.select(current["folder"], readonly=False)
    return conn


def test_connection() -> dict:
    conn = _connect()
    try:
        status, folders = conn.list()
        return {"ok": True, "folders": len(folders or [])}
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass


def _body_text(message: email.message.Message) -> str:
    html = ""
    plain = ""
    if message.is_multipart():
        for part in message.walk():
            content_type = (part.get_content_type() or "").lower()
            if content_type not in {"text/html", "text/plain"}:
                continue
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                text = payload.decode(charset, errors="ignore") if payload else ""
            except Exception:  # noqa: BLE001
                continue
            if content_type == "text/html" and not html:
                html = text
            elif content_type == "text/plain" and not plain:
                plain = text
    else:
        try:
            payload = message.get_payload(decode=True)
            charset = message.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="ignore") if payload else ""
        except Exception:  # noqa: BLE001
            text = ""
        if (message.get_content_type() or "").lower() == "text/html":
            html = text
        else:
            plain = text
    return html or plain


def parse_alert_html(html_text: str) -> list[dict]:
    """从 Google Scholar 提醒邮件 HTML 里抽出论文条目。"""
    anchors = list(_ANCHOR_RE.finditer(html_text or ""))
    items: list[dict] = []
    for index, match in enumerate(anchors):
        href = html_lib.unescape(match.group(1) or "").strip()
        title = _plain_text(match.group(2))
        if not href.startswith("http") or len(title) < 8:
            continue
        if title.lower() in {"unsubscribe", "privacy", "help", "terms"}:
            continue
        # 提醒邮件顶部/底部的“搜索这条快讯”链接不是论文，跳过
        if "scholar.google" in href and "scholar_url" not in href and (
            "/scholar?" in href or "q=" in href
        ):
            continue
        if title.startswith("[") or title.startswith("\"") or re.search(r"\b(OR|AND)\b", title):
            continue
        if title.endswith("的新搜索结果") or title.endswith("的新搜索结果。"):
            continue
        if "google.com" in href and "scholar" not in href and "url=" not in href:
            continue
        next_start = anchors[index + 1].start() if index + 1 < len(anchors) else len(html_text)
        segment_html = html_text[match.end():next_start]
        divs = re.findall(
            r'<div[^>]*class="[^"]*gse_alrt_sni[^"]*"[^>]*>(.*?)</div>',
            segment_html,
            re.IGNORECASE | re.DOTALL,
        )
        if divs:
            meta_line = _plain_text(divs[0])[:300]
            snippet = _plain_text(" ".join(divs[1:]))[:1000] if len(divs) > 1 else meta_line
        else:
            segment = _plain_text(segment_html)
            meta_line = segment[:300]
            snippet = segment[:1000]
        authors = ""
        venue = ""
        if " - " in meta_line:
            left, right = meta_line.split(" - ", 1)
            authors = left.strip()
            venue = right.strip()
        elif meta_line:
            authors = meta_line
        year_match = re.search(r"(?:19|20)\d{2}", meta_line)
        items.append(
            {
                "title": title[:500],
                "authors": authors[:300],
                "venue": venue[:200],
                "snippet": snippet[:1000],
                "url": href[:1000],
                "published": year_match.group(0) if year_match else "",
            }
        )
    return items


def _title_key(text: str) -> str:
    """论文标题归一化：忽略大小写、标点和多余空格，降低跨快讯重复概率。"""
    value = html_lib.unescape(str(text or "")).lower()
    value = re.sub(r"\s*\(v\d+\)\s*$", "", value)
    value = re.sub(r"[^\w\u4e00-\u9fff]+", "", value)
    return value


def _existing_index() -> tuple[set[str], set[str]]:
    titles: set[str] = set()
    urls: set[str] = set()
    for row in db.query("SELECT title, url FROM scholar_items"):
        key = _title_key(row["title"])
        if key:
            titles.add(key)
        value = str(row["url"] or "").strip()
        if value:
            urls.add(value)
    return titles, urls


@tracked
def sync(limit: int = 40) -> dict:
    conn = _connect()
    added = 0
    messages = 0
    seen_titles, seen_urls = _existing_index()
    try:
        status, data = conn.uid("search", None, "FROM", f'"{SCHOLAR_SENDER}"')
        uids = (data[0] or b"").split() if status == "OK" and data else []
        if not uids:
            status, data = conn.uid("search", None, "SUBJECT", '"Google Scholar Alert"')
            uids = (data[0] or b"").split() if status == "OK" and data else []
        for uid in uids[-limit:]:
            status, fetched = conn.uid("fetch", uid, "(RFC822)")
            if status != "OK" or not fetched or not fetched[0]:
                continue
            raw = fetched[0][1]
            if not isinstance(raw, bytes):
                continue
            message = email.message_from_bytes(raw)
            messages += 1
            body = _body_text(message)
            for item in parse_alert_html(body):
                title_key = _title_key(item["title"])
                if (title_key and title_key in seen_titles) or (item["url"] and item["url"] in seen_urls):
                    continue
                item_id = db.new_id()
                now = db.now()
                db.execute(
                    "INSERT INTO scholar_items(id,message_uid,title,authors,venue,snippet,url,published,status,created_at,updated_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        item_id,
                        str(uid),
                        item["title"],
                        item["authors"],
                        item["venue"],
                        item["snippet"],
                        item["url"],
                        item.get("published", ""),
                        "new",
                        now,
                        now,
                    ),
                )
                added += 1
                if title_key:
                    seen_titles.add(title_key)
                if item["url"]:
                    seen_urls.add(item["url"])
            try:
                conn.uid("store", uid, "+FLAGS", "(\\Seen)")
            except Exception:  # noqa: BLE001
                pass
    finally:
        try:
            conn.logout()
        except Exception:  # noqa: BLE001
            pass
    cleanup_noise()
    config.update_settings({"scholar_last_sync": db.now()})
    return {"added": added, "messages": messages}


def list_items(status: str = "") -> list[dict]:
    if status:
        rows = db.query(
            "SELECT * FROM scholar_items WHERE status = ? ORDER BY updated_at DESC LIMIT 200",
            (status,),
        )
    else:
        rows = db.query(
            "SELECT * FROM scholar_items WHERE status = 'new' "
            "ORDER BY updated_at DESC LIMIT 200"
        )
    return [dict(row) for row in rows]


def unread_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS c FROM scholar_items WHERE status = 'new'")
    return int(row["c"]) if row else 0


def cleanup_noise() -> int:
    """把邮件里的查询/订阅噪音标记为忽略，不误当论文保留。"""
    rows = db.query(
        "SELECT id, title, snippet FROM scholar_items WHERE status = 'new'"
    )
    noise = 0
    for row in rows:
        title = str(row["title"] or "").strip()
        snippet = str(row["snippet"] or "")
        if (
            title.startswith("[")
            or title.startswith('"')
            or "的新搜索结果" in snippet
            or "的新搜索结果" in title
            or re.search(r"\b(OR|AND)\b", title)
        ):
            db.execute(
                "UPDATE scholar_items SET status = 'ignored', updated_at = ? WHERE id = ?",
                (db.now(), row["id"]),
            )
            noise += 1
    return noise


@tracked
def translate_missing(limit: int = 24) -> int:
    """把未翻译的订阅条目标题/摘要批量翻成中文，便于快速筛选。"""
    if not llm.is_configured() and not llm.is_mock():
        return 0
    cleanup_noise()
    rows = db.query(
        "SELECT id, title, snippet FROM scholar_items "
        "WHERE status != 'ignored' AND COALESCE(title_zh, '') = '' "
        "ORDER BY updated_at DESC LIMIT ?",
        (int(limit),),
    )
    rows = [row for row in rows if str(row["title"] or "").strip()]
    if not rows:
        return 0
    system = (
        "你是学术文献翻译助手。把用户给出的英文标题和摘要翻成简洁、准确的中文；"
        "专业术语、模型名、缩写保留英文原文。只输出 JSON 对象："
        '{"items": [{"title_zh": "中文标题", "snippet_zh": "中文摘要"}]}，'
        "items 数量、顺序必须与输入一致；摘要为空就输出空字符串。"
        "这是独立的订阅翻译任务，不读取聊天历史或用户研究画像，不据此添加个性化评价。"
    ) + ai_context.RULES
    done = 0
    for start in range(0, len(rows), 8):
        batch = rows[start:start + 8]
        payload = [
            {"title": str(row["title"] or "")[:300], "snippet": str(row["snippet"] or "")[:700]}
            for row in batch
        ]
        try:
            data = llm.chat_json(system, json.dumps(payload, ensure_ascii=False))
            items = data.get("items") if isinstance(data, dict) else None
            if not isinstance(items, list):
                continue
            for row, translated in zip(batch, items):
                if not isinstance(translated, dict):
                    continue
                title_zh = str(translated.get("title_zh") or "").strip()[:500]
                snippet_zh = str(translated.get("snippet_zh") or "").strip()[:1000]
                if not title_zh and not snippet_zh:
                    continue
                db.execute(
                    "UPDATE scholar_items SET title_zh = ?, snippet_zh = ?, updated_at = ? WHERE id = ?",
                    (title_zh, snippet_zh, db.now(), row["id"]),
                )
                done += 1
        except Exception:  # noqa: BLE001
            continue
    return done


def add_item(item_id: str) -> dict:
    item = db.query_one("SELECT * FROM scholar_items WHERE id = ?", (item_id,))
    if not item:
        raise ValueError("这条订阅记录不存在")
    meta = {
        "title": item["title"],
        "authors": item["authors"],
        "venue": item["venue"],
        "year": item["published"],
        "url": item["url"],
        "source": "scholar",
    }
    paper_id = ingest.create_paper_from_metadata(meta, source="scholar")
    try:
        pdf_fetch.manager.start([paper_id])
    except Exception:  # noqa: BLE001
        pass
    db.execute(
        "UPDATE scholar_items SET status = 'saved', paper_id = ?, updated_at = ? WHERE id = ?",
        (paper_id, db.now(), item_id),
    )
    return {"paper": db.get_paper(paper_id), "item": dict(db.query_one("SELECT * FROM scholar_items WHERE id = ?", (item_id,)))}


def ignore_item(item_id: str) -> dict:
    db.execute(
        "UPDATE scholar_items SET status = 'ignored', updated_at = ? WHERE id = ?",
        (db.now(), item_id),
    )
    return {"ok": True}


def _auto_loop() -> None:
    time.sleep(60)
    while True:
        try:
            current = _settings()
            if configured() and current["auto"]:
                last = current["last_sync"]
                due = True
                if last:
                    try:
                        due = datetime.fromisoformat(last) + timedelta(hours=24) <= datetime.now()
                    except ValueError:
                        due = True
                if due:
                    sync(limit=50)
                    translate_missing(24)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3600)


def _translation_loop() -> None:
    time.sleep(8)
    while True:
        try:
            translate_missing(24)
        except Exception:  # noqa: BLE001
            pass
        time.sleep(3600)


def start_auto_sync() -> None:
    threading.Thread(target=_auto_loop, name="scholar-auto-sync", daemon=True).start()
    threading.Thread(target=_translation_loop, name="scholar-translate", daemon=True).start()
