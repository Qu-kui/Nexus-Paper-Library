"""导入时用户自选归档路径：分类要先写进论文，PDF 才会落到对应目录。

隔离要求：PAPERWHALE_DATA=tests\\.tmp-data、PAPERWHALE_LLM_MOCK=1；
本文件自行 patch config 路径到 tempfile，不碰用户真实库。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.routers.webimport import WebImportIn
from paperwhale_server.services import ingest

PDF = (Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes()


class ArchivePathTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-archive-")
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
        self.pdf = root / "paper.pdf"
        self.pdf.write_bytes(PDF)

    def tearDown(self):
        self.settings.stop()
        self.temp.cleanup()

    def test_chosen_category_path_decides_where_the_pdf_lands(self):
        paper_id = ingest.create_paper_from_metadata(
            {
                "title": "Archive Path Test Paper",
                "doi": "10.1000/archive-path",
                "category_path": ["光学工程", "偏振成像"],
            },
            pdf_path=self.pdf,
            source="browser",
        )
        paper = db.get_paper(paper_id)
        self.assertEqual(json.loads(paper["category_path"]), ["光学工程", "偏振成像"])
        self.assertEqual(paper["category_major"], "光学工程")
        self.assertEqual(paper["category_minor"], "偏振成像")
        self.assertEqual(paper["category"], "光学工程 / 偏振成像")
        file_path = Path(paper["file_path"])
        self.assertTrue(file_path.is_file())
        self.assertEqual(file_path.parent.name, "偏振成像")
        self.assertEqual(file_path.parent.parent.name, "光学工程")

    def test_without_choice_pdf_goes_to_uncategorized(self):
        paper_id = ingest.create_paper_from_metadata(
            {"title": "No Archive Choice Paper", "doi": "10.1000/no-choice"},
            pdf_path=self.pdf,
            source="browser",
        )
        paper = db.get_paper(paper_id)
        self.assertEqual(json.loads(paper["category_path"] or "[]"), [])
        self.assertTrue(Path(paper["file_path"]).is_file())

    def test_webimport_model_accepts_category_path(self):
        body = WebImportIn(title="T", category_path=["一级", "二级"])
        self.assertEqual(body.category_path, ["一级", "二级"])
        self.assertEqual(WebImportIn(title="T").category_path, [])


if __name__ == "__main__":
    unittest.main()
