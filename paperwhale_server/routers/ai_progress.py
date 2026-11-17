"""Read-only progress for a specific conversation generation."""
import asyncio
import json

from fastapi import APIRouter, Query, Request
from fastapi.responses import StreamingResponse

from ..services import ai_progress, conversations

router = APIRouter(prefix='/api', tags=['ai'])

#: 前端只需要的字段，避免把服务端内部时间戳也发出去
PROGRESS_FIELDS = ('stage', 'reasoning', 'answer', 'state', 'thinking_seconds', 'elapsed_seconds', 'web_trace')
#: 生成线程还没注册进度时的最长等待（秒）
WAIT_FOR_START_SECONDS = 20
#: 单次流的最长寿命（秒），防止断线后服务端一直挂着
MAX_STREAM_SECONDS = 900


@router.get('/ai/progress/{scope}')
def progress(scope: str, conversation_id: str = Query(...), revision: int = Query(..., ge=0)):
    conversations.validate_scope(scope)
    return ai_progress.snapshot(scope, conversation_id, revision)


@router.get('/ai/progress/{scope}/stream')
async def progress_stream(scope: str, request: Request,
                          conversation_id: str = Query(...), revision: int = Query(..., ge=0)):
    """SSE 版进度：前端不用每 650ms 拉一次，文字可以按 25fps 冒出来。"""
    conversations.validate_scope(scope)

    async def events():
        loop = asyncio.get_event_loop()
        started = loop.time()
        last = None
        waiting = 0
        while True:
            if await request.is_disconnected():
                return
            row = ai_progress.snapshot(scope, conversation_id, revision)
            payload = {key: row.get(key) for key in PROGRESS_FIELDS}
            if payload != last:
                yield 'data: ' + json.dumps(payload, ensure_ascii=False) + '\n\n'
                last = payload
            if payload['state'] != 'running':
                if payload['state'] == 'idle' and waiting < WAIT_FOR_START_SECONDS:
                    waiting += 0.04
                else:
                    yield 'event: done\ndata: {}\n\n'
                    return
            if loop.time() - started > MAX_STREAM_SECONDS:
                yield 'event: done\ndata: {}\n\n'
                return
            await asyncio.sleep(0.04)

    return StreamingResponse(events(), media_type='text/event-stream', headers={
        'Cache-Control': 'no-cache',
        'X-Accel-Buffering': 'no',
        'Connection': 'keep-alive',
    })
