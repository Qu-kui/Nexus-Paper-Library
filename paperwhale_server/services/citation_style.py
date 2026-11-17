"""自定义引文格式：规格校验、确定性渲染、AI 编译、CSL 导入、存取。

设计原则（与产品约定一致）：
- AI 只把一句自然语言编译成「格式规格 JSON」，渲染永远由本模块确定性完成，
  不允许每次渲染都调模型。
- 外部 .csl 也映射成同一份规格；解析不了的写法降级成顺序拼接并写进 warnings，
  不因为一个 <choose>/<group> 就整份拒绝。
- 内置 6 种格式仍走 services/citation.py 的老代码，本模块完全不管它们。
"""
from __future__ import annotations

import copy
import json
import uuid
import xml.etree.ElementTree as ET

from .. import db, llm
from . import citation

#: 规格字段默认值（version 1；字段名固定，缺字段补这里）
DEFAULT_SPEC: dict = {
    "name": "自定义格式",
    "numeric": False,
    "author_limit": 3,
    "inline_author_limit": 1,
    "et_al": "et al.",
    "inline_et_al": "et al.",
    "name_format": "family-initials",
    "initials_dots": True,
    "author_delimiter": ", ",
    "last_author_join": " & ",
    "inline_wrapper": "paren",
    "inline_year_sep": ", ",
    "bib_field_sep": ". ",
    "bib_year_style": "paren",
    "bib_order": ["authors", "year", "title", "venue", "volume_pages", "doi"],
    "italic": ["venue"],
    "title_quotes": False,
    "doi_style": "url",
    "url_style": "off",
    "trailing_period": True,
}

#: 内置样式 id（来自 citation.STYLES，独一份，不要另写一套）
NUMERIC_BUILTINS = {"ieee", "nature", "gbt7714"}
NAME_FORMATS = {"family-initials": "family-initials", "initials-family": "initials-family", "family": "family"}
INLINE_WRAPPERS = {"paren": "paren", "bracket": "bracket", "none": "none"}
BIB_YEAR_STYLES = {"paren": "paren", "plain": "plain"}
DOI_STYLES = {"url": "url", "plain": "plain", "off": "off"}
URL_STYLES = {"on": "on", "off": "off"}
BIB_FIELDS = ("authors", "year", "title", "venue", "volume", "pages", "volume_pages", "doi", "url")
BIB_FIELD_SET = set(BIB_FIELDS)
#: CSL 变量 → 规格字段
_CSL_VARIABLES = {
    "author": "authors",
    "authors": "authors",
    "editor": "authors",
    "issued": "year",
    "year": "year",
    "title": "title",
    "container-title": "venue",
    "journal": "venue",
    "DOI": "doi",
    "URL": "url",
}
_QUOTE_CHARS = "\"“”«»‘’'"
MAX_LENGTHS = {"name": 60, "et_al": 20, "inline_et_al": 20}
MAX_DELIMITER_LENGTH = 10
MAX_LIMIT = 99

