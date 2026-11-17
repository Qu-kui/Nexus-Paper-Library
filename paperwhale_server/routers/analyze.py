"""一键精读（批量分析）API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db, llm
from ..services.analysis_queue import queue_manager

router = APIRouter(prefix="/api", tags=["analyze"])


class AnalyzeStartIn(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    only_new: bool = True


@router.post("/ai/analyze-batch")
def start_analyze(body: AnalyzeStartIn) -> dict:
    if not llm.is_configured() and not llm.is_mock():
        raise HTTPException(400, "大模型未配置，请先到设置页填写 API")
    existing = [pid for pid in body.paper_ids if db.get_paper(pid)] if body.paper_ids else []
    if body.paper_ids and not existing:
        raise HTTPException(404, '选中的论文已不存在，请刷新列表')
    queued = queue_manager.start(existing if body.paper_ids else None, only_new=body.only_new)
    total_row = db.query_one('SELECT COUNT(*) AS c FROM papers WHERE pdf_available(file_path)=1')
    total_with_pdf = int(total_row["c"]) if total_row else 0
    if queued == 0 and body.paper_ids:
        raise HTTPException(400, "这些论文都已经精读过了")
    return {
        "queued": queued,
        "status": queue_manager.status(),
        "total_with_pdf": total_with_pdf,
    }


@router.get("/ai/analyze-status")
def analyze_status() -> dict:
    return queue_manager.status()
