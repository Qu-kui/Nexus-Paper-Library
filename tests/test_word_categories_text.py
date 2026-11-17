# -*- coding: utf-8 -*-
"""Word 分类下拉数据源：/api/word/categories.txt 的逐字节契约。

行格式 ``<depth>\\t<path>\\t<count>``，count 是整棵子树的论文数，顺序为树前序。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app
from paperwhale_server.routers import word


class WordCategoriesTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-word-categories-")
        self.root = Path(self.temp.name)
        self.settings = patch.multiple(
            config,
            DATA_DIR=self.root,
            DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp",
            DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False},
        )
        self.settings.start()
        self.env = patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "1"})
        self.env.start()
        db.init_db()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def add_paper(self, paper_id, title="Paper", category=""):
        db.execute(
            "INSERT INTO papers(id,title,category) VALUES(?,?,?)", (paper_id, title, category)
        )

    def fetch(self):
        response = self.client.get("/api/word/categories.txt")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            response.headers["content-type"].startswith("text/plain"),
            response.headers["content-type"],
        )
        self.assertFalse(response.content.startswith(b"\xef\xbb\xbf"), "UTF-8 不能带 BOM")
        self.assertNotIn(b"\r", response.content, "行分隔只能是 \\n")
        lines = response.text.split("\n")
        stats = lines[0].split("\t")
        rows = [line.split("\t") for line in lines[1:]] if len(lines) > 1 else []
        return response, stats, rows

    # ------------------------------------------------------------ 主场景
    def test_stats_and_preorder_with_subtree_counts(self):
        self.add_paper("p1", "Optics", "光学")
        self.add_paper("p2", "Meta 1", "光学 / 超表面")
        self.add_paper("p3", "Meta 2", "光学 / 超表面")
        self.add_paper("p4", "Struct", "光学 / 超表面 / 结构")
        self.add_paper("p5", "Bio", "生物")
        self.add_paper("p6", "No Category")
        response, stats, rows = self.fetch()
        self.assertEqual(stats, ["#stats", "6", "1", "4"])
        self.assertEqual(
            rows,
            [
                ["0", "光学", "4"],
                ["1", "光学 / 超表面", "3"],
                ["2", "光学 / 超表面 / 结构", "1"],
                ["0", "生物", "1"],
            ],
            "前序遍历 + depth + 子树篇数",
        )
        self.assertEqual(response.text.count("\n"), 4, "1 行 stats + 4 行分类，无尾随换行")

    def test_parent_count_includes_children(self):
        self.add_paper("p1", "Parent", "光学")
        for index in range(3):
            self.add_paper(f"c{index}", f"Child {index}", "光学 / 超表面")
        _, stats, rows = self.fetch()
        by_path = {row[1]: int(row[2]) for row in rows}
        self.assertEqual(by_path["光学"], 4)
        self.assertEqual(by_path["光学 / 超表面"], 3)
        self.assertEqual(stats[3], "2")

    # ------------------------------------------------------------ 边界
    def test_empty_library_only_stats_line(self):
        response, stats, rows = self.fetch()
        self.assertEqual(response.text, "#stats\t0\t0\t0")
        self.assertEqual(stats, ["#stats", "0", "0", "0"])
        self.assertEqual(rows, [])

    def test_synthetic_parent_row_is_emitted(self):
        """只有子类里挂了论文时，父节点也要输出，VBA 缩进才不会跳级。"""
        self.add_paper("p1", "Neuron", "生物 / 神经")
        _, stats, rows = self.fetch()
        self.assertEqual(rows, [["0", "生物", "1"], ["1", "生物 / 神经", "1"]])
        self.assertEqual(stats[3], "2")

    def test_control_chars_in_category_are_replaced(self):
        self.add_paper("p1", "Weird", "A\tB / C")
        response, _, rows = self.fetch()
        self.assertEqual(len(rows), 2, "父行 + 子行")
        for row in rows:
            self.assertEqual(len(row), 3, "字段里的 tab 不能把列拆错")
        self.assertEqual(rows[1][1], "A B / C")
        self.assertNotIn(b"\r", response.content)

    def test_percent_and_underscore_are_not_wildcards(self):
        self.add_paper("a", "Exact", "100%_test")
        self.add_paper("b", "Similar", "100Xtest")
        _, _, rows = self.fetch()
        self.assertEqual(
            rows, [["0", "100%_test", "1"], ["0", "100Xtest", "1"]], "两个分类各 1 篇，不互相命中"
        )

    def test_same_level_sorted_by_path_and_preorder(self):
        self.add_paper("p1", "A", "a")
        self.add_paper("p2", "AY", "a / y")
        self.add_paper("p3", "AZ", "a / z")
        self.add_paper("p4", "B", "b")
        self.add_paper("p5", "C", "c")
        _, _, rows = self.fetch()
        self.assertEqual([row[1] for row in rows], ["a", "a / y", "a / z", "b", "c"])

    def test_query_failure_returns_stats_only_not_500(self):
        with patch.object(word.db, "query", side_effect=RuntimeError("boom")):
            response = self.client.get("/api/word/categories.txt")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, "#stats\t0\t0\t0")


if __name__ == "__main__":
    unittest.main()
