"""Local conversation storage. No network call holds a SQLite transaction open."""
from __future__ import annotations

import json
import re
import threading
import time
import weakref
from contextlib import closing
from typing import Callable

from .. import config, db
from .activity import tracked


_BRANCH_RE = re.compile(r'^[A-Za-z0-9_-]{1,32}$')


_READING_RE = re.compile(r'^reading:[A-Za-z0-9_-]{4,40}$')


def split_scope(scope: str) -> tuple[str, str]:
    """paper:<id>~<branch> / reading:<sid>~<branch> → (base, branch)；其它范围第二个值为空串。"""
    scope = str(scope or '')
    if '~' in scope and (scope.startswith('paper:') or scope.startswith('reading:')):
        base, _, branch = scope.partition('~')
        return base, branch
    return scope, ''


def reading_scope(session_id: str, branch: str = '') -> str:
    """本次阅读会话的临时对话范围（退出阅读器即删除）。"""
    session_id = str(session_id or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9_-]{4,40}', session_id):
        raise ConversationError('阅读会话标识不合法', 400)
    base = f'reading:{session_id}'
    branch = str(branch or '').strip()
    if not branch:
        return base
    if not _BRANCH_RE.fullmatch(branch):
        raise ConversationError('分支标识不合法', 400)
    return f'{base}~{branch}'


def branch_scope(paper_id: str, branch: str) -> str:
    """校验并拼出分支 scope；branch 为空表示主对话。"""
    branch = str(branch or '').strip()
    if not branch:
        return f'paper:{paper_id}'
    if not _BRANCH_RE.fullmatch(branch):
        raise ConversationError('分支标识不合法', 400)
    return f'paper:{paper_id}~{branch}'


def new_branch_id() -> str:
    """短、可读、只含 [A-Za-z0-9_-] 的分支标识。"""
    return 'b' + format(int(time.time()), 'x') + format(int((time.time() % 1) * 1296), 'x')


class ConversationError(Exception):
    def __init__(self, message: str, status: int = 409):
        super().__init__(message)
        self.status = status


# Weak references let deleted/idle paper locks be reclaimed without collisions between scopes.
_locks = weakref.WeakValueDictionary()
_locks_guard = threading.Lock()


def _lock_key(scope: str) -> str:
    """阅读会话的主对话和分支共用一把锁，存档/清理才不会被进行中的回答绕过。"""
    text = str(scope or '')
    base, _branch = split_scope(text)
    return base if text.startswith('reading:') else text


def _scope_lock(scope):
    key = _lock_key(scope)
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _locks[key] = lock
        return lock


def _acquire_locks(keys: list[str]) -> list:
    """按固定顺序一次性拿多把锁；拿不到就全部释放并报忙。"""
    locks = []
    for key in sorted({_lock_key(k) for k in keys if k}):
        lock = _scope_lock(key)
        if not lock.acquire(blocking=False):
            for held in locks:
                held.release()
            raise ConversationError('这个会话正在回答，请稍后再试')
        locks.append(lock)
    return locks


def _escape_like(value: str) -> str:
    return value.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')


def has_inflight() -> bool:
    with _locks_guard:
        return any(lock.locked() for lock in list(_locks.values()))


def validate_scope(scope: str) -> None:
    if scope in {'library', 'profile', 'categories', 'career'}:
        return
    base, branch = split_scope(scope)
    if base.startswith('reading:'):
        if not _READING_RE.fullmatch(base):
            raise ConversationError('阅读会话标识不合法', 400)
        if branch and not _BRANCH_RE.fullmatch(branch):
            raise ConversationError('分支标识不合法', 400)
        return
    if scope.startswith('discovery:') and scope[10:]:
        if not db.query_one('SELECT id FROM discovery_sessions WHERE id=?',(scope[10:],)):
            raise ConversationError('找论文话题不存在',404)
        return
    if scope.startswith('paper:') and scope[6:]:
        base, branch = split_scope(scope)
        if branch and not _BRANCH_RE.fullmatch(branch):
            raise ConversationError('分支标识不合法', 400)
        if not db.query_one('SELECT id FROM papers WHERE id=?', (base[6:],)):
            raise ConversationError('论文不存在', 404)
        return
    raise ConversationError('无效的对话范围', 400)


