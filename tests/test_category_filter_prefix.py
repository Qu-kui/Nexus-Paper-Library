# -*- coding: utf-8 -*-
"""分类筛选按「文件夹」语义：选中一级要连带它的所有子级。

背景（2.7.13）：分类下拉改成层级选择器后，点「人工智能」如果后端还是 category 精确匹配，
一篇都筛不出来——数据全在二级/三级里。这里锁住「父级命中子级」以及「% _ 不当通配符」。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app


class CategoryFilterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-catfilter-")
        self.root = Path(self.temp.name)
        self.patch = patch.multiple(
            config,
            DATA_DIR=self.root,
            DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp",
            DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False, "pdf_browser_session_enabled": False},
        )
        self.patch.start()
        db.init_db()
        for paper_id, title, category in (
            ("a", "Parent only", "人工智能"),
            ("b", "Child neural net", "人工智能 / 光神经网络"),
            ("c", "Child segmentation", "人工智能 / 图像分割"),
            ("d", "Other branch", "偏振测量与偏振成像"),
            ("e", "Deep grandchild", "人工智能 / 光神经网络 / 深层网络"),
            ("f", "Percent category", "100%_测试"),
        ):
            db.execute("INSERT INTO papers(id,title,category) VALUES(?,?,?)", (paper_id, title, category))
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.patch.stop()
        self.temp.cleanup()

    def titles(self, category: str) -> list[str]:
        response = self.client.get("/api/papers", params={"category": category, "limit": 50})
        self.assertEqual(response.status_code, 200, response.text)
        return sorted(item["title"] for item in response.json()["items"])

    def test_parent_level_includes_all_descendants(self):
        self.assertEqual(
            self.titles("人工智能"),
            ["Child neural net", "Child segmentation", "Deep grandchild", "Parent only"],
        )

    def test_second_level_includes_grandchildren(self):
        self.assertEqual(
            self.titles("人工智能 / 光神经网络"),
            ["Child neural net", "Deep grandchild"],
        )

    def test_leaf_level_matches_only_itself(self):
        self.assertEqual(self.titles("人工智能 / 图像分割"), ["Child segmentation"])

    def test_other_branch_is_not_mixed_in(self):
        self.assertEqual(self.titles("偏振测量与偏振成像"), ["Other branch"])

    def test_percent_and_underscore_are_not_wildcards(self):
        # 只精确等于「100%」的才命中；不能把 '100%_测试' 靠通配符捞进来
        self.assertEqual(self.titles("100%"), [])


if __name__ == "__main__":
    unittest.main()
