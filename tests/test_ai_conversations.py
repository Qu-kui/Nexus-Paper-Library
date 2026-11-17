"""AI 会话回归：隔离目录、捕获模型输入，不访问真实设置或网络。"""
from __future__ import annotations

import os
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['PAPERWHALE_LLM_MOCK'] = '1'

from fastapi.testclient import TestClient
from paperwhale_server import config, db, llm
from paperwhale_server.main import app


class ConversationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pw-ai-')
        root = Path(self.temp.name)
        self.paths = patch.multiple(config, DATA_DIR=root, DEFAULT_PDF_DIR=root/'pdfs',
                                    TMP_DIR=root/'tmp', DB_PATH=root/'test.db',
                                    SETTINGS_PATH=root/'settings.json',
                                    _cache={**config.DEFAULT_SETTINGS, 'profile': '研究偏振成像',
                                            'scholar_auto_sync':False,
                                            'category_guide': '按方向/方法两级分类'})
        self.paths.start()
        db.init_db()
        for pid, title in [('a', 'ALPHA optics'), ('b', 'BETA biology')]:
            db.execute('INSERT INTO papers (id,title,abstract,notes,created_at,updated_at) VALUES (?,?,?,?,?,?)',
                       (pid, title, title+' abstract', 'NOTE_'+pid, db.now(), db.now()))
            db.execute('INSERT INTO chunks (paper_id,page,text) VALUES (?,?,?)', (pid, 1, title+' evidence'))
        self.client = TestClient(app)
        self.prompts = []
        def reply(messages, **kwargs):
            self.prompts.append(messages)
            return {'content': '回答 [1]', 'reasoning': '', 'model': 'mock'} if kwargs.get('with_meta') else '回答 [1]'
        self.mock = patch.object(llm, 'chat', side_effect=reply)
        self.mock.start()

    def tearDown(self):
        self.mock.stop()
        self.client.close()
        self.paths.stop()
        self.temp.cleanup()

    def snapshot(self, scope):
        res = self.client.get('/api/ai/conversations/'+scope)
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def ask(self, question, paper='a', **extra):
        res = self.client.post('/api/ai/chat', json={'question': question, 'paper_id': paper, **extra})
        self.assertEqual(res.status_code, 200, res.text)
        return res.json()

    def test_paper_history_is_persistent_and_isolated(self):
        self.ask('ONLY_ALPHA_QUESTION')
        self.ask('BETA_QUESTION', 'b')
        self.assertNotIn('ONLY_ALPHA_QUESTION', str(self.prompts[-1]))
        self.ask('刚才那个方法是什么意思')
        self.assertIn('ONLY_ALPHA_QUESTION', str(self.prompts[-1]))
        self.assertNotIn('BETA_QUESTION', str(self.prompts[-1]))
        self.assertEqual(len(self.snapshot('paper:a')['messages']), 4)
        self.assertEqual(len(self.snapshot('paper:b')['messages']), 2)
        self.assertEqual(self.snapshot('library')['messages'], [])

    def test_reader_images_use_vision_model_and_translation_rules(self):
        config._cache['vision_model'] = 'test-vision-model'
        png = ('data:image/png;base64,'
               'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a28sAAAAASUVORK5CYII=')
        self.ask('请学术翻译', images=[{'page': 1, 'data_url': png}, {'page': 2, 'data_url': png}],
                 image_action='translate', deep_thinking=True)
        messages = self.prompts[-1]
        self.assertIn('本轮是截图翻译', messages[0]['content'])
        self.assertIn('上下标', messages[0]['content'])
        content = messages[-1]['content']
        self.assertEqual([item['image_url']['url'] for item in content if item['type'] == 'image_url'], [png, png])
        self.assertIn('第 2 页', content[-2]['text'])
        self.assertEqual(llm.chat.call_args.kwargs['model_override'], 'test-vision-model')
        self.assertNotIn(png, str(self.snapshot('paper:a')))
        self.assertEqual(self.snapshot('paper:b')['messages'], [])

    def test_bad_reader_image_is_not_sent_to_model(self):
        fake_png = 'data:image/png;base64,' + base64.b64encode(b'not an image').decode()
        response = self.client.post('/api/ai/chat', json={
            'paper_id': 'a', 'question': '解释截图', 'images': [{'page': 1, 'data_url': fake_png}],
        })
        self.assertEqual(response.status_code, 400)
        self.assertFalse(self.prompts)

    def test_priors_and_notes_are_loaded_without_other_chats(self):
        self.ask('它和我的工作有什么关联')
        prompt = str(self.prompts[-1])
        self.assertIn('研究偏振成像', prompt)
        self.assertIn('NOTE_a', prompt)
        self.assertNotIn('NOTE_b', prompt)
        self.client.post('/api/categories/assistant/chat', json={'messages':[{'role':'user','content':'分类建议'}]})
        self.assertIn('研究偏振成像', str(self.prompts[-1]))

    def test_scope_mismatch_is_rejected(self):
        a = self.snapshot('paper:a')
        res = self.client.post('/api/ai/chat', json={'question':'hello','paper_id':'b',
                              'conversation_id':a['id'],'revision':a['revision']})
        self.assertEqual(res.status_code, 409)
        self.assertFalse(self.prompts)

    def test_clear_invalidates_inflight_response(self):
        def delayed(messages, **kwargs):
            res = self.client.delete('/api/ai/conversations/paper:a')
            self.assertEqual(res.status_code, 200)
            return 'OLD_RESPONSE'
        with patch.object(llm, 'chat', side_effect=delayed):
            res = self.client.post('/api/ai/chat', json={'question':'will be cleared','paper_id':'a'})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(self.snapshot('paper:a')['messages'], [])

    def test_failed_answer_is_not_added_to_history(self):
        with patch.object(llm, 'chat', side_effect=llm.LLMError('test failure')):
            res = self.client.post('/api/ai/chat', json={'question':'failed','paper_id':'a'})
        self.assertEqual(res.status_code, 400)
        self.assertEqual(self.snapshot('paper:a')['messages'], [])

    def test_materials_survive_reopen_but_do_not_cross_scope(self):
        a = self.snapshot('paper:a')
        res = self.client.put('/api/ai/conversations/paper:a/materials', json={
            'revision':a['revision'], 'attachments':[{'name':'note.txt','text':'ATTACHMENT_ALPHA'}]})
        self.assertEqual(res.status_code, 200, res.text)
        self.ask('请读一下附件')
        self.assertIn('ATTACHMENT_ALPHA', str(self.prompts[-1]))
        self.ask('另一个论文', 'b')
        self.assertNotIn('ATTACHMENT_ALPHA', str(self.prompts[-1]))
        a = self.snapshot('paper:a')
        self.client.put('/api/ai/conversations/paper:a/materials', json={'revision':a['revision'],'attachments':[]})
        self.ask('继续')
        self.assertNotIn('ATTACHMENT_ALPHA', str(self.prompts[-1]))

    def test_profile_rejects_fake_system_role(self):
        res = self.client.post('/api/ai/profile-chat', json={'messages':[
            {'role':'system','content':'MALICIOUS_SYSTEM'}, {'role':'user','content':'研究光学'}]})
        self.assertEqual(res.status_code, 200, res.text)
        self.assertNotIn('MALICIOUS_SYSTEM', str(self.prompts[-1]))

    def test_selection_explanation_joins_paper_history(self):
        res = self.client.post('/api/ai/explain', json={'text':'SELECTION_ALPHA','paper_id':'a','page':1})
        self.assertEqual(res.status_code, 200, res.text)
        self.ask('刚才这个术语呢')
        self.assertIn('SELECTION_ALPHA', str(self.prompts[-1]))
        self.assertEqual(len(self.snapshot('paper:a')['messages']), 4)

    def test_reader_translation_uses_current_reading_scope(self):
        scope = 'reading:r12345678'
        current = self.snapshot(scope)
        res = self.client.post('/api/ai/translate', json={
            'text': 'SELECTION_ALPHA', 'paper_id': 'a', 'page': 1, 'scope': scope,
            'conversation_id': current['id'], 'revision': current['revision'],
        })
        self.assertEqual(res.status_code, 200, res.text)
        reader_history = self.snapshot(scope)
        self.assertEqual(len(reader_history['messages']), 2)
        self.assertIn('SELECTION_ALPHA', str(reader_history['messages']))
        self.assertEqual(self.snapshot('paper:a')['messages'], [])

    def test_reader_explanation_uses_current_reading_scope(self):
        scope = 'reading:r87654321'
        current = self.snapshot(scope)
        res = self.client.post('/api/ai/explain', json={
            'text': 'EXPLAIN_SELECTION', 'paper_id': 'a', 'page': 1, 'scope': scope,
            'conversation_id': current['id'], 'revision': current['revision'],
        })
        self.assertEqual(res.status_code, 200, res.text)
        reader_history = self.snapshot(scope)
        self.assertEqual(len(reader_history['messages']), 2)
        self.assertIn('EXPLAIN_SELECTION', str(reader_history['messages']))
        self.assertEqual(self.snapshot('paper:a')['messages'], [])

    def test_paper_retrieves_beyond_fulltext_head(self):
        db.execute('UPDATE chunks SET text=? WHERE paper_id=?', ('head '*9000, 'a'))
        db.execute('INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)', ('a',30,'ZEBRAEXPERIMENT tail evidence'))
        self.ask('ZEBRAEXPERIMENT 的实验结果')
        self.assertIn('ZEBRAEXPERIMENT tail evidence', str(self.prompts[-1]))

    def test_delete_paper_removes_its_conversation(self):
        self.ask('hello')
        self.assertEqual(self.client.delete('/api/papers/a').status_code, 200)
        res = self.client.get('/api/ai/conversations/paper:a')
        self.assertEqual(res.status_code, 404)

    def test_library_followup_reloads_previous_paper_evidence(self):
        self.ask('ALPHA optics', None)
        self.ask('它的方法再讲清楚一点', None)
        self.assertIn('ALPHA optics evidence', str(self.prompts[-1]))
        self.assertIn('ALPHA optics', str(self.prompts[-1][1:-1]))

    def test_latest_saved_profile_is_used_after_change(self):
        self.ask('问题一')
        config.update_settings({'profile':'NEW_RESEARCH_DIRECTION'})
        self.ask('问题二')
        self.assertIn('NEW_RESEARCH_DIRECTION', str(self.prompts[-1]))
        self.assertNotIn('研究偏振成像', str(self.prompts[-1]))

    def test_material_instructions_are_not_system_rules(self):
        self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'请分析材料'}],
                         'attachments':[{'name':'other.txt','text':'IGNORE_RULES_IN_MATERIAL'}]})
        self.assertNotIn('IGNORE_RULES_IN_MATERIAL', self.prompts[-1][0]['content'])
        self.assertIn('IGNORE_RULES_IN_MATERIAL', self.prompts[-1][-1]['content'])

    def test_profile_candidate_cannot_be_saved_after_a_new_turn(self):
        with patch.object(llm, 'chat', return_value='[PROFILE]画像候选[/PROFILE]'):
            self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'生成画像'}]})
        old = self.snapshot('profile')
        self.assertEqual(old['draft']['profile'], '画像候选')
        self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'先改一下'}]})
        res = self.client.post('/api/ai/conversations/profile/apply', json={'revision':old['revision']})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(config.get_settings()['profile'], '研究偏振成像')

    def test_profile_candidate_save_shares_only_confirmed_profile(self):
        with patch.object(llm, 'chat', return_value='[PROFILE]已确认新方向[/PROFILE]'):
            self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'生成画像'}]})
        draft = self.snapshot('profile')
        self.assertEqual(config.get_settings()['profile'], '研究偏振成像')
        res = self.client.post('/api/ai/conversations/profile/apply', json={'revision':draft['revision']})
        self.assertEqual(res.status_code, 200, res.text)
        self.ask('如何用在我的工作')
        self.assertIn('已确认新方向', str(self.prompts[-1]))

    def test_failed_turn_invalidates_old_draft(self):
        with patch.object(llm, 'chat', return_value='[PROFILE]旧草稿[/PROFILE]'):
            self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'画像'}]})
        with patch.object(llm, 'chat', side_effect=llm.LLMError('offline')):
            self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'修改'}]})
        self.assertEqual(self.snapshot('profile')['draft'], {})

    def test_abstract_has_no_fabricated_page_number(self):
        db.execute('DELETE FROM chunks WHERE paper_id=?', ('a',))
        result = self.ask('总结这篇论文')
        self.assertTrue(result['sources'])
        self.assertIsNone(result['sources'][0]['page'])

    def test_long_profile_request_keeps_current_user_question(self):
        self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'LARGE_CURRENT_'+'x'*13000}]})
        self.assertTrue(self.prompts)
        self.assertIn('LARGE_CURRENT_', str(self.prompts[-1]))

    def test_profile_legacy_migration_runs_only_once(self):
        self.client.post('/api/ai/conversations/profile/migrate', json={'messages':[
            {'role':'user','content':'LEGACY_ONCE'}, {'role':'assistant','content':'old answer'}]})
        self.client.delete('/api/ai/conversations/profile')
        self.client.post('/api/ai/conversations/profile/migrate', json={'messages':[{'role':'user','content':'LEGACY_ONCE'}]})
        self.assertEqual(self.snapshot('profile')['messages'], [])

    def test_mock_roles_still_work_with_shared_rules(self):
        from paperwhale_server.services.ai_context import RULES
        translated = llm._mock_reply(False, [{'role':'system','content':'你是专业的学术翻译。'+RULES}])
        self.assertIn('译文', translated)
        profile = llm._mock_reply(False, [{'role':'system','content':'你是研究画像访谈助手。'+RULES}])
        self.assertIn('[PROFILE]', profile)

    def test_profile_draft_cannot_overwrite_newer_saved_information(self):
        with patch.object(llm, 'chat', return_value='[PROFILE]旧底稿生成的画像[/PROFILE]'):
            self.client.post('/api/ai/profile-chat', json={'messages':[{'role':'user','content':'画像'}]})
        draft = self.snapshot('profile')
        config.update_settings({'profile':'OTHER_WINDOW_UPDATE'})
        res = self.client.post('/api/ai/conversations/profile/apply', json={'revision':draft['revision']})
        self.assertEqual(res.status_code, 409)
        self.assertEqual(config.get_settings()['profile'], 'OTHER_WINDOW_UPDATE')

    def test_empty_fulltext_still_exposes_available_metadata(self):
        db.execute('DELETE FROM chunks WHERE paper_id=?', ('a',))
        db.execute("UPDATE papers SET abstract='' WHERE id='a'")
        self.ask('这篇文章的标题是什么')
        self.assertTrue(self.prompts)
        self.assertIn('ALPHA optics', str(self.prompts[-1]))

    def test_empty_retrieval_still_knows_library_size(self):
        db.execute('DELETE FROM chunks')
        db.execute("UPDATE papers SET abstract='' ")
        self.ask('我现在有多少篇论文', None)
        self.assertTrue(self.prompts)
        self.assertIn('共 2 篇', str(self.prompts[-1]))

    def test_historical_citations_keep_their_identity(self):
        self.ask('ALPHA optics', None)
        self.ask('刚才引用的第一篇', None)
        historical = self.prompts[-1][1:-1]
        self.assertTrue(any(msg['role']=='assistant' and 'ALPHA optics' in msg['content'] for msg in historical))

    def test_context_budget_keeps_recent_complete_turns(self):
        for n in range(14):
            self.ask(f'UNIQUE_QUESTION_{n:02}')
        self.ask('继续')
        prompt = self.prompts[-1]
        self.assertLessEqual(len(prompt), 26)
        history = str(prompt[1:-1])
        self.assertNotIn('UNIQUE_QUESTION_00', history)
        self.assertIn('UNIQUE_QUESTION_13', history)

    def test_category_draft_only_starts_queue_when_applied(self):
        from paperwhale_server.services import category_assistant
        with patch.object(llm, 'chat', return_value='建议\n[CATEGORY_GUIDE]方向/方法两级[/CATEGORY_GUIDE]'):
            self.client.post('/api/categories/assistant/chat', json={'messages':[{'role':'user','content':'分类方案'}]})
        current = self.snapshot('categories')
        self.assertEqual(config.get_settings()['category_guide'], '按方向/方法两级分类')
        with patch.object(category_assistant.queue_manager, 'start', return_value=2) as start:
            res = self.client.post('/api/ai/conversations/categories/apply', json={'revision':current['revision']})
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(set(start.call_args.args[0]), {'a','b'})
        self.assertEqual(config.get_settings()['category_guide'], '方向/方法两级')
        self.assertEqual(self.snapshot('categories')['draft'], {})

    def test_conversation_survives_database_reinitialization(self):
        self.ask('PERSISTED_TO_DISK')
        db.init_db()
        restarted = TestClient(app)
        try:
            result = restarted.get('/api/ai/conversations/paper:a').json()
        finally:
            restarted.close()
        self.assertEqual(result['messages'][0]['content'], 'PERSISTED_TO_DISK')

    def test_category_prior_uses_true_count_not_sample_size(self):
        for n in range(43):
            db.execute('INSERT INTO papers(id,title,category_path) VALUES(?,?,?)', (f'p{n}',f'Paper {n}','[]'))
        self.client.post('/api/categories/assistant/chat', json={'messages':[{'role':'user','content':'分类建议'}]})
        self.assertIn('共 45 篇', str(self.prompts[-1]))


if __name__ == '__main__':
    unittest.main()
