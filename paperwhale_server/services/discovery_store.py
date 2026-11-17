"""Persist research topics and user decisions without automatically importing results."""
from __future__ import annotations
import hashlib
import json
import re
import threading
from contextlib import closing
from urllib.parse import urlsplit
from .. import db
from . import conversations,metadata
from .activity import tracked

_actions=threading.RLock()
_DEFAULT_TITLE='新找论文话题'


def _json(value):return json.dumps(value,ensure_ascii=False)
def _text(value,limit):return str(value or '').strip()[:limit]
def _title(value):return re.sub(r'[^\w\u4e00-\u9fff]+','',str(value or '').lower())
def _url(value):
    value=_text(value,2000)
    try:
        parsed=urlsplit(value)
        return value if parsed.scheme.lower() in {'http','https'} and parsed.hostname and not parsed.username and not parsed.password else ''
    except ValueError:return ''


def _citation_graph(edges):
    if not isinstance(edges,list):return []
    result=[];seen=set()
    for edge in edges[:100]:
        if not isinstance(edge,dict) or edge.get('source')!='OpenAlex' or edge.get('evidence') not in {'referenced_works','cites_filter'}:continue
        if any(not isinstance(edge.get(key),str) or not re.fullmatch(r'https://openalex\.org/W\d{1,30}',edge[key]) for key in ('citing','cited')):continue
        signature=(edge['citing'],edge['cited'],edge['evidence'])
        if signature in seen:continue
        seen.add(signature);result.append({key:edge[key] for key in ('source','citing','cited','evidence')})
        if len(result)>=12:break
    return result


def get_session(session_id):
    row=db.query_one('SELECT * FROM discovery_sessions WHERE id=?',(session_id,))
    if not row:raise conversations.ConversationError('找论文话题不存在',404)
    return dict(row)


def create_session(title=''):
    sid=db.new_id();now=db.now()
    db.execute('INSERT INTO discovery_sessions(id,title,created_at,updated_at) VALUES(?,?,?,?)',(sid,_text(title,120) or _DEFAULT_TITLE,now,now))
    return get_session(sid)


def list_sessions():
    return [dict(row) for row in db.query('SELECT * FROM discovery_sessions ORDER BY updated_at DESC,rowid DESC LIMIT 100')]


def _run(row):
    item=dict(row)
    for key in ('queries','warnings'):item[key]=json.loads(item[key])
    references=json.loads(item.pop('references_json'))
    item['tracking'] = json.loads(item.pop('tracking_json', '{}'))
    item['references']=references;item['candidate_ids']=[ref['id'] for ref in references]
    return item


def last_seed(session_id):
    rows = db.query("SELECT tracking_json FROM discovery_runs WHERE session_id=? AND intent='followup' ORDER BY rowid DESC LIMIT 12", (session_id,))
    for row in rows:
        seed = json.loads(row['tracking_json']).get('seed') or {}
        if seed:
            return seed.get('doi') or ('arXiv:'+seed['arxiv_id'] if seed.get('arxiv_id') else seed.get('title',''))
    return ''


def _library_match(item,papers):
    doi=metadata.normalize_doi(item.get('doi') or '').lower();arxiv=metadata.normalize_arxiv_id(item.get('arxiv_id') or '')
    title=_title(item.get('title'))
    for paper in papers:
        if doi and doi==str(paper['doi'] or '').lower():return paper['id']
        if arxiv and arxiv==metadata.normalize_arxiv_id(paper['arxiv_id'] or ''):return paper['id']
    for paper in papers:
        conflict=(doi and paper['doi'] and doi!=str(paper['doi']).lower()) or (arxiv and paper['arxiv_id'] and arxiv!=metadata.normalize_arxiv_id(paper['arxiv_id']))
        if title and title==_title(paper['title']) and not conflict:return paper['id']
    return ''


def _present(row,papers,reference=None):
    item={**json.loads(row['payload']),**{key:row[key] for key in ('id','session_id','run_id','status','paper_id','created_at','updated_at')}}
    match=_library_match(item,papers)
    item['in_library']=bool(match);item['paper_id']=match
    if match and item['status']=='pending':item['status']='added'
    if not match and item['status']=='added':item['status']='pending'
    item['citation_index']=reference['index'] if reference else None
    item['is_current']=bool(reference)
    item['relation'] = reference.get('relation', '') if reference else ''
    return item


