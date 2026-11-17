"""阅读器对话分支：从任意一轮分出一条新线索，不丢原对话。"""
from fastapi import APIRouter, HTTPException

from fastapi import Query

from ..models import (BranchCreateIn, BranchDeleteIn, ReadingBranchIn,
                      ReadingBranchDeleteIn, ReadingSessionIn)
from ..services import conversations

router = APIRouter(prefix='/api', tags=['ai'])


def _fail(exc: conversations.ConversationError) -> None:
    raise HTTPException(exc.status, str(exc)) from exc


@router.get('/ai/branches/{paper_id}')
def list_branches(paper_id: str) -> dict:
    try:
        return {'items': conversations.branches(paper_id)}
    except conversations.ConversationError as exc:
        _fail(exc)


@router.post('/ai/branches')
def create_branch(body: BranchCreateIn) -> dict:
    try:
        conversation = conversations.create_branch(
            body.paper_id, body.from_branch, body.upto_index
        )
    except conversations.ConversationError as exc:
        _fail(exc)
    branch = conversations.split_scope(conversation['scope'])[1]
    return {'branch': branch, 'conversation': conversation}


@router.delete('/ai/branches')
def delete_branch(body: BranchDeleteIn) -> dict:
    try:
        return conversations.delete_branch(body.paper_id, body.branch)
    except conversations.ConversationError as exc:
        _fail(exc)


@router.get('/ai/reading/branches')
def reading_branches(scope: str = Query(..., min_length=8, max_length=80)) -> dict:
    try:
        return {'items': conversations.branches_for_scope(conversations.split_scope(scope)[0])}
    except conversations.ConversationError as exc:
        _fail(exc)


@router.post('/ai/reading/branches')
def reading_create_branch(body: ReadingBranchIn) -> dict:
    try:
        conversation = conversations.create_branch_for_scope(
            conversations.split_scope(body.scope)[0], body.from_branch, body.upto_index
        )
    except conversations.ConversationError as exc:
        _fail(exc)
    return {'branch': conversations.split_scope(conversation['scope'])[1], 'conversation': conversation}


@router.delete('/ai/reading/branches')
def reading_delete_branch(body: ReadingBranchDeleteIn) -> dict:
    try:
        return conversations.delete_branch_for_scope(conversations.split_scope(body.scope)[0], body.branch)
    except conversations.ConversationError as exc:
        _fail(exc)


@router.delete('/ai/reading/session')
def reading_close_session(body: ReadingSessionIn) -> dict:
    try:
        return conversations.purge_scope(conversations.split_scope(body.scope)[0])
    except conversations.ConversationError as exc:
        _fail(exc)
