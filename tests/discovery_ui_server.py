"""Isolated discovery browser fixture. Uses only a newly-created test data directory."""
from __future__ import annotations
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if os.environ.get('PAPERWHALE_UI_TEST') != '1' or not os.environ.get('PAPERWHALE_DATA'):
    raise SystemExit('Run through tests/discovery_ui.mjs')
os.environ['PAPERWHALE_LLM_MOCK'] = '1'
from paperwhale_server import config, db, llm
from paperwhale_server.services import metadata, scholar, pdf_fetch, ai_progress
config.update_settings({'scholar_auto_sync': False, 'pdf_browser_session_enabled': False})
db.init_db()
metadata.repair_bad_titles = lambda: None
scholar.start_auto_sync = lambda: None
scholar.configured = lambda: True
scholar.list_items = lambda *a: [{'id':'mail-one','title':'Independent Scholar alert','status':'new','authors':'Mail Author','url':'https://example.org/alert'}]
scholar.unread_count = lambda: 1
scholar.sync = lambda: {'added':0}
scholar.translate_missing = lambda *a: 0
pdf_fetch.manager.resume = lambda: None
pdf_fetch.manager.start = lambda *a, **kw: {'queued': 1}
# These are published bibliographic records; all research is deterministic and offline.
RECORDS = [
    {'title':'Attention Is All You Need','authors':'Ashish Vaswani; Noam Shazeer','year':'2017','published':'2017-06-12','arxiv_id':'1706.03762','url':'https://arxiv.org/abs/1706.03762'},
    {'title':'BERT: Pre-training of Deep Bidirectional Transformers for Language Understanding','authors':'Jacob Devlin; Ming-Wei Chang','year':'2018','published':'2018-10-11','arxiv_id':'1810.04805','url':'https://arxiv.org/abs/1810.04805'},
    {'title':'Language Models are Few-Shot Learners','authors':'Tom B. Brown; Benjamin Mann','year':'2020','published':'2020-05-28','arxiv_id':'2005.14165','url':'https://arxiv.org/abs/2005.14165'},
]
TITLES_ZH=['注意力就是你所需要的一切','BERT：用于语言理解的深度双向 Transformer 预训练','语言模型是少样本学习者']
# Synthetic mixed-language records cover UI language detection, separate from published records.
MIXED_RECORDS=[
    {'title':'Transformer methods for image reconstruction 中文评测','title_zh':'用于图像重建的 Transformer 方法与中文评测','url':'https://example.org/mixed-translated'},
    {'title':'Efficient language model evaluation 中文数据','title_zh':'','url':'https://example.org/mixed-missing'},
    {'title':'面向复杂场景的偏振成像与重建方法','title_zh':'','url':'https://example.org/native-chinese'},
]
TRANSLATIONS={row['title']:TITLES_ZH[i] for i,row in enumerate(RECORDS)}
TRANSLATIONS[MIXED_RECORDS[1]['title']]='基于中文数据的高效语言模型评估'
original_model=llm.chat
def model(messages, **kwargs):
    if '将论文原始题名准确翻译' not in messages[0]['content']:
        return original_model(messages, **kwargs)
    rows=json.loads(messages[-1]['content'])['items']
    content=json.dumps({'items':[{'id':row['id'],'title_zh':TRANSLATIONS[row['title']]} for row in rows]},ensure_ascii=False)
    return {'content':content,'model':'offline-translator'}
llm.chat=model
attempts = defaultdict(int)
def research(question, history, profile, **kwargs):
    attempts[question] += 1
    ai_progress.emit(stage='核对文献来源', reasoning_delta='正在对照已检索的来源，筛选与中文请求相关的研究。')
    delay = 6 if '滚动测试' in question else 2.4 if '慢速' in question or ('停止测试' in question and attempts[question] == 1) else .8
    for _ in range(int(delay/.04)):
        time.sleep(.04)
        if '滚动测试' in question:
            ai_progress.emit(reasoning_delta='核对当前研究与来源证据。\n'*3)
        if ai_progress.cancelled():
            raise ValueError('本轮研究已停止')
    if '失败测试' in question:
        raise ValueError('测试来源暂时不可用')
    options = kwargs.get('options', {})
    if options.get('mode') == 'file':
        material = kwargs['materials'][0]
        return {'intent':'file','answer':material['name']+'\n\n'+material['text'],'items':[], 'tracking':{'options':options}}
    if '追踪回归' in question:
        seed = RECORDS[0]
        rows = [] if '空结果' in question and not options.get('broaden') else [
            {**RECORDS[1], 'relation':'direct', 'citation_graph':[{'source':'OpenAlex','evidence':'cites_filter',
             'citing':'https://openalex.org/W2','cited':'https://openalex.org/W1'}]}, RECORDS[2]]
        return {'intent':'followup','answer':'核对原文','items':rows,'date_from':options.get('date_from') or seed['published'],
                'date_to':'2026-11-08','tracking':{'seed':seed,'basis':'publication','options':options},
                'warnings':[] if rows else ['OpenAlex 直接引用访问受限（HTTP 403）。']}
    timeline = '脉络' in question
    items = [{**row,'venue':'arXiv','abstract':'用于浏览器测试的离线摘要。','sources':[{'name':'arXiv','url':row['url']}],
              'title_zh':TITLES_ZH[i] if i==0 else '',
              'reason':f'与请求相关的第 {i+1} 条实际文献','stage':['基础架构','预训练','规模扩展'][i] if timeline else ''} for i,row in enumerate(RECORDS)]
    if '混合题名回归' in question:
        items=[{**row,'reason':'混合语言题名回归用合成候选','sources':[{'name':'fixture','url':row['url']}]} for row in MIXED_RECORDS]
    if '请找出 Attention' in question:
        items = items[:1]
    return {'answer': ('## 发展脉络\n基础架构 [1] → 预训练 [2] → 规模扩展 [3]' if timeline else f'研究请求：{question}\n\n找到可核对的论文。{''.join(' ['+str(i+1)+']' for i in range(len(items)))}'),
            'reasoning':'已核对三条公开元数据。','model':'offline-ui-fixture','intent':'timeline' if timeline else 'latest' if '最新' in question else 'search',
            'topic':question[:24],'queries':['transformer language model'],'date_from':'2017-01-01','date_to':'2026-10-28',
            'items':items,'warnings':['测试检索范围有限，未覆盖全部文献。']}
from paperwhale_server.services import research_discovery
research_discovery.research = research
from paperwhale_server.main import app
import uvicorn
uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
