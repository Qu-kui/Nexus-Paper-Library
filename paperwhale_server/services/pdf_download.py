"""Bounded PDF/landing resolver shared by imports, discovery and background fetching."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlencode

import httpx
import pypdfium2 as pdfium

from .. import config
from . import pdf_trace, pdf_domains

MAX_BYTES=80*1024*1024
HTML_LIMIT=2*1024*1024


class PdfDownloadError(ValueError):
    def __init__(self, code: str, *, status: int | None=None):
        self.code=code
        self.status=status
        super().__init__(pdf_trace.MESSAGES.get(code,'PDF 获取失败'))


@dataclass
class Download:
    path: Path
    url: str
    sha256: str


class Links(HTMLParser):
    def __init__(self, base):
        super().__init__(convert_charrefs=True)
        self.base=base
        self.links=[]
        self.supplements=[]
        self.anchor=None
        self.publisher=(urlsplit(base).hostname or '').lower() in {'www.sciencedirect.com','sciencedirect.com'}
        self.json_script=None
        self.json_payloads=[]
        self.redirect_container=False
        self.refresh_links=set()

    def add(self, value, supplementary=False):
        if not value or not str(value).strip() or str(value).strip().startswith('#'):
            return
        url=urljoin(self.base,value or '')
        try:
            if urlsplit(url).scheme not in {'http','https'}:return
        except ValueError:return
        supplementary=supplementary or bool(re.search(r'(?:supplement|supporting|(?:^|[/_-])mmc\d)',url,re.I))
        if supplementary:
            if url in self.links:self.links.remove(url)
            if url not in self.supplements:self.supplements.append(url)
        elif url not in self.links and url not in self.supplements:
            self.links.append(url)

    def handle_starttag(self, tag, attrs):
        attrs=dict(attrs)
        if tag=='script' and attrs.get('type','').lower()=='application/json':
            self.json_script=[]
        canonical=(attrs.get('href') if tag=='link' and 'canonical' in attrs.get('rel','').lower().split() else
                   attrs.get('content') if tag=='meta' and attrs.get('name','').lower()=='citation_public_url' else '')
        if canonical:
            try:
                original=urlsplit(self.base);hint=urlsplit(urljoin(self.base,canonical))
                pii=lambda path: re.fullmatch(r'/science/article/(?:abs/)?pii/([A-Z0-9]+)/?',path)
                here,there=pii(original.path),pii(hint.path)
                if hint.scheme in {'http','https'} and hint.hostname in {'sciencedirect.com','www.sciencedirect.com'} and here and there and here[1]==there[1]:
                    self.publisher=True
            except ValueError:pass
        if self.publisher and attrs.get('id')=='redirect-message':
            self.redirect_container=True
        if tag=='meta' and attrs.get('http-equiv','').lower()=='refresh':
            match=re.search(r'^\s*\d+(?:\.\d+)?\s*;\s*url\s*=\s*(.+)$',attrs.get('content',''),re.I)
            if match and (self.publisher or (urlsplit(self.base).hostname or '').lower()=='linkinghub.elsevier.com'
                          or re.search(r'\.pdf(?:[?#]|$)',match[1],re.I)):
                value=match[1].strip().strip('\"\'')
                self.add(value)
                resolved=urljoin(self.base,value)
                if resolved in self.links:self.refresh_links.add(resolved)
        if tag=='meta' and (attrs.get('name') or attrs.get('property') or '').lower() in {
            'citation_pdf_url','og:pdf','eprints.document_url'}:
            self.add(attrs.get('content'))
        if tag=='link' and attrs.get('type','').lower()=='application/pdf':
            self.add(attrs.get('href'))
        if tag in {'iframe','embed','object'}:
            src=attrs.get('src') or attrs.get('data') or ''
            if '.pdf' in src.lower() or '/pdf' in src.lower() or attrs.get('type')=='application/pdf':
                self.add(src)
        if tag=='a':
            if self.publisher and (attrs.get('id')=='pdfLink' or self.redirect_container or 'pdf-download-btn-link' in attrs.get('class','')):
                self.add(attrs.get('href'))
            self.anchor=[attrs.get('href',''),'']
            hint=' '.join(str(attrs.get(k) or '') for k in ('href','title','type','class'))
            if 'download' in attrs or re.search(r'\.pdf(?:[?#\s]|$)|application/pdf|stampPDF/getPDF', hint,re.I):
                self.add(attrs.get('href'))

    def handle_data(self, text):
        if self.json_script is not None:
            self.json_script.append(text)
        if self.anchor:
            self.anchor[1]+=text[:300]

    def handle_endtag(self, tag):
        if tag=='div':
            self.redirect_container=False
        if tag=='script' and self.json_script is not None:
            if len(self.json_payloads)<16:self.json_payloads.append(''.join(self.json_script))
            self.json_script=None
        if tag=='a' and self.anchor:
            if re.search(r'\bpdf\b|下载全文|全文下载|download|supplement|supporting|补充材料',self.anchor[1],re.I):
                self.add(self.anchor[0],bool(re.search(r'supplement|supporting|补充材料',self.anchor[1],re.I)))
            self.anchor=None


def landing_links(html: str, base: str, *, refresh_links: set | None=None) -> list[str]:
    parser=Links(base)
    parser.feed(html[:HTML_LIMIT])
    if parser.publisher:
        for payload in parser.json_payloads:
            try:
                info=json.loads(payload)['article']['pdfDownload']['urlMetadata']
                path=info['path'];pii=info['pii'];suffix=info['pdfExtension'];query=info['queryParams']
                expected=re.search(r'/pii/([A-Z0-9]+)(?:/|$)',urlsplit(base).path)
                if (isinstance(path,str) and re.fullmatch(r'[\w/-]+',path)
                    and isinstance(pii,str) and re.fullmatch(r'[A-Z0-9]+',pii)
                    and (not expected or expected[1]==pii) and suffix in {'/pdfft','.pdf'} and isinstance(query,dict)
                    and all(isinstance(query.get(key),str) and query[key] for key in ('md5','pid'))):
                    parser.add('/'+path.strip('/')+'/'+pii+suffix+'?'+urlencode({key:query[key] for key in ('md5','pid')}))
            except (ValueError,KeyError,TypeError):pass
        path=urlsplit(base).path
        match=re.fullmatch(r'/science/article/(?:abs/)?pii/([A-Z0-9]+)(?:/)?',path)
        if match:
            parser.add('/science/article/pii/'+match[1]+'/pdfft?download=true')
    if 'ieeexplore.ieee.org' == (urlsplit(base).hostname or ''):
        match=re.search(r'arnumber[=\"\s:]+(\d+)',base+' '+html,re.I)
        if match:
            parser.add('/stampPDF/getPDF.jsp?tp=&arnumber='+match.group(1))
    if refresh_links is not None:refresh_links.update(parser.refresh_links)
    return (parser.links+parser.supplements)[:12]


def validate_pdf(path: Path) -> None:
    try:
        with path.open('rb') as handle:
            if b'%PDF-' not in handle.read(1024):
                raise PdfDownloadError('invalid_pdf')
        document=pdfium.PdfDocument(path)
        try:
            if len(document)<1:
                raise PdfDownloadError('invalid_pdf')
        finally:
            document.close()
    except PdfDownloadError:
        raise
    except Exception as exc:
        raise PdfDownloadError('invalid_pdf') from exc


def verify_identity(path: Path, expected: dict) -> dict:
    from . import metadata, pdftext

    try:
        found=metadata.extract_pdf_metadata(path)
        head=pdftext.first_pages_text(path,1)[:6000]
    except Exception:
        return {'state':'unverified','reason':'metadata_unavailable'}
    title=str(found.get('title') or '').strip()
    target=str(expected.get('title') or '').strip()
    norm=lambda text: re.sub(r'[^\w\u4e00-\u9fff]','',text.lower())
    score=SequenceMatcher(None,norm(title),norm(target)).ratio() if title and target else None
    # Verify against the first-page title, never a DOI found somewhere in the references.
    title_words=set(re.findall(r'[a-z]{3,}',title.lower()))
    target_words=set(re.findall(r'[a-z]{3,}',target.lower()))
    different_english=(len(title_words)>=2 and len(target_words)>=2 and not title_words.intersection(target_words) and score is not None and score<0.6)
    if score is not None and len(norm(title))>=15 and len(norm(target))>=15 and (different_english or (score<0.35 and bool(title_words)==bool(target_words))):
        raise PdfDownloadError('identity_mismatch')
    if re.search(r'supplementary|supporting information',title,re.I) and not re.search(r'supplementary|supporting information',target,re.I):
        raise PdfDownloadError('identity_mismatch')
    doi=metadata.normalize_doi(expected.get('doi') or '')
    same_doi=bool(doi and doi.lower() in head.lower())
    return {'state':'matched' if same_doi or (score is not None and score>=0.75) else 'unverified',
            'title_similarity':round(score,3) if score is not None else None,
            'doi_found_on_first_page':same_doi}


def download(url: str, *, filename: str | None=None, headers: dict | None=None,
             client: httpx.Client | None=None, cookie_provider=None, max_bytes: int=MAX_BYTES,
             max_depth: int=2, route: str='download', deadline: float | None=None,
             request_policy: pdf_domains.RequestPolicy | None=None) -> Download:
    config.ensure_dirs()
    own_client=client is None
    policy=request_policy or (pdf_domains.default_policy if own_client else None)
    now=policy.clock if policy else time.monotonic
    sleep=policy.sleep if policy else time.sleep
    client=client or httpx.Client(timeout=httpx.Timeout(20,connect=8),follow_redirects=False)
    seen=set()
    budget=[0]
    trace=pdf_trace.active.get()
    deadline=deadline or getattr(trace,'deadline',None) or now()+90
    first_origin=(urlsplit(url).scheme,urlsplit(url).netloc)
    base_headers={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0 Safari/537.36 PaperWhale/2.5',
                  'Accept':'application/pdf,text/html;q=0.9,*/*;q=0.5', **(headers or {})}

    def visit(target, depth, referer=''):
        if urlsplit(target).scheme not in {'http','https'}:
            raise PdfDownloadError('not_pdf')
        if target in seen or budget[0]>=16 or depth>max_depth:
            raise PdfDownloadError('redirect_limit')
        seen.add(target)
        for retry in range(2):
            if now()>=deadline:
                raise PdfDownloadError('deadline')
            if budget[0]>=16:
                raise PdfDownloadError('redirect_limit')
            budget[0]+=1
            request_headers=dict(base_headers)
            origin=(urlsplit(target).scheme,urlsplit(target).netloc)
            if origin!=first_origin:
                for key in list(request_headers):
                    if key.lower() in {'authorization','cookie','proxy-authorization'}:
                        del request_headers[key]
            if cookie_provider:
                cookie=cookie_provider(target)
                jar_cookie=client.build_request('GET',target).headers.get('cookie','')
                if jar_cookie:
                    names={part.strip().split('=',1)[0] for part in jar_cookie.split(';')}
                    cookie='; '.join([part.strip() for part in cookie.split(';') if part.strip() and part.strip().split('=',1)[0] not in names]+[jar_cookie])
                if cookie:
                    request_headers['Cookie']=cookie
                else:
                    request_headers.pop('Cookie',None)
            for key in list(request_headers):
                if key.lower()=='referer':
                    value=request_headers.pop(key)
                    safe=pdf_trace.request_referer(value,target)
                    if safe:
                        request_headers['Referer']=safe
            if referer:
                safe=pdf_trace.request_referer(referer,target)
                request_headers.pop('Referer',None)
                if safe:
                    request_headers['Referer']=safe
            try:
                context=None
                if policy:
                    context=policy.context(client.build_request('GET',target,headers=request_headers).headers)
                    try:
                        policy.acquire(target,context,deadline)
                    except pdf_domains.DomainPaused as exc:
                        pdf_trace.record(route,target,code=exc.code,cooldown_seconds=round(exc.seconds,2))
                        raise PdfDownloadError(exc.code) from exc
                with client.stream('GET',target,headers=request_headers,follow_redirects=False) as response:
                    status=response.status_code
                    if policy:
                        policy.observe(target,context,status,response.headers.get('retry-after',''))
                    if status in (301,302,303,307,308):
                        pdf_trace.record(route,target,code='redirect',status=status)
                        return visit(urljoin(target,response.headers.get('location','')),depth, target)
                    if status>=400:
                        code=pdf_trace.http_code(status)
                        delay=response.headers.get('retry-after','')
                        wait=pdf_domains.retry_seconds(delay,now=policy.wall_clock() if policy else None,
                                                       default=1 if status==429 else 0.5)
                        pdf_trace.record(route,target,code=code,status=status,retry_after=delay[:80],retry=retry)
                        if retry==0 and status in (429,500,502,503,504) and wait<=3 and now()+wait<deadline:
                            sleep(wait)
                            continue
                        raise PdfDownloadError(code,status=status)
                    declared=response.headers.get('content-length','')
                    if declared.isdigit() and int(declared)>max_bytes:
                        raise PdfDownloadError('too_large')
                    fd,name=tempfile.mkstemp(prefix='fetch-',suffix='.part',dir=config.TMP_DIR)
                    os.close(fd)
                    path=Path(name)
                    try:
                        head=b''
                        total=0
                        digest=hashlib.sha256()
                        with path.open('wb') as handle:
                            for chunk in response.iter_bytes(chunk_size=65536):
                                if now()>=deadline:
                                    raise PdfDownloadError('deadline')
                                total+=len(chunk)
                                if total>max_bytes:
                                    raise PdfDownloadError('too_large')
                                if len(head)<1024:
                                    head=(head+chunk)[:1024]
                                if total>HTML_LIMIT and b'%PDF-' not in head:
                                    raise PdfDownloadError('not_pdf')
                                handle.write(chunk)
                                digest.update(chunk)
                        if b'%PDF-' in head:
                            validate_pdf(path)
                            destination=path.with_suffix('.pdf')
                            path.replace(destination)
                            pdf_trace.record(route,target,code='ok',status=status)
                            return Download(destination,target,digest.hexdigest())
                        html=path.read_text(encoding='utf-8',errors='replace')
                        if re.search(r'cf-chl-|captcha|verify you are human|sign in to access|institutional login',html,re.I):
                            raise PdfDownloadError('needs_verification')
                        refresh_links=set()
                        candidates=landing_links(html,target,refresh_links=refresh_links)
                        pdf_trace.record(route,target,code='landing' if candidates else 'not_pdf',status=status)
                    finally:
                        path.unlink(missing_ok=True)
                last=None
                for candidate in candidates:
                    try:
                        # A declared HTML refresh is navigation, like HTTP 302.
                        # Request/seen/deadline bounds still apply to every hop.
                        return visit(candidate,depth if candidate in refresh_links else depth+1,target)
                    except PdfDownloadError as exc:
                        last=exc
                raise last or PdfDownloadError('not_pdf')
            except httpx.TimeoutException as exc:
                pdf_trace.record(route,target,code='timeout',retry=retry)
                if retry==0:
                    continue
                raise PdfDownloadError('timeout') from exc
            except httpx.HTTPError as exc:
                pdf_trace.record(route,target,code='network_error',retry=retry)
                if retry==0:
                    continue
                raise PdfDownloadError('network_error') from exc
            except PdfDownloadError as exc:
                pdf_trace.record(route,target,code=exc.code,status=exc.status)
                raise
        raise PdfDownloadError('network_error')
    try:
        return visit(url,0)
    finally:
        if own_client:
            client.close()
