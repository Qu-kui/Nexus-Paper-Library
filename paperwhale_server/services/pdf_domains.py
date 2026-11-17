"""Process-local, bounded coordination for actual PDF/landing HTTP requests.

429/503 cooldowns and start spacing are shared by host. Repeated 403s are scoped
to a digest of the effective credentials, so browser login can retry immediately.
No credentials or context digests are exposed in diagnostics.
"""
from __future__ import annotations

import hashlib
import hmac
import math
import secrets
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit


def retry_seconds(value: str, *, now: float | None = None, default: float = 1) -> float:
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            delay = parsedate_to_datetime(value).timestamp() - (time.time() if now is None else now)
        except (ValueError, TypeError, OverflowError):
            return default
    return max(0, delay) if math.isfinite(delay) else default


class DomainPaused(Exception):
    def __init__(self, code: str, seconds: float = 0):
        self.code = code
        self.seconds = max(0, seconds)
        super().__init__(code)


@dataclass
class _Host:
    touched: float
    next_start: float = 0
    limited_until: float = 0
    cooldown_code: str = 'rate_limited'
    spacing_boost: float = 0.0
    contexts: OrderedDict = field(default_factory=OrderedDict)


class RequestPolicy:
    def __init__(self, *, spacing=1.0, forbidden_limit=3, cooldown=60.0,
                 max_hosts=512, ttl=900.0, clock=time.monotonic,
                 wall_clock=time.time, sleep=time.sleep, adaptive_spacing=False):
        self.spacing = spacing
        self.forbidden_limit = forbidden_limit
        self.cooldown = cooldown
        self.max_hosts = max_hosts
        self.ttl = ttl
        self.clock = clock
        self.wall_clock = wall_clock
        self.sleep = sleep
        self.adaptive_spacing = adaptive_spacing
        self._hosts = OrderedDict()
        self._lock = threading.Lock()
        self._salt = secrets.token_bytes(32)

    def context(self, headers) -> bytes:
        # Salted hash prevents recovering even low-entropy cookie values from state.
        values = [(str(key).lower(), str(value)) for key, value in headers.items()
                  if str(key).lower() in {'cookie', 'authorization', 'proxy-authorization'}]
        return hmac.digest(self._salt, repr(sorted(values)).encode(), hashlib.sha256)

    def _state(self, url, now):
        host = (urlsplit(url).hostname or '').lower()
        for key, state in list(self._hosts.items()):
            if now - state.touched > self.ttl and state.limited_until <= now:
                del self._hosts[key]
        state = self._hosts.pop(host, None) or _Host(now)
        state.touched = now
        self._hosts[host] = state
        while len(self._hosts) > self.max_hosts:
            self._hosts.popitem(last=False)
        return state

    def acquire(self, url: str, context: bytes, deadline: float):
        while True:
            with self._lock:
                now = self.clock()
                if now >= deadline:
                    raise DomainPaused('deadline')
                state = self._state(url, now)
                _, paused_until = state.contexts.get(context, (0, 0))
                if paused_until > now:
                    raise DomainPaused('domain_paused', paused_until - now)
                if state.limited_until > now:
                    # Defer rather than tying up a worker for a server cooldown.
                    raise DomainPaused(state.cooldown_code, state.limited_until - now)
                wait = max(0, state.next_start - now)
                if now + wait >= deadline:
                    raise DomainPaused('deadline')
                if not wait:
                    state.next_start = now + self.spacing + (state.spacing_boost if self.adaptive_spacing else 0.0)
                    return now
            # Recheck state after waking; no lock is held during waits or HTTP IO.
            self.sleep(min(wait, 0.25))

    def observe(self, url: str, context: bytes, status: int, retry_after: str = '') -> float:
        with self._lock:
            now = self.clock()
            state = self._state(url, now)
            delay = 0
            if status == 429 or (status == 503 and retry_after):
                delay = retry_seconds(retry_after, now=self.wall_clock(), default=1 if status == 429 else 0.5)
                if now + delay >= state.limited_until:
                    state.limited_until = now + delay
                    state.cooldown_code = 'rate_limited' if status == 429 else 'server_error'
            count, _ = state.contexts.pop(context, (0, 0))
            if status == 403:
                count += 1
                state.contexts[context] = (count, now + self.cooldown if count >= self.forbidden_limit else 0)
            elif status < 400:
                state.contexts[context] = (0, 0)
            else:
                # Only consecutive forbidden responses trip this circuit.
                state.contexts[context] = (0, 0)
            while len(state.contexts) > 16:
                state.contexts.popitem(last=False)
            # 同一站被限流/拒绝后自动降速：退避加倍（封顶 8 秒），成功后再慢慢回调。
            if self.adaptive_spacing:
                if status in (429, 403) or (status == 503 and retry_after):
                    state.spacing_boost = min(8.0, state.spacing_boost * 2 + 2.0)
                elif status < 400:
                    state.spacing_boost = max(0.0, state.spacing_boost * 0.5 - 0.25)
            return delay


default_policy = RequestPolicy(adaptive_spacing=True)
