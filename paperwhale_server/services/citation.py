"""参考文献格式化与检索：浏览器扩展 / Word 插件共用。"""
from __future__ import annotations

import re

from .. import db

#: 支持的引文格式
STYLES = [
    {"id": "apa", "name": "APA 7th"},
    {"id": "mla", "name": "MLA 9th"},
    {"id": "chicago", "name": "Chicago author-date"},
    {"id": "ieee", "name": "IEEE"},
    {"id": "nature", "name": "Nature"},
    {"id": "gbt7714", "name": "GB/T 7714-2015"},
]


def _authors(paper: dict) -> list[str]:
    raw = (paper.get("authors") or "").strip()
    if not raw:
        return []
    if ";" in raw:
        parts = [part.strip() for part in raw.split(";") if part.strip()]
    elif " and " in raw and raw.count(" and ") <= 3:
        parts = [part.strip() for part in raw.split(" and ") if part.strip()]
    else:
        parts = [raw]
    cleaned = []
    for item in parts:
        item = re.sub(r"\s+", " ", item).strip(" ,.")
        if item:
            cleaned.append(item)
    return cleaned


def _split_name(name: str) -> tuple[str, str]:
    name = name.strip()
    if "," in name:
        last, first = name.split(",", 1)
        return last.strip(), first.strip()
    bits = name.split()
    if len(bits) == 1:
        return name, ""
    return bits[-1], " ".join(bits[:-1])


def _initials(first: str) -> str:
    pieces = re.split(r"[\s.\-]+", first)
    out = []
    for piece in pieces:
        if not piece:
            continue
        out.append(piece[0].upper() + ".")
    return " ".join(out)


def _apa_authors(names: list[str]) -> str:
    if not names:
        return ""
    formatted = []
    for name in names:
        last, first = _split_name(name)
        formatted.append(f"{last}, {_initials(first)}".strip().rstrip(","))
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) <= 20:
        return ", ".join(formatted[:-1]) + ", & " + formatted[-1]
    return ", ".join(formatted[:19]) + ", ... " + formatted[-1]


def _ieee_authors(names: list[str]) -> str:
    if not names:
        return ""
    formatted = []
    for name in names:
        last, first = _split_name(name)
        formatted.append(f"{_initials(first)} {last}".strip())
    if len(formatted) == 1:
        return formatted[0]
    if len(formatted) == 2:
        return f"{formatted[0]} and {formatted[1]}"
    return ", ".join(formatted[:-1]) + ", and " + formatted[-1]


def _nth_author(names: list[str], index: int) -> str:
    if index < len(names):
        return names[index]
    return ""


def _custom_spec(style: str) -> dict | None:
    """自定义样式命中就返回规格；内置/未知样式返回 None，走原来的代码路径。"""
    style = (style or "").strip()
    if not style:
        return None
    if style in {item["id"] for item in STYLES}:
        return None
    try:
        from .citation_style import get_spec
    except Exception:  # noqa: BLE001 - 渲染不能被新模块拖垮
        return None
    try:
        return get_spec(style)
    except Exception:  # noqa: BLE001 - 自定义格式读取失败时退回默认风格
        return None


def is_numeric(style: str) -> bool:
    """数值型格式：内置 IEEE/Nature/GB-T 7714 或自定义 spec.numeric。"""
    if style in {"ieee", "nature", "gbt7714"}:
        return True
    spec = _custom_spec(style)
    return bool(spec.get("numeric")) if spec else False


def _volume_issue_pages(paper: dict) -> tuple[str, str, str]:
    """取出并归一化卷/期/页；入库时已归一，这里兼容手工编辑留下的 45-67。"""
    from .metadata import normalize_issue, normalize_pages, normalize_volume

    return (
        normalize_volume(paper.get("volume")),
        normalize_issue(paper.get("issue")),
        normalize_pages(paper.get("pages")),
    )


