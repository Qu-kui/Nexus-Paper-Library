"""Read, clear, and manage material within a single AI conversation."""
from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..services import conversations

router = APIRouter(prefix='/api/ai/conversations', tags=['ai'])


class MaterialsIn(BaseModel):
    attachments: list[dict[str, str]] = Field(default_factory=list, max_length=8)
    revision: int | None = None


class MigrationIn(BaseModel):
    messages: list[dict] = Field(default_factory=list, max_length=40)


class ApplyIn(BaseModel):
    revision: int


@router.get('/{scope}')
def read(scope: str):
    return conversations.snapshot(scope)


@router.delete('/{scope}')
def clear(scope: str):
    return conversations.clear(scope)


@router.post('/{scope}/undo')
def undo(scope: str):
    return conversations.undo(scope)


@router.post('/{scope}/cancel')
def cancel(scope: str, body: ApplyIn):
    return conversations.cancel(scope, body.revision)


@router.put('/{scope}/materials')
def materials(scope: str, body: MaterialsIn):
    return conversations.set_materials(scope, body.attachments, body.revision)


@router.post('/profile/migrate')
def migrate(body: MigrationIn):
    return conversations.migrate_profile(body.messages)


@router.post('/{scope}/apply')
def apply(scope: str, body: ApplyIn):
    return conversations.apply_draft(scope, body.revision)
