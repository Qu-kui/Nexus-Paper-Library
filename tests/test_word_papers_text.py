# -*- coding: utf-8 -*-
"""Word「插入引文」弹窗列表：/api/word/papers.txt 的逐字节契约。

VBA 只按 \\t 和 \\n 解析，这里锁死统计行、列顺序、字段清洗与截断规则。
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


class WordPapersTextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-word-papers-")
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
        (self.root / "pdfs").mkdir(parents=True, exist_ok=True)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def add_paper(
        self, paper_id, *, title="", authors="", year="", venue="", doi="", file_path="", category=""
    ):
        db.execute(
            "INSERT INTO papers(id,title,authors,year,venue,doi,file_path,category)"
            " VALUES(?,?,?,?,?,?,?,?)",
            (paper_id, title, authors, year, venue, doi, file_path, category),
        )

    def fetch(self, q="", limit=None):
        params = {"q": q}
        if limit is not None:
            params["limit"] = limit
        response = self.client.get("/api/word/papers.txt", params=params)
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

    # ------------------------------------------------------------ 格式与统计行
    def test_stats_line_and_row_format(self):
        self.add_paper("p1", title="Alpha", authors="Wei Zhang", year="2024", venue="Optics")
        self.add_paper("p2", title="Beta", authors="Na Li; Qiang Wang", year="2023")
        response, stats, rows = self.fetch()
        self.assertEqual(stats, ["#count", "2", "2"])
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(len(row) == 6 for row in rows), rows)
        first = rows[0]
        self.assertEqual(first[0], "p1")
        self.assertEqual(first[1], "2024")
        self.assertEqual(first[2], "Zhang")
        self.assertEqual(first[3], "Alpha")
        self.assertEqual(first[4], "Optics")
        self.assertEqual(first[5], "0", "没有 PDF 文件")
        self.assertEqual(response.text.count("\n"), 2, "1 行统计 + 2 行数据，无尾随换行")

    def test_authors_short_form(self):
        self.add_paper("p1", title="One", authors="Wei Zhang")
        self.add_paper("p2", title="Two", authors="Wei Zhang; Na Li; Qiang Wang")
        self.add_paper("p3", title="Three", authors="A" * 60)
        _, _, rows = self.fetch()
        by_id = {row[0]: row[2] for row in rows}
        self.assertEqual(by_id["p1"], "Zhang", "单作者只写姓")
        self.assertEqual(by_id["p2"], "Zhang et al.", "多作者加 et al.")
        self.assertEqual(len(by_id["p3"]), 40, "作者串最长 40 字符")

    def test_empty_fields_keep_empty_columns(self):
        self.add_paper("p1", title="Only Title")
        response, stats, rows = self.fetch()
        self.assertEqual(stats, ["#count", "1", "1"])
        self.assertEqual(rows[0], ["p1", "", "", "Only Title", "", "0"])
        self.assertIn("p1\t\t\tOnly Title\t\t0", response.text)

    def test_control_chars_are_replaced_by_spaces(self):
        self.add_paper(
            "p1",
            title="Line1\tLine2\nLine3\rLine4",
            authors="Wei\tZhang",
            venue="Optics\nExpress",
        )
        _, _, rows = self.fetch()
        self.assertEqual(len(rows), 1, "字段里的换行不能把一行拆成两行")
        self.assertEqual(rows[0][2], "Zhang")
        self.assertEqual(rows[0][3], "Line1 Line2 Line3 Line4")
        self.assertEqual(rows[0][4], "Optics Express")
        for field in rows[0]:
            self.assertNotIn("\t", field)
            self.assertNotIn("\n", field)

    def test_title_and_venue_truncation(self):
        self.add_paper("p1", title="T" * 300, venue="V" * 100)
        _, _, rows = self.fetch()
        title = rows[0][3]
        self.assertEqual(len(title), 200, "标题最长 200 字符（含省略号）")
        self.assertTrue(title.endswith("…"))
        self.assertEqual(len(rows[0][4]), 80, "期刊名最长 80 字符")

    def test_has_pdf_flag_matches_db_pdf_available(self):
        pdf = self.root / "pdfs" / "exists.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        self.add_paper("p1", title="With PDF", file_path=str(pdf))
        self.add_paper("p2", title="Without PDF")
        _, _, rows = self.fetch()
        by_id = {row[0]: row[5] for row in rows}
        self.assertEqual(by_id["p1"], "1")
        self.assertEqual(by_id["p2"], "0")

    # ------------------------------------------------------------ 搜索与分页
    def test_q_matches_title_author_doi_case_insensitively(self):
        self.add_paper("p1", title="Deep Learning", authors="Someone Else")
        self.add_paper("p2", title="Another Paper", authors="Wei Zhang")
        self.add_paper("p3", title="Third Paper", doi="10.1000/ABC")
        for keyword, expected in (
            ("deep", ["p1"]),
            ("DEEP", ["p1"]),
            ("wei", ["p2"]),
            ("abc", ["p3"]),
            ("10.1000/abc", ["p3"]),
        ):
            with self.subTest(q=keyword):
                _, stats, rows = self.fetch(q=keyword)
                self.assertEqual(stats[1], "1")
                self.assertEqual([row[0] for row in rows], expected)

    def test_q_no_hit_returns_stats_only(self):
        self.add_paper("p1", title="Alpha")
        response, stats, rows = self.fetch(q="zzz-not-found")
        self.assertEqual(response.text, "#count\t0\t0")
        self.assertEqual(stats, ["#count", "0", "0"])
        self.assertEqual(rows, [])

    def test_limit_is_clamped(self):
        for index in range(3):
            self.add_paper(f"p{index}", title=f"Paper {index}")
        _, stats, rows = self.fetch(limit=1)
        self.assertEqual(stats, ["#count", "3", "1"])
        self.assertEqual(len(rows), 1)
        _, stats, rows = self.fetch(limit=999)
        self.assertEqual(stats, ["#count", "3", "3"])
        for bad_limit in (0, -5):
            with self.subTest(limit=bad_limit):
                _, stats, rows = self.fetch(limit=bad_limit)
                self.assertEqual(stats, ["#count", "3", "1"])
                self.assertEqual(len(rows), 1)

    def test_sorting_year_desc_then_title_asc_empty_last(self):
        self.add_paper("p2024b", title="Beta", year="2024")
        self.add_paper("p2024a", title="Alpha", year="2024")
        self.add_paper("p2026", title="Zeta", year="2026")
        self.add_paper("pnone", title="Gamma", year="")
        _, _, rows = self.fetch()
        self.assertEqual([row[0] for row in rows], ["p2026", "p2024a", "p2024b", "pnone"])

    def test_more_than_limit_total_keeps_full_count(self):
        db.executemany(
            "INSERT INTO papers(id,title) VALUES(?,?)",
            [(f"bulk{index:04d}", f"Bulk {index:04d}") for index in range(505)],
        )
        _, stats, rows = self.fetch()
        self.assertEqual(stats, ["#count", "505", "500"])
        self.assertEqual(len(rows), 500)
        self.assertEqual(rows[0][3], "Bulk 0000", "同 year 按标题升序")
        _, stats, rows = self.fetch(limit=999)
        self.assertEqual(stats, ["#count", "505", "500"])

    # ------------------------------------------------------------ 分类 scope
    def add_scope_fixtures(self):
        self.add_paper("p1", title="Optics A", category="光学")
        self.add_paper("p2", title="Metasurface B", category="光学 / 超表面")
        self.add_paper("p3", title="Bio C", category="生物")
        self.add_paper("p4", title="Uncategorized D")
        self.add_paper("p5", title="Optics Eng E", category="光学工程")

    def fetch_scope(self, scope, q="", limit=None):
        params = {"q": q, "scope": scope}
        if limit is not None:
            params["limit"] = limit
        response = self.client.get("/api/word/papers.txt", params=params)
        self.assertEqual(response.status_code, 200, response.text)
        lines = response.text.split("\n")
        stats = lines[0].split("\t")
        rows = [line.split("\t") for line in lines[1:]] if len(lines) > 1 else []
        return stats, rows

    def test_scope_default_all_uncategorized_and_subtree(self):
        self.add_scope_fixtures()
        _, rows = self.fetch_scope("")
        self.assertEqual(len(rows), 5, "空 scope = 全部")
        _, rows = self.fetch_scope("__none__")
        self.assertEqual([row[0] for row in rows], ["p4"], "只有未分类")
        stats, rows = self.fetch_scope("光学")
        self.assertEqual(stats, ["#count", "2", "2"], "光学 + 子类，不含 光学工程")
        self.assertEqual([row[0] for row in rows], ["p2", "p1"], "同 year 空时按标题升序")
        _, rows = self.fetch_scope("生物")
        self.assertEqual([row[0] for row in rows], ["p3"])

    def test_scope_combines_with_q_as_and(self):
        self.add_scope_fixtures()
        stats, rows = self.fetch_scope("光学", q="metasurface")
        self.assertEqual(stats, ["#count", "1", "1"])
        self.assertEqual([row[0] for row in rows], ["p2"])
        stats, rows = self.fetch_scope("生物", q="metasurface")
        self.assertEqual(stats, ["#count", "0", "0"])
        self.assertEqual(rows, [])

    def test_scope_total_reflects_filter_before_limit(self):
        self.add_scope_fixtures()
        stats, rows = self.fetch_scope("光学", limit=1)
        self.assertEqual(stats, ["#count", "2", "1"], "total 是过滤后总数，不被 limit 截断")
        self.assertEqual(len(rows), 1)

    def test_scope_escapes_like_wildcards(self):
        self.add_paper("a", title="Exact Percent", category="100%_test")
        self.add_paper("b", title="Similar Name", category="100Xtest")
        self.add_paper("c", title="Child", category="100%_test / 子类")
        stats, rows = self.fetch_scope("100%_test")
        self.assertEqual(stats, ["#count", "2", "2"], "% 和 _ 不能当通配符")
        self.assertEqual({row[0] for row in rows}, {"a", "c"})

    def test_scope_unknown_returns_empty_stats(self):
        self.add_scope_fixtures()
        for scope in ("不存在的分类", "光学 / 不存在"):
            with self.subTest(scope=scope):
                response = self.client.get("/api/word/papers.txt", params={"scope": scope})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.text, "#count\t0\t0")

    def test_scope_does_not_break_default_output_format(self):
        self.add_scope_fixtures()
        _, rows = self.fetch_scope("")
        self.assertTrue(all(len(row) == 6 for row in rows), rows)


if __name__ == "__main__":
    unittest.main()
