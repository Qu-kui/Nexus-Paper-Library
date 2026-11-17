"""后台补齐 PDF：给导入的元数据条目自动找并下载全文。

策略：arXiv id → DOI（OpenAlex/Crossref）→ 标题在 arXiv 精确匹配。
下载后交给 ingest.attach_pdf 归档并建索引；失败只记录，不影响原条目。
"""
from __future__ import annotations

import os
import json
import time
import hashlib
import queue
import re
import threading
from pathlib import Path
from urllib.parse import quote, urlsplit

import httpx

from .. import config, db
from . import browser_cookies, discover, ingest, pdf_download, pdf_trace, metadata as metadata_service
from . import batches
from .activity import serialized,tracked


def sanitize_browser_attempts(attempts, *, allow_validation=False):
    """Accept only bounded, credential-free browser diagnostics."""
    clean=[]
    codes=set(pdf_trace.MESSAGES) | {'ok','http_error'}
    for entry in (attempts or [])[:24]:
        if not isinstance(entry,dict):
            continue
        code=entry.get('code')
        route=entry.get('route')
        routes=('browser-page','browser-extension','browser-validation') if allow_validation else ('browser-page','browser-extension')
        if not isinstance(code,str) or code not in codes or route not in routes:
            continue
        raw=entry.get('url')
        url=''
        if isinstance(raw,str) and len(raw)<=4096 and not any(ord(char)<32 or ord(char)==127 for char in raw):
            candidate=pdf_trace.safe_url(raw)
            if urlsplit(candidate).scheme in ('http','https') and urlsplit(candidate).hostname:
                url=candidate
        status=entry.get('status')
        if type(status) is not int or not 100<=status<=599:
            status=None
        clean.append({'url':url,'code':code,'route':route,'status':status,
                      'message':pdf_trace.MESSAGES.get(code,'已取得结果' if code=='ok' else '网站返回了 HTTP 错误')})
    return clean