_COMPILER_PROMPT = (
    "你是一名 AI 引文格式编译器，负责把用户的一句话格式需求编译成一份 JSON 规格。\n"
    "只输出一个合法 JSON 对象，不要输出任何其他文字；字符串内不要出现真实换行。\n"
    "字段含义（没提到的字段可以省略，程序会补默认值）：\n"
    '- numeric: true=数值型（正文 [1]、参考文献按出现顺序编号）；false=作者-年份型\n'
    '- author_limit: 参考文献最多列几位作者；0=全部列出，超过就省略并写 et_al\n'
    '- inline_author_limit: 正文引文最多列几位作者；0=全部\n'
    '- et_al / inline_et_al: 省略时追加的文字（如 "et al." 或 "等"）\n'
    '- name_format: "family-initials"（Last, A. B.）/ "initials-family"（A. B. Last）/ "family"（Last）\n'
    '- initials_dots: 名字缩写是否带点；author_delimiter / last_author_join: 作者分隔符\n'
    '- inline_wrapper: "paren"（(…)）/ "bracket"（[…]）/ "none"（不加括号）\n'
    '- inline_year_sep: 正文里作者与年份之间的分隔符\n'
    '- bib_field_sep: 参考文献字段之间的分隔符；bib_year_style: "paren"（(2020)）/ "plain"（2020）\n'
    '- bib_order: 参考文献字段顺序，取值只能是 authors / year / title / venue / '
    "volume / pages / volume_pages / doi / url；volume_pages 会渲染成 12(3): 45–67\n"
    "- 用户没提到卷期页时也要默认带上：在 venue 之后放 volume_pages\n"
    '- italic: 需要斜体的字段（bib_order 子集）；title_quotes: 标题是否加引号\n'
    '- doi_style: "url" / "plain" / "off"；url_style: "on" / "off"；trailing_period: 结尾是否补句号\n'
    "可参考这三个例子：\n"
    '1) 「只保留前三个作者，多了就写 et al.」→ {"author_limit": 3, "et_al": "et al."}\n'
    '2) 「数字编号格式」→ {"numeric": true, "inline_wrapper": "bracket"}\n'
    '3) 「正文只要第一作者，期刊名斜体」→ {"inline_author_limit": 1, "italic": ["venue"]}\n'
    "输出必须是一份完整字段名合法的 JSON 规格；bib_order 里默认要包含 volume_pages。"
)

_SAMPLE_PAPER = {
    "id": "sample-paper",
    "title": "示例论文标题",
    "authors": "张伟; 李娜; 王强; 赵敏",
    "year": "2020",
    "venue": "示例期刊",
    "doi": "10.1000/example.2020.001",
    "url": "https://example.org/sample",
}


