"""标签清洗与标签库：AI 生成标签时复用已有标签，避免标签爆炸。"""
from __future__ import annotations

import json
import re

from .. import db

#: 明显是导入噪音的标签
_JUNK_EXACT = {
    "unread",
    "to read",
    "toread",
    "todo",
    "to-do",
    "no doi found",
    "no doi",
    "no pdf",
    "imported",
    "未读",
    "待读",
    "待读列表",
    "其他",
    "misc",
    "others",
    "untitled",
}
_JUNK_PATTERNS = (
    re.compile(r"^no\s*doi", re.IGNORECASE),
    re.compile(r"^#?\d+$"),
    re.compile(r"^[^\w\u4e00-\u9fff]+$"),
)
#: 像 Zotero 自动打的超长学科分类标签，内容重复且占地方
_LONG_HIERARCHY = re.compile(r"\s[-–—]\s")


def clean_tag(raw: object) -> str:
    text = str(raw or "").strip().lstrip("#").strip()
    text = re.sub(r"\s+", " ", text)
    if not text:
        return ""
    lowered = text.lower()
    if lowered in _JUNK_EXACT:
        return ""
    if any(pattern.search(text) for pattern in _JUNK_PATTERNS):
        return ""
    if len(text) < 2 or len(text) > 32:
        return ""
    if len(text) > 24 and _LONG_HIERARCHY.search(text):
        return ""
    return text


def clean_tags(items, limit: int = 12) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items or []:
        tag = clean_tag(item)
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(tag)
        if len(result) >= limit:
            break
    return result


def tag_inventory(limit: int = 80) -> list[dict]:
    """全库标签统计（已清洗），按出现次数排序。"""
    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for row in db.query("SELECT tags FROM papers"):
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception:
            tags = []
        for raw in tags if isinstance(tags, list) else []:
            tag = clean_tag(raw)
            if not tag:
                continue
            key = tag.lower()
            counts[key] = counts.get(key, 0) + 1
            display.setdefault(key, tag)
    ordered = sorted(counts.items(), key=lambda item: (-item[1], item[0]))
    return [{"name": display[key], "count": count} for key, count in ordered[:limit]]


def cleanup_all_paper_tags() -> int:
    """清理库里已有的垃圾标签，返回被修改的论文数。"""
    changed = 0
    rows = db.query("SELECT id, tags FROM papers")
    for row in rows:
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception:
            tags = []
        if not isinstance(tags, list):
            tags = []
        cleaned = clean_tags(tags)
        if cleaned != [str(item) for item in tags if str(item).strip()]:
            db.update_paper(row["id"], {"tags": cleaned})
            changed += 1
    return changed