class PdfFetchManager:
    def __init__(self, workers: int=1):
        self._queue=queue.Queue()
        self._lock=threading.Lock()
        self._queued=set()
        self._inflight=set()
        self._running=False
        self._workers=max(1,workers)
        self._paper_batches={}
        self._retries={}

    def _ensure_workers(self):
        with self._lock:
            if self._running:
                return
            self._running=True
            for index in range(self._workers):
                threading.Thread(target=self._worker,name=f'pdf-fetch-{index}',daemon=True).start()

    @serialized
    def start(self, paper_ids=None, all_missing=False):
        ids=list(paper_ids or [])
        if all_missing:
            for row in db.query('SELECT id,file_path FROM papers'):
                if not row['file_path'] or not config.resolve_pdf_path(row['file_path']).is_file():
                    ids.append(row['id'])
        fresh=[]
        with self._lock:
            for pid in dict.fromkeys(ids):
                if not pid or pid in self._queued or pid in self._inflight or not db.get_paper(pid):
                    continue
                db.execute("INSERT INTO pdf_fetch_jobs(paper_id,state,updated_at) VALUES(?,'queued',?) ON CONFLICT(paper_id) DO UPDATE SET state='queued',updated_at=excluded.updated_at",(pid,db.now()))
                self._queued.add(pid)
                self._retries.pop(pid,None)
                fresh.append(pid)
            batch_id=batches.create('pdf',fresh)
            for pid in fresh:
                self._paper_batches[pid]=batch_id
        for pid in fresh:
            self._queue.put(pid)
        if fresh:
            browser_cookies.invalidate_cache()
            self._ensure_workers()
        return len(fresh)

    def resume(self):
        batches.interrupt_unfinished('pdf')
        rows=db.query("SELECT paper_id FROM pdf_fetch_jobs WHERE state IN ('queued','running')")
        return self.start([row['paper_id'] for row in rows])

    def report(self,pid):
        available=bool((db.get_paper(pid) or {}).get('has_pdf'))
        row=db.query_one('SELECT * FROM pdf_fetch_jobs WHERE paper_id=?',(pid,))
        if not row:
            return {'paper_id':pid,'state':'not_attempted','attempts':[],'browser_attempts':[],'pdf_available':available}
        report=json.loads(row['report'])
        return {'attempts':[],**report,'browser_attempts':sanitize_browser_attempts(report.get('browser_attempts',[]),allow_validation=True),
                'paper_id':pid,'state':row['state'],'updated_at':row['updated_at'],'pdf_available':available}

    def record_browser_attempts(self,pid,attempts,has_pdf=False):
        attempts=sanitize_browser_attempts(attempts,allow_validation=True)
        if not attempts:
            return
        # Merge inside SQLite so workers cannot overwrite a concurrently received browser report.
        report=json.dumps({'browser_attempts':attempts},ensure_ascii=False)
        db.execute("""INSERT INTO pdf_fetch_jobs(paper_id,state,report,updated_at)
                      SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM papers WHERE id=?)
                      ON CONFLICT(paper_id) DO UPDATE SET
                      report=json_set(pdf_fetch_jobs.report,'$.browser_attempts',json_extract(excluded.report,'$.browser_attempts')),
                      updated_at=excluded.updated_at""",
                   (pid,'skipped' if has_pdf else 'failed',report,db.now(),pid))

    def status(self):
        return batches.status(batches.current('pdf'))

    def _worker(self):
        while True:
            pid=self._queue.get()
            try:
                result=self.fetch_one(pid,refresh_session=False)
                self._schedule_retry(pid,result)
            except Exception:
                self._save(pid,'failed',pdf_trace.Trace(),code='index_failed',error=pdf_trace.MESSAGES['index_failed'])
            finally:
                with self._lock:
                    self._queued.discard(pid)
                self._queue.task_done()

    # 被限流 / 需要人工验证 / 超时这类失败不是"没有全文"，隔一段时间自动再试一次。
    _RETRYABLE={'rate_limited','domain_paused','needs_verification','timeout','network_error'}
    _RETRY_DELAY={'rate_limited':90,'domain_paused':90,'needs_verification':150,'timeout':30,'network_error':30}

    def _schedule_retry(self,pid,result):
        if not isinstance(result,dict) or result.get('ok'):
            self._retries.pop(pid,None)
            return
        code=str(result.get('code') or '')
        if code not in self._RETRYABLE:
            return
        with self._lock:
            used=int(self._retries.get(pid,0))
            if used>=1:
                return
            self._retries[pid]=used+1
        timer=threading.Timer(self._RETRY_DELAY.get(code,60),self._requeue,(pid,))
        timer.daemon=True
        timer.start()

    def _requeue(self,pid):
        try:
            paper=db.get_paper(pid)
            if not paper:
                return
            current=paper.get('file_path') or ''
            if current and config.resolve_pdf_path(current).is_file():
                return
            with self._lock:
                if pid in self._queued or pid in self._inflight:
                    return
                self._queued.add(pid)
                db.execute("UPDATE pdf_fetch_jobs SET state='queued', updated_at=? WHERE paper_id=?",(db.now(),pid))
            self._queue.put(pid)
        except Exception:  # noqa: BLE001 - 后台重试失败不影响主流程
            return

    @tracked
    def fetch_one(self,paper_id,refresh_session=True):
        with self._lock:
            if paper_id in self._inflight:
                return {'ok':False,'error':'这篇论文正在获取，请稍后查看记录'}
            self._inflight.add(paper_id)
            if paper_id not in self._queued:
                self._paper_batches[paper_id]=batches.create('pdf',[paper_id])
        trace=pdf_trace.Trace()
        trace.deadline=time.monotonic()+150
        token=pdf_trace.active.set(trace)
        try:
            return self._fetch(paper_id,trace,refresh_session)
        finally:
            pdf_trace.active.reset(token)
            with self._lock:
                self._inflight.discard(paper_id)

    def _save(self,pid,state,trace,**result):
        report={'attempts':trace.attempts,'provenance':trace.provenance,**result}
        db.execute("""INSERT INTO pdf_fetch_jobs(paper_id,state,report,updated_at)
                   SELECT ?,?,?,? WHERE EXISTS(SELECT 1 FROM papers WHERE id=?)
                   ON CONFLICT(paper_id) DO UPDATE SET state=excluded.state,
                   report=json_set(excluded.report,'$.browser_attempts',
                     json(COALESCE(json_extract(pdf_fetch_jobs.report,'$.browser_attempts'),'[]'))),
                   updated_at=excluded.updated_at""",
                   (pid,state,json.dumps(report,ensure_ascii=False),db.now(),pid))
        report['browser_attempts']=self.report(pid)['browser_attempts']
        if not db.query_one('SELECT id FROM papers WHERE id=?',(pid,)):
            state='cancelled'
        batches.finish(self._paper_batches.get(pid),pid,state,result.get('error',''))
        return report

    def _fetch(self,pid,trace,refresh_session=True):
        paper=db.get_paper(pid)
        if not paper:
            batches.finish(self._paper_batches.get(pid),pid,'cancelled')
            return {'ok':False,'error':'论文不存在'}
        paper['_refresh_pdf_session']=refresh_session
        current=paper.get('file_path') or ''
        if current and config.resolve_pdf_path(current).is_file():
            self._save(pid,'skipped',trace)
            return {'ok':True,'skipped':True}
        self._save(pid,'running',trace)
        trace.on_change=lambda: self._save(pid,'running',trace)
        pdf_path=None
        updates={}
        try:
            arxiv_id=str(paper.get('arxiv_id') or '').strip()
            doi=str(paper.get('doi') or '').strip()
            title=str(paper.get('title') or '').strip()
            if arxiv_id:
                pdf_path=_try_candidates([{'url':f'https://arxiv.org/pdf/{arxiv_id}','source':'arxiv','version':'submittedVersion'}],paper)
            if pdf_path is None and paper.get('url') and urlsplit(paper['url']).hostname!='doi.org':
                pdf_path=_try_candidates([{'url':paper['url'],'source':'paper-url'}],paper)
            if pdf_path is None and not doi and title:
                try:
                    found=metadata_service.find_metadata_by_title(title)
                    updates={key:found[key] for key in ('authors','venue','year','doi','url') if found.get(key) and not paper.get(key)}
                    paper={**paper,**updates}
                    doi=str(paper.get('doi') or '')
                    pdf_path=_try_candidates([{'url':url,'source':'title-search'} for url in found.get('_pdf_urls') or []],paper)
                except Exception:
                    trace.add('title-search',code='no_candidate')
            if pdf_path is None and doi:
                pdf_path=_find_doi_pdf(doi,title,paper)
            if pdf_path is None and paper.get('url'):
                pdf_path=_try_candidates([{'url':paper['url'],'source':'paper-url'}],paper)
            if pdf_path is None and title:
                pdf_path=_search_arxiv_by_title(title)
                if pdf_path is not None:
                    try:
                        match=pdf_download.verify_identity(pdf_path,paper)
                        downloaded=next((entry['url'] for entry in reversed(trace.attempts) if entry['code']=='ok' and entry['route']=='download'),'')
                        trace.provenance={'source':'arxiv-title','url':downloaded,'version':'submittedVersion','identity':match}
                    except pdf_download.PdfDownloadError as exc:
                        trace.add('arxiv-title',code=exc.code)
                        pdf_path.unlink(missing_ok=True)
                        pdf_path=None
            if pdf_path is None and time.monotonic()<trace.deadline:
                pdf_path=_browser_session_pdf(paper)
            if pdf_path is None:
                code,reason=trace.failure()
                db.update_paper(pid,{'pdf_error':reason})
                report=self._save(pid,'failed',trace,code=code,error=reason)
                return {'ok':False,'error':reason,'code':code,'report':report}
            if not db.get_paper(pid):
                self._save(pid,'cancelled',trace,code='cancelled')
                return {'ok':False,'code':'cancelled','error':pdf_trace.MESSAGES['cancelled']}
            latest=db.get_paper(pid)
            if latest.get('file_path') and config.resolve_pdf_path(latest['file_path']).is_file():
                self._save(pid,'skipped',trace)
                return {'ok':True,'skipped':True}
            chunks=ingest.attach_pdf(pid,pdf_path)
            with pdf_path.open('rb') as handle:
                digest=hashlib.file_digest(handle,'sha256').hexdigest()
            provenance={**trace.provenance,'sha256':digest,'fetched_at':db.now()}
            trace.provenance=provenance
            updates.update({'pdf_error':'','pdf_acquisition':json.dumps(provenance,ensure_ascii=False)})
            if not paper.get('arxiv_id'):
                arxiv=_extract_arxiv_id(pdf_path)
                if arxiv:
                    updates['arxiv_id']=arxiv
            db.update_paper(pid,updates)
            report=self._save(pid,'succeeded',trace)
            return {'ok':True,'chunks':chunks,'via':provenance.get('source',''),'report':report}
        except Exception as exc:
            code=exc.code if isinstance(exc,pdf_download.PdfDownloadError) else 'index_failed'
            reason=pdf_trace.MESSAGES[code]
            trace.add('fetch',code=code)
            if db.get_paper(pid):
                db.update_paper(pid,{'pdf_error':reason})
            self._save(pid,'failed',trace,code=code,error=reason)
            return {'ok':False,'error':reason,'code':code}
        finally:
            trace.on_change=None
            if pdf_path is not None:
                pdf_path.unlink(missing_ok=True)


