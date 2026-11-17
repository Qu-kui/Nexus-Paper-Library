"""Bounded public scholarly metadata searches; no model-generated bibliography."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from datetime import date
from itertools import zip_longest
import re
import threading
import time
import unicodedata
from urllib.parse import quote, urlsplit

import httpx

from . import ai_progress, metadata

CROSSREF_API = 'https://api.crossref.org/works'
OPENALEX_API = 'https://api.openalex.org/works'
ARXIV_API = 'https://export.arxiv.org/api/query'
EUROPEPMC_API = 'https://www.ebi.ac.uk/europepmc/webservices/rest/search'
TIMEOUT = 12.0
SEARCH_BUDGET = 38.0
MAX_ITEMS = 30
_arxiv_lock = threading.Lock()
_arxiv_last_call = 0.0


def safe_url(value) -> str:
    text = str(value or '').strip()
    try:
        parsed = urlsplit(text)
        if (parsed.scheme.lower() in ('http', 'https') and parsed.hostname
                and parsed.username is None and parsed.password is None
                and not re.search(r'[\s<>\x00-\x1f]', text)):
            return text
    except ValueError:
        pass
    return ''


def _date(value) -> str:
    text = str(value or '')[:10]
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError:
        return ''


def _crossref_date(work: dict) -> str:
    for key in ('published-online', 'published', 'published-print', 'issued', 'posted'):
        parts = (work.get(key) or {}).get('date-parts') or []
        if not parts or not isinstance(parts[0], list):
            continue
        values = parts[0]
        if len(values) >= 3:
            parsed = _date('-'.join(str(v).zfill(2) for v in values[:3]))
            if parsed:
                return parsed
    return ''


def _normalise(raw: dict, source: str, source_url='') -> dict | None:
    cleaned = metadata.clean_meta(raw)
    title = cleaned.get('title', '')[:1000]
    if not title:
        return None
    doi = metadata.normalize_doi(cleaned.get('doi')).lower()
    arxiv_id = metadata.normalize_arxiv_id(cleaned.get('arxiv_id')).lower()
    url = safe_url(cleaned.get('url'))
    if not url and doi:
        url = 'https://doi.org/'+quote(doi, safe='/():;._-')
    if not url and arxiv_id:
        url = 'https://arxiv.org/abs/'+arxiv_id
    if not url:
        return None
    return {
        'title':title, 'authors':cleaned.get('authors', '')[:3000],
        'year':cleaned.get('year', '')[:4], 'published':_date(raw.get('published')),
        'venue':cleaned.get('venue', '')[:500], 'doi':doi, 'arxiv_id':arxiv_id,
        'url':url, 'abstract':cleaned.get('abstract', '')[:6000],
        'sources':[{'name':source, 'url':safe_url(source_url) or url}],
        'reason':'', 'stage':'',
    }


def _get(url, params, *, timeout=TIMEOUT):
    response = metadata._get(url, params=params, timeout=timeout)
    response.raise_for_status()
    return response


def source_warning(name, error):
    status = error.response.status_code if isinstance(error, httpx.HTTPStatusError) else None
    if status in (401, 403) and name.startswith('OpenAlex'):
        return f'{name} 访问受限（HTTP {status}）。可在设置的「PDF 来源」检查 OpenAlex Key 后重试。'
    if status == 429:
        return f'{name} 暂时限流或额度已用完（HTTP 429），稍后可重试。'
    if isinstance(error, httpx.TimeoutException) or isinstance(error.__cause__, httpx.TimeoutException):
        return f'{name} 查询超时，可重试。'
    return f'{name} 查询失败' + (f'（HTTP {status}）。' if status else '，可重试。')


SOURCE_ERRORS = (httpx.HTTPError, ValueError, TypeError, KeyError, AttributeError, metadata.MetadataError)


def title_key(value):
    return re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', str(value or '')).casefold())


def resolve_seed(reference):
    """Verify the original independently of the follow-up date filters."""
    doi = metadata.normalize_doi(reference)
    arxiv_id = metadata.normalize_arxiv_id(reference) if re.match(r'^(?:arxiv:|https?://(?:www\.)?arxiv\.org/)', reference, re.I) else ''
    if doi:
        work = _get(f'{CROSSREF_API}/{quote(doi, safe="/")}', {}, timeout=8).json()['message']
        raw = metadata._crossref_message_to_meta(work)
        # Online publication is the appropriate starting point for a journal article.
        raw['published'] = _crossref_date({'published-online': work.get('published-online')}) or _crossref_date(work)
        row = _normalise(raw, 'Crossref')
        return ([row], []) if row and row['doi'] == doi.lower() else ([], [])
    if arxiv_id:
        _wait_arxiv_slot()
        raw = metadata.arxiv_meta(arxiv_id)
        row = _normalise(raw, 'arXiv')
        return ([row], []) if row else ([], [])
    rows = search_crossref(reference)
    exact = [row for row in rows if title_key(row['title']) == title_key(reference)]
    return deduplicate(exact)[:4], ([] if exact else rows[:4])


def preprint_date(seed):
    if seed.get('arxiv_id'):
        return seed.get('published', '')
    first_author = title_key(seed.get('authors', '').split(';', 1)[0])
    if not first_author:
        return ''
    for row in search_arxiv(seed['title']):
        if (title_key(row['title']) == title_key(seed['title'])
                and title_key(row.get('authors', '').split(';', 1)[0]) == first_author):
            return row['published']
    return ''


def search_crossref(query: str, date_from='', date_to='', *, latest=False) -> list[dict]:
    params = {'query':query, 'rows':12, 'sort':'published' if latest else 'relevance', 'order':'desc'}
    filters = []
    if date_from:
        filters.append('from-pub-date:'+date_from)
    if date_to:
        filters.append('until-pub-date:'+date_to)
    if filters:
        params['filter'] = ','.join(filters)
    data = _get(CROSSREF_API, params).json()
    result = []
    for work in (data.get('message') or {}).get('items', [])[:12]:
        raw = metadata._crossref_message_to_meta(work)
        raw['published'] = _crossref_date(work)
        item = _normalise(raw, 'Crossref')
        if item:
            result.append(item)
    return result


def _abstract(index) -> str:
    if not isinstance(index, dict):
        return ''
    words = {}
    for word, positions in list(index.items())[:3000]:
        if not isinstance(positions, list):
            continue
        for position in positions[:100]:
            if isinstance(position, int) and 0 <= position < 6000:
                words[position] = str(word)
    return ' '.join(words[k] for k in sorted(words))


def search_openalex(query: str, date_from='', date_to='', *, latest=False) -> list[dict]:
    params = {'search':query, 'per-page':12}
    filters = []
    if date_from:
        filters.append('from_publication_date:'+date_from)
    if date_to:
        filters.append('to_publication_date:'+date_to)
    if filters:
        params['filter'] = ','.join(filters)
    if latest:
        params['sort'] = 'publication_date:desc'
    data = _get(OPENALEX_API, params).json()
    return _openalex_rows(data)


def _openalex_id(value) -> str:
    """Accept only work IDs from the returned API records, never arbitrary URLs."""
    text = str(value or '')
    if text.startswith('https://openalex.org/'):
        text = text[len('https://openalex.org/'):]
    return text if re.fullmatch(r'W\d+', text) else ''


def _openalex_rows(data) -> list[dict]:
    works = data.get('results', [])
    if not isinstance(works, list):
        raise ValueError('Invalid OpenAlex results')
    result = []
    for work in works[:12]:
        if not isinstance(work, dict):
            continue
        try:
            raw = metadata._openalex_work_to_meta(work)
            raw['published'] = work.get('publication_date')
            raw['abstract'] = _abstract(work.get('abstract_inverted_index'))
            location = work.get('primary_location') or {}
            raw['url'] = (safe_url(raw.get('url')) or safe_url(location.get('landing_page_url'))
                          or safe_url(work.get('id')))
            item = _normalise(raw, 'OpenAlex', work.get('id'))
        except (ValueError, TypeError, AttributeError, KeyError):
            continue
        if item:
            item['_openalex_id'] = _openalex_id(work.get('id'))
            references = work.get('referenced_works')
            item['_referenced_works'] = list(dict.fromkeys(
                identifier for value in references[:200]
                if (identifier := _openalex_id(value)))) if isinstance(references, list) else []
            result.append(item)
    return result


def search_europepmc(query: str, date_from='', date_to='', *, latest=False) -> list[dict]:
    # Restrict model-generated keywords to words; source operators are ours alone.
    terms = re.findall(r'[\w\-]+', query, re.UNICODE)[:30]
    if not terms:
        return []
    expression = '('+' '.join(terms)+')'
    if date_from or date_to:
        expression += f' AND FIRST_PDATE:[{date_from or "1900-01-01"} TO {date_to or "3000-12-31"}]'
    if latest:
        expression += ' sort_date:y'
    data = _get(EUROPEPMC_API, {'query':expression,'resultType':'core','format':'json','pageSize':12}).json()
    works = (data.get('resultList') or {}).get('result', [])
    if not isinstance(works, list):
        raise ValueError('Invalid Europe PMC results')
    result = []
    for work in works[:12]:
        if not isinstance(work, dict):
            continue
        identifier, source = str(work.get('id') or ''), str(work.get('source') or '')
        # Stable source identifiers make canonical, source-backed landing pages.
        source_url = ('https://europepmc.org/article/'+source+'/'+quote(identifier, safe='')
                      if re.fullmatch(r'[A-Z]{2,8}', source) and re.fullmatch(r'[\w.\-]+', identifier) else '')
        journal = work.get('journalInfo')
        journal = journal.get('journal') if isinstance(journal, dict) else {}
        raw = {'title':work.get('title'), 'authors':work.get('authorString'),
               'year':work.get('pubYear'), 'published':work.get('firstPublicationDate'),
               'doi':work.get('doi'), 'abstract':work.get('abstractText'),
               'venue':journal.get('title') if isinstance(journal, dict) else '', 'url':source_url}
        item = _normalise(raw, 'Europe PMC', source_url)
        if item:
            result.append(item)
    return result


def _citation_jobs(rows, date_from, date_to, deadline):
    # One genuine returned seed bounds graph breadth and keeps calls reproducible.
    seed = next((row for row in rows if _openalex_id(row.get('_openalex_id'))), None)
    if not seed:
        return []
    seed_id = _openalex_id(seed['_openalex_id'])
    references = list(dict.fromkeys(identifier for value in (seed.get('_referenced_works') or [])[:200]
                                    if (identifier := _openalex_id(value))))
    jobs = []
    if references:
        jobs.append(('OpenAlex 后向参考文献', 'openalex:'+'|'.join(references[:12]), seed_id, 'referenced_works'))
    jobs.append(('OpenAlex 前向引用', 'cites:'+seed_id, seed_id, 'cites_filter'))
    return [(name, _search_citations, (expression, seed_id, evidence, references[:12], date_from, date_to, deadline))
            for name, expression, seed_id, evidence in jobs]


def _search_citations(expression, seed_id, evidence, references, date_from, date_to, deadline):
    filters = [expression]
    if date_from:
        filters.append('from_publication_date:'+date_from)
    if date_to:
        filters.append('to_publication_date:'+date_to)
    remaining = deadline-time.monotonic()
    if remaining <= 0:
        raise httpx.TimeoutException('Discovery deadline reached')
    rows = _openalex_rows(_get(OPENALEX_API, {'filter':','.join(filters),'per-page':12},
                              timeout=min(TIMEOUT, remaining)).json())
    result = []
    for row in rows:
        identifier = row['_openalex_id']
        if not identifier or identifier == seed_id:
            continue
        if evidence == 'referenced_works' and identifier not in references:
            continue
        citing, cited = (seed_id, identifier) if evidence == 'referenced_works' else (identifier, seed_id)
        row['citation_graph'] = [{'source':'OpenAlex', 'citing':'https://openalex.org/'+citing,
                                  'cited':'https://openalex.org/'+cited, 'evidence':evidence}]
        result.append(row)
    return result


def _wait_arxiv_slot():
    """Serialize live arXiv request starts, including concurrent discovery runs."""
    global _arxiv_last_call
    with _arxiv_lock:
        remaining = 3.0-(time.monotonic()-_arxiv_last_call)
        if remaining > 0:
            time.sleep(remaining)
        _arxiv_last_call = time.monotonic()


def search_arxiv(query: str, date_from='', date_to='', *, latest=False) -> list[dict]:
    # Treat natural-language plans as words, never as untrusted arXiv operators.
    terms = re.findall(r'[\w\-]+', query, re.UNICODE)[:20]
    expression = ' AND '.join('all:'+term for term in terms)
    if not expression:
        return []
    if date_from or date_to:
        lower = (date_from or '1900-01-01').replace('-', '')+'0000'
        upper = (date_to or date.today().isoformat()).replace('-', '')+'2359'
        expression += f' AND submittedDate:[{lower} TO {upper}]'
    params = {'search_query':expression, 'start':0, 'max_results':12,
              'sortBy':'submittedDate' if latest else 'relevance', 'sortOrder':'descending'}
    _wait_arxiv_slot()
    data = _get(ARXIV_API, params).text
    result = []
    for raw in metadata.parse_arxiv_feed(data)[:12]:
        item = _normalise(raw, 'arXiv')
        if item:
            result.append(item)
    return result


def _keys(item):
    doi = metadata.normalize_doi(item.get('doi')).lower()
    arxiv_id = metadata.normalize_arxiv_id(item.get('arxiv_id')).lower()
    title = re.sub(r'[^\w]', '', unicodedata.normalize('NFKC', item.get('title', '')).casefold())
    return {kind+value for kind, value in [('doi:',doi), ('arxiv:',arxiv_id), ('title:',title)] if value}


def deduplicate(items: list[dict]) -> list[dict]:
    """Merge transitive identifier/title matches, retaining real provenance."""
    groups: list[tuple[set, dict]] = []
    for original in items:
        item = dict(original)
        item['doi'] = metadata.normalize_doi(item.get('doi')).lower()
        item['arxiv_id'] = metadata.normalize_arxiv_id(item.get('arxiv_id')).lower()
        item['sources'] = [dict(s) for s in item.get('sources', []) if safe_url(s.get('url'))]
        for key in ('_referenced_works', 'citation_graph'):
            if isinstance(item.get(key), list):
                item[key] = list(item[key])
        keys = _keys(item)
        matches = []
        combined_keys = set(keys)
        for group in groups:
            if _same_record(keys, group[0]) and _same_record(combined_keys, group[0]):
                matches.append(group)
                combined_keys.update(group[0])
        if not matches:
            groups.append((keys, item))
            continue
        base_keys, base = matches[0]
        for merged_keys, merged in matches[1:]+[(keys,item)]:
            base_keys.update(merged_keys)
            for key in ('authors','year','published','venue','doi','arxiv_id','url','abstract','_openalex_id'):
                if not base.get(key) and merged.get(key):
                    base[key] = merged[key]
            for source in merged.get('sources', []):
                if source not in base['sources']:
                    base['sources'].append(source)
            for key in ('_referenced_works', 'citation_graph'):
                for value in merged.get(key, []):
                    if value not in base.setdefault(key, []):
                        base[key].append(value)
            if merged.get('relation') == 'direct':
                base['relation'] = 'direct'
        for group in matches[1:]:
            groups.remove(group)
    return [item for _, item in groups]


def _same_record(left, right):
    common = left & right
    if any(key.startswith(('doi:', 'arxiv:')) for key in common):
        return True
    left_dois = {key for key in left if key.startswith('doi:')}
    right_dois = {key for key in right if key.startswith('doi:')}
    if left_dois and right_dois and not left_dois & right_dois:
        return False
    return any(key.startswith('title:') for key in common)


def _in_range(item, lower, upper):
    published = _date(item.get('published'))
    if published:
        return (not lower or published >= lower) and (not upper or published <= upper)
    year = str(item.get('year') or '')
    if re.fullmatch(r'\d{4}', year):
        return (not lower or year >= lower[:4]) and (not upper or year <= upper[:4])
    return not (lower or upper)


def _timeline_sample(rows):
    known = sorted((row for row in rows if row.get('published') or row.get('year')),
                   key=lambda row: row.get('published') or row.get('year'))
    unknown = [row for row in rows if not (row.get('published') or row.get('year'))]
    # Leave a small place for relevant undated work while preserving both dated endpoints.
    count = min(len(known), max(2, MAX_ITEMS-min(3,len(unknown))))
    if count and len(known) > count:
        known = [known[round(index*(len(known)-1)/(count-1))] for index in range(count)]
    return (known+unknown[:MAX_ITEMS-len(known)])[:MAX_ITEMS]


def _search_forward(seed, date_from, date_to):
    doi = seed.get('doi') or ('10.48550/arxiv.'+seed['arxiv_id'] if seed.get('arxiv_id') else '')
    if not doi:
        raise ValueError('No persistent identifier')
    work = _get(f'{OPENALEX_API}/https://doi.org/{quote(doi, safe="/")}', {}, timeout=8).json()
    seed_id = _openalex_id(work.get('id'))
    if not seed_id or metadata.normalize_doi(work.get('doi')).lower() != doi.lower():
        raise ValueError('Seed not verified in OpenAlex')
    rows = _search_citations('cites:'+seed_id, seed_id, 'cites_filter', [], date_from, date_to, time.monotonic()+TIMEOUT)
    for row in rows:
        row['relation'] = 'direct'
    return rows


def collect(queries: list[str], date_from='', date_to='', *, latest=False, timeline=False, seed=None) -> tuple[list[dict], list[str]]:
    """At most eight searches plus two timeline graph calls within one deadline."""
    from .. import llm
    queries = list(dict.fromkeys(queries))[:3]
    jobs = []
    for query in queries:
        jobs.extend([('Crossref', search_crossref, query), ('OpenAlex', search_openalex, query)])
    if queries:
        jobs.append(('arXiv', search_arxiv, queries[0]))
        jobs.append(('Europe PMC', search_europepmc, queries[0]))
    executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix='discovery-source')
    pending = {}
    results = {}
    warnings = []
    deadline = time.monotonic()+SEARCH_BUDGET

    def drain():
        while pending:
            if ai_progress.cancelled():
                raise llm.LLMError('检索已停止')
            if time.monotonic() >= deadline:
                if any(name == 'OpenAlex 直接引用' for _, name in pending.values()):
                    warnings.append('OpenAlex 直接引用未在本轮时限内返回，可单独重试。')
                warnings.append('检索时间已到，部分来源未返回；本轮结果不代表完整覆盖。')
                break
            done, _ = wait(pending, timeout=0.2, return_when=FIRST_COMPLETED)
            for future in done:
                index, name = pending.pop(future)
                try:
                    results[index] = future.result()
                    ai_progress.emit(stage=f'{name} 已返回 {len(results[index])} 条元数据')
                except SOURCE_ERRORS as exc:
                    warnings.append(source_warning(name, exc))
        if ai_progress.cancelled():
            raise llm.LLMError('检索已停止')

    try:
        if seed:
            pending[executor.submit(_search_forward, seed, date_from, date_to)] = (-1, 'OpenAlex 直接引用')
        for index, (name, function, query) in enumerate(jobs):
            pending[executor.submit(function, query, date_from, date_to, latest=latest)] = (index, name)
        drain()
        if timeline and not pending and time.monotonic() < deadline:
            seeds = [row for index in sorted(results) for row in results[index]
                     if _in_range(row, date_from, date_to)]
            graph_jobs = _citation_jobs(seeds, date_from, date_to, deadline)
            for index, (name, function, args) in enumerate(graph_jobs, start=len(jobs)):
                pending[executor.submit(function, *args)] = (index, name)
            if graph_jobs:
                ai_progress.emit(stage='正在核对 OpenAlex 返回种子的参考文献与后续引用')
                drain()
        # Interleave ranked lists so an early provider/query cannot fill the entire cap.
        ranked_lists = [results[index] for index in sorted(results)]
        rows = [row for rank in zip_longest(*ranked_lists) for row in rank if row is not None]
        rows = deduplicate(rows)
        if seed:
            rows = [row for row in rows if title_key(row['title']) != title_key(seed['title'])
                    and not (seed.get('doi') and row.get('doi') == seed['doi'])]
            rows.sort(key=lambda row: row.get('relation') != 'direct')
            if date_from:
                if any(not row.get('published') and row.get('year') == date_from[:4] for row in rows):
                    warnings.append('部分候选只标了年份，无法确认是否晚于原文，已排除。')
                rows = [row for row in rows if row.get('published') or row.get('year','') > date_from[:4]]
        if (date_from or date_to) and any(not _date(row.get('published')) and not re.fullmatch(r'\d{4}',str(row.get('year') or '')) for row in rows):
            warnings.append('已排除未提供日期与年份的记录，无法确认其是否属于本轮日期范围。')
        rows = [row for row in rows if _in_range(row, date_from, date_to)]
        if latest:
            rows.sort(key=lambda r: (r.get('published') or r.get('year') or ''), reverse=True)
        rows = _timeline_sample(rows) if timeline else rows[:MAX_ITEMS]
        for row in rows:
            row.pop('_openalex_id', None)
            row.pop('_referenced_works', None)
        if (date_from or date_to) and any(not r.get('published') for r in rows):
            warnings.append('部分来源仅提供年份或未提供日期，无法确认其精确发表日期。')
        return rows, list(dict.fromkeys(warnings))
    finally:
        for future in pending:
            future.cancel()
        # Running requests have their own short timeout; stopping need not wait for them.
        executor.shutdown(wait=False, cancel_futures=True)
