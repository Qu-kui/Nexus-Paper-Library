"""方法与实验参数库 API：批量提取、批次状态、列表查询与单篇详情。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db, llm
from ..services import methods
from ..services.methods import queue_manager

router = APIRouter(prefix="/api/methods", tags=["methods"])


class ExtractIn(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    only_new: bool = True


def _item(row) -> dict:
    """列表行 → 前端约定的条目结构；没有 profile 时给空值而不是 null。"""
    payload: dict = {}
    raw = row["data"] or ""
    try:
        parsed = json.loads(raw) if raw else {}
        if isinstance(parsed, dict):
            payload = parsed
    except Exception:  # noqa: BLE001 - 坏 JSON 不能让整个列表 500
        payload = {}
    return {
        "paper_id": row["paper_id"],
        "title": row["title"] or "",
        "year": row["year"] or "",
        "venue": row["venue"] or "",
        "status": row["status"] or "",
        "method_summary": row["method_summary"] or "",
        "experiment_type": row["experiment_type"] or "",
        "parameters": payload.get("parameters") or [],
        "materials": payload.get("materials") or [],
        "equipment": payload.get("equipment") or [],
        "conditions": payload.get("conditions") or [],
        "error": row["error"] or "",
        "updated_at": row["updated_at"] or "",
    }


@router.post("/extract")
def start_extract(body: ExtractIn) -> dict:
    if not llm.is_configured() and not llm.is_mock():
        raise HTTPException(400, "大模型未配置，请先到设置页填写 API")
    existing = [pid for pid in body.paper_ids if db.get_paper(pid)] if body.paper_ids else []
    if body.paper_ids and not existing:
        raise HTTPException(404, "选中的论文已不存在，请刷新列表")
    queued = queue_manager.start(existing if body.paper_ids else None, only_new=body.only_new)
    if queued == 0 and body.paper_ids:
        raise HTTPException(400, "这些论文都已经提取过了")
    return {"queued": queued, "status": queue_manager.status()}


@router.get("/status")
def extract_status() -> dict:
    return queue_manager.status()


@router.get("")
def list_methods(q: str = "", state: str = "all") -> dict:
    where: list[str] = []
    params: list = []
    if state == "done":
        where.append("m.status = 'done'")
    elif state == "pending":
        # 还没有 done 记录：包括完全没有 profile 行、排队中、失败
        where.append("COALESCE(m.status, '') <> 'done'")
    keyword = (q or "").strip().lower()
    if keyword:
        like = f"%{keyword}%"
        where.append(
            "(lower(p.title) LIKE ? OR lower(COALESCE(m.method_summary, '')) LIKE ?"
            " OR lower(COALESCE(m.experiment_type, '')) LIKE ? OR lower(COALESCE(m.data, '')) LIKE ?)"
        )
        params.extend([like, like, like, like])
    clause = (" WHERE " + " AND ".join(where)) if where else ""

    total_row = db.query_one(
        "SELECT COUNT(*) AS c FROM papers p"
        " LEFT JOIN method_profiles m ON m.paper_id = p.id" + clause,
        params,
    )
    total = int(total_row["c"]) if total_row else 0
    rows = db.query(
        "SELECT p.id AS paper_id, p.title, p.year, p.venue,"
        " m.status, m.method_summary, m.experiment_type, m.data, m.error, m.updated_at"
        " FROM papers p LEFT JOIN method_profiles m ON m.paper_id = p.id"
        + clause
        + " ORDER BY COALESCE(m.updated_at, p.updated_at) DESC LIMIT 200",
        params,
    )
    return {"items": [_item(row) for row in rows], "total": total}


@router.get("/paper/{paper_id}")
def paper_method(paper_id: str) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    row = db.query_one("SELECT * FROM method_profiles WHERE paper_id = ?", (paper_id,))
    return {"paper": paper, "profile": methods.profile_dict(row)}