def snapshot(scope: str) -> dict:
    validate_scope(scope)
    # Metadata and messages must describe the same committed revision.
    with closing(db.connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        # Recheck the parent under the write lock: deletion can follow validation.
        if scope.startswith('discovery:') and not conn.execute('SELECT id FROM discovery_sessions WHERE id=?',(scope[10:],)).fetchone():
            raise ConversationError('找论文话题不存在',404)
        paper_base = split_scope(scope)[0]
        if paper_base.startswith('paper:') and not conn.execute('SELECT id FROM papers WHERE id=?',(paper_base[6:],)).fetchone():
            raise ConversationError('论文不存在',404)
        conn.execute('INSERT OR IGNORE INTO ai_conversations(scope,id) VALUES (?,?)', (scope, db.new_id()))
        row = conn.execute('SELECT * FROM ai_conversations WHERE scope=?', (scope,)).fetchone()
        rows = conn.execute('SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id', (row['id'],)).fetchall()
    messages = []
    for msg in rows:
        messages.append({**json.loads(msg['metadata']), 'role': msg['role'], 'content': msg['content'],
                         'created_at': msg['created_at'], 'id': msg['id']})
    undo_row=db.query_one("SELECT payload FROM recovery_items WHERE kind='conversation' AND restored_at='' AND json_extract(payload,'$.scope')=? ORDER BY created_at DESC,rowid DESC LIMIT 1",(scope,))
    undo_available=bool(undo_row and json.loads(undo_row['payload']).get('after_revision')==row['revision'])
    return {'id': row['id'], 'scope': scope, 'revision': row['revision'], 'messages': messages,
            'attachments': json.loads(row['attachments']), 'draft': json.loads(row['draft']),
            'migrated': bool(row['migrated']), 'history_limit': 24,'undo_available':undo_available}


def check(current: dict, conversation_id: str | None = None, revision: int | None = None) -> None:
    if conversation_id is not None and conversation_id != current['id']:
        raise ConversationError('会话与当前入口不匹配，请重新打开对话')
    if revision is not None and revision != current['revision']:
        raise ConversationError('对话已更新或清空，请刷新后重试')


def clean_materials(items: list[dict]) -> list[dict]:
    if len(items) > 8:
        raise ConversationError('每个会话最多保留 8 个附件', 400)
    result = []
    for item in items:
        text = str(item.get('text') or '').strip()[:30000]
        if text:
            result.append({'name': str(item.get('name') or '附件')[:120], 'text': text})
    return result


def set_materials(scope: str, attachments: list[dict], revision: int | None = None) -> dict:
    current = snapshot(scope)
    check(current, revision=revision)
    with closing(db.connect()) as conn, conn:
        changed = conn.execute('UPDATE ai_conversations SET attachments=?, revision=revision+1 WHERE scope=? AND revision=?',
                               (json.dumps(clean_materials(attachments), ensure_ascii=False), scope, current['revision']))
        if changed.rowcount != 1:
            raise ConversationError('会话已更新，请重试附件操作')
    return snapshot(scope)


def clear(scope: str) -> dict:
    from . import recovery,ai_progress

    current = snapshot(scope)
    with closing(db.connect()) as conn, conn:
        conn.execute('BEGIN IMMEDIATE')
        fresh=conn.execute('SELECT * FROM ai_conversations WHERE scope=?',(scope,)).fetchone()
        payload={'scope':scope,'conversation':dict(fresh),'messages':[dict(row) for row in conn.execute('SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id',(fresh['id'],))],
                 'after_revision':fresh['revision']+1}
        recovery.archive(conn,'conversation',scope,payload)
        conn.execute("UPDATE ai_conversations SET revision=revision+1, attachments='[]', draft='{}', migrated=1, summary='', summary_upto=0 WHERE scope=?", (scope,))
        conn.execute('DELETE FROM ai_messages WHERE conversation_id=?', (current['id'],))
    ai_progress.cancel(scope)
    return snapshot(scope)


def undo(scope: str, item_id: str | None = None) -> dict:
    current=snapshot(scope)
    with closing(db.connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        clause=' AND id=?' if item_id else ''
        params=(scope,item_id) if item_id else (scope,)
        item=conn.execute("SELECT * FROM recovery_items WHERE kind='conversation' AND restored_at='' AND json_extract(payload,'$.scope')=?"+clause+" ORDER BY created_at DESC,rowid DESC LIMIT 1",params).fetchone()
        if not item:
            raise ConversationError('没有可撤销的清空记录')
        payload=json.loads(item['payload'])
        changed=conn.execute('UPDATE ai_conversations SET attachments=?,draft=?,revision=revision+1 WHERE scope=? AND revision=?',
            (payload['conversation']['attachments'],payload['conversation']['draft'],scope,payload['after_revision']))
        if changed.rowcount!=1:
            raise ConversationError('清空后已有新消息或材料，不能覆盖当前会话')
        for msg in payload['messages']:
            conn.execute('INSERT INTO ai_messages(conversation_id,role,content,metadata,created_at) VALUES(?,?,?,?,?)',
                (current['id'],msg['role'],msg['content'],msg['metadata'],msg['created_at']))
        conn.execute('UPDATE recovery_items SET restored_at=? WHERE id=?',(db.now(),item['id']))
    return snapshot(scope)


def cancel(scope: str, revision: int | None = None) -> dict:
    from . import ai_progress

    current=snapshot(scope)
    # A stop can arrive before the generation request has entered the server.
    if revision is not None and revision not in (current['revision'],current['revision']+1):
        raise ConversationError('对话已更新，未停止其他轮次')
    with closing(db.connect()) as conn,conn:
        next_revision=max(current['revision'],revision if revision is not None else current['revision'])+1
        changed=conn.execute("UPDATE ai_conversations SET revision=?,draft='{}' WHERE scope=? AND revision=?",(next_revision,scope,current['revision']))
        if not changed.rowcount:
            raise ConversationError('对话已更新，未停止其他轮次')
    ai_progress.cancel(scope)
    return snapshot(scope)


def migrate_profile(messages: list[dict]) -> dict:
    current = snapshot('profile')
    with closing(db.connect()) as conn, conn:
        changed = conn.execute("UPDATE ai_conversations SET migrated=1, revision=revision+1 WHERE scope='profile' AND migrated=0 AND revision=?",
                               (current['revision'],))
        if changed.rowcount and not current['messages']:
            # Legacy localStorage is imported once, never used to replace backend history.
            for msg in messages[-40:]:
                if msg.get('role') in {'user', 'assistant'} and str(msg.get('content') or '').strip():
                    conn.execute('INSERT INTO ai_messages(conversation_id,role,content,created_at) VALUES(?,?,?,?)',
                                 (current['id'], msg['role'], str(msg['content'])[:20000], db.now()))
    return snapshot('profile')


@tracked
def _conversation_row(scope: str):
    return db.query_one('SELECT * FROM ai_conversations WHERE scope=?', (scope,))


def _message_count(conversation_id: str) -> int:
    row = db.query_one('SELECT COUNT(*) AS c FROM ai_messages WHERE conversation_id=?', (conversation_id,))
    return int(row['c']) if row else 0


def _first_question(conversation_id: str) -> str:
    row = db.query_one("SELECT content FROM ai_messages WHERE conversation_id=? AND role='user' ORDER BY id ASC LIMIT 1", (conversation_id,))
    return str((row['content'] if row else '') or '').strip()


def _last_answer(conversation_id: str) -> str:
    row = db.query_one("SELECT content FROM ai_messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1", (conversation_id,))
    return str((row['content'] if row else '') or '').strip()


def _last_question(conversation_id: str) -> str:
    row = db.query_one("SELECT content FROM ai_messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1", (conversation_id,))
    return str((row['content'] if row else '') or '').strip()


def _base_scope(scope: str) -> str:
    return split_scope(scope)[0]


def _require_base_scope(base_scope: str) -> None:
    if base_scope.startswith('paper:'):
        if not db.query_one('SELECT id FROM papers WHERE id=?', (base_scope[6:],)):
            raise ConversationError('论文不存在', 404)
        return
    validate_scope(base_scope)


def branches_for_scope(base_scope: str) -> list[dict]:
    """某个基础会话（论文或本次阅读会话）的全部分支列表。"""
    _require_base_scope(base_scope)
    prefix = base_scope + '~'
    rows = db.query('SELECT * FROM ai_conversations ORDER BY rowid ASC')
    rows = [row for row in rows if str(row['scope']) == base_scope or str(row['scope']).startswith(prefix)]
    items: list[dict] = []
    for row in rows:
        branch = split_scope(row['scope'])[1]
        first = _first_question(row['id'])
        last = _last_question(row['id'])
        answer = _last_answer(row['id'])
        meta = db.query_one('SELECT parent_scope, fork_index FROM ai_branch_meta WHERE scope=?', (row['scope'],))
        parent_scope = str((meta['parent_scope'] if meta else '') or '')
        items.append({
            'scope': row['scope'],
            'branch': branch,
            'parent_branch': split_scope(parent_scope)[1] if parent_scope else '',
            'fork_index': int((meta['fork_index'] if meta else -1) or -1),
            'title': (' '.join(first.split())[:40] or ('主对话' if not branch else '新分支（待提问）')),
            'last_question': ' '.join(last.split())[:80],
            'preview': ' '.join(answer.split())[:200],
            'messages': _message_count(row['id']),
        })
    return items


def branches(paper_id: str) -> list[dict]:
    return branches_for_scope(f'paper:{paper_id}')


def create_branch_for_scope(base_scope: str, from_branch: str = '', upto_index: int = -1) -> dict:
    """在任意基础会话下建一条空分支（不复制消息，只记录父对话与切点）。"""
    _require_base_scope(base_scope)
    from_branch = str(from_branch or '').strip()
    source_scope = f'{base_scope}~{from_branch}' if from_branch else base_scope
    source = _conversation_row(source_scope)
    upto_index = int(upto_index)
    if upto_index >= 0 and not source and from_branch:
        raise ConversationError('原对话不存在，无法分支', 404)
    branch = ''
    for _ in range(6):
        candidate = new_branch_id()
        if not _conversation_row(f'{base_scope}~{candidate}'):
            branch = candidate
            break
    if not branch:
        raise ConversationError('分支创建太频繁，请稍后再试', 429)
    scope = f'{base_scope}~{branch}'
    with closing(db.connect()) as conn, conn:
        conn.execute(
            'INSERT INTO ai_conversations(scope,id,revision,attachments,draft) VALUES (?,?,0,?,\'{}\')',
            (scope, db.new_id(), source['attachments'] if source else '[]'),
        )
        conn.execute(
            'INSERT OR REPLACE INTO ai_branch_meta(scope,parent_scope,fork_index,created_at) VALUES (?,?,?,?)',
            (scope, source_scope if source else '', upto_index, db.now()),
        )
    return snapshot(scope)


def create_branch(paper_id: str, from_branch: str = '', upto_index: int = -1,
                  title: str = '') -> dict:
    return create_branch_for_scope(f'paper:{paper_id}', from_branch, upto_index)


def delete_branch_for_scope(base_scope: str, branch: str) -> dict:
    branch = str(branch or '').strip()
    if not branch:
        raise ConversationError('不能删除主对话', 400)
    if not _BRANCH_RE.fullmatch(branch):
        raise ConversationError('分支标识不合法', 400)
    scope = f'{base_scope}~{branch}'
    row = _conversation_row(scope)
    if not row:
        raise ConversationError('分支不存在', 404)
    with closing(db.connect()) as conn, conn:
        conn.execute('DELETE FROM ai_messages WHERE conversation_id=?', (row['id'],))
        conn.execute('DELETE FROM ai_conversations WHERE id=?', (row['id'],))
        conn.execute('DELETE FROM ai_branch_meta WHERE scope=?', (scope,))
    return {'ok': True, 'branch': branch}


def delete_branch(paper_id: str, branch: str) -> dict:
    return delete_branch_for_scope(f'paper:{paper_id}', branch)


def purge_scope(base_scope: str) -> dict:
    """删除一个阅读会话及其所有分支（只允许 reading:，避免误删论文存档）。"""
    base = str(base_scope or '')
    if not base.startswith('reading:') or not _READING_RE.fullmatch(base):
        raise ConversationError('只能清理阅读会话', 400)
    lock = _scope_lock(base)
    if not lock.acquire(blocking=False):
        raise ConversationError('这个阅读会话正在回答，请稍后再试')
    try:
        rows = _session_rows(base)
        ids = [str(row['id']) for row in rows]
        scopes = [str(row['scope']) for row in rows]
        removed = 0
        with closing(db.connect()) as conn, conn:
            for conversation_id in ids:
                conn.execute('DELETE FROM ai_messages WHERE conversation_id=?', (conversation_id,))
                removed += conn.execute('DELETE FROM ai_conversations WHERE id=?', (conversation_id,)).rowcount
            for scope in scopes:
                conn.execute('DELETE FROM ai_branch_meta WHERE scope=?', (scope,))
        return {'ok': True, 'removed': int(removed or 0)}
    finally:
        lock.release()


def purge_stale_reading_sessions() -> int:
    """应用启动时清掉上次残留的阅读会话（阅读对话不跨会话保存）。"""
    rows = db.query("SELECT scope FROM ai_conversations WHERE scope LIKE 'reading:%'")
    removed = 0
    for row in rows:
        try:
            purge_scope(split_scope(row['scope'])[0])
            removed += 1
        except Exception:  # noqa: BLE001
            continue
    return removed


def branch_parent(scope: str) -> dict:
    """读取分支的父对话信息（没有则返回空）。"""
    base, branch = split_scope(scope)
    if not branch:
        return {}
    if not _conversation_row(scope):
        return {}
    meta = db.query_one('SELECT parent_scope, fork_index FROM ai_branch_meta WHERE scope=?', (scope,))
    if not meta:
        return {}
    parent_scope = str(meta['parent_scope'] or '')
    if not parent_scope:
        return {}
    return {'scope': parent_scope, 'fork_index': int(meta['fork_index'] or -1)}


def parent_digest(scope: str, max_messages: int = 8, max_chars: int = 6000) -> list[dict]:
    """父对话里能帮新分支理解指代的最近消息（截断，避免挤占上下文）。"""
    info = branch_parent(scope)
    if not info:
        return []
    parent = _conversation_row(info['scope'])
    if not parent:
        return []
    rows = db.query('SELECT role, content FROM ai_messages WHERE conversation_id=? ORDER BY id ASC', (parent['id'],))
    fork_index = info.get('fork_index', -1)
    if fork_index >= 0:
        rows = rows[:fork_index + 1]
    rows = [row for row in rows if str(row['role']) in {'user', 'assistant'} and str(row['content'] or '').strip()]
    rows = rows[-max_messages:]
    digest: list[dict] = []
    used = 0
    for row in reversed(rows):
        text = ' '.join(str(row['content']).split())[:800]
        if not text:
            continue
        if used + len(text) > max_chars:
            remain = max_chars - used
            if remain <= 80:
                break
            text = text[:remain]
        digest.append({'role': row['role'], 'content': text})
        used += len(text)
    return list(reversed(digest))


def _message_pairs(rows) -> list[tuple[dict, dict | None]]:
    """把消息整理成问答对，保留原始顺序；孤儿消息单独保留。"""
    pairs: list[tuple[dict, dict | None]] = []
    pending: dict | None = None
    for row in rows:
        item = dict(row)
        role = str(item.get('role') or '')
        if role == 'user':
            if pending is not None:
                pairs.append((pending, None))
            pending = item
        elif role == 'assistant' and pending is not None:
            pairs.append((pending, item))
            pending = None
        else:
            pairs.append((item, None))
    if pending is not None:
        pairs.append((pending, None))
    return pairs


def _copy_pairs(conn, source_id: str, target_id: str, branch: str = '', dedupe: bool = False) -> int:
    rows = conn.execute('SELECT role, content, metadata, created_at FROM ai_messages WHERE conversation_id=? ORDER BY id ASC',
                        (source_id,)).fetchall()
    pairs = _message_pairs(rows)
    existing: set[tuple[str, str]] = set()
    if dedupe:
        tail = conn.execute('SELECT role, content FROM ai_messages WHERE conversation_id=? ORDER BY id DESC LIMIT 400',
                            (target_id,)).fetchall()
        tail_rows = [dict(item) for item in reversed(tail)]
        for left, right in _message_pairs(tail_rows):
            existing.add((str(left.get('content') or ''), str((right or {}).get('content') or '')))
    copied = 0
    for left, right in pairs:
        key = (str(left.get('content') or ''), str((right or {}).get('content') or ''))
        if dedupe and key in existing:
            continue
        for item in (left, right):
            if item is None:
                continue
            metadata = item.get('metadata') or '{}'
            try:
                parsed = json.loads(metadata)
            except ValueError:
                parsed = {}
            if branch:
                parsed = {**parsed, 'archived_branch': branch}
            conn.execute('INSERT INTO ai_messages(conversation_id,role,content,metadata,created_at) VALUES (?,?,?,?,?)',
                         (target_id, item.get('role'), item.get('content'), json.dumps(parsed, ensure_ascii=False), db.now()))
            copied += 1
        existing.add(key)
    return copied


def _session_rows(base: str) -> list[dict]:
    prefix = base + '~'
    rows = db.query('SELECT * FROM ai_conversations ORDER BY rowid ASC')
    return [dict(row) for row in rows if str(row['scope']) == base or str(row['scope']).startswith(prefix)]


def archive_session(scope: str, paper_id: str) -> dict:
    """把一次阅读会话（含分支）存进这篇论文的长期对话，然后清掉临时会话。"""
    base, _branch = split_scope(scope)
    if not base.startswith('reading:') or not _READING_RE.fullmatch(base):
        raise ConversationError('只有阅读会话可以保存', 400)
    paper_scope = f'paper:{paper_id}'
    if not db.query_one('SELECT id FROM papers WHERE id=?', (paper_id,)):
        raise ConversationError('论文不存在', 404)
    locks = _acquire_locks([base, paper_scope])
    try:
        sources = _session_rows(base)
        if not sources:
            raise ConversationError('没有可保存的对话', 400)
        saved = 0
        with closing(db.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            conn.execute('INSERT OR IGNORE INTO ai_conversations(scope,id,revision) VALUES (?,?,0)',
                         (paper_scope, db.new_id()))
            target = conn.execute('SELECT * FROM ai_conversations WHERE scope=?', (paper_scope,)).fetchone()
            for conversation in sources:
                branch = split_scope(str(conversation['scope']))[1]
                saved += _copy_pairs(conn, conversation['id'], target['id'], branch=branch, dedupe=True)
            if saved:
                conn.execute('UPDATE ai_conversations SET revision=revision+1 WHERE id=?', (target['id'],))
            for conversation in sources:
                conn.execute('DELETE FROM ai_messages WHERE conversation_id=?', (conversation['id'],))
                conn.execute('DELETE FROM ai_conversations WHERE id=?', (conversation['id'],))
            conn.execute("DELETE FROM ai_branch_meta WHERE scope=? OR scope LIKE ? ESCAPE '\\'",
                         (base, _escape_like(base) + '~%'))
        return {'saved': saved, 'paper_scope': paper_scope}
    finally:
        for lock in reversed(locks):
            lock.release()


def restore_archive(scope: str, paper_id: str) -> dict:
    """把论文的长期对话导回当前阅读会话（会话为空时才导）。"""
    base, _branch = split_scope(scope)
    if not base.startswith('reading:') or not _READING_RE.fullmatch(base):
        raise ConversationError('只有阅读会话可以恢复', 400)
    paper_scope = f'paper:{paper_id}'
    locks = _acquire_locks([base, paper_scope])
    try:
        snapshot(base)  # 确保会话存在
        with closing(db.connect()) as conn, conn:
            conn.execute('BEGIN IMMEDIATE')
            current = conn.execute('SELECT * FROM ai_conversations WHERE scope=?', (base,)).fetchone()
            if not current:
                raise ConversationError('阅读会话不存在', 404)
            existing = conn.execute('SELECT COUNT(*) AS c FROM ai_messages WHERE conversation_id=?',
                                    (current['id'],)).fetchone()
            if existing and int(existing['c']) > 0:
                return snapshot(base)
            archive = conn.execute('SELECT * FROM ai_conversations WHERE scope=?', (paper_scope,)).fetchone()
            if not archive:
                return snapshot(base)
            copied = _copy_pairs(conn, archive['id'], current['id'])
            if copied:
                conn.execute('UPDATE ai_conversations SET revision=revision+1 WHERE id=?', (current['id'],))
        return snapshot(base)
    finally:
        for lock in reversed(locks):
            lock.release()


SUMMARY_TRIGGER_CHARS = 12000
SUMMARY_KEEP_CHARS = 6000
SUMMARY_KEEP_MESSAGES = 8
SUMMARY_MIN_NEW_CHARS = 1500


def _compact_history(scope: str, messages: list[dict]) -> list[dict]:
    """长对话滚动摘要：把较早的消息压成一段摘要，保留最近几轮原文。"""
    try:
        if not config.get_settings().get('auto_summary', True):
            return messages
        total = sum(len(str(item.get('content') or '')) for item in messages)
        if total <= SUMMARY_TRIGGER_CHARS:
            return messages
        row = db.query_one('SELECT summary, summary_upto FROM ai_conversations WHERE scope=?', (scope,))
        if not row:
            return messages
        previous = str(row['summary'] or '').strip()
        upto = int(row['summary_upto'] or 0)
        keep_chars = 0
        cutoff = len(messages)
        for index in range(len(messages) - 1, -1, -1):
            keep_chars += len(str(messages[index].get('content') or ''))
            cutoff = index
            if keep_chars >= SUMMARY_KEEP_CHARS and (len(messages) - index) >= SUMMARY_KEEP_MESSAGES:
                break
        older = messages[:cutoff]
        fresh = [item for item in older if int(item.get('id') or 0) > upto]
        fresh_chars = sum(len(str(item.get('content') or '')) for item in fresh)
        if fresh_chars < SUMMARY_MIN_NEW_CHARS:
            return messages
        transcript = '\n'.join(
            f"{'用户' if item.get('role') == 'user' else '助手'}：{str(item.get('content') or '').strip()[:3000]}"
            for item in fresh
        )
        from .. import llm
        summary = llm.chat_text(
            '你是对话压缩器。只做忠实压缩：保留用户目标、事实、数字、结论、未完成事项和约定；'
            '不添加原文没有的信息，不评价，不写客套话。用中文输出，不超过 800 字。',
            f'已有摘要：{previous or "（无）"}\n\n新增对话：\n{transcript}\n\n'
            '请把已有摘要和新增对话合并成一份更新后的摘要，只输出摘要正文。',
        ).strip()
        if not summary:
            return messages
        db.execute('UPDATE ai_conversations SET summary=?, summary_upto=? WHERE scope=?',
                   (summary[:4000], int(older[-1].get('id') or upto), scope))
        return [{'role': 'system', 'content': '[早前对话摘要]\n' + summary}] + messages[cutoff:]
    except Exception:  # noqa: BLE001 - 摘要失败不影响正常回答
        return messages


def turn(scope: str, question: str, generate: Callable, *, conversation_id: str | None = None,
         revision: int | None = None, attachments: list[dict] | None = None, user_metadata: dict | None = None) -> dict:
    question = question.strip()
    if not question:
        raise ConversationError('问题不能为空', 400)
    lock = _scope_lock(_lock_key(scope))
    if not lock.acquire(blocking=False):
        raise ConversationError('这个会话正在回答，请稍后再试')
    try:
        current = snapshot(scope)
        check(current, conversation_id, revision)
        # Starting a new request invalidates any old unsaved candidate, even if generation fails.
        with closing(db.connect()) as conn, conn:
            changed = conn.execute("UPDATE ai_conversations SET draft='{}', revision=revision+1 WHERE scope=? AND revision=?",
                                   (scope, current['revision']))
            if changed.rowcount != 1:
                raise ConversationError('对话已更新，请重试')
        expected = current['revision'] + 1
        materials = clean_materials(attachments) if attachments is not None else current['attachments']
        settings = config.get_settings()
        base = {key: settings.get(key) or '' for key in ('profile', 'category_guide')}
        from . import ai_progress

        progress_token=ai_progress.begin(scope,current['id'],expected)
        try:
            result = generate(_compact_history(scope, current['messages']), materials)
        except Exception:
            ai_progress.emit(state='failed',stage='本次生成未完成，可重试')
            raise
        finally:
            ai_progress.finish(progress_token)
        try:
            timing = ai_progress.snapshot(scope, current['id'], expected)
            if isinstance(result, dict):
                result = {**result, 'thinking_seconds': timing.get('thinking_seconds') or 0}
        except Exception:  # noqa: BLE001 - 计时失败不能影响回答
            pass
        answer = str(result.get('answer') or result.get('reply') or result.get('text') or '').strip()
        if not answer:
            raise ConversationError('没有收到回答，请重试', 400)
        draft = {key: result[key] for key in ('profile', 'guide') if result.get(key)}
        if draft:
            draft['_base'] = base
        metadata = {key: result[key] for key in ('sources', 'retrieval_candidates','web_sources', 'web_images', 'model', 'reasoning', 'action',
                          'thinking_seconds', 'web_trace') if key in result}
        with closing(db.connect()) as conn, conn:
            changed = conn.execute('UPDATE ai_conversations SET revision=revision+1, attachments=?, draft=? WHERE scope=? AND id=? AND revision=?',
                                   (json.dumps(materials, ensure_ascii=False), json.dumps(draft, ensure_ascii=False),
                                    scope, current['id'], expected))
            if changed.rowcount != 1:
                raise ConversationError('对话已清空或材料已更新，旧回答未保存，请重试')
            for role, content, meta in [('user', question, user_metadata or {}), ('assistant', answer, metadata)]:
                conn.execute('INSERT INTO ai_messages(conversation_id,role,content,metadata,created_at) VALUES(?,?,?,?,?)',
                             (current['id'], role, content, json.dumps(meta, ensure_ascii=False), db.now()))
        return {**result, 'conversation': snapshot(scope)}
    finally:
        lock.release()


def remove_paper(paper_id: str) -> None:
    db.execute('DELETE FROM ai_conversations WHERE scope=?', ('paper:'+paper_id,))


def apply_draft(scope: str, revision: int) -> dict:
    if scope not in {'profile', 'categories'}:
        raise ConversationError('当前会话没有可保存的画像或分类偏好', 400)
    lock = _scope_lock(scope)
    if not lock.acquire(blocking=False):
        raise ConversationError('当前会话正在生成，请等待回答完成')
    try:
        current = snapshot(scope)
        check(current, revision=revision)
        key, setting = ('profile','profile') if scope == 'profile' else ('guide','category_guide')
        draft = current['draft']
        value = str(draft.get(key) or '').strip()
        if not value:
            raise ConversationError('草稿已失效，请先生成新的草稿')
        with config._lock:
            latest = config.get_settings()
            relevant = ('profile',) if scope == 'profile' else ('profile','category_guide')
            if any((draft.get('_base') or {}).get(k, '') != (latest.get(k) or '') for k in relevant):
                raise ConversationError('已保存的研究画像或分类偏好已改变，请基于最新信息重新生成')
            with closing(db.connect()) as conn, conn:
                changed = conn.execute("UPDATE ai_conversations SET draft='{}', revision=revision+1 WHERE scope=? AND revision=?",
                                       (scope, revision))
                if changed.rowcount != 1:
                    raise ConversationError('草稿已更新，请刷新后重试')
                config.update_settings({setting: value})
        result = {'profile':value} if scope == 'profile' else {}
        if scope == 'categories':
            from . import category_assistant

            result = category_assistant.apply('')
        return {**result, 'conversation': snapshot(scope)}
    finally:
        lock.release()
