"""Isolated fixture server for ai_conversations_ui.mjs; never reads user settings."""
from __future__ import annotations

import os
import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
if not os.environ.get('PAPERWHALE_UI_TEST') or not os.environ.get('PAPERWHALE_DATA'):
    raise SystemExit('Run through tests/ai_conversations_ui.mjs')
os.environ['PAPERWHALE_LLM_MOCK'] = '1'

from paperwhale_server import config, db
from paperwhale_server.services import metadata, websearch

config.update_settings({'profile':'研究偏振成像', 'scholar_auto_sync':False,'pdf_browser_session_enabled':False,
                        'clipboard_watch':False,'clipboard_title_guess':False})
db.init_db()
for pid, title in [('a','ALPHA optics study'), ('b','BETA biology study')]:
    path = config.DATA_DIR / (pid+'.pdf')
    shutil.copyfile(ROOT/'tests/fixtures/attention.pdf', path)
    db.execute('INSERT INTO papers(id,title,file_path,status,abstract,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
               (pid,title,str(path),'indexed',title+' abstract',db.now(),db.now()))
    db.execute('INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)', (pid,1,title+' evidence'))
metadata.repair_bad_titles = lambda: None
websearch.search = lambda *args, **kwargs: []

if os.environ.get('PAPERWHALE_UI_AUDIT'):
    titles=['Polarization-resolved computational imaging with a physics-informed neural representation',
            'Broadband silicon photonic devices for programmable optical neural networks',
            '面向复杂场景的偏振超分辨成像与可解释重建方法']
    for n in range(230):
        category=['光学工程 / 偏振成像 / 超分辨','人工智能 / 物理约束学习','光学工程 / 光子计算'][n%3]
        pid=f'audit{n:03}'
        db.execute('INSERT INTO papers(id,title,authors,year,venue,category,category_path,status,file_path,summary,tags,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)',
                   (pid,titles[n%3]+f' — study {n+1}','Alice Zhang; Bo Chen; Carol Wang','2025',
                    ['Nature Photonics','Optics Express','IEEE Transactions on Computational Imaging'][n%3],
                    category,json.dumps(category.split(' / '),ensure_ascii=False),
                    ['indexed','analyzed','new','failed'][n%4],str(config.DATA_DIR/'a.pdf') if n%4 else '',
                    '## 核心贡献\n提出结合物理先验的重建方法。\n\n## 方法与实验\n这里是用于布局审查的合成摘要。',
                    json.dumps(['偏振成像','计算成像','物理约束'],ensure_ascii=False),db.now(),db.now()))
    db.execute('INSERT INTO papers(id,title,status,file_path) VALUES(?,?,?,?)',
               ('broken','A paper whose previously stored PDF has been moved','indexed',str(config.DATA_DIR/'missing.pdf')))

from paperwhale_server.main import app
import uvicorn

uvicorn.run(app, host='127.0.0.1', port=int(sys.argv[1]), log_level='warning')