def _try_candidates(candidates,paper,cookie_provider=None):
    trace=pdf_trace.active.get()
    for candidate in candidates:
        url=candidate.get('url') or ''
        if not url.startswith(('http://','https://')):
            continue
        if trace and time.monotonic()>=trace.deadline:
            trace.add(candidate.get('source','download'),url,code='deadline')
            break
        result=None
        try:
            headers=metadata_service.openalex_content_headers(url) if candidate.get('hosted') else {}
            result=pdf_download.download(url,headers=headers,cookie_provider=cookie_provider,
                route=candidate.get('source','download'),deadline=trace.deadline if trace else None)
            identity=pdf_download.verify_identity(result.path,paper)
            if trace:
                trace.provenance={'source':candidate.get('source','download'),'url':pdf_trace.safe_url(result.url),
                                  'version':candidate.get('version',''),'license':candidate.get('license',''),
                                  'identity':identity,'sha256':result.sha256}
            return result.path
        except pdf_download.PdfDownloadError as exc:
            pdf_trace.record(candidate.get('source','download'),url,code=exc.code,status=exc.status)
            if result:
                result.path.unlink(missing_ok=True)
    return None


def _browser_session_pdf(paper: dict) -> Path | None:
    settings=config.get_settings()
    if not settings.get('pdf_browser_session_enabled',True):
        pdf_trace.record('browser-session',code='source_configuration')
        return None
    browser=str(settings.get('pdf_browser') or 'Edge')
    profile=str(settings.get('pdf_browser_profile') or 'Default')
    landing=str(paper.get('url') or '') or ('https://doi.org/'+paper['doi'] if paper.get('doi') else '')
    refreshed=[False]
    def provider(url):
        # A DOI redirector does not need publisher cookies and should not trigger browser extraction.
        if not urlsplit(url).hostname or urlsplit(url).hostname=='doi.org':
            return ''
        force=bool(paper.get('_refresh_pdf_session',True)) and not refreshed[0]
        try:
            value=browser_cookies.cookie_header(url,force=force,browser=browser,profile=profile)
        except Exception:
            pdf_trace.record('browser-session',url,code='session_error',browser=browser,profile=profile)
            refreshed[0]=True
            return ''
        refreshed[0]=True
        pdf_trace.record('browser-session',url,code='session_available' if value else 'no_session',browser=browser,profile=profile)
        return value
    return _try_candidates([{'url':landing,'source':'browser-session'}],paper,provider)


