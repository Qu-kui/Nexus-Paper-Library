"""术语 / 缩写复习：抽取、手动添加、按遗忘曲线排复习。"""
from __future__ import annotations

import json
from contextlib import closing
import re
from datetime import datetime, timedelta

from .. import config, db, llm

#: 复习等级
GRADES = ('again', 'hard', 'good')
import threading

_TERMS_LOCK = threading.RLock()
_AI_JOB_LOCK = threading.Lock()
_EXPORT_TIMER = None
_EXPORT_ERROR = ''


def schedule_dictionary_sync(delay=0.5):
    """Coalesce exports; a file failure must never turn a committed answer into failure."""
    global _EXPORT_TIMER
    with _TERMS_LOCK:
        if _EXPORT_TIMER:
            _EXPORT_TIMER.cancel()
        database = str(config.DB_PATH)

        def write():
            global _EXPORT_ERROR
            with _TERMS_LOCK:
                if str(config.DB_PATH) != database:
                    return
                try:
                    sync_dictionary()
                    _EXPORT_ERROR = ''
                except OSError as exc:
                    _EXPORT_ERROR = str(exc)
                    schedule_dictionary_sync(5)
        _EXPORT_TIMER = threading.Timer(delay, write)
        _EXPORT_TIMER.daemon = True
        _EXPORT_TIMER.start()


def flush_dictionary_sync():
    global _EXPORT_TIMER
    with _TERMS_LOCK:
        if _EXPORT_TIMER:
            _EXPORT_TIMER.cancel()
            _EXPORT_TIMER = None
        return sync_dictionary()
#: 艾宾浩斯间隔阶梯（天）：5 分钟 → 25 分钟 → 2 小时 → 12 小时 → 1/2/4/7/15/30/60/120 天
EBBINGHAUS_DAYS = (5 / 1440, 25 / 1440, 2 / 24, 12 / 24, 1, 2, 4, 7, 15, 30, 60, 120)
STEPS = EBBINGHAUS_DAYS
QUIZ_OPTIONS = 6
QUIZ_MODES = ('term2meaning', 'meaning2term', 'mixed')

_ABBR_RE = re.compile(r'([A-Z][A-Za-z0-9\-\u00b7 ]{2,60}?)\s*\(\s*([A-Z][A-Z0-9\-]{1,9})\s*\)')
_LEAD_STOPWORDS = ('the', 'a', 'an', 'of', 'in', 'on', 'and', 'or', 'to', 'for', 'with', 'by', 'using',
                   'see', 'fig', 'figure', 'table', 'section', 'eq', 'we', 'our', 'this', 'that',
                   'these', 'those', 'it', 'they', 'propose', 'proposed', 'show', 'shown', 'discuss')
_SKIP_ABBR = {'FIG', 'FIGS', 'EQ', 'EQS', 'TAB', 'SEC', 'REF', 'REFS', 'SUPP', 'NO'}
#: 句子里常见的功能词：抓到的「展开」像句子就不是术语
_SENTENCE_WORDS = {'is', 'are', 'was', 'were', 'has', 'have', 'can', 'will', 'solves', 'solution',
                   'problem', 'equation', 'defined', 'denoted', 'given', 'means', 'represents',
                   'where', 'which', 'that', 'from', 'then', 'also', 'such', 'when',
                   'yields', 'yield', 'enhancement', 'while', 'based', 'toward', 'via',
                   'shows', 'show', 'gives', 'give', 'achieves', 'enable', 'enables',
                   'allows', 'allowing', 'compared', 'respectively', 'these', 'their',
                   'under', 'over', 'between', 'through', 'without', 'within', 'foundation',
                   'funded', 'grant', 'university', 'institute', 'laboratory', 'college'}
#: 单独一个通用词当展开，多半是截断没抓全
_GENERIC_WORDS = {'transform', 'method', 'function', 'network', 'image', 'signal', 'light',
                  'model', 'system', 'process', 'analysis', 'data', 'value', 'field', 'beam',
                  'wave', 'medium', 'surface', 'structure', 'result', 'term', 'fiber', 'layer'}
#: 参考文献/会议名里的「伪缩写」，背了也没用
_VENUE_WORDS = {
    'conference', 'proceedings', 'symposium', 'workshop', 'journal', 'transactions',
    'expo', 'digest', 'letters', 'communications', 'society', 'ieee', 'spie', 'osa',
    'acm', 'springer', 'elsevier', 'press', 'publishing', 'vol', 'pp', 'doi', 'editor',
    'editors', 'annual', 'international', 'meeting', 'advances', 'review', 'reviews',
}


def _now() -> datetime:
    return datetime.now()


def _stamp(moment: datetime) -> str:
    return moment.strftime('%Y-%m-%d %H:%M:%S')


def _add_days(days: float) -> str:
    return _stamp(_now() + timedelta(days=max(0.0, float(days))))


def _public(row) -> dict:
    item = dict(row)
    item['aliases'] = json.loads(item.get('aliases_json') or '[]')
    item['fuzzy'] = item.get('fuzzy_count', 0)
    item['correct'] = item.get('correct_count', 0)
    item['wrong'] = item.get('wrong_count', 0)
    item['unclassified'] = max(0, item.get('asked', 0) - item['correct'] - item['wrong'])
    return item


def _paper_match(item, paper_id):
    return not paper_id or item['paper_id'] == paper_id or any(s.get('paper_id') == paper_id for s in _load_sources(item))


def _list_filter(paper_id='', q=''):
    clauses, params = [], []
    if paper_id:
        clauses.append("(paper_id=? OR EXISTS (SELECT 1 FROM json_each(CASE WHEN json_valid(sources_json) THEN sources_json ELSE '[]' END) WHERE json_extract(value,'$.paper_id')=?))")
        params.extend((paper_id, paper_id))
    if q:
        clauses.append("(instr(lower(term || ' ' || meaning || ' ' || expansion || ' ' || aliases_json), lower(?)) > 0)")
        params.append(q)
    return (' WHERE ' + ' AND '.join(clauses) if clauses else ''), params


