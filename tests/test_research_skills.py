"""研究页两个新技能：多篇脉络梳理 / 单篇投稿匹配（不联网、不写真实数据）。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db, llm
from paperwhale_server.services import skills


class ResearchSkillsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-research-")
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            _cache={**config.DEFAULT_SETTINGS, "answer_context_chars": 60000},
        )
        self.paths.start()
        db.init_db()
        for pid, title, year in (("a", "ALPHA phase retrieval", "2019"), ("b", "BETA optical vortex", "2023")):
            db.execute(
                "INSERT INTO papers (id,title,abstract,key_points,created_at,updated_at) VALUES (?,?,?,?,?,?)",
                (pid, title, title + " abstract", "point-1", db.now(), db.now()),
            )
            db.execute("INSERT INTO chunks (paper_id,page,text) VALUES (?,?,?)", (pid, 1, title + " full text"))
        self.calls = []

        def fake_chat(messages, **kwargs):
            self.calls.append({"messages": messages, "kwargs": kwargs})
            return "## 一、脉络总览\n示例 [1][2]"

        self.chat_patch = patch.object(llm, "chat", side_effect=fake_chat)
        self.chat_patch.start()

    def tearDown(self):
        self.chat_patch.stop()
        self.paths.stop()
        self.temp.cleanup()

    def test_catalog_lists_new_skills(self):
        items = {item["id"]: item for item in skills.list_skills()}
        self.assertIn("literature-map", items)
        self.assertIn("journal-fit", items)
        self.assertEqual(items["literature-map"]["input"], "multi")
        self.assertEqual(items["journal-fit"]["input"], "single")
        self.assertTrue(items["literature-map"]["runnable"])

    def test_literature_map_needs_two_papers(self):
        with self.assertRaises(ValueError):
            skills.run_skill("literature-map", paper_ids=["a"])
        self.assertEqual(self.calls, [], "参数不够时不该调用模型")

    def test_literature_map_builds_numbered_context(self):
        result = skills.run_skill("literature-map", paper_ids=["a", "b"])
        self.assertEqual(result["skill_id"], "literature-map")
        self.assertEqual(result["paper_ids"], ["a", "b"])
        self.assertIn("示例", result["text"])
        prompt = self.calls[0]["messages"][1]["content"]
        self.assertIn("[1] 标题：ALPHA phase retrieval", prompt)
        self.assertIn("[2] 标题：BETA optical vortex", prompt)
        self.assertIn("只允许用上面的编号", prompt)
        system = self.calls[0]["messages"][0]["content"]
        self.assertIn("研究脉络", system)

    def test_literature_map_skips_missing_paper(self):
        result = skills.run_skill("literature-map", paper_ids=["a", "b", "gone"])
        self.assertEqual(result["paper_ids"], ["a", "b", "gone"])
        prompt = self.calls[0]["messages"][1]["content"]
        self.assertNotIn("gone", prompt)
        self.assertIn("共 2 篇", prompt)

    def test_journal_fit_uses_single_paper(self):
        result = skills.run_skill("journal-fit", paper_id="a")
        self.assertEqual(result["paper_id"], "a")
        prompt = self.calls[0]["messages"][1]["content"]
        self.assertIn("ALPHA phase retrieval", prompt)
        self.assertNotIn("BETA optical vortex", prompt)
        system = self.calls[0]["messages"][0]["content"]
        self.assertIn("投稿匹配", system)

    def test_journal_fit_requires_paper(self):
        with self.assertRaises(ValueError):
            skills.run_skill("journal-fit", question="随便聊聊")

    def test_single_mode_keeps_first_of_many(self):
        result = skills.run_skill("journal-fit", paper_ids=["b", "a"])
        self.assertEqual(result["paper_id"], "b")
        prompt = self.calls[0]["messages"][1]["content"]
        self.assertIn("BETA optical vortex", prompt)


if __name__ == "__main__":
    unittest.main()