def detail(session_id):
    session=get_session(session_id)
    runs=[_run(row) for row in db.query('SELECT * FROM discovery_runs WHERE session_id=? ORDER BY rowid DESC',(session_id,))]
    references={ref['id']:ref for ref in runs[0]['references']} if runs else {}
    papers=db.query('SELECT id,title,doi,arxiv_id FROM papers')
    rows=db.query('SELECT * FROM discovery_candidates WHERE session_id=? ORDER BY rowid DESC',(session_id,))
    items=[_present(row,papers,references.get(row['id'])) for row in rows]
    order={ref['id']:index for index,ref in enumerate(runs[0]['references'])} if runs else {}
    items.sort(key=lambda item:(not item['is_current'],order.get(item['id'],0)))
    return {'session':session,'conversation':conversations.snapshot('discovery:'+session_id),'runs':runs,'run':runs[0] if runs else None,'items':items}


def _clean_candidate(raw):
    from .discovery_translation import clean_title_zh,native_title_zh
    doi=metadata.normalize_doi(str(raw.get('doi') or ''))
    arxiv=metadata.normalize_arxiv_id(str(raw.get('arxiv_id') or ''))
    url=_url(raw.get('url')) or ('https://doi.org/'+doi if doi else 'https://arxiv.org/abs/'+arxiv if arxiv else '')
    title=_text(raw.get('title'),1000)
    if not title or not url:return None
    item={key:_text(raw.get(key),limit) for key,limit in {'authors':2000,'year':8,'published':30,'venue':500,'abstract':12000,'reason':1800,'stage':300}.items()}
    item.update(title=title,doi=doi,arxiv_id=arxiv,url=url)
    item['title_zh']=clean_title_zh(raw.get('title_zh')) or native_title_zh(title)
    item['citation_graph']=_citation_graph(raw.get('citation_graph'))
    item['sources']=[{'name':_text(source.get('name'),100),'url':_url(source.get('url'))} for source in (raw.get('sources') or [])[:10] if isinstance(source,dict) and _url(source.get('url'))]
    return item