# ---------------------------------------------------------------- 校验
def _as_bool(value, field: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    raise ValueError(f"{field} 必须是 true / false")


def _as_limit(value, field: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{field} 必须是 0-99 的整数")
    if isinstance(value, int):
        number = value
    elif isinstance(value, float) and value.is_integer():
        number = int(value)
    elif isinstance(value, str) and value.strip().isdigit():
        number = int(value.strip())
    else:
        raise ValueError(f"{field} 必须是 0-99 的整数")
    if number < 0 or number > MAX_LIMIT:
        raise ValueError(f"{field} 必须在 0-99 之间")
    return number


def _as_enum(value, field: str, allowed: dict) -> str:
    text = str(value or "").strip().lower()
    if text not in allowed:
        raise ValueError(f"{field} 只能是 {' / '.join(allowed)}")
    return text


def _as_text_field(value, field: str, limit: int, default: str) -> str:
    if value is None:
        return default
    text = str(value)
    return text[:limit]


def normalize_spec(raw: dict) -> dict:
    """校验并补默认值，返回一份只含固定字段的新 dict；非法输入抛 ValueError。"""
    if not isinstance(raw, dict):
        raise ValueError("引文格式规格必须是 JSON 对象")
    spec = copy.deepcopy(DEFAULT_SPEC)

    for field in ("numeric", "initials_dots", "title_quotes", "trailing_period"):
        if field in raw and raw[field] is not None:
            spec[field] = _as_bool(raw[field], field)
    for field in ("author_limit", "inline_author_limit"):
        if field in raw and raw[field] is not None:
            spec[field] = _as_limit(raw[field], field)

    if raw.get("name_format") is not None:
        spec["name_format"] = _as_enum(raw["name_format"], "name_format", NAME_FORMATS)
    if raw.get("inline_wrapper") is not None:
        spec["inline_wrapper"] = _as_enum(raw["inline_wrapper"], "inline_wrapper", INLINE_WRAPPERS)
    if raw.get("bib_year_style") is not None:
        spec["bib_year_style"] = _as_enum(raw["bib_year_style"], "bib_year_style", BIB_YEAR_STYLES)
    if raw.get("doi_style") is not None:
        spec["doi_style"] = _as_enum(raw["doi_style"], "doi_style", DOI_STYLES)
    if raw.get("url_style") is not None:
        spec["url_style"] = _as_enum(raw["url_style"], "url_style", URL_STYLES)

    spec["name"] = _as_text_field(raw.get("name"), "name", MAX_LENGTHS["name"], DEFAULT_SPEC["name"])
    spec["et_al"] = _as_text_field(raw.get("et_al"), "et_al", MAX_LENGTHS["et_al"], DEFAULT_SPEC["et_al"])
    spec["inline_et_al"] = _as_text_field(
        raw.get("inline_et_al"), "inline_et_al", MAX_LENGTHS["inline_et_al"], DEFAULT_SPEC["inline_et_al"]
    )
    for field in ("author_delimiter", "last_author_join", "inline_year_sep", "bib_field_sep"):
        if field in raw and raw[field] is not None:
            spec[field] = str(raw[field])[:MAX_DELIMITER_LENGTH]

    raw_order = raw.get("bib_order")
    if raw_order is not None:
        if not isinstance(raw_order, (list, tuple)):
            raise ValueError("bib_order 必须是字段数组")
        order: list[str] = []
        for item in raw_order:
            field = str(item or "").strip().lower()
            if field not in BIB_FIELD_SET:
                raise ValueError(
                    "bib_order 只能包含 authors / year / title / venue / volume / pages / "
                    "volume_pages / doi / url"
                )
            if field not in order:
                order.append(field)  # 去重但保留首次出现顺序
        if not order:
            raise ValueError("bib_order 不能为空")
        spec["bib_order"] = order

    raw_italic = raw.get("italic")
    if raw_italic is not None:
        if not isinstance(raw_italic, (list, tuple)):
            raise ValueError("italic 必须是字段数组")
        italic: list[str] = []
        for item in raw_italic:
            field = str(item or "").strip().lower()
            if field not in spec["bib_order"]:
                raise ValueError("italic 里的字段必须同时出现在 bib_order 中")
            if field not in italic:
                italic.append(field)
        spec["italic"] = italic
    else:
        # 默认斜体只对当前 bib_order 里实际存在的字段生效
        spec["italic"] = [field for field in DEFAULT_SPEC["italic"] if field in spec["bib_order"]]

    return spec


# ---------------------------------------------------------------- 作者渲染
def _format_name(name: str, spec: dict) -> str:
    family, given = citation._split_name(name)
    initials = citation._initials(given)
    if not spec["initials_dots"]:
        initials = initials.replace(".", "").strip()
    if spec["name_format"] == "family":
        return family
    if spec["name_format"] == "initials-family":
        return f"{initials} {family}".strip() if initials else family
    return f"{family}, {initials}".strip().rstrip(",") if initials else family


def _join_names(parts: list[str], spec: dict, fallback_join: str) -> str:
    if not parts:
        return ""
    if len(parts) == 1:
        return parts[0]
    join = spec["last_author_join"] or fallback_join
    if len(parts) == 2:
        return parts[0] + join + parts[1]
    return spec["author_delimiter"].join(parts[:-1]) + join + parts[-1]


def _append_et_al(text: str, spec: dict, et_al: str, *, comma: bool = True) -> str:
    """追加 et-al 文字；参考文献用逗号，正文用空格（(Xiang et al., 2023) 不带多余逗号）。"""
    if not et_al:
        return text
    if comma:
        # 约定：et_al 以空格开头时自带分隔，不再补 ", "
        return text + (et_al if et_al.startswith(" ") else ", " + et_al)
    return text + (et_al if et_al.startswith(" ") else " " + et_al)


def _author_block(paper: dict, spec: dict) -> str:
    names = citation._authors(paper)
    if not names:
        return ""
    limit = spec["author_limit"]
    truncated = limit > 0 and len(names) > limit
    shown = names[:limit] if truncated else names
    formatted = [item for item in (_format_name(name, spec) for name in shown) if item]
    if not formatted:
        return ""
    if truncated:
        # 一旦截断，前 N 位一律用 author_delimiter，不用 last_author_join（避免 "& … , et al."）
        text = spec["author_delimiter"].join(formatted)
        return _append_et_al(text, spec, spec["et_al"])
    return _join_names(formatted, spec, spec["author_delimiter"])


def _inline_authors(paper: dict, spec: dict) -> str:
    names = citation._authors(paper)
    if not names:
        return ""
    limit = spec["inline_author_limit"]
    truncated = limit > 0 and len(names) > limit
    shown = names[:limit] if truncated else names
    families = [citation._split_name(name)[0] for name in shown if citation._split_name(name)[0]]
    if not families:
        return ""
    text = _join_names(families, spec, " & ")
    if truncated:
        text = _append_et_al(text, spec, spec["inline_et_al"], comma=False)
    return text


# ---------------------------------------------------------------- 渲染
def _doi_text(paper: dict) -> str:
    doi = str(paper.get("doi") or "").strip()
    lowered = doi.lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if lowered.startswith(prefix):
            doi = doi[len(prefix):].strip()
            lowered = doi.lower()
    return doi


def _bib_numbers(paper: dict) -> tuple[str, str, str]:
    """卷/期/页（归一化后的），复刻 citation 的取值口径。"""
    return citation._volume_issue_pages(paper)


def _ensure_volume_pages(order: list[str]) -> list[str]:
    """自定义格式默认带上卷期页：没拆成 volume/pages 时在 venue 后插 volume_pages。"""
    if "volume" in order or "pages" in order:
        return order
    order = [field for field in order if field != "volume_pages"]
    index = order.index("venue") + 1 if "venue" in order else len(order)
    order.insert(index, "volume_pages")
    return order


def _field_text(paper: dict, field: str, spec: dict) -> str:
    if field == "authors":
        value = _author_block(paper, spec)
    elif field == "year":
        year = str(paper.get("year") or "").strip()
        if not year:
            return ""
        value = f"({year})" if spec["bib_year_style"] == "paren" else year
    elif field == "title":
        title = str(paper.get("title") or "").strip()
        if not title:
            return ""
        value = f'"{title}"' if spec["title_quotes"] else title
    elif field == "venue":
        value = str(paper.get("venue") or "").strip()
    elif field == "volume":
        value = _bib_numbers(paper)[0]
    elif field == "pages":
        value = _bib_numbers(paper)[2]
    elif field == "volume_pages":
        value = citation._volume_pages_text(*_bib_numbers(paper))
    elif field == "doi":
        if spec["doi_style"] == "off":
            return ""
        doi = _doi_text(paper)
        if not doi:
            return ""
        value = f"https://doi.org/{doi}" if spec["doi_style"] == "url" else doi
    elif field == "url":
        if spec["url_style"] != "on":
            return ""
        value = str(paper.get("url") or "").strip()
    else:
        value = ""
    if not value:
        return ""
    if field in spec["italic"]:
        value = f"*{value}*"
    return value


def render_reference(paper: dict, spec: dict) -> str:
    """按规格渲染一条参考文献；空字段跳过，不留下空分隔符。"""
    parts = [text for text in (_field_text(paper, field, spec) for field in spec["bib_order"]) if text]
    if not parts:
        return ""
    # 分隔符以 "." 开头、上一字段本身已以 "." 结尾时不重复句点（如 et al. + ". "）
    text = parts[0]
    for part in parts[1:]:
        connector = spec["bib_field_sep"]
        if connector.startswith(".") and text.endswith("."):
            connector = connector[1:]
        text += connector + part
    if spec["trailing_period"] and not text.endswith("."):
        text += "."
    return text


def render_inline(paper: dict, spec: dict, index: int = 1) -> str:
    """按规格渲染正文引文：numeric 用 [index]，否则作者-年份。"""
    if spec["numeric"]:
        return f"[{index}]"
    author_text = _inline_authors(paper, spec)
    year = str(paper.get("year") or "").strip()
    if author_text and year:
        inner = author_text + spec["inline_year_sep"] + year
    else:
        inner = author_text or year
    if not inner:
        return ""
    if spec["inline_wrapper"] == "paren":
        return f"({inner})"
    if spec["inline_wrapper"] == "bracket":
        return f"[{inner}]"
    return inner


# ---------------------------------------------------------------- 存取
def is_builtin(style_id: str) -> bool:
    return (style_id or "") in {item["id"] for item in citation.STYLES}


def _builtin_name(style_id: str) -> str:
    for item in citation.STYLES:
        if item["id"] == style_id:
            return item["name"]
    return style_id


def _safe_spec(raw) -> dict | None:
    try:
        return normalize_spec(json.loads(raw) if isinstance(raw, str) else raw)
    except (ValueError, TypeError, json.JSONDecodeError):
        return None


def _style_meta(style_id: str, name: str, source: str, builtin: bool, spec: dict | None) -> dict:
    return {
        "id": style_id,
        "name": name,
        "source": source,
        "builtin": builtin,
        "spec": spec,
    }


def list_styles() -> list[dict]:
    """内置在前、自定义在后（created_at 升序）。"""
    items = [
        {
            "id": item["id"],
            "name": item["name"],
            "builtin": True,
            "numeric": item["id"] in NUMERIC_BUILTINS,
        }
        for item in citation.STYLES
    ]
    try:
        rows = db.query("SELECT id, name, spec FROM citation_styles ORDER BY created_at ASC, rowid ASC")
    except Exception:  # noqa: BLE001 - 老库/测试未建表时只返回内置
        return items
    for row in rows:
        spec = _safe_spec(row["spec"]) or {}
        items.append(
            {
                "id": row["id"],
                "name": row["name"],
                "builtin": False,
                "numeric": bool(spec.get("numeric")),
            }
        )
    return items


def get_style(style_id: str) -> dict | None:
    style_id = (style_id or "").strip()
    if not style_id:
        return None
    if is_builtin(style_id):
        return _style_meta(style_id, _builtin_name(style_id), "builtin", True, None)
    try:
        row = db.query_one(
            "SELECT id, name, spec, source FROM citation_styles WHERE id = ?", (style_id,)
        )
    except Exception:  # noqa: BLE001
        return None
    if not row:
        return None
    return _style_meta(row["id"], row["name"], row["source"] or "manual", False, _safe_spec(row["spec"]))


def get_spec(style_id: str) -> dict | None:
    """自定义样式返回校验后的规格；内置/未知样式返回 None（调用方走原路径）。"""
    style = get_style(style_id)
    if not style or style["builtin"]:
        return None
    return style["spec"]


def _new_id() -> str:
    for _ in range(20):
        candidate = "custom-" + uuid.uuid4().hex[:8]
        if not db.query_one("SELECT id FROM citation_styles WHERE id = ?", (candidate,)):
            return candidate
    raise ValueError("生成自定义格式 id 失败，请重试")


def save_style(spec: dict, source: str = "manual", name: str | None = None) -> dict:
    """校验后写库；返回 {id,name,source,builtin:False,spec}。"""
    normalized = normalize_spec(spec)
    final_name = (name or "").strip() or normalized.get("name") or "自定义格式"
    normalized["name"] = str(final_name)[:MAX_LENGTHS["name"]]
    style_id = _new_id()
    stamp = db.now()
    db.execute(
        "INSERT INTO citation_styles(id, name, spec, source, created_at, updated_at)"
        " VALUES(?,?,?,?,?,?)",
        (style_id, normalized["name"], json.dumps(normalized, ensure_ascii=False), source or "manual", stamp, stamp),
    )
    return _style_meta(style_id, normalized["name"], source or "manual", False, normalized)


def delete_style(style_id: str) -> bool:
    """删除自定义样式；不存在返回 False（内置判断由路由做）。"""
    style_id = (style_id or "").strip()
    if not style_id or is_builtin(style_id):
        return False
    row = db.query_one("SELECT id FROM citation_styles WHERE id = ?", (style_id,))
    if not row:
        return False
    db.execute("DELETE FROM citation_styles WHERE id = ?", (style_id,))
    return True


def sample_paper() -> dict:
    """预览用样例：库里第一篇有标题的论文；没有就用内置假数据。"""
    try:
        row = db.query_one(
            "SELECT * FROM papers WHERE COALESCE(title, '') <> '' ORDER BY rowid LIMIT 1"
        )
    except Exception:  # noqa: BLE001
        row = None
    if not row:
        return dict(_SAMPLE_PAPER)
    paper = dict(row)
    paper.setdefault("url", "")
    return paper


# ---------------------------------------------------------------- AI 编译
def compile_style(description: str, name: str | None = None) -> dict:
    """把一句格式需求编译成规格并保存；返回保存后的样式 dict。"""
    text = (description or "").strip()
    if not text:
        raise ValueError("请先描述你想要的引文格式")
    if not llm.is_configured() and not llm.is_mock():
        raise llm.LLMError(llm.NOT_CONFIGURED_MESSAGE)

    data = llm.chat_json(
        _COMPILER_PROMPT,
        f"我的格式要求：{text}\n请输出引文格式规格 JSON。",
    )
    raw = data
    if isinstance(data, dict) and isinstance(data.get("spec"), dict):
        raw = data["spec"]
    spec = normalize_spec(raw if isinstance(raw, dict) else {})
    spec["bib_order"] = _ensure_volume_pages(spec["bib_order"])
    final_name = (name or "").strip() or str(spec.get("name") or "").strip() or "我的格式"
    return save_style(spec, "ai", final_name)


# ---------------------------------------------------------------- CSL 导入
def _local(tag: str) -> str:
    return str(tag).rsplit("}", 1)[-1] if "}" in str(tag) else str(tag)


def _find(root, name: str) -> list:
    return [element for element in root.iter() if _local(element.tag) == name]


def _csl_inline_wrapper(citation_nodes: list) -> str:
    """按 citation layout 的 prefix/suffix 推断正文包围符：[]→bracket，()→paren，都没有→none。"""
    markers: list[str] = []
    for node in citation_nodes:
        layouts = [element for element in node.iter() if _local(element.tag) == "layout"] or [node]
        for layout in layouts:
            markers.append(str(layout.get("prefix") or "") + str(layout.get("suffix") or ""))
            for element in layout.iter():
                if _local(element.tag) in {"text", "group", "names", "date", "choose"}:
                    marker = str(element.get("prefix") or "") + str(element.get("suffix") or "")
                    if marker:
                        markers.append(marker)
                        break
    joined = "".join(markers)
    if "[" in joined or "]" in joined:
        return "bracket"
    if "(" in joined or ")" in joined:
        return "paren"
    return "none"


def _parse_xml(raw: str):
    try:
        try:
            return ET.fromstring(raw)
        except ValueError as exc:
            if "encoding declaration" in str(exc):
                return ET.fromstring(raw.encode("utf-8"))
            raise
    except ET.ParseError as exc:
        raise ValueError(f"CSL 不是合法的 XML：{exc}") from exc


def import_csl(xml_text: str, name: str | None = None) -> dict:
    """把 .csl 映射成规格；尽量降级并返回 warnings，不因为高级写法整份拒绝。"""
    raw = (xml_text or "").strip()
    if not raw:
        raise ValueError("CSL 内容为空")
    root = _parse_xml(raw)
    if _local(root.tag) != "style":
        raise ValueError("CSL 根元素必须是 <style>")

    warnings: list[str] = []
    spec = normalize_spec({})
    spec["bib_order"] = []
    spec["italic"] = []

    # 数值型：citation layout 里出现 <text variable="citation-number">
    citation_nodes = _find(root, "citation")
    spec["numeric"] = any(
        _local(element.tag) == "text"
        and str(element.get("variable") or "").strip() == "citation-number"
        for node in citation_nodes
        for element in node.iter()
    )
    # 正文包围符：CSL 里常在 citation/layout 上写 prefix="[" suffix="]"
    spec["inline_wrapper"] = _csl_inline_wrapper(citation_nodes)

    # 作者规则：第一个带 et-al-use-first 的 <name>
    name_nodes = _find(root, "name")
    author_limit_found = None
    for node in name_nodes:
        if node.get("et-al-use-first") is not None:
            try:
                author_limit_found = int(str(node.get("et-al-use-first")).strip())
            except (TypeError, ValueError):
                warnings.append("CSL 的 et-al-use-first 不是整数，作者数量限制未采用")
            break
    if author_limit_found is not None:
        try:
            spec["author_limit"] = _as_limit(author_limit_found, "author_limit")
        except ValueError:
            warnings.append("CSL 的作者数量限制超出 0-99，已使用默认值")

    locale = str(root.get("default-locale") or "").strip().lower()
    if locale.startswith("zh"):
        spec["et_al"] = "等"
        spec["inline_et_al"] = "等"

    sort_orders = {str(node.get("name-as-sort-order") or "").strip().lower() for node in name_nodes}
    if sort_orders & {"all", "first"}:
        spec["name_format"] = "family-initials"
    else:
        spec["name_format"] = "initials-family"

    initialize_with = [str(node.get("initialize-with") or "") for node in name_nodes if node.get("initialize-with") is not None]
    if initialize_with:
        spec["initials_dots"] = any("." in value for value in initialize_with)

    # 参考文献：按 layout 里变量出现顺序生成 bib_order
    bibliography_nodes = _find(root, "bibliography")
    order: list[str] = []
    italic: list[str] = []
    title_quotes = False
    doi_style = spec["doi_style"]
    doi_seen = False
    url_seen = False
    unknown: set[str] = set()
    for section in bibliography_nodes:
        for element in section.iter():
            tag = _local(element.tag)
            variable = str(element.get("variable") or "").strip()
            if tag == "choose":
                if "不支持的条件样式（choose），已简化为按字段顺序拼接" not in warnings:
                    warnings.append("不支持的条件样式（choose），已简化为按字段顺序拼接")
                continue
            if tag not in {"names", "text", "date"} or not variable:
                continue
            if tag == "text" and variable == "citation-number":
                continue  # 编号由程序按出现顺序补 [N]，不用 CSL 的
            field = _CSL_VARIABLES.get(variable)
            if not field:
                unknown.add(variable)
                continue
            if field not in order:
                order.append(field)
            if field == "title" and tag == "text":
                marker = str(element.get("prefix") or "") + str(element.get("suffix") or "")
                if any(char in marker for char in _QUOTE_CHARS):
                    title_quotes = True
            if field == "venue" and element.get("font-style") == "italic" and "venue" not in italic:
                italic.append("venue")
            if field == "title" and element.get("font-style") == "italic" and "title" not in italic:
                italic.append("title")
            if field == "doi":
                doi_seen = True
                marker = (str(element.get("prefix") or "") + str(element.get("suffix") or "")).lower()
                if "doi.org" in marker:
                    doi_style = "url"
                else:
                    doi_style = "plain"
                    if "CSL 里的 DOI 没有链接前缀，按裸 DOI 渲染" not in warnings:
                        warnings.append("CSL 里的 DOI 没有链接前缀，按裸 DOI 渲染")
            if field == "url":
                url_seen = True

    for field in DEFAULT_SPEC["bib_order"]:
        if field not in order:
            order.append(field)
    spec["bib_order"] = _ensure_volume_pages(order)
    spec["italic"] = [field for field in italic if field in spec["bib_order"]]
    spec["title_quotes"] = title_quotes
    if doi_seen:
        spec["doi_style"] = doi_style
    spec["url_style"] = "on" if url_seen else "off"
    if unknown:
        shown = "、".join(sorted(unknown)[:8])
        warnings.append(f"以下字段暂不支持，已忽略：{shown}")

    if not bibliography_nodes:
        warnings.append("CSL 没有 bibliography 布局，参考文献字段顺序使用默认值")
    elif not any(_local(node.tag) == "layout" for node in bibliography_nodes[0].iter()):
        warnings.append("CSL 的 bibliography 没有 layout，参考文献字段顺序使用默认值")

    style_name = (name or "").strip()
    if not style_name:
        for info in _find(root, "info"):
            for child in info:
                if _local(child.tag) == "title" and (child.text or "").strip():
                    style_name = child.text.strip()
                    break
            if style_name:
                break
    spec["name"] = (style_name or "导入的格式")[:MAX_LENGTHS["name"]]
    return {"spec": normalize_spec(spec), "warnings": warnings}