def list_terms(paper_id: str = '', q: str = '', limit: int = 500, offset: int = 0) -> list[dict]:
    clause, params = _list_filter(paper_id, q)
    rows = db.query('SELECT * FROM terms' + clause + ' ORDER BY created_at DESC,rowid DESC LIMIT ? OFFSET ?',
                    params + [max(1, min(limit, 2000)), max(0, offset)])
    return [_public(r) for r in rows]


def count_terms(paper_id='', q=''):
    clause, params = _list_filter(paper_id, q)
    return int(db.query_one('SELECT COUNT(*) AS c FROM terms' + clause, params)['c'])


def stats() -> dict:
    """学习分桶与停用词分开；累计计数独立于最近 300 次明细。"""
    rows = db.query('SELECT * FROM terms')
    total = len(rows)
    unlearned = unknown = unfamiliar = familiar = mastered = 0
    for row in rows:
        if row['retired']:
            continue
        history = _load_history(row)
        score = float(dict(row).get('mastery') or 0)
        if not history:
            unlearned += 1
        elif score >= 100:
            mastered += 1
        elif history[-1].get('unknown'):
            unknown += 1
        elif score < 40:
            unfamiliar += 1
        else:
            familiar += 1
    due_count = len(_eligible_rows())
    return {
        'total': total,
        'unlearned': unlearned,
        'unknown': unknown,
        'unfamiliar': unfamiliar,
        'familiar': familiar,
        'mastered': mastered,
        'bucket_sum': unlearned + unknown + unfamiliar + familiar + mastered,
        'due': due_count,
        'retired': sum(int(r['retired']) for r in rows),
        'asked': sum(r['asked'] for r in rows),
        'correct': sum(r['correct_count'] for r in rows),
        'wrong': sum(r['wrong_count'] for r in rows),
        'fuzzy': sum(r['fuzzy_count'] for r in rows),
        'unclassified': sum(max(0, r['asked'] - r['correct_count'] - r['wrong_count']) for r in rows),
        'dictionary': str(config.dictionary_dir()),
        'dictionary_error': _EXPORT_ERROR,
    }


def _load_sources(row: dict) -> list[dict]:
    try:
        value = json.loads(str(row.get('sources_json') or '[]'))
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _append_source(term_id: str, paper_id: str, page: int) -> None:
    with _TERMS_LOCK:
        _append_source_locked(term_id, paper_id, page)


def _append_source_locked(term_id: str, paper_id: str, page: int) -> None:
    if not paper_id:
        return
    row = db.query_one('SELECT sources_json FROM terms WHERE id=?', (term_id,))
    if not row:
        return
    sources = _load_sources({'sources_json': row['sources_json']})
    if any(str(item.get('paper_id')) == paper_id and int(item.get('page') or 0) == int(page or 0) for item in sources):
        return
    sources.append({'paper_id': paper_id, 'page': int(page or 0)})
    db.execute('UPDATE terms SET sources_json=? WHERE id=?', (json.dumps(sources, ensure_ascii=False), term_id))


def sync_dictionary() -> dict:
    """Export a regenerable snapshot; the database remains authoritative."""
    global _EXPORT_ERROR
    with _TERMS_LOCK:
        try:
            result = _sync_dictionary_locked()
        except OSError as exc:
            _EXPORT_ERROR = str(exc)
            raise
        _EXPORT_ERROR = ''
        return result