def _find_doi_pdf(doi: str,title: str,paper: dict) -> Path | None:
    try:
        result=metadata_service.openalex_by_doi(doi)
        path=_try_candidates(metadata_service.openalex_candidates(result),paper)
        if path:
            return path
    except Exception:
        pdf_trace.record('openalex',code='no_candidate')
    candidates=[]
    if doi.lower().startswith('10.18653/'):
        candidates.append({'url':'https://aclanthology.org/'+doi.split('/',1)[1]+'.pdf','source':'acl'})
    try:
        crossref=metadata_service.crossref_by_doi(doi)
        candidates.extend({'url':url,'source':'crossref'} for url in [crossref.get('pdf_url')]+list(crossref.get('pdf_urls') or []) if url)
    except Exception:
        pdf_trace.record('crossref',code='no_candidate')
    for source,urls in [('unpaywall',_unpaywall_pdfs(doi)),('europe-pmc',_europe_pmc_pdfs(doi))]:
        candidates.extend({'url':url,'source':source} if isinstance(url,str) else url for url in urls)
    candidates.append({'url':'https://doi.org/'+quote(doi,safe='/'),'source':'doi-landing'})
    path=_try_candidates(candidates,paper)
    if path:
        return path
    url=_semantic_scholar_pdf(doi)
    return _try_candidates([{'url':url,'source':'semantic-scholar'}],paper) if url else None


