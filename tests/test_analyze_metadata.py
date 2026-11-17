# -*- coding: utf-8 -*-
"""精读时按原文核对基础信息（标题/作者/期刊/年份/DOI）。

背景（用户报障）：Applied Optics 那篇，PDF 首页印的是
"An Optical Frontend for a Convolutional Neural Network"，
Crossref/Zotero 元数据是 "Optical frontend for a convolutional neural network"。
精读读了全文，就应该把基础信息按原文校正一遍——但不能拿模型输出乱覆盖
（模型也可能把别的论文标题塞进来）。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import ingest

PDF_BYTES = (Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes()

SUMMARY = "## 一句话结论\n测试用摘要。\n\n## 研究问题\n略。\n\n## 方法\n略。\n\n## 关键结果\n略。\n\n## 局限\n略。"


class AnalyzeMetadataTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-analyze-meta-")
        self.root = Path(self.temp.name)
        self.settings = patch.multiple(
            config,
            DATA_DIR=self.root,
            DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp",
            DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False, "pdf_browser_session_enabled": False},
        )
        self.settings.start()
        db.init_db()
        (self.root / "pdfs").mkdir(parents=True, exist_ok=True)
        self.pdf = self.root / "pdfs" / "p1.pdf"
        self.pdf.write_bytes(PDF_BYTES)

    def tearDown(self):
        self.settings.stop()
        self.temp.cleanup()

    def add_paper(self, **fields):
        row = {
            "id": "p1",
            "title": "",
            "authors": "",
            "venue": "",
            "year": "",
            "doi": "",
            "file_path": str(self.pdf),
        }
        row.update(fields)
        db.execute(
            "INSERT INTO papers(id,title,authors,venue,year,doi,file_path) VALUES(?,?,?,?,?,?,?)",
            (row["id"], row["title"], row["authors"], row["venue"], row["year"], row["doi"], row["file_path"]),
        )
        db.execute("INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)", ("p1", 1, "An Optical Frontend for a Convolutional Neural Network. Applied Optics 2019."))
        return row

    def analyze(self, payload: dict) -> dict:
        with patch.object(ingest.llm, "chat_json", return_value=payload), patch.object(
            ingest.metadata, "ensure_metadata", lambda paper_id: None
        ), patch.object(ingest, "regenerate_tags", lambda *args, **kwargs: None):
            return ingest.analyze_paper("p1")

    # ------------------------------------------------------------ 主场景
    def test_title_is_corrected_to_the_printed_one(self):
        self.add_paper(title="Optical frontend for a convolutional neural network")
        result = self.analyze(
            {
                "summary": SUMMARY,
                "key_points": ["要点一"],
                "title": "An Optical Frontend for a Convolutional Neural Network",
                "authors": "Shane Colburn; Yi Chu; Eli Shlizerman; Arka Majumdar",
                "venue": "Applied Optics",
                "year": "2019",
                "doi": "10.1364/AO.58.003179",
            }
        )
        paper = db.get_paper("p1")
        self.assertEqual(paper["title"], "An Optical Frontend for a Convolutional Neural Network")
        self.assertEqual(paper["authors"], "Shane Colburn; Yi Chu; Eli Shlizerman; Arka Majumdar")
        self.assertEqual(paper["venue"], "Applied Optics")
        self.assertEqual(paper["year"], "2019")
        self.assertEqual(paper["doi"], "10.1364/AO.58.003179")
        self.assertEqual(paper["status"], "analyzed")
        self.assertIn("标题", result["metadata_updates"])
        self.assertIn("作者", result["metadata_updates"])

    def test_empty_metadata_is_filled_from_full_text(self):
        self.add_paper(title="An Optical Frontend for a Convolutional Neural Network")
        result = self.analyze(
            {
                "summary": SUMMARY,
                "key_points": [],
                "authors": "Shane Colburn; Yi Chu; Eli Shlizerman; Arka Majumdar",
                "venue": "Applied Optics",
                "year": "2019",
            }
        )
        paper = db.get_paper("p1")
        self.assertEqual(paper["authors"], "Shane Colburn; Yi Chu; Eli Shlizerman; Arka Majumdar")
        self.assertEqual(paper["venue"], "Applied Optics")
        self.assertEqual(paper["year"], "2019")
        self.assertNotIn("标题", result["metadata_updates"])

    # ------------------------------------------------------------ 护栏
    def test_title_of_another_paper_is_never_written(self):
        self.add_paper(title="An Optical Frontend for a Convolutional Neural Network")
        result = self.analyze(
            {
                "summary": SUMMARY,
                "key_points": [],
                "title": "Spatially varying nanophotonic neural networks",
                "authors": "Some Other Author",
            }
        )
        paper = db.get_paper("p1")
        self.assertEqual(paper["title"], "An Optical Frontend for a Convolutional Neural Network")
        self.assertTrue(result["metadata_warnings"], "标题对不上要留警告，方便排查错配 PDF")
        self.assertNotIn("标题", result["metadata_updates"])

    def test_repeated_analysis_does_not_duplicate_knowledge(self):
        """同一篇重复精读只能覆盖，不能新增条目/正文片段/索引行。"""
        self.add_paper(title="An Optical Frontend for a Convolutional Neural Network")
        db.execute(
            "INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)",
            ("p1", 2, "Second page body text for the knowledge base."),
        )
        with patch.object(ingest, "regenerate_tags", lambda *args, **kwargs: None), patch.object(
            ingest.metadata, "ensure_metadata", lambda paper_id: None
        ):
            payloads = [
                {"summary": "## 一句话结论\n第一次精读。", "key_points": ["旧要点"], "title": "An Optical Frontend for a Convolutional Neural Network"},
                {"summary": "## 一句话结论\n第二次精读（更新）。", "key_points": ["新要点"], "title": "An Optical Frontend for a Convolutional Neural Network"},
            ]
            for payload in payloads:
                with patch.object(ingest.llm, "chat_json", return_value=payload):
                    ingest.analyze_paper("p1")

        counts = lambda sql: db.query_one(sql)["c"]
        self.assertEqual(counts("SELECT COUNT(*) AS c FROM papers"), 1, "不能多出论文条目")
        self.assertEqual(counts("SELECT COUNT(*) AS c FROM chunks WHERE paper_id='p1'"), 2, "正文片段数必须不变")
        self.assertEqual(
            counts("SELECT COUNT(*) AS c FROM chunks_fts"),
            counts("SELECT COUNT(*) AS c FROM chunks"),
            "FTS 索引行数必须和 chunks 一致，不能重复索引",
        )
        self.assertEqual(counts("SELECT COUNT(*) AS c FROM ai_conversations"), 0)
        self.assertEqual(counts("SELECT COUNT(*) AS c FROM ai_messages"), 0)
        paper = db.get_paper("p1")
        self.assertIn("第二次精读", paper["summary"], "摘要要被最新一次覆盖")
        self.assertEqual(paper["key_points"], ["新要点"])

    def test_regenerate_tags_is_idempotent_and_keeps_manual_tags(self):
        """精读会重新生成标签：重复跑不能把同一个标签堆成重复项，也不能丢掉手动标签。"""
        self.add_paper(title="An Optical Frontend for a Convolutional Neural Network")
        db.update_paper("p1", {"tags": ["手动标签"]})
        with patch.object(ingest.llm, "chat_json", return_value={"tags": ["光学计算", "卷积神经网络"]}):
            ingest.regenerate_tags("p1", {"summary": "摘要", "key_points": []})
            first = db.get_paper("p1")["tags"]
            ingest.regenerate_tags("p1", {"summary": "摘要", "key_points": []})
            second = db.get_paper("p1")["tags"]
        self.assertIn("手动标签", first)
        self.assertEqual(len(first), len(set(first)), f"标签不能重复: {first}")
        self.assertEqual(sorted(first), sorted(second), "重复精读后标签集合应保持不变")

    def test_generic_journal_title_is_replaced_using_pdf_evidence(self):
        """Zotero 导出里常见脏标题（标题就是期刊名），用原文证据纠正。"""
        self.add_paper(title="Optics Express")
        db.execute("DELETE FROM chunks WHERE paper_id='p1'")
        db.execute(
            "INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)",
            ("p1", 1, "Fast single-pixel imaging via pattern reshaping. Optics Express 2020."),
        )
        result = self.analyze(
            {
                "summary": SUMMARY,
                "key_points": [],
                "title": "Fast single-pixel imaging via pattern reshaping",
                "venue": "Optics Express",
            }
        )
        self.assertEqual(db.get_paper("p1")["title"], "Fast single-pixel imaging via pattern reshaping")
        self.assertIn("标题", result["metadata_updates"])

    def test_pdf_evidence_does_not_override_a_title_that_is_in_the_pdf(self):
        """旧标题能在原文里找到时，绝不用模型给的另一个标题覆盖。"""
        self.add_paper(title="From stars to molecules: AI guided super-resolution")
        result = self.analyze(
            {
                "summary": SUMMARY,
                "key_points": [],
                "title": "Some Other Paper Title",
            }
        )
        self.assertEqual(
            db.get_paper("p1")["title"], "From stars to molecules: AI guided super-resolution"
        )
        self.assertTrue(result["metadata_warnings"])

    def test_fewer_authors_never_downgrade_existing(self):
        self.add_paper(
            title="An Optical Frontend for a Convolutional Neural Network",
            authors="Shane Colburn; Yi Chu; Eli Shlizerman; Arka Majumdar",
        )
        result = self.analyze(
            {"summary": SUMMARY, "key_points": [], "authors": "Shane Colburn; Yi Chu"}
        )
        self.assertEqual(
            db.get_paper("p1")["authors"],
            "Shane Colburn; Yi Chu; Eli Shlizerman; Arka Majumdar",
        )
        self.assertNotIn("作者", result["metadata_updates"])

    def test_existing_venue_year_doi_are_kept(self):
        self.add_paper(
            title="An Optical Frontend for a Convolutional Neural Network",
            venue="Applied Optics",
            year="2019",
            doi="10.1364/AO.58.003179",
        )
        result = self.analyze(
            {
                "summary": SUMMARY,
                "key_points": [],
                "venue": "Some Other Journal",
                "year": "2021",
                "doi": "10.9999/wrong",
            }
        )
        paper = db.get_paper("p1")
        self.assertEqual(paper["venue"], "Applied Optics")
        self.assertEqual(paper["year"], "2019")
        self.assertEqual(paper["doi"], "10.1364/AO.58.003179")
        self.assertEqual(result["metadata_updates"], [])


if __name__ == "__main__":
    unittest.main()
