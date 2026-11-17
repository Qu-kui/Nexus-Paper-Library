"""谷歌学术邮件订阅 API。"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import config
from ..services import scholar

router = APIRouter(prefix="/api/scholar", tags=["scholar"])


class ScholarConfigIn(BaseModel):
    email: str = ""
    imap_host: str = ""
    password: str = ""
    folder: str = "INBOX"
    auto_sync: bool = True


@router.get("")
def scholar_state() -> dict:
    current = config.get_settings()
    items = scholar.list_items()
    last_sync = str(current.get("scholar_last_sync") or "")
    last_sync_display = ""
    if last_sync:
        try:
            moment = datetime.fromisoformat(last_sync)
            if moment.tzinfo is None:
                moment = moment.replace(tzinfo=timezone.utc)
            last_sync_display = moment.astimezone().strftime("%Y-%m-%d %H:%M")
        except ValueError:
            last_sync_display = last_sync[:16].replace("T", " ")
    return {
        "configured": scholar.configured(),
        "email": str(current.get("scholar_email") or ""),
        "imap_host": str(current.get("scholar_imap_host") or "imap.gmail.com"),
        "folder": str(current.get("scholar_folder") or "INBOX"),
        "auto_sync": bool(current.get("scholar_auto_sync", True)),
        "last_sync": last_sync,
        "last_sync_display": last_sync_display,
        "unread": scholar.unread_count(),
        "items": items,
    }


@router.put("/config")
def save_config(body: ScholarConfigIn) -> dict:
    patch = {
        "scholar_email": body.email.strip(),
        "scholar_imap_host": body.imap_host.strip() or "imap.gmail.com",
        "scholar_folder": body.folder.strip() or "INBOX",
        "scholar_auto_sync": bool(body.auto_sync),
    }
    if body.password:
        patch["scholar_password"] = body.password
    config.update_settings(patch)
    return scholar_state()


@router.post("/test")
def test_config() -> dict:
    try:
        return {"ok": True, **scholar.test_connection()}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"连接邮箱失败：{exc}") from exc


@router.post("/sync")
def sync_now() -> dict:
    try:
        result = scholar.sync()
        translated = scholar.translate_missing(20)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"同步失败：{exc}") from exc
    return {
        **result,
        "translated": translated,
        "unread": scholar.unread_count(),
        "items": scholar.list_items(),
    }


@router.get("/items")
def items(status: str = "") -> dict:
    return {"items": scholar.list_items(status)}


@router.post("/items/{item_id}/add")
def add_item(item_id: str) -> dict:
    try:
        return scholar.add_item(item_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"加入文献库失败：{exc}") from exc


@router.post("/items/{item_id}/ignore")
def ignore_item(item_id: str) -> dict:
    return scholar.ignore_item(item_id)
