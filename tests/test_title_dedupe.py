"""标题归一化去重（Zotero 导入路径依赖 db.find_duplicate）。

隔离要求：必须设 PAPERWHALE_DATA=tests\\.tmp-data、PAPERWHALE_LLM_MOCK=1；
本文件也自行 patch config 路径，绝不碰用户真实库。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db


class TitleDedupeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-dedupe-")
        root = Path(self.temp.name)
        self.settings = patch.multiple(
            config,
            DATA_DIR=root,
            DEFAULT_PDF_DIR=root / "pdfs",
            TMP_DIR=root / "tmp",
            DB_PATH=root / "test.db",
            SETTINGS_PATH=root / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False, "pdf_browser_session_enabled": False},
        )
        self.settings.start()
        db.init_db()

    def tearDown(self):
        self.settings.stop()
        self.temp.cleanup()

    def add(self, paper_id, title, doi="", arxiv_id=""):
        db.execute(
            "INSERT INTO papers(id,title,doi,arxiv_id) VALUES(?,?,?,?)",
            (paper_id, title, doi, arxiv_id),
        )

    def test_normalize_title_strips_punctuation_width_and_case(self):
        self.assertEqual(db.normalize_title("Attention Is All You Need."), "attention is all you need")
        self.assertEqual(db.normalize_title("  attention  IS all—you need  "), "attention is all you need")
        self.assertEqual(db.normalize_title("（中文）标题：副标题"), "中文 标题 副标题")

    def test_punctuation_and_case_difference_is_a_duplicate(self):
        self.add("p1", "Attention Is All You Need")
        self.assertEqual(db.find_duplicate("attention is all you need."), "p1")
        self.assertEqual(db.find_duplicate("Attention  Is All You Need"), "p1")

    def test_full_width_and_half_width_are_the_same_title(self):
        self.add("p1", "ＡＴＴＥＮＴＩＯＮ is all you need")
        self.assertEqual(db.find_duplicate("attention is all you need"), "p1")

    def test_subtitle_difference_is_a_duplicate(self):
        self.add("p1", "Deep Learning for Medical Image Segmentation: A Survey")
        self.assertEqual(db.find_duplicate("Deep Learning for Medical Image Segmentation"), "p1")
        self.assertEqual(db.find_duplicate("deep learning for medical image segmentation. a survey"), "p1")

    def test_leading_article_difference_is_a_duplicate(self):
        """PDF 首页标题带 "An"，Crossref/Zotero 元数据没有——必须是同一篇。

        真实案例：Applied Optics 10.1364/AO.58.003179，
        PDF 印的是 "An Optical Frontend for a Convolutional Neural Network"，
        Crossref 给的是 "Optical frontend for a convolutional neural network"，
        以前会导成两条重复记录。
        """
        self.add("p1", "Optical frontend for a convolutional neural network", doi="10.1364/AO.58.003179")
        self.assertEqual(
            db.find_duplicate("An Optical Frontend for a Convolutional Neural Network"),
            "p1",
        )
        # 反过来同样成立
        self.add("p2", "An Optical Frontend for a Convolutional Neural Network")
        self.assertEqual(db.find_duplicate("Optical frontend for a convolutional neural network"), "p1")

    def test_leading_the_difference_is_a_duplicate(self):
        self.add("p1", "Low-rank hypothesis of complex systems")
        self.assertEqual(db.find_duplicate("The low-rank hypothesis of complex systems"), "p1")

    def test_short_titles_keep_article_treatment_conservative(self):
        """短标题不剥冠词：'An Editorial' 与 'Editorial' 不能硬合并。"""
        self.add("p1", "Editorial")
        self.assertIsNone(db.find_duplicate("An Editorial"))
        self.assertIsNone(db.find_duplicate("The Editorial"))

    def test_different_dois_are_not_merged_even_with_same_title(self):
        self.add("p1", "A Study of Something", doi="10.1000/aaa")
        self.assertIsNone(db.find_duplicate("A Study of Something", doi="10.1000/bbb"))
        self.assertEqual(db.find_duplicate("A Study of Something", doi="10.1000/AAA"), "p1")

    def test_short_titles_are_not_fuzzily_merged(self):
        self.add("p1", "Editorial")
        self.assertIsNone(db.find_duplicate("Editorial Note"))
        self.assertIsNone(db.find_duplicate("An Editorial"))

    def test_unrelated_titles_are_not_merged(self):
        self.add("p1", "Deep Learning for Medical Image Segmentation: A Survey")
        self.assertIsNone(db.find_duplicate("Deep Learning for Natural Language Parsing"))

    def test_strict_identifiers_keeps_exact_title_only(self):
        self.add("p1", "Attention Is All You Need", doi="10.1000/aaa")
        self.assertIsNone(db.find_duplicate("attention is all you need.", strict_identifiers=True))
        self.assertEqual(db.find_duplicate("Attention Is All You Need", strict_identifiers=True), "p1")


if __name__ == "__main__":
    unittest.main()
