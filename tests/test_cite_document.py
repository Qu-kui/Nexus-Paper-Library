# -*- coding: utf-8 -*-
"""Word 插件的文档引文接口：/api/cite/document.txt 的逐字节格式与编号规则。

Word VBA 按 \\t 和 \\n 解析，所以这里断言的换行、空行、marker 位置都不能变。
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

MARKER = "--BIBLIOGRAPHY--"


class CiteDocumentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-cite-doc-")
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
        self.add_paper(
            "p1", "Paper One", "Shane Colburn; Yi Chu", "2019", "Applied Optics", "10.1/a"
        )
        self.add_paper("p2", "Paper Two", "Sharma", "2020", "Nature", "10.1/b")
        self.add_paper("p3", "Paper Three", "Zhu", "2021", "Optics Express", "")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def add_paper(self, paper_id, title, authors, year, venue, doi):
        db.execute(
            "INSERT INTO papers(id,title,authors,year,venue,doi) VALUES(?,?,?,?,?,?)",
            (paper_id, title, authors, year, venue, doi),
        )

    def document(self, ids, style="apa"):
        response = self.client.post("/api/cite/document.txt", json={"ids": ids, "style": style})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(
            response.headers["content-type"].startswith("text/plain"),
            response.headers["content-type"],
        )
        return response.text

    # ------------------------------------------------------------ 格式与编号
    def test_ieee_numbers_by_document_order_and_bibliography_matches(self):
        text = self.document(["p3", "p1", "p2"], "ieee")
        lines = text.split("\n")
        self.assertEqual(lines[0], "p3\t1\t[1]")
        self.assertEqual(lines[1], "p1\t2\t[2]")
        self.assertEqual(lines[2], "p2\t3\t[3]")
        self.assertEqual(lines[3], MARKER)
        entries = "\n".join(lines[4:]).split("\n\n")
        self.assertEqual(len(entries), 3)
        self.assertTrue(entries[0].startswith("[1] "))
        self.assertTrue(entries[1].startswith("[2] "))
        self.assertTrue(entries[2].startswith("[3] "))
        self.assertIn("Paper Three", entries[0])
        self.assertIn("Paper One", entries[1])
        self.assertIn("Paper Two", entries[2])

    def test_marker_is_separate_line_and_no_carriage_return(self):
        text = self.document(["p1", "p2"], "apa")
        self.assertEqual(text.count(MARKER), 1)
        self.assertNotIn("\r", text)
        self.assertFalse(text.startswith("\n"))
        head, _, bib = text.partition("\n" + MARKER + "\n")
        self.assertEqual(head, "p1\t1\t(Colburn & Chu, 2019)\np2\t2\t(Sharma, 2020)")
        self.assertEqual(len(bib.split("\n\n")), 2)

    def test_apa_bibliography_has_no_number_prefix(self):
        text = self.document(["p2", "p1"], "apa")
        entries = text.partition(MARKER + "\n")[2].split("\n\n")
        self.assertTrue(all(not entry.startswith("[") for entry in entries), entries)
        self.assertIn("Paper Two", entries[0])
        self.assertIn("Paper One", entries[1])

    def test_duplicate_ids_are_deduped_keeping_first_order(self):
        text = self.document(["p2", "p1", "p2", "p1"], "ieee")
        lines = text.split("\n")
        self.assertEqual(lines[:3], ["p2\t1\t[1]", "p1\t2\t[2]", MARKER])
        entries = "\n".join(lines[3:]).split("\n\n")
        self.assertEqual(len(entries), 2, "重复 id 不能重复出现")

    def test_missing_ids_are_skipped_without_consuming_numbers(self):
        text = self.document(["ghost", "p1", "p2"], "ieee")
        lines = text.split("\n")
        self.assertEqual(lines[:3], ["p1\t1\t[1]", "p2\t2\t[2]", MARKER])
        self.assertTrue("\n".join(lines[3:]).startswith("[1] "))
        self.assertIn("Paper One", lines[3])

    def test_only_missing_ids_returns_marker_with_empty_body(self):
        text = self.document(["ghost"], "ieee")
        self.assertEqual(text, MARKER + "\n")

    def test_style_is_case_insensitive(self):
        text = self.document(["p1", "p2"], "IEEE")
        self.assertIn("p1\t1\t[1]", text)

    # ------------------------------------------------------------ 三列格式（id/index/引文）
    def test_each_line_has_id_index_citation_columns(self):
        text = self.document(["p2", "p1"], "ieee")
        lines = text.split("\n")
        self.assertEqual(lines[0], "p2\t1\t[1]")
        self.assertEqual(lines[1], "p1\t2\t[2]")
        for line in lines[:2]:
            self.assertEqual(len(line.split("\t")), 3, line)

    def test_index_contiguous_with_duplicates_and_missing_ids(self):
        text = self.document(["p2", "ghost-a", "p1", "p2", "ghost-b"], "apa")
        head = text.partition("\n" + MARKER + "\n")[0]
        rows = [line.split("\t") for line in head.split("\n")]
        self.assertEqual([row[0] for row in rows], ["p2", "p1"], "去重 + 跳过不存在的 id")
        self.assertEqual([row[1] for row in rows], ["1", "2"], "index 连续且不占号")
        for row in rows:
            self.assertEqual(len(row), 3, row)

    def test_bibliography_block_format_unchanged_after_three_columns(self):
        text = self.document(["p2", "p1"], "ieee")
        prefix, _, bib = text.partition("\n" + MARKER + "\n")
        self.assertEqual(len(prefix.split("\n")), 2, "marker 前只有两行引文")
        self.assertTrue(bib.startswith("[1] "), "数值型 bib 仍带 [N] 前缀")
        self.assertEqual(len(bib.split("\n\n")), 2)
        self.assertIn("Paper Two", bib.split("\n\n")[0])
        self.assertNotIn("\r", text)

    def test_styles_txt_has_numeric_flag_column(self):
        response = self.client.get("/api/cite/styles.txt")
        self.assertEqual(response.status_code, 200, response.text)
        rows = [line.split("\t") for line in response.text.split("\n")]
        self.assertTrue(all(len(row) == 3 for row in rows), rows)
        flags = {row[0]: row[2] for row in rows}
        for style in ("ieee", "nature", "gbt7714"):
            self.assertEqual(flags[style], "1", style)
        for style in ("apa", "mla", "chicago"):
            self.assertEqual(flags[style], "0", style)
        self.assertEqual(
            [row[0] for row in rows[:6]],
            ["apa", "mla", "chicago", "ieee", "nature", "gbt7714"],
            "内置在前",
        )
        self.assertNotIn("\r", response.text)

    def test_styles_txt_custom_numeric_flag_follows_spec(self):
        numeric_csl = (
            '<style xmlns="http://purl.org/net/xbiblio/csl"><info><title>Numeric</title></info>'
            "<citation><layout><text variable=\"citation-number\"/></layout></citation>"
            "<bibliography><layout><names variable=\"author\"/></layout></bibliography></style>"
        )
        plain_csl = (
            '<style xmlns="http://purl.org/net/xbiblio/csl"><info><title>AuthorYear</title></info>'
            "<citation><layout><names variable=\"author\"/><date variable=\"issued\"/></layout></citation>"
            "<bibliography><layout><names variable=\"author\"/></layout></bibliography></style>"
        )
        numeric_id = self.client.post(
            "/api/cite/styles/import", json={"csl": numeric_csl, "name": "数字样式"}
        ).json()["style"]["id"]
        plain_id = self.client.post(
            "/api/cite/styles/import", json={"csl": plain_csl, "name": "作者年样式"}
        ).json()["style"]["id"]
        rows = [line.split("\t") for line in self.client.get("/api/cite/styles.txt").text.split("\n")]
        flags = {row[0]: row[2] for row in rows}
        self.assertEqual(flags[numeric_id], "1")
        self.assertEqual(flags[plain_id], "0")
        self.assertEqual([row[0] for row in rows[6:]], [numeric_id, plain_id], "自定义按创建顺序在后")

    # ------------------------------------------------------------ 参数校验
    def test_empty_ids_returns_400(self):
        for ids in ([], [""], ["  "]):
            with self.subTest(ids=ids):
                response = self.client.post(
                    "/api/cite/document.txt", json={"ids": ids, "style": "apa"}
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "没有可生成引文的文献")

    def test_unknown_style_returns_400(self):
        response = self.client.post(
            "/api/cite/document.txt", json={"ids": ["p1"], "style": "harvard"}
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("不支持的引文格式", response.json()["detail"])

    def test_default_style_is_apa(self):
        response = self.client.post("/api/cite/document.txt", json={"ids": ["p1"]})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("p1\t1\t(Colburn & Chu, 2019)", response.text)

    def test_chinese_content_is_utf8_without_bom(self):
        """VBA 按字节取文本，中文参考文献必须是 UTF-8 且没有 BOM。"""
        self.add_paper("p4", "偏振成像综述", "张三; 李四", "2021", "光学学报", "")
        response = self.client.post(
            "/api/cite/document.txt", json={"ids": ["p4"], "style": "apa"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        raw = response.content
        self.assertGreater(len(raw), 0)
        self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "不能带 UTF-8 BOM")
        self.assertEqual(len(raw), len(response.text.encode("utf-8")))
        self.assertIn("偏振成像综述", response.text)
        self.assertIn("(张三 & 李四, 2021)", response.text)


if __name__ == "__main__":
    unittest.main()
