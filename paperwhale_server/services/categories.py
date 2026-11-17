"""任意层级分类（大类 / 中类 / 小类 / …）与文件归档。

- 分类文件结构：<PDF目录>/论文文件/一级/二级/三级/.../<论文>.pdf
- 兼容旧的两级 major/minor 字段；完整路径以 category_path JSON 数组保存。
"""
from __future__ import annotations

import json
import shutil

from .. import config, db

MAX_LEVELS = 6


def _clean(value: str) -> str:
    return config.sanitize_segment(value)


def clean_levels(value) -> list[str]:
    """把列表或 "一级 / 二级 / 三级" 字符串规范成分类层级。"""
    raw: list[str] = []
    if isinstance(value, (list, tuple)):
        raw = [str(item or "").strip() for item in value]
    elif isinstance(value, str):
        raw = [
            part.strip()
            for part in value.replace("／", "/").replace(" / ", "/").split("/")
            if part.strip()
        ]
    levels: list[str] = []
    for item in raw:
        cleaned = _clean(item)
        if not cleaned:
            continue
        if levels and levels[-1] == cleaned:
            continue
        levels.append(cleaned)
    return levels[:MAX_LEVELS]


def levels_of(paper: dict) -> list[str]:
    raw = paper.get("category_path") or ""
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                levels = clean_levels(parsed)
                if levels:
                    return levels
        except Exception:  # noqa: BLE001 - 旧数据/损坏时回退
            pass
    levels = []
    if (paper.get("category_major") or "").strip():
        levels.append(str(paper["category_major"]).strip())
        if (paper.get("category_minor") or "").strip():
            levels.append(str(paper["category_minor"]).strip())
    if not levels and (paper.get("category") or "").strip():
        levels = clean_levels(paper["category"])
    return levels


def path_text(levels: list[str]) -> str:
    return " / ".join(levels)


def _move_pdf(paper_id: str, levels: list[str]) -> str:
    paper = db.get_paper(paper_id)
    if not paper:
        return ""
    rel_path = (paper.get("file_path") or "").strip()
    if not rel_path:
        return ""
    source = config.resolve_pdf_path(rel_path)
    if not source.is_file():
        return paper.get("file_path") or ""
    target_dir = config.paper_pdf_dir(*levels)
    target = target_dir / f"{paper_id}.pdf"
    try:
        if source.resolve() != target.resolve():
            shutil.move(str(source), str(target))
    except OSError:
        return paper.get("file_path") or ""
    db.update_paper(paper_id, {"file_path": str(target)})
    return str(target)


def assign_path(paper_id: str, levels, *, move: bool = True) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise ValueError("论文不存在")
    levels = clean_levels(levels)
    if not levels:
        db.update_paper(
            paper_id,
            {
                "category": "",
                "category_major": "",
                "category_minor": "",
                "category_path": "",
                "category_suggest": "",
                "category_status": "",
            },
        )
        return db.get_paper(paper_id) or {}
    major = levels[0]
    minor = levels[1] if len(levels) > 1 else ""
    db.update_paper(
        paper_id,
        {
            "category_major": major,
            "category_minor": minor,
            "category_path": json.dumps(levels, ensure_ascii=False),
            "category": path_text(levels),
            "category_suggest": "",
            "category_status": "approved",
        },
    )
    if move:
        _move_pdf(paper_id, levels)
    return db.get_paper(paper_id) or {}


def assign(paper_id: str, major: str, minor: str = "") -> dict:
    """兼容旧接口：两级分类。"""
    levels = [part for part in (major, minor) if str(part or "").strip()]
    return assign_path(paper_id, levels)


def taxonomy_paths() -> list[dict]:
    counts: dict[tuple[str, ...], int] = {}
    for row in db.query("SELECT category, category_major, category_minor, category_path FROM papers"):
        levels = levels_of(dict(row))
        if levels:
            key = tuple(levels)
            counts[key] = counts.get(key, 0) + 1
    items = [
        {"path": list(levels), "label": path_text(list(levels)), "count": count}
        for levels, count in counts.items()
    ]
    items.sort(key=lambda item: [part.lower() for part in item["path"]])
    return items


def taxonomy() -> list[dict]:
    """旧的两级树，保留给旧界面；新界面请用 taxonomy_paths。"""
    tree: dict[str, dict] = {}
    for item in taxonomy_paths():
        levels = item["path"]
        major = levels[0]
        minor = " / ".join(levels[1:]) or "未分类"
        node = tree.setdefault(major, {"major": major, "count": 0, "minors": {}})
        node["count"] += item["count"]
        node["minors"][minor] = node["minors"].get(minor, 0) + item["count"]
    return [
        {
            "major": node["major"],
            "count": node["count"],
            "minors": [{"name": name, "count": count} for name, count in node["minors"].items()],
        }
        for node in tree.values()
    ]


def existing_pairs() -> list[dict]:
    return [
        {"major": item["path"][0], "minor": " / ".join(item["path"][1:]) or "未分类"}
        for item in taxonomy_paths()
    ]


def existing_paths() -> list[tuple[str, ...]]:
    return [tuple(item["path"]) for item in taxonomy_paths()]


def register_suggestion(
    paper_id: str,
    major: str = "",
    minor: str = "",
    path: list[str] | None = None,
) -> dict:
    """AI 建议：已存在则直接归档，新路径挂起等待用户确认。"""
    levels = clean_levels(path if path else [major, minor])
    if levels and tuple(levels) in set(existing_paths()):
        return assign_path(paper_id, levels)
    suggestion = {
        "path": levels,
        "major": levels[0] if levels else "",
        "minor": levels[1] if len(levels) > 1 else "",
    }
    db.update_paper(
        paper_id,
        {
            "category_suggest": json.dumps(suggestion, ensure_ascii=False),
            "category_status": "pending",
        },
    )
    return db.get_paper(paper_id) or {}


def approve_suggestion(paper_id: str) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise ValueError("论文不存在")
    suggestion = paper.get("category_suggest") or {}
    if not suggestion:
        raise ValueError("没有待确认的分类建议")
    levels = suggestion.get("path") or [suggestion.get("major") or "", suggestion.get("minor") or ""]
    return assign_path(paper_id, levels)


def dismiss_suggestion(paper_id: str) -> dict:
    db.update_paper(paper_id, {"category_suggest": "", "category_status": ""})
    return db.get_paper(paper_id) or {}


def apply_category_string(paper_id: str, value: str) -> dict:
    """兼容旧接口：接受任意层级的 "一级 / 二级 / 三级" 字符串。"""
    return assign_path(paper_id, clean_levels(value))
