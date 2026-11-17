"""Visible feedback fixture: isolated data, deterministic model output, no network."""
import os
import runpy
import sys
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
if not os.environ.get('PAPERWHALE_UI_TEST') or not os.environ.get('PAPERWHALE_DATA'):
    raise SystemExit('Run through tests/ai_feedback_ui.mjs')
os.environ['PAPERWHALE_LLM_MOCK']='1'
from paperwhale_server import config, llm
from paperwhale_server.services import ai_progress
config.update_settings({'reasoning_model':'fixture-reasoner'})
original=llm.chat

def model(messages, **kwargs):
    if 'PROGRESS_TEST' not in str(messages[-1].get('content','')):
        return original(messages, **kwargs)
    ai_progress.emit(stage='核对检索证据')
    reasoning='本轮模型分析摘要：核对选定论文中的证据边界。'
    for fragment in [reasoning[:10],reasoning[10:]]:
        if ai_progress.cancelled():
            raise llm.LLMError('生成已停止')
        ai_progress.emit(reasoning_delta=fragment)
        time.sleep(.6)
    ai_progress.emit(stage='生成回答')
    answer='基于本轮资料作答 [1]。'
    ai_progress.emit(answer_delta=answer)
    time.sleep(.6)
    return {'content':answer,'reasoning':reasoning,'model':'fixture'} if kwargs.get('with_meta') else answer
llm.chat=model
runpy.run_path(str(ROOT/'tests/ai_ui_server.py'),run_name='__main__')
