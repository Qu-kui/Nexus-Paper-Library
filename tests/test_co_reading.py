"""多篇共读上下文：同时打开的论文要一起进对话资料区。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['PAPERWHALE_LLM_MOCK'] = '1'

from paperwhale_server import config, db, llm
from paperwhale_server.services import rag


class CoReadingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pw-co-read-')
        root = Path(self.temp.name)
        self.paths = patch.multiple(config, DATA_DIR=root, DEFAULT_PDF_DIR=root/'pdfs',
                                    TMP_DIR=root/'tmp', DB_PATH=root/'test.db',
                                    SETTINGS_PATH=root/'settings.json',
                                    _cache={**config.DEFAULT_SETTINGS, 'profile': '研究偏振成像'})
        self.paths.start()
        db.init_db()
        self.add_paper('p1', 'PRIMARY optics', abstract='主论文摘要')
        self.add_paper('p2', 'BETA biology', abstract='第二篇摘要', key_points='["发现 A"]')
        self.add_paper('p3', 'GAMMA chemistry', abstract='第三篇摘要')
        self.add_paper('p4', 'DELTA materials', abstract='第四篇摘要')
        self.add_paper('p5', 'EPSILON climate', abstract='第五篇摘要')
        self.add_paper('p6', 'ZETA medicine', abstract='第六篇摘要')
        self.add_paper('empty', 'EMPTY paper')
        self.prompts = []
        def reply(messages, **kwargs):
            self.prompts.append(messages)
            return {'content': '回答 [1]', 'reasoning': '', 'model': 'mock'} if kwargs.get('with_meta') else '回答 [1]'
        self.mock = patch.object(llm, 'chat', side_effect=reply)
        self.mock.start()
        self.fulltext = patch.object(rag, '_paper_fulltext_rows', side_effect=self.fake_fulltext)
        self.fulltext.start()

    def tearDown(self):
        self.fulltext.stop()
        self.mock.stop()
        self.paths.stop()
        self.temp.cleanup()

    def add_paper(self, pid, title, abstract='', key_points='[]', venue=''):
        db.execute(
            'INSERT INTO papers (id,title,abstract,key_points,venue,year,created_at,updated_at)'
            ' VALUES (?,?,?,?,?,?,?,?)',
            (pid, title, abstract, key_points, venue, '2024', db.now(), db.now()),
        )

    def fake_fulltext(self, paper_id, limit=rag.FULLTEXT_LIMIT):
        """固定片段，避免测试依赖真实 PDF 或 chunks 表。"""
        if paper_id == 'empty' or limit <= 0:
            return []
        return [{'id': 1, 'paper_id': paper_id, 'page': 1, 'text': f'{paper_id} PDF 片段'[:limit]}]

    def ask(self, question, co_paper_ids=None):
        result = rag.chat(question, 'p1', co_paper_ids=co_paper_ids)
        system = self.prompts[-1][0]['content']
        user = self.prompts[-1][-1]['content']
        return result, system, user

    def test_two_co_papers_reach_prompt_with_source_rules(self):
        result, system, user = self.ask('这两篇的差异是什么', ['p2', 'p3'])
        self.assertIn('BETA biology', user)
        self.assertIn('GAMMA chemistry', user)
        self.assertIn('【共读论文 1：BETA biology】', user)
        self.assertIn('【共读论文 2：GAMMA chemistry】', user)
        self.assertIn('第二篇摘要', user)
        self.assertIn('p2 PDF 片段', user)
        # 共读块在主论文资料之后、【资料区结束】之前
        self.assertLess(user.index('【共读论文 1：BETA biology】'), user.index('【资料区结束】'))
        self.assertIn('共读论文', system)
        self.assertIn('标注论文来源', system)
        self.assertEqual([row['id'] for row in result['co_papers']], ['p2', 'p3'])

    def test_primary_paper_is_excluded_from_co_reading(self):
        result, _, user = self.ask('继续对比', ['p1', 'p2', 'p2'])
        self.assertEqual([row['id'] for row in result['co_papers']], ['p2'])
        self.assertIn('【共读论文 1：BETA biology】', user)
        self.assertNotIn('【共读论文 2：', user)

    def test_missing_or_empty_paper_is_skipped(self):
        result, _, user = self.ask('对比一下', ['ghost', 'empty', 'p2'])
        self.assertEqual([row['id'] for row in result['co_papers']], ['p2'])
        self.assertNotIn('ghost', user)
        self.assertNotIn('EMPTY paper', user)
        self.assertIn('BETA biology', user)

    def test_result_exposes_at_most_four_co_papers(self):
        result, _, _ = self.ask('一起看看', ['p2', 'p3', 'p4', 'p5', 'p6'])
        self.assertEqual([row['id'] for row in result['co_papers']], ['p2', 'p3', 'p4', 'p5'])
        for index, row in enumerate(result['co_papers'], start=1):
            self.assertEqual(row['index'], index)
            self.assertTrue(row['title'])

    def test_single_paper_chat_keeps_legacy_result(self):
        result, system, _ = self.ask('总结这篇论文')
        self.assertEqual(result['co_papers'], [])
        self.assertNotIn('共读论文', system)
        self.assertIn('当前入口仅讨论这篇论文', system)
        for key in ('answer', 'sources', 'retrieval_candidates', 'web_sources',
                    'external_sources', 'web_trace', 'reasoning', 'model'):
            self.assertIn(key, result)

    def test_co_reading_budget_is_capped(self):
        block, meta = rag._co_reading_context('p1', ['p2', 'p3', 'p4', 'p5'], 4000)
        self.assertEqual(len(meta), 4)
        self.assertLessEqual(len(block), 4000)
        huge = lambda pid, limit=rag.FULLTEXT_LIMIT: (
            [{'id': 1, 'paper_id': pid, 'page': 1, 'text': 'X' * limit}] if limit > 0 else []
        )
        with patch.object(rag, '_paper_fulltext_rows', side_effect=huge):
            block, _ = rag._co_reading_context('p1', ['p2', 'p3', 'p4', 'p5'], 999999)
        self.assertLessEqual(len(block), 12000)

    def test_chat_api_forwards_co_paper_ids(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app

        with patch.object(rag, 'chat', return_value={
            'answer': '回答 [1]', 'sources': [], 'co_papers': [], 'reasoning': '', 'model': 'mock',
        }) as chat:
            client = TestClient(app)
            try:
                response = client.post('/api/ai/chat', json={
                    'question': '对比一下', 'paper_id': 'p1', 'co_paper_ids': ['p2', 'p3', 'p4'],
                })
            finally:
                client.close()
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(chat.call_args.kwargs['co_paper_ids'], ['p2', 'p3', 'p4'])


if __name__ == '__main__':
    unittest.main()