def _unpaywall_pdfs(doi: str) -> list[dict]:
    settings=config.get_settings()
    email=str(
        settings.get('unpaywall_email')
        or settings.get('scholar_email')
        or os.environ.get('PAPERWHALE_UNPAYWALL_EMAIL')
        or ''
    ).strip()
    if not email or '@' not in email:
        pdf_trace.record('unpaywall',code='source_configuration')
        return []
    try:
        response=metadata_service._get('https://api.unpaywall.org/v2/'+quote(doi,safe='/'),params={'email':email},timeout=10)
        if response.status_code>=400:
            return []
        data=response.json()
    except Exception:
        return []
    result=[]
    seen=set()
    for location in [data.get('best_oa_location'),*(data.get('oa_locations') or [])]:
        if not isinstance(location,dict):
            continue
        for field in ('url_for_pdf','url_for_landing_page','url'):
            url=str(location.get(field) or '').strip()
            if url.startswith(('http://','https://')) and url not in seen:
                seen.add(url)
                result.append({'url':url,'source':'unpaywall','version':location.get('version') or '', 'license':location.get('license') or ''})
    if not result:
        pdf_trace.record('unpaywall',code='no_candidate')
    return result


def _europe_pmc_pdfs(doi: str) -> list[str]:
    try:
        response = metadata_service._get(
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core"},
            timeout=12,
            headers={"User-Agent": metadata_service.USER_AGENT},
        )
        if response.status_code >= 400:
            return []
        data = response.json() or {}
        results = ((data.get("resultList") or {}).get("result") or [])
        urls: list[str] = []
        for item in results[:1]:
            block = item.get("fullTextUrlList") or {}
            for entry in block.get("fullTextUrl") or []:
                if entry.get("availability") == "Open access" or str(entry.get("documentStyle", "")).lower() == "pdf":
                    url = str(entry.get("url") or "")
                    if url:
                        urls.append(url)
        return urls
    except Exception:  # noqa: BLE001
        return []


def _semantic_scholar_pdf(doi: str) -> str:
    try:
        response = metadata_service._get(
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "openAccessPdf"},
            timeout=12,
            headers={"User-Agent": metadata_service.USER_AGENT},
        )
        if response.status_code >= 400:
            return ""
        data = response.json() or {}
        return str((data.get("openAccessPdf") or {}).get("url") or "")
    except Exception:  # noqa: BLE001
        return ""


def _search_arxiv_by_title(title: str) -> Path | None:
    """标题几乎完全一致才下载，避免抓错论文。"""
    normalized = " ".join(title.split())
    if len(normalized) < 8:
        return None
    candidates: list[dict] = []
    try:
        candidates.extend(discover.arxiv_search(normalized, limit=5))
    except Exception:  # noqa: BLE001
        pass
    if not candidates:
        candidates = _arxiv_title_query(normalized)
    from difflib import SequenceMatcher

    def norm(text: str) -> str:
        return "".join(ch for ch in (text or "").lower() if ch.isalnum())

    best = None
    best_score = 0.0
    for item in candidates:
        score = SequenceMatcher(None, norm(normalized), norm(item.get("title") or "")).ratio()
        if score > best_score:
            best, best_score = item, score
    if best is None or best_score < 0.86:
        return None
    try:
        path, _meta = discover.download_arxiv(best["arxiv_id"])
        return path
    except Exception:  # noqa: BLE001
        return None


def _arxiv_title_query(title: str) -> list[dict]:
    words = " ".join(title.split()[:12])
    for attempt in range(2):
        try:
            response = metadata_service._get(
                "https://export.arxiv.org/api/query",
                params={
                    "search_query": f'ti:"{words}"',
                    "start": 0,
                    "max_results": 5,
                    "sortBy": "relevance",
                },
                timeout=metadata_service.ARXIV_TIMEOUT,
                headers={"User-Agent": metadata_service.USER_AGENT, "Accept": "application/atom+xml"},
            )
            if response.status_code == 429 and attempt == 0:
                import time

                time.sleep(4)
                continue
            if response.status_code < 400:
                return metadata_service.parse_arxiv_feed(response.text)
            break
        except Exception:  # noqa: BLE001
            if attempt == 0:
                import time

                time.sleep(2)
                continue
            break
    return []


def _extract_arxiv_id(pdf_path: Path) -> str:
    try:
        from . import pdftext

        head = pdftext.first_pages_text(pdf_path, 1)[:1200]
    except Exception:  # noqa: BLE001
        return ""
    import re

    match = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})", head, re.IGNORECASE)
    return match.group(1) if match else ""


manager = PdfFetchManager()
