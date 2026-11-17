"""联网意图与「强制联网」兜底：用户明确要上网时模型不许说自己不会联网。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db
from paperwhale_server.services import rag


class WebIntentTests(unittest.TestCase):
    def test_explicit_requests_are_detected(self):
        for question in ("你上网查一下", "帮我搜一下最新进展", "联网看看有什么新论文", "查一下最新进展"):
            self.assertTrue(rag._wants_web_search(question), question)

    def test_normal_paper_questions_do_not_force_web(self):
        for question in ("这篇论文的方法是什么", "解释一下图 3", "它的限制在哪"):
            self.assertFalse(rag._wants_web_search(question), question)


class AnswerLlmWebTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-webintent-")
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            _cache={**config.DEFAULT_SETTINGS, "model": "deepseek-flash",
                    "base_url": "https://api.deepseek.com/v1", "api_key": "sk-test"},
        )
        self.paths.start()
        db.init_db()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def _run(self, replies, **kwargs):
        calls = []

        def fake_chat(messages, **opts):
            calls.append(messages[0]["content"])
            return replies[min(len(calls) - 1, len(replies) - 1)]

        with patch.object(rag.llm, "chat", side_effect=fake_chat):
            result = rag._answer_llm("系统提示", "用户问题", **kwargs)
        return calls, result

    def test_tool_notice_is_added_when_tools_available(self):
        calls, _ = self._run(
            [{"content": "答", "model": "deepseek-flash", "web_trace": [{"type": "search"}],
              "server_tool_calls": [{}]}],
            server_tools=[rag.llm.SERVER_TOOL_SEARCH],
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("已开启内置联网工具", calls[0])

    def test_retries_once_when_web_required_but_not_used(self):
        calls, result = self._run(
            [
                {"content": "我没有联网能力", "model": "deepseek-flash", "web_trace": [], "server_tool_calls": []},
                {"content": "查到三条新结果", "model": "deepseek-flash",
                 "web_trace": [{"type": "search"}], "server_tool_calls": [{"index": 0}]},
            ],
            server_tools=[rag.llm.SERVER_TOOL_SEARCH],
            require_web=True,
        )
        self.assertEqual(len(calls), 2)
        self.assertIn("强制", calls[1])
        self.assertEqual(result[0], "查到三条新结果")
        self.assertTrue(result[3]["web_trace"])

    def test_no_retry_when_web_not_required(self):
        calls, _ = self._run(
            [{"content": "普通回答", "model": "deepseek-flash", "web_trace": [], "server_tool_calls": []}],
            server_tools=[rag.llm.SERVER_TOOL_SEARCH],
            require_web=False,
        )
        self.assertEqual(len(calls), 1)

    def test_no_retry_when_tool_already_used(self):
        calls, _ = self._run(
            [{"content": "答", "model": "deepseek-flash", "web_trace": [{"type": "search"}],
              "server_tool_calls": [{}]}],
            server_tools=[rag.llm.SERVER_TOOL_SEARCH],
            require_web=True,
        )
        self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
