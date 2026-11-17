# -*- coding: utf-8 -*-
"""网页一键入库的卷期页：扩展显式发送优先，旧扩展靠 page_html 兜底解析。

用 TestClient 直接打 /api/import/web；不下载 PDF、不联外网、临时库。
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
from paperwhale_server.routers import webimport

HIGHWIRE_HTML = """<html><head>
<meta name="citation_title" content="Paper One">
<meta name="citation_author" content="Shane Colburn">
<meta name="citation_journal_title" content="Applied Optics">
<meta name="citation_publication_date" content="2019/01/01">
<meta name="citation_doi" content="10.1000/example">
<meta name="citation_volume" content="12">
<meta name="citation_issue" content="3">
<meta name="citation_firstpage" content="45">
<meta name="citation_lastpage" content="67">
</head><body>正文</body></html>"""

PRISM_HTML = """<html><head>
<meta name="citation_title" content="Paper Two">
<meta name="prism.publicationname" content="Optics Express">
<meta name="prism.volume" content="7">
<meta name="prism.number" content="2">
<meta name="prism.startingpage" content="101">
<meta name="prism.endingpage" content="110">
<meta name="prism.doi" content="10.1000/prism">
</head></html>"""

GARBAGE_HTML = "<html><body><p>这里没有任何 citation meta</p></body></html>"


class WebImportBibliographicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-webimport-bib-")
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

    def import_web(self, **body):
        payload = {
            "title": "Paper One",
            "url": "https://example.org/p1",
            "download_pdf": False,
            "analyze": False,
        }
        payload.update(body)
        response = self.client.post("/api/import/web", json=payload)
        self.assertEqual(response.status_code, 200, response.text)
        data = response.json()
        paper = data["paper"]
        return data, paper, db.get_paper(paper["id"])

    # ------------------------------------------------------------ 旧扩展兜底
    def test_page_html_highwire_fills_volume_issue_pages(self):
        _, paper, stored = self.import_web(page_html=HIGHWIRE_HTML)
        for record in (paper, stored):
            self.assertEqual(
                (record["volume"], record["issue"], record["pages"]),
                ("12", "3", "45–67"),
            )

    def test_page_html_prism_fills_volume_issue_pages(self):
        _, _, stored = self.import_web(
            title="Paper Two", url="https://example.org/p2", page_html=PRISM_HTML
        )
        self.assertEqual(
            (stored["volume"], stored["issue"], stored["pages"]), ("7", "2", "101–110")
        )

    def test_garbage_page_html_keeps_import_working(self):
        data, _, stored = self.import_web(page_html=GARBAGE_HTML)
        self.assertEqual(data["pdf"], "none")
        self.assertEqual((stored["volume"], stored["issue"], stored["pages"]), ("", "", ""))

    def test_missing_page_html_keeps_import_working(self):
        _, _, stored = self.import_web()
        self.assertEqual((stored["volume"], stored["issue"], stored["pages"]), ("", "", ""))

    def test_page_html_parse_failure_is_silent(self):
        with patch.object(
            webimport.metadata, "metadata_from_html", side_effect=RuntimeError("boom")
        ):
            data, _, stored = self.import_web(page_html=HIGHWIRE_HTML)
        self.assertIn("paper", data)
        self.assertEqual((stored["volume"], stored["issue"], stored["pages"]), ("", "", ""))

    # ------------------------------------------------------------ 显式字段优先
    def test_body_fields_win_over_page_html(self):
        _, _, stored = self.import_web(
            volume="20", issue="4", pages="9-10", page_html=HIGHWIRE_HTML
        )
        self.assertEqual(
            (stored["volume"], stored["issue"], stored["pages"]), ("20", "4", "9-10")
        )

    def test_partial_body_fields_are_backfilled_from_page_html(self):
        _, _, stored = self.import_web(volume="20", page_html=HIGHWIRE_HTML)
        self.assertEqual(stored["volume"], "20", "显式给的卷号为准")
        self.assertEqual(stored["issue"], "3", "空期号用网页 meta 补")
        self.assertEqual(stored["pages"], "45–67", "空页码用网页 meta 补")

    def test_duplicate_import_enriches_empty_fields(self):
        _, first, stored = self.import_web()
        self.assertEqual((stored["volume"], stored["pages"]), ("", ""))
        _, second, enriched = self.import_web(page_html=HIGHWIRE_HTML)
        self.assertEqual(second["id"], first["id"], "同标题应命中去重")
        self.assertEqual(
            (enriched["volume"], enriched["issue"], enriched["pages"]), ("12", "3", "45–67")
        )


if __name__ == "__main__":
    unittest.main()
