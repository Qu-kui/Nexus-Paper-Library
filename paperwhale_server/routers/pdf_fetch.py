"""自动补齐 PDF 的后台任务 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import db
from ..services.pdf_fetch import manager

router = APIRouter(prefix="/api", tags=["pdf-fetch"])


class FetchStartIn(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    all_missing: bool = False


@router.post("/pdf-fetch/start")
def start_fetch(body: FetchStartIn) -> dict:
    if not body.paper_ids and not body.all_missing:
        raise HTTPException(400, "需要提供 paper_ids 或 all_missing=true")
    queued = manager.start(body.paper_ids, all_missing=body.all_missing)
    return {"queued": queued, "status": manager.status()}


@router.get("/pdf-fetch/status")
def fetch_status() -> dict:
    return manager.status()


@router.get('/pdf-fetch/report/{paper_id}')
def fetch_report(paper_id: str):
    if not db.get_paper(paper_id):
        raise HTTPException(404,'论文不存在')
    return manager.report(paper_id)


@router.post("/pdf-fetch/one/{paper_id}")
def fetch_one(paper_id: str) -> dict:
    if not db.get_paper(paper_id):
        raise HTTPException(404, "论文不存在")
    return manager.fetch_one(paper_id)
