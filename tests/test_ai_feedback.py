"""AI feedback regressions: isolated storage and simulated OpenAI SSE."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from paperwhale_server import config, db, llm
from paperwhale_server.services import rag

class FeedbackTests(unittest.TestCase):
    def test_stream_exposes_actual_deltas_and_preserves_return_contract(self):
        events = []
        stream = '\n\n'.join('data: '+json.dumps(x) for x in [
            {'choices':[{'delta':{'reasoning_content':'证据不足。'}}]},
            {'choices':[{'delta':{'content':'回答 [1]'}}]},
        ])+'\n\ndata: [DONE]\n\n'
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=stream, headers={'content-type':'text/event-stream'}))
        real_client = httpx.Client
        with patch.object(llm, 'is_mock', return_value=False), patch.object(llm, '_resolve_config', return_value={'base_url':'https://test/v1','api_key':'x','model':'m','temperature':.2}), patch.object(llm.httpx, 'Client', side_effect=lambda **kw: real_client(transport=transport, **kw)):
            result = llm.chat([{'role':'user','content':'hello'}], with_meta=True, on_delta=lambda **kw: events.append(kw))
        self.assertEqual(result['content'], '回答 [1]')
        self.assertEqual(result['reasoning'], '证据不足。')
        self.assertIn({'reasoning_delta':'证据不足。'}, events)
        self.assertIn({'answer_delta':'回答 [1]'}, events)

    def test_uncited_retrieval_is_not_reported_as_used_evidence(self):
        with tempfile.TemporaryDirectory() as name:
            root = Path(name)
            with patch.multiple(config, DB_PATH=root/'test.db', _cache={**config.DEFAULT_SETTINGS}):
                db.init_db()
                for pid in ('a','b'):
                    db.execute('INSERT INTO papers(id,title,abstract,created_at,updated_at) VALUES(?,?,?,?,?)', (pid,pid,'abstract '+pid,db.now(),db.now()))
                    db.execute('INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)',(pid,1,'optics '+pid))
                with patch.object(rag,'_answer_llm',return_value=('没有明确引用','', 'm')):
                    result = rag.chat('optics', paper_ids=['a'])
                self.assertEqual(result['sources'], [])
                self.assertEqual({r['paper_id'] for r in result['retrieval_candidates']}, {'a'})
                self.assertEqual(result['retrieval_candidates'][0]['source_kind'], 'original')

    def test_stream_rejection_retries_without_stream_and_publishes_only_returned_text(self):
        calls, events = [], []
        def respond(request):
            payload=json.loads(request.content)
            calls.append(payload['stream'])
            if payload['stream']:
                return httpx.Response(400,text='stream unsupported')
            return httpx.Response(200,json={'choices':[{'message':{'content':'真实回答','reasoning_content':'简短分析'}}]})
        transport=httpx.MockTransport(respond)
        real_client=httpx.Client
        with patch.object(llm,'is_mock',return_value=False), patch.object(llm,'_resolve_config',return_value={'base_url':'https://test/v1','api_key':'x','model':'m','temperature':.2}), patch.object(llm.httpx,'Client',side_effect=lambda **kw: real_client(transport=transport,**kw)):
            result=llm.chat([{'role':'user','content':'hello'}],on_delta=lambda **kw:events.append(kw),progress_channel='reasoning')
        self.assertEqual(calls,[True,False])
        self.assertEqual(result,'真实回答')
        self.assertEqual(events,[{'reasoning_delta':'简短分析'},{'reasoning_delta':'真实回答'}])

    def test_progress_is_isolated_by_scope_id_and_revision(self):
        from paperwhale_server.services import ai_progress as progress
        token=progress.begin('paper:a','c',5)
        try:
            progress.emit(stage='检索原文',reasoning_delta='真实摘要')
            self.assertEqual(progress.snapshot('paper:a','c',5)['reasoning'],'真实摘要')
            self.assertEqual(progress.snapshot('paper:b','c',5)['state'],'idle')
            self.assertEqual(progress.snapshot('paper:a','c',6)['reasoning'],'')
            self.assertTrue(progress.has_active())
            progress.cancel('paper:a')
            progress.emit(reasoning_delta='不能加入')
        finally:
            progress.finish(token)
        self.assertEqual(progress.snapshot('paper:a','c',5)['state'],'stopped')
        self.assertEqual(progress.snapshot('paper:a','c',5)['reasoning'],'真实摘要')

    def test_each_citation_has_a_card_even_when_same_page_is_cited_twice(self):
        items=[{'index':1,'paper_id':'a','page':1,'source_kind':'original'},
               {'index':2,'paper_id':'a','page':1,'source_kind':'original'},
               {'index':1001,'title':'附件','source_kind':'attachment'}]
        result=rag._evidence_result('论据 [1][2]','','m',items)
        self.assertEqual([s['citation_index'] for s in result['sources']],[1,2])
        self.assertEqual([s['citation_index'] for s in result['retrieval_candidates']],[1001])

if __name__ == '__main__': unittest.main()
