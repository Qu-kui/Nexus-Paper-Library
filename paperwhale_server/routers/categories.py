"""两级别分类 API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from .. import db
from ..models import CategoryApplyIn, CategoryChatIn
from ..services import categories, category_assistant

router = APIRouter(prefix="/api", tags=["categories"])


class AssignIn(BaseModel):
    paper_id: str
    major: str = ""
    minor: str = ""
    path: list[str] = []


class PaperIn(BaseModel):
    paper_id: str


@router.get("/categories/tree")
def category_tree() -> dict:
    return {
        "items": categories.taxonomy(),
        "paths": categories.taxonomy_paths(),
        "pending": _pending_count(),
    }


def _pending_count() -> int:
    row = db.query_one("SELECT COUNT(*) AS c FROM papers WHERE category_status = 'pending'")
    return int(row["c"]) if row else 0


@router.post("/categories/assign")
def assign_category(body: AssignIn) -> dict:
    try:
        if body.path:
            paper = categories.assign_path(body.paper_id, body.path)
        else:
            paper = categories.assign(body.paper_id, body.major, body.minor)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"paper": paper}


@router.post("/categories/approve")
def approve_category(body: PaperIn) -> dict:
    try:
        paper = categories.approve_suggestion(body.paper_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"paper": paper}


@router.post("/categories/dismiss")
def dismiss_category(body: PaperIn) -> dict:
    return {"paper": categories.dismiss_suggestion(body.paper_id)}


# ---------- 分类助手 ---------- #


@router.post("/categories/assistant/chat")
def assistant_chat(body: CategoryChatIn) -> dict:
    from ..services import ai_context, conversations

    try:
        question = next((str(item.get('content') or '').strip()[:16000] for item in reversed(body.messages)
                         if item.get('role') == 'user' and str(item.get('content') or '').strip()), '')
        return conversations.turn('categories', question or '帮我完善分类方案',
            lambda history, materials: category_assistant.chat([*history, {'role':'user', 'content':question or '帮我完善分类方案'}]),
            conversation_id=body.conversation_id, revision=body.revision)
    except conversations.ConversationError:
        raise
    except Exception as exc:  # noqa: BLE001
        from .. import llm

        if isinstance(exc, llm.LLMError):
            raise HTTPException(400, str(exc)) from exc
        raise HTTPException(500, f"分类助手调用失败：{exc}") from exc


@router.post("/categories/assistant/apply")
def assistant_apply(body: CategoryApplyIn) -> dict:
    return category_assistant.apply(body.guide, scope=body.scope, move_files=body.move_files)


@router.get("/categories/assistant/preview")
def assistant_preview(scope: str = "unclassified") -> dict:
    return category_assistant.preview(scope)


@router.get("/categories/assistant/status")
def assistant_status() -> dict:
    return category_assistant.queue_manager.status()
