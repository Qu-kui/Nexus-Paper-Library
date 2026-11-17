"""SQLite 访问层：建表、连接、通用查询帮手。"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS employment_items (
    id TEXT PRIMARY KEY, url TEXT NOT NULL UNIQUE, title TEXT NOT NULL,
    snippet TEXT DEFAULT '', kind TEXT DEFAULT 'other', reason TEXT DEFAULT '',
    status TEXT DEFAULT 'pending', notes TEXT DEFAULT '',
    created_at TEXT DEFAULT '', updated_at TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS employment_checks (item_id TEXT PRIMARY KEY,payload TEXT DEFAULT '{}');
CREATE TABLE IF NOT EXISTS papers (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    authors TEXT DEFAULT '',
    year TEXT DEFAULT '',
    venue TEXT DEFAULT '',
    doi TEXT DEFAULT '',
    arxiv_id TEXT DEFAULT '',
    url TEXT DEFAULT '',
    abstract TEXT DEFAULT '',
    file_path TEXT DEFAULT '',
    source TEXT DEFAULT 'manual',
    tags TEXT DEFAULT '[]',
    category TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    key_points TEXT DEFAULT '[]',
    relevance TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    pdf_error TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_papers_updated_at ON papers(updated_at DESC, created_at DESC, id ASC);

CREATE TABLE IF NOT EXISTS chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    page INTEGER DEFAULT 0,
    text TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chunks_paper ON chunks(paper_id);

CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(text, tokenize='unicode61');

CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
    INSERT INTO chunks_fts(rowid, text) VALUES (new.id, new.text);
END;
CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
    DELETE FROM chunks_fts WHERE rowid = old.id;
END;

CREATE TABLE IF NOT EXISTS annotations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT NOT NULL,
    page INTEGER DEFAULT 1,
    color TEXT DEFAULT '#ffd666',
    rects TEXT DEFAULT '[]',
    quote TEXT DEFAULT '',
    note TEXT DEFAULT '',
    kind TEXT DEFAULT 'highlight',
    created_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_annotations_paper ON annotations(paper_id);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS scholar_items (
    id TEXT PRIMARY KEY,
    message_uid TEXT DEFAULT '',
    title TEXT DEFAULT '',
    authors TEXT DEFAULT '',
    venue TEXT DEFAULT '',
    snippet TEXT DEFAULT '',
    url TEXT DEFAULT '',
    published TEXT DEFAULT '',
    status TEXT DEFAULT 'new',
    paper_id TEXT DEFAULT '',
    title_zh TEXT DEFAULT '',
    snippet_zh TEXT DEFAULT '',
    created_at TEXT DEFAULT '',
    updated_at TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_scholar_status ON scholar_items(status);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id TEXT,
    role TEXT,
    content TEXT,
    sources TEXT DEFAULT '[]',
    created_at TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS ai_conversations (
    scope TEXT PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    revision INTEGER NOT NULL DEFAULT 0,
    attachments TEXT NOT NULL DEFAULT '[]',
    draft TEXT NOT NULL DEFAULT '{}',
    migrated INTEGER NOT NULL DEFAULT 0,
    summary TEXT NOT NULL DEFAULT '',
    summary_upto INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS ai_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT NOT NULL REFERENCES ai_conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ai_messages_conversation ON ai_messages(conversation_id, id);

-- 对话分支的父对话信息（不能存在 ai_conversations.draft 里：每轮问答都会重写 draft）
CREATE TABLE IF NOT EXISTS ai_branch_meta (
    scope TEXT PRIMARY KEY,
    parent_scope TEXT NOT NULL DEFAULT '',
    fork_index INTEGER NOT NULL DEFAULT -1,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pdf_fetch_jobs (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    state TEXT NOT NULL DEFAULT 'queued',
    report TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS task_batches (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS task_batch_items (
    batch_id TEXT NOT NULL REFERENCES task_batches(id) ON DELETE CASCADE,
    paper_id TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'queued', error TEXT NOT NULL DEFAULT '',
    PRIMARY KEY(batch_id,paper_id)
);
CREATE TABLE IF NOT EXISTS recovery_items (
    id TEXT PRIMARY KEY, kind TEXT NOT NULL, label TEXT NOT NULL,
    payload TEXT NOT NULL, created_at TEXT NOT NULL, restored_at TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS discovery_sessions (
    id TEXT PRIMARY KEY, title TEXT NOT NULL,
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS discovery_runs (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES discovery_sessions(id) ON DELETE CASCADE,
    question TEXT NOT NULL, answer TEXT NOT NULL, intent TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '', queries TEXT NOT NULL DEFAULT '[]', warnings TEXT NOT NULL DEFAULT '[]',
    date_from TEXT NOT NULL DEFAULT '', date_to TEXT NOT NULL DEFAULT '',
    references_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL,
    tracking_json TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_discovery_runs_session ON discovery_runs(session_id,created_at);
CREATE TABLE IF NOT EXISTS discovery_candidates (
    id TEXT PRIMARY KEY, session_id TEXT NOT NULL REFERENCES discovery_sessions(id) ON DELETE CASCADE,
    run_id TEXT REFERENCES discovery_runs(id) ON DELETE SET NULL,
    fingerprint TEXT NOT NULL, payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','added','ignored')),
    paper_id TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
    UNIQUE(session_id,fingerprint)
);
CREATE INDEX IF NOT EXISTS idx_discovery_candidates_session ON discovery_candidates(session_id,status);

CREATE TABLE IF NOT EXISTS method_profiles (
    paper_id TEXT PRIMARY KEY REFERENCES papers(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'queued',
    method_summary TEXT NOT NULL DEFAULT '',
    experiment_type TEXT NOT NULL DEFAULT '',
    data TEXT NOT NULL DEFAULT '{}',
    error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_method_profiles_status ON method_profiles(status);

CREATE TABLE IF NOT EXISTS flashcards (
    id TEXT PRIMARY KEY,
    paper_id TEXT NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
    front TEXT NOT NULL,
    back TEXT NOT NULL,
    page INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'ai',
    ease REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    due_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS terms (
    id TEXT PRIMARY KEY,
    term TEXT NOT NULL,
    expansion TEXT NOT NULL DEFAULT '',
    meaning TEXT NOT NULL DEFAULT '',
    paper_id TEXT NOT NULL DEFAULT '',
    page INTEGER NOT NULL DEFAULT 0,
    source TEXT NOT NULL DEFAULT 'regex',
    ease REAL NOT NULL DEFAULT 2.5,
    interval_days REAL NOT NULL DEFAULT 0,
    reps INTEGER NOT NULL DEFAULT 0,
    lapses INTEGER NOT NULL DEFAULT 0,
    step INTEGER NOT NULL DEFAULT 0,
    mastery REAL NOT NULL DEFAULT 0,
    mastery_en2zh REAL NOT NULL DEFAULT 0,
    mastery_zh2en REAL NOT NULL DEFAULT 0,
    retired INTEGER NOT NULL DEFAULT 0,
    kind TEXT NOT NULL DEFAULT 'word',
    phonetic TEXT NOT NULL DEFAULT '',
    pos TEXT NOT NULL DEFAULT '',
    history_json TEXT NOT NULL DEFAULT '[]',
    sources_json TEXT NOT NULL DEFAULT '[]',
    due_at TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT '',
    reviewed_at TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_terms_due ON terms(due_at);
CREATE INDEX IF NOT EXISTS idx_terms_paper ON terms(paper_id);
CREATE INDEX IF NOT EXISTS idx_terms_term ON terms(term);

CREATE INDEX IF NOT EXISTS idx_flashcards_due ON flashcards(due_at);
CREATE INDEX IF NOT EXISTS idx_flashcards_paper ON flashcards(paper_id);

CREATE TABLE IF NOT EXISTS citation_styles (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    spec TEXT NOT NULL DEFAULT '{}',
    source TEXT NOT NULL DEFAULT 'manual',
    created_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""


def now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def new_id() -> str:
    return uuid.uuid4().hex


def connect() -> sqlite3.Connection:
    config.ensure_dirs()
    conn = sqlite3.connect(config.DB_PATH, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.create_function('pdf_available',1,pdf_available)
    return conn


def pdf_available(file_path) -> int:
    if not str(file_path or '').strip():
        return 0
    try:
        return int(config.resolve_pdf_path(str(file_path)).is_file())
    except (OSError,ValueError):
        return 0


def init_db() -> None:
    config.ensure_dirs()
    conn = connect()
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        from .services.bibliography import repair_venues
        repair_venues(conn)
        conn.commit()
    finally:
        conn.close()


def _migrate(conn: sqlite3.Connection) -> None:
    """轻量迁移：给老库补上两级别分类相关列。"""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(papers)")}
    discovery_columns = {row[1] for row in conn.execute('PRAGMA table_info(discovery_runs)')}
    if discovery_columns and 'tracking_json' not in discovery_columns:
        conn.execute("ALTER TABLE discovery_runs ADD COLUMN tracking_json TEXT NOT NULL DEFAULT '{}'")
    conversation_columns = {row[1] for row in conn.execute('PRAGMA table_info(ai_conversations)')}
    for name, ddl in (('summary', "TEXT NOT NULL DEFAULT ''"), ('summary_upto', 'INTEGER NOT NULL DEFAULT 0')):
        if name not in conversation_columns:
            conn.execute(f'ALTER TABLE ai_conversations ADD COLUMN {name} {ddl}')
    additions = {
        "category_major": "TEXT DEFAULT ''",
        "category_minor": "TEXT DEFAULT ''",
        "category_suggest": "TEXT DEFAULT ''",
        "category_status": "TEXT DEFAULT ''",
        "category_path": "TEXT DEFAULT ''",
        "pdf_error": "TEXT DEFAULT ''",
        "pdf_acquisition": "TEXT DEFAULT '{}'",
        "category_error": "TEXT DEFAULT ''",
        "index_error": "TEXT DEFAULT ''",
        "analysis_error": "TEXT DEFAULT ''",
        "volume": "TEXT DEFAULT ''",
        "issue": "TEXT DEFAULT ''",
        "pages": "TEXT DEFAULT ''",
        "imported_notes": "TEXT DEFAULT '[]'",
    }
    for name, ddl in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE papers ADD COLUMN {name} {ddl}")
    conn.execute("UPDATE papers SET category_error=pdf_error,pdf_error='' WHERE pdf_error LIKE '分类失败：%' AND COALESCE(category_error,'')=''")
    term_columns = {row[1] for row in conn.execute("PRAGMA table_info(terms)")}
    for name, ddl in (
        ('step', 'INTEGER NOT NULL DEFAULT 0'),
        ('mastery', 'REAL NOT NULL DEFAULT 0'),
        ('mastery_en2zh', 'REAL NOT NULL DEFAULT 0'),
        ('mastery_zh2en', 'REAL NOT NULL DEFAULT 0'),
        ('retired', 'INTEGER NOT NULL DEFAULT 0'),
        ('sources_json', "TEXT NOT NULL DEFAULT '[]'"),
        ('kind', "TEXT NOT NULL DEFAULT 'word'"),
        ('phonetic', "TEXT NOT NULL DEFAULT ''"),
        ('pos', "TEXT NOT NULL DEFAULT ''"),
        ('history_json', "TEXT NOT NULL DEFAULT '[]'"),
    ):
        if term_columns and name not in term_columns:
            conn.execute(f"ALTER TABLE terms ADD COLUMN {name} {ddl}")
    # Preserve every existing row, including legacy duplicates and their history.
    conn.execute('DROP INDEX IF EXISTS idx_terms_unique_nocase')
    conn.execute('CREATE INDEX IF NOT EXISTS idx_terms_sense ON terms(term COLLATE NOCASE, kind, meaning, expansion)')
    additions = {
        'aliases_json': "TEXT NOT NULL DEFAULT '[]'",
        'due_en2zh': "TEXT NOT NULL DEFAULT ''", 'due_zh2en': "TEXT NOT NULL DEFAULT ''",
        'step_en2zh': 'INTEGER NOT NULL DEFAULT 0', 'step_zh2en': 'INTEGER NOT NULL DEFAULT 0',
        'asked': 'INTEGER NOT NULL DEFAULT 0', 'correct_count': 'INTEGER NOT NULL DEFAULT 0',
        'fuzzy_count': 'INTEGER NOT NULL DEFAULT 0',
        'wrong_count': 'INTEGER NOT NULL DEFAULT 0',
    }
    for name, ddl in additions.items():
        if name not in term_columns:
            conn.execute(f'ALTER TABLE terms ADD COLUMN {name} {ddl}')
    if 'mastery_en2zh' not in term_columns:
        conn.execute('UPDATE terms SET mastery_en2zh=mastery')
        conn.execute('UPDATE terms SET mastery=MIN(mastery_en2zh,mastery_zh2en)')
    if 'asked' not in term_columns:
        for row in conn.execute('SELECT id,reps,history_json,due_at,step,mastery FROM terms').fetchall():
            try:
                history = json.loads(row['history_json'])
                if not isinstance(history, list): history = []
            except (ValueError, TypeError):
                history = []
            due = row['due_at']
            if row['mastery'] >= 100 and not due:
                due = (datetime.now() + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
            conn.execute('UPDATE terms SET asked=?,correct_count=?,fuzzy_count=?,due_en2zh=?,due_zh2en=?,step_en2zh=?,step_zh2en=?,due_at=? WHERE id=?',
                         (max(row['reps'], len(history)), sum(bool(e.get('correct')) for e in history),
                          sum(bool(e.get('fuzzy')) for e in history), due, due, row['step'], row['step'], due, row['id']))
            conn.execute('UPDATE terms SET wrong_count=? WHERE id=?',
                         (sum(not bool(e.get('correct')) for e in history), row['id']))
    conn.execute('CREATE TABLE IF NOT EXISTS term_questions (id TEXT PRIMARY KEY, term_id TEXT NOT NULL, payload TEXT NOT NULL, expires_at TEXT NOT NULL, result TEXT)')
    scholar_columns = {row[1] for row in conn.execute("PRAGMA table_info(scholar_items)")}
    for name, ddl in {"title_zh": "TEXT DEFAULT ''", "snippet_zh": "TEXT DEFAULT ''"}.items():
        if name not in scholar_columns:
            conn.execute(f"ALTER TABLE scholar_items ADD COLUMN {name} {ddl}")
    # 老库把两级别分类补成任意层级 path
    for row in conn.execute(
        "SELECT id, category_major, category_minor, category, category_path FROM papers "
        "WHERE COALESCE(category_path, '') = ''"
    ).fetchall():
        pid, major, minor, category, _path = row
        levels = []
        if (major or "").strip():
            levels = [str(major).strip()]
            if (minor or "").strip():
                levels.append(str(minor).strip())
        elif (category or "").strip():
            levels = [part.strip() for part in str(category).replace("／", "/").split("/") if part.strip()]
        if levels:
            conn.execute(
                "UPDATE papers SET category_path = ? WHERE id = ?",
                (json.dumps(levels, ensure_ascii=False), pid),
            )


def execute(sql: str, params: Iterable[Any] = ()) -> int:
    conn = connect()
    try:
        cur = conn.execute(sql, tuple(params))
        conn.commit()
        return cur.lastrowid or 0
    finally:
        conn.close()


def executemany(sql: str, rows: Iterable[Iterable[Any]]) -> None:
    conn = connect()
    try:
        conn.executemany(sql, [tuple(r) for r in rows])
        conn.commit()
    finally:
        conn.close()


def query(sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    conn = connect()
    try:
        return conn.execute(sql, tuple(params)).fetchall()
    finally:
        conn.close()


def query_one(sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
    rows = query(sql, params)
    return rows[0] if rows else None


def paper_to_dict(row: sqlite3.Row | dict) -> dict:
    from .services.bibliography import clean_venue_fields
    data = clean_venue_fields(dict(row))
    # Migration bodies are loaded only by /notes, not in every library row or AI context.
    data.pop('imported_notes', None)
    for key in ("tags", "key_points"):
        raw = data.get(key) or "[]"
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
        except Exception:
            parsed = []
        data[key] = parsed if isinstance(parsed, list) else []
    suggestion_raw = data.get("category_suggest") or ""
    try:
        data["category_suggest"] = json.loads(suggestion_raw) if suggestion_raw else {}
    except Exception:
        data["category_suggest"] = {}
    data['has_pdf']=bool(pdf_available(data.get('file_path')))
    data['pdf_state']='available' if data['has_pdf'] else ('missing' if data.get('file_path') else 'not_acquired')
    data['index_state']='failed' if data.get('index_error') else ('indexed' if data.get('status') in {'indexed','analyzed'} else 'not_indexed')
    data['analysis_state']='failed' if data.get('analysis_error') else ('analyzed' if data.get('summary') else 'pending')
    return data


def get_paper(paper_id: str) -> dict | None:
    row = query_one("SELECT * FROM papers WHERE id = ?", (paper_id,))
    if not row:
        return None
    paper = paper_to_dict(row)
    cnt = query_one("SELECT COUNT(*) AS c FROM annotations WHERE paper_id = ?", (paper_id,))
    paper["annotation_count"] = cnt["c"] if cnt else 0
    return paper


def normalize_title(title: str) -> str:
    """标题归一化：全角转半角、忽略大小写、去掉标点、压缩空白。

    只用于"是不是同一篇"的判断，不写回数据库；目的是让
    “Attention Is All You Need.” 与 “attention is all you need” 判成重复。
    """
    import re
    import unicodedata

    text = unicodedata.normalize("NFKC", str(title or "")).lower()
    text = re.sub(r"[^\w]+", " ", text, flags=re.UNICODE)
    return " ".join(text.split())


def _title_prefix_match(a: str, b: str) -> bool:
    """副标题差异：较短的一方是较长一方的前缀（按词边界），且本身足够长，避免误合并短标题。"""
    if not a or not b:
        return False
    if a == b:
        return True
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    if len(shorter) < 24 or len(shorter.split()) < 4:
        return False
    return longer.startswith(shorter + " ")


def _drop_leading_article(normalized: str) -> str:
    """去掉开头的 a / an / the，且去掉后仍然足够长才生效。

    同一篇论文在 PDF 首页（"An Optical Frontend for a Convolutional Neural
    Network"）和 Crossref/Zotero 元数据（"Optical frontend for a convolutional
    neural network"）里经常差一个冠词，不归一化就会导成两条重复记录。
    短标题（去掉后不足 24 字符或不足 4 词）不剥冠词，避免把 "Editorial" 和
    "An Editorial" 这类硬合并。
    """
    for article in ("a ", "an ", "the "):
        if normalized.startswith(article):
            rest = normalized[len(article):]
            if len(rest) >= 24 and len(rest.split()) >= 4:
                return rest
            return normalized
    return normalized


def normalize_doi_key(doi: str) -> str:
    """DOI 归一化，只用于判重：去 doi.org 前缀、统一小写、去掉 .full/.pdf 这类后缀。

    Zotero 导出里同一篇常出现 ``10.1117/1.JBO.23.12.121609`` 与
    ``.../121609.full``，或大小写不同（``OPTICA`` / ``optica``），不归一化就会重复入库。
    """
    import re

    text = str(doi or "").strip().lower()
    text = re.sub(r"^https?://(?:dx\.)?doi\.org/", "", text)
    text = re.sub(r"^doi:\s*", "", text)
    text = re.sub(r"\.(?:full|fulltext|pdf|html)$", "", text.strip())
    return text.strip()


def normalize_arxiv_key(arxiv_id: str) -> str:
    """Match arXiv URL/prefix/version variants without rewriting stored metadata."""
    import re

    text = str(arxiv_id or "").strip().lower()
    text = re.sub(r"^https?://(?:www\.)?arxiv\.org/(?:abs|pdf)/", "", text)
    text = re.sub(r"^arxiv:\s*", "", text)
    text = re.sub(r"\.pdf$", "", text)
    return re.sub(r"v\d+$", "", text).strip()


def find_duplicate(title: str, doi: str = "", arxiv_id: str = "", *, strict_identifiers: bool = False, conn: sqlite3.Connection | None = None) -> str | None:
    # Reuse the caller's transaction for atomic import lookup + insertion.
    def lookup(sql, params=()):
        return conn.execute(sql, tuple(params)).fetchall() if conn is not None else query(sql, params)

    def lookup_one(sql, params=()):
        rows = lookup(sql, params)
        return rows[0] if rows else None

    if doi:
        row = lookup_one("SELECT id FROM papers WHERE lower(doi) = lower(?)", (doi,))
        if row:
            return row["id"]
        want_doi = normalize_doi_key(doi)
        if want_doi:
            for candidate in lookup("SELECT id, doi FROM papers WHERE ifnull(doi,'') <> ''"):
                if normalize_doi_key(candidate["doi"]) == want_doi:
                    return candidate["id"]
    if arxiv_id:
        want_arxiv = normalize_arxiv_key(arxiv_id)
        if want_arxiv:
            for candidate in lookup("SELECT id, arxiv_id FROM papers WHERE ifnull(arxiv_id,'') <> ''"):
                if normalize_arxiv_key(candidate["arxiv_id"]) == want_arxiv:
                    return candidate["id"]
    if title:
        if strict_identifiers:
            for row in lookup('SELECT id,doi,arxiv_id FROM papers WHERE lower(trim(title))=lower(trim(?))',(title,)):
                if doi and row['doi'] and normalize_doi_key(doi) != normalize_doi_key(row['doi']):continue
                if arxiv_id and row['arxiv_id'] and normalize_arxiv_key(arxiv_id) != normalize_arxiv_key(row['arxiv_id']):continue
                return row['id']
            return None
        for row in lookup('SELECT id,doi,arxiv_id FROM papers WHERE lower(trim(title)) = lower(trim(?))', (title,)):
            # 标题完全一致但 DOI/arXiv 冲突 → 当作两篇不同文献（更安全的失败方向：
            # 宁可多一条可疑记录让用户合并，也不要把两篇论文合成一条）。
            if doi and row['doi'] and normalize_doi_key(doi) != normalize_doi_key(row['doi']):
                continue
            if arxiv_id and row['arxiv_id'] and normalize_arxiv_key(arxiv_id) != normalize_arxiv_key(row['arxiv_id']):
                continue
            return row['id']
        # 归一化匹配：标点、全半角、大小写差异、副标题差异，以及开头冠词差异（a/an/the），
        # 都当作同一篇；但两边都有 DOI 且不同 → 视为两篇不同文献，不做模糊合并。
        want = _drop_leading_article(normalize_title(title))
        if len(want) >= 8:
            for row in lookup('SELECT id,title,doi,arxiv_id FROM papers'):
                other = _drop_leading_article(normalize_title(row['title']))
                if not other or not _title_prefix_match(other, want):
                    continue
                if doi and row['doi'] and normalize_doi_key(doi) != normalize_doi_key(row['doi']):
                    continue
                if arxiv_id and row['arxiv_id'] and normalize_arxiv_key(arxiv_id) != normalize_arxiv_key(row['arxiv_id']):
                    continue
                return row['id']
    return None


def update_paper(paper_id: str, fields: dict, *, conn: sqlite3.Connection | None = None) -> None:
    from .services.bibliography import clean_venue_fields
    explicit=set(fields)
    fields = clean_venue_fields(fields)
    inferred=set(fields)-explicit
    if inferred:
        lookup=conn.execute if conn is not None else query_one
        existing=lookup('SELECT volume,issue,pages,doi FROM papers WHERE id=?',(paper_id,))
        if conn is not None:existing=existing.fetchone()
        if existing:
            for key in inferred:
                if str(existing[key] or '').strip():fields.pop(key,None)
    allowed = {
        "title", "authors", "year", "venue", "doi", "arxiv_id", "url", "abstract",
        "file_path", "source", "tags", "category", "summary", "key_points",
        "relevance", "status", "notes", "category_major", "category_minor",
        "category_suggest", "category_status", "category_path", "pdf_error", "pdf_acquisition",
        "category_error", "index_error", "analysis_error", "volume", "issue", "pages",
    }
    sets, values = [], []
    for key, value in fields.items():
        if key not in allowed:
            continue
        if key in ("tags", "key_points") and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False)
        sets.append(f"{key} = ?")
        values.append(value)
    if not sets:
        return
    sets.append("updated_at = ?")
    values.append(now())
    values.append(paper_id)
    write = conn.execute if conn is not None else execute
    write(f"UPDATE papers SET {', '.join(sets)} WHERE id = ?", values)
