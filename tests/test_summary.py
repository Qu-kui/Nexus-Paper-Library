"""长对话滚动摘要：触发、落库、prompt 注入与关闭开关。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['PAPERWHALE_LLM_MOCK'] = '1'

from paperwhale_server import config, db, llm
from paperwhale_server.services import ai_context, conversations


class SummaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pw-summary-')
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config, DATA_DIR=root, DEFAULT_PDF_DIR=root / 'pdfs', TMP_DIR=root / 'tmp',
            DB_PATH=root / 'test.db', SETTINGS_PATH=root / 'settings.json',
            _cache={**config.DEFAULT_SETTINGS},
        )
        self.paths.start()
        db.init_db()
        self.scope = conversations.reading_scope('sessSUMMAR')
        conversations.snapshot(self.scope)

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def _messages(self, count: int, size: int = 1000):
        return [{'id': index + 1, 'role': 'user' if index % 2 == 0 else 'assistant',
                 'content': f'第{index}条 ' + '内容' * size} for index in range(count)]

    def test_compact_folds_old_messages_and_persists(self):
        with patch.object(llm, 'chat_text', return_value='用户在研究偏振成像；已确认使用 532 nm。') as mocked:
            compacted = conversations._compact_history(self.scope, self._messages(20))
        self.assertEqual(compacted[0]['role'], 'system')
        self.assertIn('[早前对话摘要]', compacted[0]['content'])
        self.assertLess(len(compacted), 20, '旧消息应被折叠')
        row = db.query_one('SELECT summary, summary_upto FROM ai_conversations WHERE scope=?', (self.scope,))
        self.assertIn('偏振成像', row['summary'])
        self.assertGreater(row['summary_upto'], 0)
        self.assertEqual(mocked.call_count, 1)
        # 已覆盖的消息不再重复摘要
        with patch.object(llm, 'chat_text', return_value='不应再调用') as again:
            conversations._compact_history(self.scope, self._messages(20))
        self.assertEqual(again.call_count, 0)

    def test_summary_can_be_disabled(self):
        config.update_settings({'auto_summary': False})
        messages = self._messages(20)
        with patch.object(llm, 'chat_text', return_value='不该调用') as mocked:
            self.assertEqual(conversations._compact_history(self.scope, messages), messages)
        self.assertEqual(mocked.call_count, 0)

    def test_history_messages_keeps_summary_prefix(self):
        history = [{'role': 'system', 'content': '[早前对话摘要]\n关键结论'},
                   {'role': 'user', 'content': '继续'}, {'role': 'assistant', 'content': '好'}]
        kept = ai_context.history_messages(history)
        self.assertEqual(kept[0]['role'], 'system')
        self.assertIn('关键结论', kept[0]['content'])

    def test_turn_passes_summary_into_generate(self):
        conversation = conversations.snapshot(self.scope)
        for index in range(16):
            db.execute('INSERT INTO ai_messages(conversation_id,role,content,metadata,created_at) VALUES (?,?,?,?,?)',
                       (conversation['id'], 'user' if index % 2 == 0 else 'assistant',
                        f'消息{index} ' + '细节' * 900, '{}', db.now()))
        captured = {}

        def generate(history, materials):
            captured['history'] = history
            return {'answer': '收到', 'sources': [], 'reasoning': ''}

        with patch.object(llm, 'chat_text', return_value='早前聊过的关键结论'):
            conversations.turn(self.scope, '继续', generate)
        self.assertTrue(any(item.get('role') == 'system' and '早前对话摘要' in item.get('content', '')
                            for item in captured['history']))
        row = db.query_one('SELECT summary FROM ai_conversations WHERE scope=?', (self.scope,))
        self.assertEqual(row['summary'], '早前聊过的关键结论')


if __name__ == '__main__':
    unittest.main()
