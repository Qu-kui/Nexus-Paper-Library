"""Thread-safe, request-owned AI progress; only model output is accumulated."""
from __future__ import annotations
from contextvars import ContextVar
from threading import Lock
from time import time

_current = ContextVar('ai_progress', default=None)
_records: dict[tuple, dict] = {}
_lock = Lock()
MAX_TEXT = 64000


def begin(scope: str, conversation_id: str, revision: int):
    key = (scope, conversation_id, revision)
    with _lock:
        cutoff = time()-1800
        for old in list(_records):
            if _records[old]['updated_at'] < cutoff:
                del _records[old]
        now = time()
        _records[key] = {'stage':'准备本轮材料', 'reasoning':'', 'answer':'', 'state':'running',
                         'updated_at':now, 'started_at':now, 'first_answer_at':0.0,
                         'web_trace':[]}
    return _current.set(key)


def active() -> bool:
    return _current.get() is not None


def emit(*, stage: str | None = None, reasoning_delta: str = '', answer_delta: str = '',
         state: str | None = None, web_trace: list | None = None):
    key = _current.get()
    if key is None:
        return
    with _lock:
        row = _records.get(key)
        if not row or row['state'] == 'stopped':
            return
        if stage is not None:
            row['stage'] = stage
        if reasoning_delta:
            row['reasoning'] = (row['reasoning']+reasoning_delta)[-MAX_TEXT:]
        if answer_delta:
            if not row.get('first_answer_at'):
                row['first_answer_at'] = time()
            row['answer'] = (row['answer']+answer_delta)[-MAX_TEXT:]
        if state:
            row['state'] = state
        if web_trace is not None:
            row['web_trace'] = web_trace
        row['updated_at'] = time()


def finish(token):
    key = _current.get()
    with _lock:
        row = _records.get(key)
        if row and row['state'] == 'running':
            row.update(state='completed', updated_at=time())
    _current.reset(token)


def cancel(scope: str):
    with _lock:
        for key, row in _records.items():
            if key[0] == scope and row['state'] == 'running':
                row.update(state='stopped', stage='已停止', updated_at=time())


def cancelled() -> bool:
    key = _current.get()
    with _lock:
        return bool(key and _records.get(key, {}).get('state') == 'stopped')


def snapshot(scope: str, conversation_id: str, revision: int) -> dict:
    with _lock:
        row = dict(_records.get((scope, conversation_id, revision),
                    {'stage':'等待服务响应', 'reasoning':'', 'answer':'', 'state':'idle', 'updated_at':0,
                     'web_trace':[]}))
    started = float(row.get('started_at') or 0)
    first = float(row.get('first_answer_at') or 0)
    updated = float(row.get('updated_at') or 0)
    if started:
        # 「已思考（用时 N 秒）」= 从开始到第一段回答出现；流式过程中先按已过时间显示
        row['thinking_seconds'] = round(max(0.0, (first or updated) - started), 1)
        row['elapsed_seconds'] = round(max(0.0, updated - started), 1)
    else:
        row['thinking_seconds'] = 0.0
        row['elapsed_seconds'] = 0.0
    return row


def has_active() -> bool:
    with _lock:
        return any(row['state'] == 'running' for row in _records.values())
