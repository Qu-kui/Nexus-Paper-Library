"""引用上文：不能把系统插入的 "> " 当成问题内容，避免模型误读成大于号。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db
from paperwhale_server.services import ai_context, rag


class QuoteNormalizationTests(unittest.TestCase):
    def test_legacy_quote_marker_becomes_labelled_block(self):
        text = ai_context.normalize_quoted_text("> 1000 ℃ 这是什么意思\n\n后面那个 C 呢？")
        self.assertIn("用户引用的上文", text)
        self.assertIn("1000 ℃ 这是什么意思", text)
        self.assertIn("后面那个 C 呢？", text)
        self.assertFalse(text.lstrip().startswith(">"), "不能再以 > 开头，否则模型会当成大于号")

    def test_plain_question_untouched(self):
        self.assertEqual(ai_context.normalize_quoted_text("这篇论文讲了什么"), "这篇论文讲了什么")

    def test_history_messages_normalizes_user_quotes(self):
        messages = [
            {"role": "user", "content": "> 1150 °C 是什么意思\n\n解释一下"},
            {"role": "assistant", "content": "那是摄氏温度。"},
            {"role": "user", "content": "继续"},
        ]
        history = ai_context.history_messages(messages)
        self.assertIn("用户引用的上文", history[0]["content"])
        self.assertNotIn(">", history[0]["content"][:2])


class QuotedPromptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-quote-")
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            _cache={**config.DEFAULT_SETTINGS},
        )
        self.paths.start()
        db.init_db()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_quoted_text_goes_to_prompt_as_reference(self):
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return {"content": "回答", "reasoning": "", "model": "deepseek-flash",
                    "citations": [], "server_tool_calls": [], "web_trace": []}

        paper = {"id": "p", "title": "Paper", "abstract": "摘要", "file_path": ""}
        with patch.object(rag.db, "get_paper", return_value=paper), \
                patch.object(rag, "_paper_fulltext_rows", return_value=[]), \
                patch.object(rag, "_search_fts", return_value=[]), \
                patch.object(rag, "_search_like", return_value=[]), \
                patch.object(rag.llm, "chat", side_effect=fake_chat):
            rag.chat("这是什么意思？", paper_id="p", quoted_text="1000 ℃ 这是什么意思")
        system = captured["messages"][0]["content"]
        prompt = captured["messages"][-1]["content"]
        self.assertIn("用户引用了上文片段", prompt)
        self.assertIn("1000 ℃ 这是什么意思", prompt)
        self.assertIn("引用结束", prompt)
        self.assertIn("不要把它里面的 > 当成大于号", system)


if __name__ == "__main__":
    unittest.main()
