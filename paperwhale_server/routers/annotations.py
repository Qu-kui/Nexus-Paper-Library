"""标注 API。"""
from __future__ import annotations

import json

from fastapi import APIRouter, HTTPException

from .. import db
from ..models import AnnotationIn, AnnotationPatch

router = APIRouter(prefix="/api", tags=["annotations"])


def _to_dict(row) -> dict:
    data = dict(row)
    try:
        data["rects"] = json.loads(data.get("rects") or "[]")
    except Exception:
        data["rects"] = []
    return data


@router.get("/papers/{paper_id}/annotations")
def list_annotations(paper_id: str) -> dict:
    rows = db.query(
        "SELECT * FROM annotations WHERE paper_id = ? ORDER BY page ASC, id ASC", (paper_id,)
    )
    return {"items": [_to_dict(r) for r in rows]}


@router.post("/papers/{paper_id}/annotations")
def create_annotation(paper_id: str, body: AnnotationIn) -> dict:
    if not db.get_paper(paper_id):
        raise HTTPException(404, "论文不存在")
    ann_id = db.execute(
        """INSERT INTO annotations(paper_id,page,color,rects,quote,note,kind,created_at)
           VALUES(?,?,?,?,?,?,?,?)""",
        (
            paper_id,
            body.page,
            body.color,
            json.dumps(body.rects, ensure_ascii=False),
            body.quote,
            body.note,
            body.kind,
            db.now(),
        ),
    )
    row = db.query_one("SELECT * FROM annotations WHERE id = ?", (ann_id,))
    return _to_dict(row) if row else {}


@router.delete("/annotations/{annotation_id}")
def delete_annotation(annotation_id: int) -> dict:
    db.execute("DELETE FROM annotations WHERE id = ?", (annotation_id,))
    return {"ok": True}


@router.patch("/annotations/{annotation_id}")
def update_annotation(annotation_id: int, body: AnnotationPatch) -> dict:
    row = db.query_one("SELECT * FROM annotations WHERE id = ?", (annotation_id,))
    if not row:
        raise HTTPException(404, "批注不存在")
    fields = {}
    if body.note is not None:
        fields["note"] = body.note.strip()[:2000]
    if body.color is not None:
        fields["color"] = body.color.strip()[:20]
    if body.kind is not None:
        fields["kind"] = body.kind.strip()[:20]
    if fields:
        sets = ", ".join(f"{key} = ?" for key in fields)
        db.execute(f"UPDATE annotations SET {sets} WHERE id = ?", (*fields.values(), annotation_id))
    updated = db.query_one("SELECT * FROM annotations WHERE id = ?", (annotation_id,))
    return _to_dict(updated) if updated else {}
