"""资讯流 API：官方媒体缓存、手动刷新、自定义方向。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..services import newsfeed

router = APIRouter(prefix='/api/newsfeed', tags=['newsfeed'])


class TopicPayload(BaseModel):
    label: str = Field(min_length=1, max_length=40)


@router.get('')
def snapshot() -> dict:
    return newsfeed.public_snapshot()


@router.post('/refresh')
def refresh() -> dict:
    accepted = newsfeed.start_refresh()
    return {'ok': True, 'accepted': accepted, 'refreshing': True}


@router.post('/topics')
def add_topic(payload: TopicPayload) -> dict:
    try:
        topic = newsfeed.start_topic(payload.label)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        'ok': True,
        'topic': {
            'id': topic['id'],
            'label': topic['label'],
            'status': topic['status'],
            'builtin': bool(topic.get('builtin')),
            'restored': bool(topic.get('restored')),
        },
    }


@router.post('/topics/{topic_id}/refresh')
def refresh_topic(topic_id: str) -> dict:
    try:
        accepted = newsfeed.start_topic_refresh(topic_id)
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return {'ok': True, 'accepted': accepted}


@router.delete('/topics/{topic_id}')
def delete_topic(topic_id: str) -> dict:
    if not newsfeed.remove_topic(topic_id):
        raise HTTPException(404, '方向不存在')
    return {'ok': True}
