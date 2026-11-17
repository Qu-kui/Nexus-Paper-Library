"""参考文献外扩 API：拉种子论文的参考文献 / 被引列表，勾选后入库。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..models import ReferenceExpandIn, ReferenceImportIn
from ..services import metadata, reference_expand

router = APIRouter(prefix="/api/references", tags=["references"])


@router.post("/expand")
def expand(body: ReferenceExpandIn) -> dict:
    try:
        return reference_expand.expand(body.paper_id, body.direction, body.limit)
    except LookupError as exc:
        raise HTTPException(404, str(exc)) from exc
    except metadata.MetadataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc


@router.post("/import")
def import_references(body: ReferenceImportIn) -> dict:
    if not body.items:
        raise HTTPException(400, "没有选中任何文献")
    return reference_expand.import_items(body.items, fetch_pdf=body.fetch_pdf)