@tracked
def delete_session(session_id):
    from . import recovery,ai_progress
    with _actions,closing(db.connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        session=conn.execute('SELECT * FROM discovery_sessions WHERE id=?',(session_id,)).fetchone()
        if not session:raise conversations.ConversationError('找论文话题不存在',404)
        conversation=conn.execute('SELECT * FROM ai_conversations WHERE scope=?',('discovery:'+session_id,)).fetchone()
        payload={'session':dict(session),'runs':[dict(row) for row in conn.execute('SELECT * FROM discovery_runs WHERE session_id=? ORDER BY rowid',(session_id,))],
                 'candidates':[dict(row) for row in conn.execute('SELECT * FROM discovery_candidates WHERE session_id=? ORDER BY rowid',(session_id,))],
                 'conversation':dict(conversation) if conversation else None,'messages':[]}
        if conversation:
            payload['messages']=[dict(row) for row in conn.execute('SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id',(conversation['id'],))]
        item_id=recovery.archive(conn,'discovery_topic',session['title'],payload)
        conn.execute('DELETE FROM ai_conversations WHERE scope=?',('discovery:'+session_id,))
        conn.execute('DELETE FROM discovery_sessions WHERE id=?',(session_id,))
        # Stop while the action lock is held so restoration cannot start a new turn first.
        ai_progress.cancel('discovery:'+session_id)
        ai_progress.cancel('discovery-translation:'+session_id)
    return {'ok':True,'trash_id':item_id,'recoverable':True}


def save_run(session_id,question,result,conversation):
    """Commit only if this exact completed turn still owns the topic revision."""
    get_session(session_id);rid=db.new_id();now=db.now();references=[]
    with closing(db.connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        if not conn.execute('SELECT id FROM discovery_sessions WHERE id=?',(session_id,)).fetchone():
            raise conversations.ConversationError('找论文话题已删除，本轮旧结果未加入候选列表')
        live=conn.execute('SELECT id,revision FROM ai_conversations WHERE scope=?',('discovery:'+session_id,)).fetchone()
        if not live or live['id']!=conversation['id'] or live['revision']!=conversation['revision']:
            raise conversations.ConversationError('找论文话题已更新，本轮旧结果未加入候选列表')
        intent=result.get('intent') if result.get('intent') in {'latest','search','timeline','followup','file'} else 'search'
        conn.execute('INSERT INTO discovery_runs(id,session_id,question,answer,intent,topic,queries,warnings,date_from,date_to,created_at,tracking_json) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                     (rid,session_id,question,str(result['answer']),intent,_text(result.get('topic'),300),_json((result.get('queries') or [])[:8]),_json((result.get('warnings') or [])[:20]),_text(result.get('date_from'),30),_text(result.get('date_to'),30),now,_json(result.get('tracking') or {})))
        existing=[dict(row) for row in conn.execute('SELECT * FROM discovery_candidates WHERE session_id=?',(session_id,))]
        for ordinal,raw in enumerate((result.get('items') or [])[:50],1):
            item=_clean_candidate(raw)
            if not item:continue
            identity='doi:'+item['doi'].lower() if item['doi'] else 'arxiv:'+item['arxiv_id'] if item['arxiv_id'] else 'title:'+_title(item['title'])
            fingerprint=hashlib.sha256(identity.encode()).hexdigest()
            found=None
            for row in existing:
                previous=json.loads(row['payload'])
                identifiers=(item['doi'] and item['doi'].lower()==str(previous.get('doi') or '').lower()) or (item['arxiv_id'] and item['arxiv_id']==previous.get('arxiv_id'))
                conflict=(item['doi'] and previous.get('doi') and item['doi'].lower()!=previous['doi'].lower()) or (item['arxiv_id'] and previous.get('arxiv_id') and item['arxiv_id']!=previous['arxiv_id'])
                same_title=_title(item['title'])==_title(previous.get('title')) and not conflict
                if row['fingerprint']==fingerprint or identifiers or same_title:found=row;break
            if found:
                cid=found['id'];previous=json.loads(found['payload'])
                combined={source['url']:source for source in [*previous.get('sources',[]),*item['sources']]}
                graph=_citation_graph([*previous.get('citation_graph',[]),*item['citation_graph']])
                item={**previous,**{key:value for key,value in item.items() if value}};item['sources']=list(combined.values())[:10]
                item['citation_graph']=graph
                conn.execute('UPDATE discovery_candidates SET run_id=?,payload=?,updated_at=? WHERE id=?',(rid,_json(item),now,cid));found['payload']=_json(item)
            else:
                cid=db.new_id();payload=_json(item)
                conn.execute('INSERT INTO discovery_candidates(id,session_id,run_id,fingerprint,payload,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',(cid,session_id,rid,fingerprint,payload,now,now))
                existing.append({'id':cid,'fingerprint':fingerprint,'payload':payload})
            if not any(ref['id']==cid for ref in references):
                index=raw.get('citation_index') or ordinal
                reference = {'id':cid,'index':int(index) if str(index).isdigit() else ordinal}
                if intent == 'followup':
                    reference['relation'] = 'direct' if raw.get('relation') == 'direct' and item['citation_graph'] else 'related'
                references.append(reference)
        conn.execute('UPDATE discovery_runs SET references_json=? WHERE id=?',(_json(references),rid))
        current=conn.execute('SELECT title FROM discovery_sessions WHERE id=?',(session_id,)).fetchone()
        title=(_text(result.get('topic'),120) or _text(question,120)) if current['title']==_DEFAULT_TITLE else current['title']
        conn.execute('UPDATE discovery_sessions SET title=?,updated_at=? WHERE id=?',(title,now,session_id))
    data=detail(session_id)
    return {**result,**data,'run':next(run for run in data['runs'] if run['id']==rid)}


def get_candidate(cid):
    row=db.query_one('SELECT * FROM discovery_candidates WHERE id=?',(cid,))
    if not row:raise conversations.ConversationError('候选论文不存在',404)
    data=detail(row['session_id'])
    return next(item for item in data['items'] if item['id']==cid)


@tracked
def change_state(cid,status):
    if status not in {'ignored','pending'}:raise conversations.ConversationError('不支持的候选处理状态',400)
    with _actions:
        get_candidate(cid)
        db.execute('UPDATE discovery_candidates SET status=?,updated_at=? WHERE id=?',(status,db.now(),cid))
        return {'item':get_candidate(cid)}


@tracked
def add_candidate(cid):
    from . import ingest,pdf_fetch
    with _actions:
        candidate=get_candidate(cid);pid=candidate.get('paper_id');created=not pid
        if not pid:
            meta={key:candidate.get(key) or '' for key in ('title','authors','year','venue','doi','arxiv_id','url','abstract')}
            pid=ingest.create_paper_from_metadata(meta,source='ai-discovery',strict_identifiers=True)
        db.execute("UPDATE discovery_candidates SET status='added',paper_id=?,updated_at=? WHERE id=?",(pid,db.now(),cid))
        if created:
            try:pdf_fetch.manager.start([pid])
            except Exception:pass
        return {'paper':db.get_paper(pid),'item':get_candidate(cid)}
