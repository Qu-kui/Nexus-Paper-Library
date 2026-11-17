"""内置科研 Skills API。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import skills

router = APIRouter(prefix="/api", tags=["skills"])


class SkillRunIn(BaseModel):
    paper_id: str | None = None
    paper_ids: list[str] = Field(default_factory=list, max_length=20)
    question: str = ""


@router.get("/skills")
def list_skills() -> dict:
    return {"items": skills.list_skills()}


@router.post("/skills/{skill_id}/run")
def run_skill(skill_id: str, body: SkillRunIn) -> dict:
    try:
        return skills.run_skill(skill_id, body.paper_id, body.question, body.paper_ids)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        from .. import llm

        if isinstance(exc, llm.LLMError):
            raise HTTPException(400, str(exc)) from exc
        raise HTTPException(500, f"技能运行失败：{exc}") from exc
