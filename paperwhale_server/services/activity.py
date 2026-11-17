"""Coordinate metadata restoration with background task admission."""
from functools import wraps
from threading import RLock

_gate = RLock()
_active = 0


def tracked(function):
    @wraps(function)
    def run(*args, **kwargs):
        global _active
        with _gate:
            _active += 1
        try:
            return function(*args, **kwargs)
        finally:
            with _gate:
                _active -= 1
    return run


def serialized(function):
    @wraps(function)
    def run(*args, **kwargs):
        with _gate:
            return function(*args, **kwargs)
    return run


def exclusive_restore(function):
    @wraps(function)
    def run(*args, **kwargs):
        with _gate:
            if _active:
                from .conversations import ConversationError
                raise ConversationError('请先等待当前任务结束，再恢复备份')
            return function(*args, **kwargs)
    return run
