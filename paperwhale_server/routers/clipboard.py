"""剪贴板监听 API：列候选、入库、忽略。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..services import clipboard_watch, metadata

router = APIRouter(prefix="/api/clipboard", tags=["clipboard"])


@router.get("/candidates")
def candidates() -> dict:
    return clipboard_watch.snapshot()


@router.post("/{candidate_id}/accept")
def accept(candidate_id: str) -> dict:
    try:
        return clipboard_watch.accept(candidate_id)
    except KeyError as exc:
        raise HTTPException(404, str(exc).strip("'\"")) from exc
    except metadata.MetadataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"入库失败：{exc}") from exc


@router.post("/{candidate_id}/dismiss")
def dismiss(candidate_id: str) -> dict:
    if not clipboard_watch.dismiss(candidate_id):
        raise HTTPException(404, "这条提示已经处理过了")
    return {"ok": True}