def _sync_dictionary_locked() -> dict:
    import os
    import tempfile

    folder = config.dictionary_dir()
    folder.mkdir(parents=True, exist_ok=True)
    rows = db.query(
        'SELECT t.*, p.title AS paper_title FROM terms t LEFT JOIN papers p ON p.id = t.paper_id '
        'ORDER BY t.created_at ASC, t.rowid ASC'
    )
    entries = []
    for row in rows:
        item = dict(row)
        term = str(item.get('term') or '')
        if not term:
            continue
        history = _load_history(item)
        entries.append({
            'term': term,
            'kind': str(item.get('kind') or 'word'),
            'expansion': str(item.get('expansion') or ''),
            'meaning': str(item.get('meaning') or ''),
            'phonetic': str(item.get('phonetic') or ''),
            'pos': str(item.get('pos') or ''),
            'mastery': round(float(item.get('mastery') or 0), 1),
            'mastery_en2zh': round(float(item.get('mastery_en2zh') or 0), 1),
            'mastery_zh2en': round(float(item.get('mastery_zh2en') or 0), 1),
            'fuzzy': item.get('fuzzy_count', 0),
            'mastered': float(item.get('mastery') or 0) >= 100,
            'retired': bool(item.get('retired')),
            'step': int(item.get('step') or 0),
            'due_at': str(item.get('due_at') or ''),
            'asked': item.get('asked', 0),
            'correct': item.get('correct_count', 0),
            'wrong': item.get('wrong_count', 0),
            'unclassified': max(0, item.get('asked', 0) - item.get('correct_count', 0) - item.get('wrong_count', 0)),
            'aliases': json.loads(item.get('aliases_json') or '[]'),
            'due_en2zh': item.get('due_en2zh', ''),
            'due_zh2en': item.get('due_zh2en', ''),
            'history': history,
            'sources': _load_sources(item),
            'created_at': str(item.get('created_at') or ''),
        })
    generation = db.new_id()
    payload = {'version': 2, 'generation': generation, 'updated_at': db.now(), 'count': len(entries), 'entries': entries}

    def _atomic_write(path, write):
        fd, temp = tempfile.mkstemp(dir=str(folder), prefix=path.name + '.', suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8-sig' if path.suffix == '.csv' else 'utf-8', newline='') as handle:
                write(handle)
            os.replace(temp, path)
        finally:
            if os.path.exists(temp):
                try:
                    os.unlink(temp)
                except OSError:
                    pass

    def _write_csv(handle):
        import csv

        writer = csv.writer(handle)
        writer.writerow(['术语', '类型', '全称', '中文释义', '音标', '词性', '记忆度(0-100)',
                         '看英文答中文', '看中文答英文', '模糊次数', '已掌握',
                         '答题次数', '答对', '答错', '下次复习', '来源论文', '页码', '不再复习', '全部来源', '别名', '生成版本'])
        titles = {row['paper_id']: row['paper_title'] for row in rows}
        for item in entries:
            source = item['sources'][0] if item['sources'] else {}
            writer.writerow([
                item['term'], '缩写' if item['kind'] == 'abbr' else '单词', item['expansion'], item['meaning'],
                item['phonetic'], item['pos'], item['mastery'],
                item['mastery_en2zh'], item['mastery_zh2en'], item['fuzzy'],
                '是' if item['mastered'] else '否',
                item['asked'], item['correct'], item['wrong'], item['due_at'],
                titles.get(source.get('paper_id')) or '', source.get('page') or '',
                item['retired'], json.dumps(item['sources'], ensure_ascii=False),
                json.dumps(item['aliases'], ensure_ascii=False), generation,
            ])

    # Alternate complete snapshot pairs. The previous manifest stays readable if
    # writing either member fails; compatibility filenames remain available.
    try:
        previous = json.loads((folder / 'manifest.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        previous = {}
    slot = 'b' if previous.get('slot') == 'a' else 'a'
    names = {'json': f'words-snapshot-{slot}.json', 'csv': f'words-snapshot-{slot}.csv'}
    write_json = lambda handle: handle.write(json.dumps(payload, ensure_ascii=False, indent=2))
    _atomic_write(folder / names['json'], write_json)
    _atomic_write(folder / names['csv'], _write_csv)
    _atomic_write(folder / 'words.json', write_json)
    _atomic_write(folder / 'words.csv', _write_csv)
    # Manifest is the commit point; use its file paths for a consistent pair.
    import hashlib
    manifest = {'generation': generation, 'slot': slot, 'files': names, 'count': len(entries), 'sha256': {
        name: hashlib.sha256((folder / name).read_bytes()).hexdigest()
        for name in (*names.values(), 'words.json', 'words.csv')}}
    _atomic_write(folder / 'manifest.json', lambda handle: handle.write(json.dumps(manifest)))
    readme = folder / 'README.txt'
    if not readme.exists():
        readme.write_text(
            '这是 PaperWhale 从数据库生成的单词本快照。\n'
            'words.json 是完整数据（含记忆度、复习进度、来源），words.csv 可直接用 Excel 打开。\n'
            '双方向满 100 是练习熟识，7 天后仍会维护复习；不再复习的词保留在词典中。\n'
            '请在程序里编辑；手工修改 JSON 不会导入数据库。manifest.json 可校验两份快照版本。\n',
            encoding='utf-8',
        )
    return {'path': str(folder), 'entries': len(entries)}


def _directions(row, mode='mixed'):
    now = _stamp(_now())
    directions = [('term2meaning', 'en2zh'), ('meaning2term', 'zh2en')]
    return [d for d, suffix in directions if (mode == 'mixed' or mode == d) and
            (not row.get('due_' + suffix) or row['due_' + suffix] <= now)]


def _answerable(row: dict) -> bool:
    if row.get('retired'):
        return False
    if str(row.get('meaning') or '').strip():
        return True
    return str(row.get('kind') or '') == 'abbr' and bool(str(row.get('expansion') or '').strip())


def _eligible_rows(paper_id='', mode='mixed'):
    rows = [dict(r) for r in db.query('SELECT * FROM terms WHERE retired=0 ORDER BY due_at ASC,rowid ASC')]
    kind_counts: dict[str, int] = {}
    for row in rows:
        kind_counts[str(row.get('kind'))] = kind_counts.get(str(row.get('kind')), 0) + 1
    return [r for r in rows
            if _paper_match(r, paper_id) and _answerable(r) and kind_counts.get(str(r.get('kind')), 0) > 1
            and _directions(r, mode)]


def due_terms(limit: int = 20) -> list[dict]:
    return [_public(r) for r in _eligible_rows()[:max(1, min(limit, 100))]]


def get_term(term_id: str) -> dict | None:
    row = db.query_one('SELECT * FROM terms WHERE id=?', (term_id,))
    return _public(row) if row else None


def add_term(term: str, expansion: str = '', meaning: str = '', paper_id: str = '',
             page: int = 0, source: str = 'manual', sync: bool = True,
             kind: str = '', phonetic: str = '', pos: str = '', aliases: list[str] | None = None) -> dict | None:
    term = ' '.join(str(term or '').split())[:120]
    if not term:
        raise ValueError('术语不能为空')
    if not kind:
        kind = 'abbr' if re.fullmatch(r'[A-Z0-9][A-Z0-9\-]{1,9}', term) else 'word'
    if kind not in {'word', 'abbr'}:
        raise ValueError('类型必须是 word / abbr')
    expansion = ' '.join(str(expansion or '').split())[:400]
    meaning = ' '.join(str(meaning or '').split())[:400]
    with _TERMS_LOCK:
        existing = next((dict(r) for r in db.query('SELECT * FROM terms WHERE kind=?', (kind,))
                         if _normalize(r['term']) == _normalize(term)
                         and (not meaning or not r['meaning'] or _normalize(r['meaning']) == _normalize(meaning))
                         and (not expansion or not r['expansion'] or _normalize(r['expansion']) == _normalize(expansion))), None)
        if existing:
            _append_source_locked(existing['id'], paper_id, page)
            enrich = {k: v for k, v in {'meaning': meaning, 'expansion': expansion, 'phonetic': phonetic, 'pos': pos}.items() if v and not existing[k]}
            existing_aliases = json.loads(existing.get('aliases_json') or '[]')
            combined = list(dict.fromkeys(existing_aliases + (aliases or []) + ([term] if term != existing['term'] else [])))
            if combined != existing_aliases:
                enrich['aliases_json'] = json.dumps(combined, ensure_ascii=False)
            if enrich:
                db.execute('UPDATE terms SET ' + ','.join(k + '=?' for k in enrich) + ' WHERE id=?', list(enrich.values()) + [existing['id']])
            if sync: schedule_dictionary_sync()
            return None
        term_id = db.new_id()
        sources = json.dumps([{'paper_id': paper_id, 'page': int(page or 0)}] if paper_id else [], ensure_ascii=False)
        inserted = db.execute(
            'INSERT OR IGNORE INTO terms(id,term,expansion,meaning,paper_id,page,source,ease,interval_days,reps,'
            'lapses,step,mastery,retired,kind,phonetic,pos,history_json,sources_json,due_at,created_at,reviewed_at) '
            'VALUES (?,?,?,?,?,?,?,2.5,0,0,0,0,0,0,?,?,?,?,?,?,?,?)',
            (term_id, term, expansion, meaning, paper_id or '', int(page or 0), source or 'manual',
             kind, str(phonetic or '')[:120], str(pos or '')[:40], '[]', sources, '', db.now(), ''),
        )
        if not inserted:
            existing = db.query_one('SELECT id FROM terms WHERE term = ? COLLATE NOCASE', (term,))
            if existing:
                _append_source_locked(existing['id'], paper_id, page)
            return None
        if aliases:
            db.execute('UPDATE terms SET aliases_json=? WHERE id=?', (json.dumps(aliases, ensure_ascii=False), term_id))
        result = get_term(term_id)
    if sync:
        schedule_dictionary_sync()
    return result


def update_term(term_id: str, **fields) -> dict | None:
    row = get_term(term_id)
    if not row:
        return None
    allowed = {'term': 120, 'expansion': 400, 'meaning': 400, 'kind': 10, 'phonetic': 120, 'pos': 40}
    patch = {}
    for key, limit in allowed.items():
        if key in fields and fields[key] is not None:
            value = ' '.join(str(fields[key]).split())[:limit]
            patch[key] = value
    if 'term' in patch and not patch['term']:
        raise ValueError('术语不能为空')
    if 'kind' in patch and patch['kind'] not in {'word', 'abbr'}:
        raise ValueError('类型必须是 word / abbr')
    if fields.get('aliases') is not None:
        patch['aliases_json'] = json.dumps(list(dict.fromkeys(str(a).strip()[:120] for a in fields['aliases'] if str(a).strip())), ensure_ascii=False)
    candidate = {**row, **patch}
    conflict = db.query_one('SELECT id FROM terms WHERE id!=? AND term=? COLLATE NOCASE AND kind=? AND meaning=? AND expansion=?', (term_id, candidate['term'], candidate['kind'], candidate['meaning'], candidate['expansion']))
    if conflict:
        raise ValueError('相同术语和释义已存在，请在原词条中修正')
    if not patch:
        return row
    assignments = ', '.join(f'{key}=?' for key in patch)
    db.execute(f'UPDATE terms SET {assignments} WHERE id=?', list(patch.values()) + [term_id])
    schedule_dictionary_sync()
    return get_term(term_id)


def delete_term(term_id: str) -> bool:
    if not get_term(term_id):
        return False
    db.execute('DELETE FROM terms WHERE id=?', (term_id,))
    schedule_dictionary_sync()
    return True


def _option_pool(field: str) -> list[str]:
    rows = db.query(f"SELECT DISTINCT {field} AS value FROM terms WHERE {field} != ''")
    return [str(row['value']) for row in rows if str(row['value'] or '').strip()]


def _shuffled(values: list, rng=None) -> list:
    import random
    items = list(values)
    (rng or random).shuffle(items)
    return items


def _pair_label(row: dict) -> str:
    term = str(row.get('term') or '').strip()
    expansion = str(row.get('expansion') or '').strip()
    return f'{term}（{expansion}）' if expansion else term


def _close_pool(all_rows: list[dict], row: dict, field: str) -> list[str]:
    """优先取同一篇论文/同一来源里的词做干扰项：同领域，才不容易一眼排除。"""

    def ids_of(item: dict) -> set[str]:
        ids = {str(item.get('paper_id') or '')}
        ids.update(str(source.get('paper_id') or '') for source in _load_sources(item))
        ids.discard('')
        return ids

    answer = str(row.get(field) or '').strip()
    own_ids = ids_of(row)
    same: list[str] = []
    others: list[str] = []
    for item in all_rows:
        value = str(item.get(field) or '').strip()
        if not value or value == answer or str(item.get('kind')) != str(row.get('kind')):
            continue
        (same if own_ids & ids_of(item) else others).append(value)
    return same + others


def _normalize(value):
    import unicodedata
    value = unicodedata.normalize('NFKC', str(value)).casefold().replace('isation', 'ization')
    return re.sub(r'[^\w]', '', value)


def _prepare_rows(all_rows: list[dict]) -> list[dict]:
    prepared = []
    for raw in all_rows:
        row = dict(raw)
        try:
            aliases = json.loads(row.get('aliases_json') or '[]')
        except ValueError:
            aliases = []
        row['_aliases_norm'] = {_normalize(a) for a in aliases if a}
        row['_meaning_norm'] = _normalize(row.get('meaning'))
        row['_expansion_norm'] = _normalize(row.get('expansion'))
        sources = set()
        try:
            for source in _load_sources(row):
                if source.get('paper_id'):
                    sources.add(str(source.get('paper_id')))
        except Exception:  # noqa: BLE001
            pass
        if row.get('paper_id'):
            sources.add(str(row.get('paper_id')))
        row['_sources_ids'] = sources
        row['_pair'] = _pair_label(row)
        prepared.append(row)
    return prepared


def _question(row, all_rows, mode):
    import random
    directions = _directions(row, mode)
    directions.sort(key=lambda d: float(row['mastery_en2zh' if d == 'term2meaning' else 'mastery_zh2en']))
    meaning, expansion = row['meaning'].strip(), row['expansion'].strip()
    for direction in directions:
        fallback = row['kind'] == 'abbr' and not meaning and bool(expansion)
        if fallback:
            # Only the full name → abbreviation direction is available until corrected.
            if direction != 'meaning2term': continue
            prompt, answer, field, note = expansion, row['term'], 'term', '选出全称对应的缩写'
        elif not meaning:
            continue
        elif direction == 'meaning2term':
            prompt, answer, field, note = meaning, (_pair_label(row) if row['kind'] == 'abbr' else row['term']), 'term', '选出对应的英文或缩写'
        else:
            prompt, answer, field, note = (_pair_label(row) if row['kind'] == 'abbr' else row['term']), meaning, 'meaning', '选出正确的中文释义'
        aliases = row.get('_aliases_norm') or {_normalize(a) for a in json.loads(row.get('aliases_json') or '[]')}
        equivalent = {_normalize(row['term']), *aliases}
        same, others, seen = [], [], {_normalize(answer)}
        own_sources = row.get('_sources_ids') or ({row['paper_id']} | {s.get('paper_id') for s in _load_sources(row)})
        own_sources = own_sources - {''}
        for other in all_rows:
            if other['id'] == row['id'] or other['kind'] != row['kind']: continue
            other_aliases = other.get('_aliases_norm') or {_normalize(other['term'])}
            other_meaning = other.get('_meaning_norm', _normalize(other.get('meaning')))
            other_expansion = other.get('_expansion_norm', _normalize(other.get('expansion')))
            if equivalent & (other_aliases | {_normalize(other['term'])}) or (meaning and other_meaning == _normalize(meaning)) or (fallback and other_expansion == row.get('_expansion_norm', _normalize(expansion))): continue
            value = (other.get('_pair') or _pair_label(other)) if field == 'term' and row['kind'] == 'abbr' and not fallback else other[field]
            value = (value or '').strip()
            key = _normalize(value)
            if not key or key in seen: continue
            seen.add(key)
            other_sources = other.get('_sources_ids') or ({other['paper_id']} | {s.get('paper_id') for s in _load_sources(other)})
            (same if (own_sources & (other_sources - {''})) else others).append(value)
        random.shuffle(same); random.shuffle(others)
        options = [answer] + (same + others)[:QUIZ_OPTIONS - 1]
        if len(options) < 2: continue
        random.shuffle(options)
        phonetic = str(row.get('phonetic') or '').strip('/ []')
        if direction == 'term2meaning' and phonetic: note = f'/{phonetic}/ · ' + note
        return {**{k: row[k] for k in ('id','term','kind','meaning','expansion','paper_id','page','pos','phonetic')}, 'direction': direction, 'prompt': prompt, 'answer': answer, 'options': options, 'note': note}
    return None


def quiz(limit: int = 20, paper_id: str = '', mode: str = 'mixed') -> list[dict]:
    mode = mode if mode in QUIZ_MODES else 'mixed'
    all_rows = _prepare_rows([dict(r) for r in db.query('SELECT * FROM terms WHERE retired=0 ORDER BY due_at,rowid')])
    items, pending = [], []
    expires = _add_days(1)
    for row in all_rows:
        if not _paper_match(row, paper_id): continue
        question = _question(row, all_rows, mode)
        if question:
            question['question_id'] = db.new_id()
            pending.append((question['question_id'], row['id'], json.dumps(question, ensure_ascii=False), expires))
            items.append(question)
            if len(items) >= max(1, min(limit, 100)): break
    if pending:
        with closing(db.connect()) as conn, conn:
            conn.executemany('INSERT INTO term_questions(id,term_id,payload,expires_at) VALUES (?,?,?,?)', pending)
    return items


def _load_history(row) -> list[dict]:
    if not isinstance(row, dict):
        row = dict(row)
    try:
        value = json.loads(str(row.get('history_json') or '[]'))
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def answer_quiz(term_id: str, correct: bool = False, unknown: bool = False,
                fuzzy: bool = False, direction: str = '', question_id: str = '',
                chosen_option: str | None = None) -> dict | None:
    """SQLite write transaction serializes read/modify/write across windows and processes."""
    with _TERMS_LOCK:
        conn = db.connect()
        try:
            conn.execute('BEGIN IMMEDIATE')
            record = conn.execute('SELECT * FROM terms WHERE id=?', (term_id,)).fetchone()
            if not record: return None
            row = dict(record)
            if question_id:
                q = conn.execute('SELECT * FROM term_questions WHERE id=? AND term_id=?', (question_id, term_id)).fetchone()
                if not q: raise ValueError('题目不存在，请重新开始复习')
                if q['result']:
                    return json.loads(q['result'])
                if q['expires_at'] < _stamp(_now()): raise ValueError('题目已过期，请重新开始复习')
                question = json.loads(q['payload'])
                if unknown and (chosen_option is not None or fuzzy): raise ValueError('不认识不能同时选择答案或模糊')
                if not unknown and chosen_option not in question['options']: raise ValueError('请选择题目中的选项')
                correct = not unknown and _normalize(chosen_option) == _normalize(question['answer'])
                direction = question['direction']
                if row['retired'] or direction not in _directions(row):
                    raise ValueError('该方向已完成或尚未到期，请重新获取复习队列')
                # An edit invalidates outstanding questions, rather than grading stale content.
                if any(row[k] != question[k] for k in ('term', 'meaning', 'expansion', 'kind')):
                    raise ValueError('词条已修改，请重新开始复习')
            else:
                if chosen_option is not None:
                    raise ValueError('选择答案时必须提供题目 ID')
                if unknown and (correct or fuzzy):
                    raise ValueError('答题状态互相矛盾')
            direction = direction or 'term2meaning'
            if direction not in ('term2meaning', 'meaning2term'): raise ValueError('无效的复习方向')
            suffix = 'en2zh' if direction == 'term2meaning' else 'zh2en'
            history = _load_history(row)
            streak = 0
            for entry in reversed(history):
                if entry.get('direction', 'term2meaning') != direction: continue
                if entry.get('correct') and not entry.get('fuzzy'): streak += 1
                else: break
            gain = (60, 25, 15, 5)[min(streak, 3)]
            current = float(row['mastery_' + suffix])
            step = int(row['step_' + suffix])
            if correct:
                current = min(99 if fuzzy else 100, current + round(gain * (0.3 if fuzzy else 1), 1))
                step = 1 if fuzzy else min(step + 1, len(STEPS)-1)
            else:
                current, step = max(0, current - 45), 0
            interval = 7 if current >= 100 else STEPS[step]
            row['mastery_' + suffix] = current
            row['step_' + suffix] = step
            row['due_' + suffix] = _add_days(interval)
            mastery = min(row['mastery_en2zh'], row['mastery_zh2en'])
            history.append({'at': db.now(), 'correct': bool(correct), 'unknown': bool(unknown), 'fuzzy': bool(fuzzy), 'direction': direction, 'mastery_after': mastery, 'mastery_en2zh': row['mastery_en2zh'], 'mastery_zh2en': row['mastery_zh2en']})
            due = min(row['due_en2zh'], row['due_zh2en'])
            conn.execute('UPDATE terms SET mastery=?,mastery_en2zh=?,mastery_zh2en=?,step=?,step_en2zh=?,step_zh2en=?,due_at=?,due_en2zh=?,due_zh2en=?,interval_days=?,reps=reps+1,asked=asked+1,correct_count=correct_count+?,fuzzy_count=fuzzy_count+?,wrong_count=wrong_count+?,lapses=lapses+?,history_json=?,reviewed_at=? WHERE id=?',
                         (mastery,row['mastery_en2zh'],row['mastery_zh2en'],step,row['step_en2zh'],row['step_zh2en'],due,row['due_en2zh'],row['due_zh2en'],round(interval,4),int(correct),int(fuzzy),int(not correct),int(not correct),json.dumps(history[-300:],ensure_ascii=False),db.now(),term_id))
            result = _public(conn.execute('SELECT * FROM terms WHERE id=?', (term_id,)).fetchone())
            if question_id:
                conn.execute('UPDATE term_questions SET result=? WHERE id=?', (json.dumps(result,ensure_ascii=False),question_id))
            conn.commit()
        finally:
            conn.close()
    schedule_dictionary_sync()
    return result


def retire_term(term_id: str) -> dict | None:
    """不再复习：词留在词典里（只增不减），只是不再出题。"""
    row = get_term(term_id)
    if not row:
        return None
    db.execute('UPDATE terms SET retired=1 WHERE id=?', (term_id,))
    schedule_dictionary_sync()
    return get_term(term_id)


def review_term(term_id: str, grade: str) -> dict | None:
    """兼容旧接口：again=答错，hard=原地复习，good=答对。"""
    row = get_term(term_id)
    if not row:
        return None
    grade = (grade or 'good').strip().lower()
    if grade not in ('again', 'hard', 'good'):
        raise ValueError('复习等级必须是 again / hard / good')
    if grade == 'good':
        return answer_quiz(term_id, True)
    if grade == 'again':
        return answer_quiz(term_id, False)
    return answer_quiz(term_id, True, fuzzy=True)


def _is_english_title(title: str) -> bool:
    text = str(title or '')
    if not re.search(r'[A-Za-z]{3,}', text):
        return False
    return not re.search(r'[\u4e00-\u9fff]', text)


def english_papers() -> list[dict]:
    rows = db.query("SELECT id, title FROM papers WHERE COALESCE(file_path,'') != '' ORDER BY updated_at DESC")
    return [dict(row) for row in rows if _is_english_title(row['title'])]


_COMMON_ABBR = {
    'SPI', 'SLM', 'NA', 'FFT', 'IFFT', 'SNR', 'PSNR', 'FWHM', 'PSF', 'OPD', 'PCI', 'MTF', 'OTF',
    'CNN', 'RNN', 'GAN', 'SVM', 'PCA', 'ICA', 'SVD', 'LDA', 'EM', 'MLE', 'MAP', 'HMM', 'CRF',
    'HDR', 'OCT', 'MRI', 'CT', 'PET', 'LED', 'CCD', 'CMOS', 'MEMS', 'DOF', 'RMS', 'TEM', 'SEM',
    'AFM', 'XRD', 'XPS', 'UV', 'IR', 'THz', 'RF', 'DC', 'AC', 'PID', 'LQR', 'MPC', 'PDE', 'ODE',
    'FEM', 'FDTD', 'FDFD', 'BPM', 'RCWA', 'SMF', 'MMF', 'AO', 'DMD', 'OAM', 'SAM', 'POI', 'FOV',
    'ROI', 'GPU', 'CPU', 'API', 'AI', 'ML', 'DL', 'NLP', 'LLM', 'SGD', 'BN', 'RL', 'DAE', 'VAE',
}
_STRONG_VENUE_WORDS = {'conference', 'proceedings', 'symposium', 'workshop', 'journal',
                       'transactions', 'expo', 'digest', 'letters', 'doi', 'vol', 'pp',
                       'foundation', 'university', 'institute', 'college', 'laboratory'}


def _bad_expansion(expansion: str) -> bool:
    if not re.fullmatch(r'[A-Za-z0-9\-\u00b7 ]+', expansion):
        return True
    words = expansion.split()
    lowered = [word.lower() for word in words]
    if any(word in _SENTENCE_WORDS for word in lowered):
        return True
    if len(words) <= 1 and (not words or words[0].lower() in _GENERIC_WORDS):
        return True
    if len(words) >= 2 and len(words[-1]) <= 2 and words[-1][:1].islower() and words[-1].lower() not in {'of', 'in', 'on', 'de', 'la', 'al'}:
        return True
    return False


def _is_venue_expansion(expansion: str) -> bool:
    words = {word.strip('.,;:()').lower() for word in expansion.split()}
    if not words:
        return True
    if words & _STRONG_VENUE_WORDS:
        return True
    return len(words & _VENUE_WORDS) >= 2


def _looks_like_references(text: str) -> bool:
    lowered = text.lower()
    if lowered.count('et al') >= 2 or lowered.count('doi:') >= 2:
        return True
    return len(re.findall(r'\[\d{1,3}\]', text)) >= 6


def _iter_abbr_matches(paper_id: str, max_page: int = 5):
    rows = db.query('SELECT page, text FROM chunks WHERE paper_id=? AND page <= ? ORDER BY page ASC, id ASC',
                    (paper_id, int(max_page)))
    for row in rows:
        chunk_text = str(row['text'] or '')
        if _looks_like_references(chunk_text):
            continue
        for match in _ABBR_RE.finditer(chunk_text):
            expansion = ' '.join(match.group(1).split()).strip(' -')
            words = expansion.split()
            while words and words[0].lower() in _LEAD_STOPWORDS:
                words.pop(0)
            expansion = ' '.join(words)
            abbr = match.group(2).strip()
            if abbr in _SKIP_ABBR or len(words) < 1 or len(words) > 7 or len(abbr) < 2:
                continue
            if _is_venue_expansion(expansion) or _bad_expansion(expansion):
                continue
            if re.fullmatch(r'[A-Z]{1,2}\d+', abbr) or re.search(r'\d', abbr):
                continue
            yield int(row['page'] or 0), abbr, expansion


def extract_abbreviations_all(per_paper: int = 20, total_limit: int = 400,
                              max_page: int = 5) -> int:
    """从全部英文文献里提取「通用」缩写：只收跨论文出现≥2次或常见缩写，跨论文去重。"""
    seen = {str(row['term']).lower() for row in db.query('SELECT term FROM terms')}
    papers = english_papers()
    # 第一遍：统计每个缩写在多少篇不同论文里出现
    usage: dict[str, set[str]] = {}
    for paper in papers:
        for _, abbr, _expansion in _iter_abbr_matches(paper['id'], max_page):
            usage.setdefault(abbr.lower(), set()).add(paper['id'])
    added = 0
    for paper in papers:
        if added >= total_limit:
            break
        paper_added = 0
        for page, abbr, expansion in _iter_abbr_matches(paper['id'], max_page):
            if paper_added >= per_paper or added >= total_limit:
                break
            key = abbr.lower()
            general = len(usage.get(key, set())) >= 2 or abbr.upper() in _COMMON_ABBR
            if not general:
                continue  # 只在这篇文章里出现的自造缩写，不收
            if add_term(abbr, expansion=expansion, paper_id=paper['id'], page=page,
                        source='regex', sync=False, kind='abbr'):
                seen.add(key)
                added += 1
                paper_added += 1
    schedule_dictionary_sync()
    fill_missing_meanings()
    return added


_AI_JOB = {
    'running': False, 'total': 0, 'done': 0, 'added': 0, 'errors': 0,
    'current': '', 'started_at': '', 'cancel': False, 'job_id': '',
}


def ai_job_status() -> dict:
    with _AI_JOB_LOCK:
        return dict(_AI_JOB)


def cancel_ai_job() -> dict:
    with _AI_JOB_LOCK:
        if _AI_JOB['running']:
            _AI_JOB['cancel'] = True
    return ai_job_status()


def _run_ai_extract_all(job_id: str) -> None:
    try:
        papers = [paper for paper in english_papers()
                  if db.query_one('SELECT id FROM chunks WHERE paper_id=? LIMIT 1', (paper['id'],))]
        with _AI_JOB_LOCK:
            if _AI_JOB['job_id'] != job_id:
                return
            _AI_JOB['total'] = len(papers)
        for paper in papers:
            with _AI_JOB_LOCK:
                if _AI_JOB['job_id'] != job_id or _AI_JOB['cancel']:
                    break
                _AI_JOB['current'] = str(paper.get('title') or '')[:40]
            try:
                count = ai_extract_terms(paper['id'], limit=25)
            except Exception:  # noqa: BLE001 - 单篇失败继续下一篇
                count = 0
                with _AI_JOB_LOCK:
                    _AI_JOB['errors'] = int(_AI_JOB['errors']) + 1
            with _AI_JOB_LOCK:
                if _AI_JOB['job_id'] != job_id:
                    return
                _AI_JOB['done'] = int(_AI_JOB['done']) + 1
                _AI_JOB['added'] = int(_AI_JOB['added']) + int(count or 0)
        try:
            fill_missing_meanings(limit=200)
        except Exception:  # noqa: BLE001
            pass
        try:
            schedule_dictionary_sync()
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001
        with _AI_JOB_LOCK:
            _AI_JOB['errors'] = int(_AI_JOB['errors']) + 1
    finally:
        with _AI_JOB_LOCK:
            if _AI_JOB['job_id'] == job_id:
                _AI_JOB['running'] = False
                _AI_JOB['current'] = ''
                _AI_JOB['cancel'] = False


def start_ai_extract_all() -> dict:
    with _AI_JOB_LOCK:
        if _AI_JOB['running']:
            raise ValueError('AI 抽取正在进行中')
        job_id = db.new_id()
        _AI_JOB.update(running=True, total=0, done=0, added=0, errors=0,
                       current='', started_at=db.now(), cancel=False, job_id=job_id)
    threading.Thread(target=_run_ai_extract_all, args=(job_id,), daemon=True).start()
    return ai_job_status()


def ai_extract_terms_all(max_papers: int | None = None, per_paper: int = 25) -> int:
    """对最近更新的英文文献逐篇做 AI 抽取（篇数和条数都限流，控制成本）。"""
    seen = {str(row['term']).lower() for row in db.query('SELECT term FROM terms')}
    added = 0
    used = 0
    for paper in english_papers():
        if max_papers is not None and used >= max_papers:
            break
        rows = db.query('SELECT id FROM chunks WHERE paper_id=? LIMIT 1', (paper['id'],))
        if not rows:
            continue
        try:
            count = ai_extract_terms(paper['id'], limit=per_paper)
        except Exception:  # noqa: BLE001 - 单篇失败不影响整体
            continue
        used += 1
        added += count
        for row in db.query('SELECT term FROM terms WHERE paper_id=?', (paper['id'],)):
            seen.add(str(row['term']).lower())
    schedule_dictionary_sync()
    return added


def fill_missing_meanings(limit: int = 80) -> int:
    """用一次模型调用给缺中文释义的缩写补释义（便于背中文）。"""
    if not llm.is_configured() and not llm.is_mock():
        return 0
    rows = db.query(
        "SELECT id, term, expansion FROM terms WHERE meaning = '' AND expansion != '' LIMIT ?", (int(limit),)
    )
    if not rows:
        return 0
    payload = [{'i': index, 'term': row['term'], 'expansion': row['expansion']} for index, row in enumerate(rows)]
    prompt = (
        '给下面这些学术缩写的全称配上简洁中文名（每个 12 字内，学术通用译名）。'
        '只输出 JSON：{"items":[{"i":0,"meaning":"中文名"}]}，不要解释。\n'
        + json.dumps(payload, ensure_ascii=False)
    )
    try:
        data = llm.chat_json('只输出严格 JSON。', prompt, thinking=False)
    except Exception:  # noqa: BLE001 - 补释义失败不影响提取
        return 0
    items = data.get('items') if isinstance(data, dict) else []
    updated = 0
    for item in items or []:
        try:
            index = int(item.get('i'))
            meaning = ' '.join(str(item.get('meaning') or '').split())[:120]
        except (TypeError, ValueError):
            continue
        if 0 <= index < len(rows) and meaning:
            db.execute('UPDATE terms SET meaning=? WHERE id=?', (meaning, rows[index]['id']))
            updated += 1
    if updated:
        schedule_dictionary_sync()
    return updated


def extract_abbreviations(paper_id: str, limit: int = 60) -> int:
    added = 0
    for page, abbr, expansion in _iter_abbr_matches(paper_id, 100000):
        if add_term(abbr, expansion=expansion, paper_id=paper_id, page=page, source='regex', sync=False, kind='abbr'):
            added += 1
        if added >= limit: break
    schedule_dictionary_sync()
    fill_missing_meanings()
    return added


def _safe_page(value):
    try: return max(0, int(value or 0))
    except (ValueError, TypeError): return 0


def ai_extract_terms(paper_id: str, limit: int = 25) -> int:
    """让模型从论文里挑出关键术语/缩写并给中文释义（一次调用）。"""
    if not llm.is_configured() and not llm.is_mock():
        raise llm.LLMError(llm.NOT_CONFIGURED_MESSAGE)
    paper = db.get_paper(paper_id)
    if not paper:
        raise ValueError('论文不存在')
    rows = db.query('SELECT page, text FROM chunks WHERE paper_id=? ORDER BY page ASC, id ASC LIMIT 12', (paper_id,))
    body = '\n'.join(f'[第 {row["page"]} 页] {str(row["text"] or "")[:1200]}' for row in rows)
    prompt = (
        '你是学术词汇整理助手。从下面的论文原文里挑出 10-20 个「通用」学术词汇，分两类：\n'
        '1) 通用缩写（例：SPI = single-pixel imaging、SLM、FWHM），只收本领域读者普遍认识的缩写，'
        '不要收这篇文章自己临时定义的、带数字或连字符的自造缩写；\n'
        '2) 一般学术名词或动词（例：retardation、dispersion、fabricate），给英文、中文释义、'
        '音标（IPA，没有就留空）、词性（n./v./adj./adv.）。\n'
        '每个词给出：term、kind（abbreviation 或 word）、expansion（缩写才需要，单词留空）、'
        'meaning（中文释义，20 字内）、phonetic（单词才需要）、pos（单词才需要）、page（页码，没有就 0）。'
        '只输出 JSON：{"items":[{"term":"","kind":"word","expansion":"","meaning":"","phonetic":"","pos":"","page":1}]}，'
        '不要输出其它文字。\n\n'
        f'论文标题：{paper.get("title") or ""}\n\n原文节选：\n{body}'
    )
    data = llm.chat_json('只输出严格的 JSON 对象，不要解释。', prompt, thinking=False)
    items = data.get('items') if isinstance(data, dict) else []
    added = 0
    for item in items or []:
        if not isinstance(item, dict):
            continue
        term = str(item.get('term') or '').strip()
        if not term:
            continue
        raw_kind = str(item.get('kind') or '').lower()
        kind = 'abbr' if raw_kind.startswith('abbr') or raw_kind in {'缩写', 'acronym'} else 'word'
        created = add_term(
            term, expansion=str(item.get('expansion') or ''), meaning=str(item.get('meaning') or ''),
            paper_id=paper_id, page=_safe_page(item.get('page')), source='ai', sync=False,
            kind=kind, phonetic=str(item.get('phonetic') or ''), pos=str(item.get('pos') or ''),
        )
        if created:
            added += 1
        if added >= limit:
            break
    schedule_dictionary_sync()
    return added