def _volume_pages_text(volume: str, issue: str, pages: str) -> str:
    """复合 token ``volume_pages``：12(3): 45–67 / 12: 45–67 / 45–67。"""
    base = volume
    if issue:
        base = f"{base}({issue})" if base else f"({issue})"
    if pages:
        return f"{base}: {pages}" if base else pages
    return base


def _with_period(text: str) -> str:
    """给作者段补句点：已经以 "." 结尾就不重复补（避免 "L.."）。空串保持原行为。"""
    return text if text.endswith(".") else (text + ".")


def _join_sentences(parts: list[str]) -> str:
    """用 ". " 拼句子；上一段已以句末标点结尾时只补一个空格，避免双句点。"""
    text = ""
    for part in parts:
        if not part:
            continue
        if not text:
            text = part
        elif text.endswith((".", "!", "?", "。")):
            text += " " + part
        else:
            text += ". " + part
    return text


def reference(paper: dict, style: str = "apa") -> str:
    spec = _custom_spec(style)
    if spec:
        from .citation_style import render_reference

        return render_reference(paper, spec)
    names = _authors(paper)
    title = (paper.get("title") or "Untitled").strip().rstrip(".")
    year = (paper.get("year") or "n.d.").strip()
    venue = (paper.get("venue") or "").strip()
    volume, issue, pages = _volume_issue_pages(paper)
    doi = (paper.get("doi") or "").strip()
    url = (paper.get("url") or "").strip()
    link = f"https://doi.org/{doi}" if doi else url
    first_last = _split_name(names[0])[0] if names else ""

    if style == "mla":
        authors = ""
        if names:
            first = _split_name(names[0])
            authors = f"{first[0]}, {first[1]}" if first[1] else first[0]
            if len(names) == 2:
                authors += f", and {names[1]}"
            elif len(names) > 2:
                authors += ", et al"
        text = f'{_with_period(authors)} "{title}."'
        if venue:
            text += f" {venue},"
        if volume:
            text += f" vol. {volume},"
        if issue:
            text += f" no. {issue},"
        text += f" {year}"
        if pages:
            text += f", pp. {pages}"
        text += "."
        if link:
            text += f" {link}."
        return text

    if style == "chicago":
        authors = ""
        if names:
            first = _split_name(names[0])
            authors = f"{first[0]}, {first[1]}" if first[1] else first[0]
            if len(names) == 2:
                authors += f", and {names[1]}"
            elif len(names) > 2:
                authors += ", et al"
        text = f'{_with_period(authors)} "{title}."'
        if venue:
            text += f" {venue}"
            if volume:
                text += f" {volume}"
            if issue:
                text += f", no. {issue}"
            if not venue.endswith("."):
                text += f" ({year})"
                if pages:
                    text += f": {pages}"
                text += "."
            else:
                if pages:
                    text += f": {pages}"
                text += "."
        else:
            text += f" {year}"
            if pages:
                text += f": {pages}"
            text += "."
        if link:
            text += f" {link}."
        return text

    if style == "ieee":
        authors = _ieee_authors(names)
        text = f'{authors}, "{title},"'
        if venue:
            text += f" {venue},"
        if volume:
            text += f" vol. {volume},"
        if issue:
            text += f" no. {issue},"
        if pages:
            text += f" pp. {pages},"
        text += f" {year}."
        if doi:
            text += f" doi: {doi}."
        return text

    if style == "nature":
        parts = []
        if names:
            formatted = []
            for name in names[:5]:
                last, first = _split_name(name)
                formatted.append(f"{last}, {_initials(first).replace(' ', '')}".strip().rstrip(","))
            parts.append(" & ".join(formatted) if len(formatted) <= 2 else ", ".join(formatted))
        parts.append(title)
        # Nature 规范只带卷号，不带期号；页码跟随卷号（用逗号）
        numbers = volume
        if pages:
            numbers = f"{numbers}, {pages}" if numbers else pages
        year_part = f"({year})"
        if venue:
            if numbers:
                # 有卷号时卷紧跟刊名；只有页码时按规则用逗号：*Journal*, 45–67
                separator = " " if volume else ", "
                parts.append(f"*{venue}*{separator}{numbers} {year_part}")
                year_part = ""
            else:
                parts.append(f"*{venue}*")
        elif numbers:
            parts.append(numbers)
        if year_part:
            parts.append(year_part)
        text = _join_sentences(parts)
        if doi:
            text += f". https://doi.org/{doi}"
        return text

    if style == "gbt7714":
        authors = ""
        if names:
            formatted = []
            for name in names[:3]:
                last, first = _split_name(name)
                formatted.append(f"{last.upper()} {_initials(first).replace(' ', '').replace('.', '')}".strip())
            authors = ", ".join(formatted)
            if len(names) > 3:
                authors += ", 等"
        text = f"{_with_period(authors)} {title}[J]."
        if venue:
            text += f" {venue},"
        text += f" {year}"
        if volume:
            text += f", {volume}" + (f"({issue})" if issue else "")
        elif issue:
            text += f", ({issue})"
        if pages:
            text += f": {pages}"
        text += "."
        if doi:
            text += f" DOI:{doi}."
        return text

    # APA 默认
    authors = _apa_authors(names)
    text = f"{authors} ({year}). {title}."
    tail_parts: list[str] = []
    if volume:
        tail_parts.append(volume + (f"({issue})" if issue else ""))
    elif issue:
        tail_parts.append(f"({issue})")
    if pages:
        tail_parts.append(pages)
    if venue:
        tail = f"*{venue}*"
        if tail_parts:
            tail += ", " + ", ".join(tail_parts)
        text += f" {tail}."
    elif tail_parts:
        text += " " + ", ".join(tail_parts) + "."
    if link:
        text += f" {link}"
    return text


