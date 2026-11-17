"""引文检索与格式化 API（浏览器/Word 插件共用）。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import db, llm
from ..services import citation, citation_style

router = APIRouter(prefix="/api/cite", tags=["cite"])


class BibIn(BaseModel):
    ids: list[str] = []
    style: str = "apa"


class SearchIn(BaseModel):
    q: str = ""
    limit: int = 20


class AiStyleIn(BaseModel):
    description: str = ""
    name: str | None = None


class ImportStyleIn(BaseModel):
    csl: str = ""
    name: str | None = None


class PreviewIn(BaseModel):
    spec: dict | None = None


@router.get("/styles")
def styles() -> dict:
    # 返回内置 + 自定义；id/name 保持原字段，builtin/numeric 是新增字段（旧客户端兼容）
    return {"items": citation_style.list_styles()}


@router.get("/search")
def search(q: str = "", limit: int = 20) -> dict:
    limit = max(1, min(50, limit))
    return {"items": citation.search(q, limit)}


@router.get("/paper/{paper_id}")
def paper_entry(paper_id: str, style: str = "apa") -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    paper["_index"] = 1
    return {
        "id": paper_id,
        "citation": citation.inline(paper, style),
        "reference": citation.reference(paper, style),
        "paper": paper,
    }


@router.post("/bibliography")
def bibliography(body: BibIn) -> dict:
    return {"items": citation.bibliography(body.ids, body.style)}


# ---------- Word 插件用的纯文本接口（VBA 解析 JSON 太麻烦） ---------- #


@router.post("/search.txt", response_class=PlainTextResponse)
def search_text(body: SearchIn) -> str:
    items = citation.search(body.q, max(1, min(20, body.limit)))
    lines = []
    for item in items:
        label = citation.search_label(item)
        lines.append(f"{item['id']}\t{label}")
    return "\n".join(lines)


@router.get("/paper/{paper_id}/citation.txt", response_class=PlainTextResponse)
def citation_text(paper_id: str, style: str = "apa") -> str:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    paper["_index"] = 1
    return citation.inline(paper, style)


@router.post("/bibliography.txt", response_class=PlainTextResponse)
def bibliography_text(body: BibIn) -> str:
    items = citation.bibliography(body.ids, body.style)
    lines = []
    for item in items:
        if body.style in {"ieee", "nature", "gbt7714"}:
            lines.append(f"[{item['index']}] {item['reference']}")
        else:
            lines.append(item["reference"])
    return "\n\n".join(lines)


#: 数值型引文格式：参考文献列表要带 [N] 前缀，与正文 [N] 对应
def _document_bibliography_text(entries: list[dict], style: str) -> str:
    if citation.is_numeric(style):
        return "\n\n".join(f"[{item['index']}] {item['reference']}" for item in entries)
    return "\n\n".join(item["reference"] for item in entries)


@router.post("/document.txt", response_class=PlainTextResponse)
def document_text(body: BibIn) -> str:
    """Word 插件按文档顺序取引文：每行 ``id\\t<index>\\t行内引文``，之后是参考文献块。

    ``index`` 是本次请求里按去重后实际返回顺序的 1 起编号（与 bibliography 的
    index 一致），VBA 直接用它算 ``[1]``/``[1-3]``/``[1,2]`` 折叠。
    去重保留首次出现顺序；查不到的 id 跳过且不占编号；编号只按实际返回的行算，
    所以 citation.bibliography 只传存在的 id，两边编号才能对齐。
    """
    ids = [str(paper_id).strip() for paper_id in body.ids if str(paper_id or "").strip()]
    if not ids:
        raise HTTPException(400, "没有可生成引文的文献")
    style = (body.style or "apa").strip().lower()
    if not citation_style.is_builtin(style) and citation_style.get_spec(style) is None:
        raise HTTPException(400, "不支持的引文格式")

    seen: set[str] = set()
    resolved: list[str] = []
    lines: list[str] = []
    for paper_id in ids:
        if paper_id in seen:
            continue
        seen.add(paper_id)
        paper = db.get_paper(paper_id)
        if not paper:
            continue
        resolved.append(paper_id)
        paper["_index"] = len(resolved)
        lines.append(f"{paper_id}\t{len(resolved)}\t{citation.inline(paper, style)}")

    entries = citation.bibliography(resolved, style)
    # 逐字节固定：引文行 + marker 行 + \n + 参考文献；无条目时 marker 后只剩换行
    return "\n".join(lines + ["--BIBLIOGRAPHY--"]) + "\n" + _document_bibliography_text(
        entries, style
    )


# ---------- 自定义 / 外部导入引文格式 ---------- #


def _style_public(style: dict) -> dict:
    return {
        "id": style["id"],
        "name": style["name"],
        "source": style["source"],
        "builtin": style["builtin"],
    }


def _preview(style_id: str, spec: dict | None = None) -> dict:
    """预览固定用一条样例论文；内置格式走老渲染，自定义格式走规格渲染。

    numeric 格式给参考文献预览加 ``[1] `` 前缀（只用于展示，不写进 spec）。
    """
    paper = citation_style.sample_paper()
    if spec is None:
        paper["_index"] = 1
        inline = citation.inline(paper, style_id)
        reference = citation.reference(paper, style_id)
        numeric = citation.is_numeric(style_id)
    else:
        inline = citation_style.render_inline(paper, spec, 1)
        reference = citation_style.render_reference(paper, spec)
        numeric = bool(spec.get("numeric"))
    if numeric and reference:
        reference = "[1] " + reference
    return {"inline": inline, "reference": reference}


@router.get("/styles.txt", response_class=PlainTextResponse)
def styles_text() -> str:
    """Word VBA 用：每行 ``id\\tname\\t<0|1>``，第三列 1 = 数值型（正文用 [n]）。

    内置在前、自定义在后；数值标记与 citation.is_numeric 同一个判定口径。
    """
    return "\n".join(
        f"{item['id']}\t{item['name']}\t{'1' if item.get('numeric') else '0'}"
        for item in citation_style.list_styles()
    )


@router.get("/styles/{style_id}")
def style_detail(style_id: str) -> dict:
    style = citation_style.get_style(style_id)
    if not style:
        raise HTTPException(404, "引文格式不存在")
    return {
        "style": _style_public(style),
        "spec": style["spec"],
        "preview": _preview(style_id, style["spec"]),
    }


@router.post("/styles/ai")
def style_ai(body: AiStyleIn) -> dict:
    try:
        style = citation_style.compile_style(body.description, body.name)
    except llm.LLMError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "style": _style_public(style),
        "spec": style["spec"],
        "preview": _preview(style["id"], style["spec"]),
    }


@router.post("/styles/import")
def style_import(body: ImportStyleIn) -> dict:
    try:
        result = citation_style.import_csl(body.csl, body.name)
        style = citation_style.save_style(result["spec"], "import", body.name)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "style": _style_public(style),
        "spec": style["spec"],
        "warnings": result["warnings"],
        "preview": _preview(style["id"], style["spec"]),
    }


@router.post("/styles/{style_id}/preview")
def style_preview(style_id: str, body: PreviewIn) -> dict:
    style = citation_style.get_style(style_id)
    if not style:
        raise HTTPException(404, "引文格式不存在")
    if body.spec is not None:
        try:
            spec = citation_style.normalize_spec(body.spec)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return _preview(style_id, spec)
    return _preview(style_id, style["spec"])


@router.delete("/styles/{style_id}")
def style_delete(style_id: str) -> dict:
    if citation_style.is_builtin(style_id):
        raise HTTPException(400, "内置引文格式不能删除")
    if not citation_style.delete_style(style_id):
        raise HTTPException(404, "引文格式不存在")
    return {"ok": True}