def inline(paper: dict, style: str = "apa") -> str:
    spec = _custom_spec(style)
    if spec:
        from .citation_style import render_inline

        return render_inline(paper, spec, int(paper.get("_index") or 1))
    names = _authors(paper)
    year = (paper.get("year") or "n.d.").strip()
    if style in {"ieee", "nature", "gbt7714"}:
        return f"[{paper.get('_index', 1)}]"
    if not names:
        return f"({year})"
    last = _split_name(names[0])[0]
    if len(names) == 1:
        return f"({last}, {year})"
    if len(names) == 2:
        second = _split_name(names[1])[0]
        return f"({last} & {second}, {year})"
    return f"({last} et al., {year})"


def search(query: str, limit: int = 20) -> list[dict]:
    query = (query or "").strip()
    sql = (
        "SELECT * FROM papers WHERE title LIKE ? OR authors LIKE ? OR doi LIKE ? "
        "OR venue LIKE ? ORDER BY updated_at DESC LIMIT ?"
    )
    like = f"%{query}%"
    rows = db.query(sql, (like, like, like, like, limit))
    items = []
    for row in rows:
        paper = dict(row)
        items.append(
            {
                "id": paper["id"],
                "title": paper.get("title") or "",
                "authors": paper.get("authors") or "",
                "year": paper.get("year") or "",
                "venue": paper.get("venue") or "",
                "doi": paper.get("doi") or "",
            }
        )
    return items


def search_label(item: dict) -> str:
    """Word 插件搜索结果的一行文字。"""
    names = _authors(item)
    parts = []
    if names:
        parts.append(_split_name(names[0])[0] + (" 等" if len(names) > 1 else ""))
    if item.get("year"):
        parts.append(str(item["year"]))
    if item.get("venue"):
        parts.append(str(item["venue"]))
    prefix = " · ".join(parts)
    title = (item.get("title") or "").strip()
    return f"{prefix}｜{title}" if prefix else title


def bibliography(paper_ids: list[str], style: str = "apa") -> list[dict]:
    items = []
    for index, paper_id in enumerate(paper_ids, start=1):
        paper = db.get_paper(paper_id)
        if not paper:
            continue
        paper["_index"] = index
        items.append(
            {
                "id": paper_id,
                "index": index,
                "citation": inline(paper, style),
                "reference": reference(paper, style),
            }
        )
    return items
