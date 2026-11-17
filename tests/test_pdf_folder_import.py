"""文件夹 PDF 批量导入：能按内容对上已有论文就补全文，对不上就新建条目。

隔离要求：PAPERWHALE_DATA=tests\\.tmp-data、PAPERWHALE_LLM_MOCK=1；本文件自行 patch 到 tempfile。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import ingest, metadata

PDF = (Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes()


class PdfFolderImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-folder-")
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
        self.folder = self.root / "incoming"
        self.folder.mkdir()
        self.extract_map = {}

    def tearDown(self):
        self.settings.stop()
        self.temp.cleanup()

    def make_pdf(self, name: str, meta: dict):
        path = self.folder / name
        path.write_bytes(PDF)
        self.extract_map[name] = meta
        return path

    def fake_extract(self, path: Path):
        return dict(self.extract_map.get(Path(path).name, {}))

    def import_one(self, path: Path):
        with patch.object(metadata, "extract_pdf_metadata", side_effect=self.fake_extract), patch.object(
            ingest.metadata, "extract_pdf_metadata", side_effect=self.fake_extract
        ), patch.object(ingest, "_enrich_from_doi", lambda merged: None), patch.object(
            ingest, "_enrich_from_arxiv", lambda merged, pdf: None
        ):
            return ingest.import_pdf_file(path, source="folder")

    def add_existing(self, paper_id: str, title: str, doi: str = "", file_path: str = ""):
        db.execute(
            "INSERT INTO papers(id,title,doi,file_path) VALUES(?,?,?,?)",
            (paper_id, title, doi, file_path),
        )

    def test_matches_existing_paper_and_attaches_pdf(self):
        self.add_existing("p1", "Attention Is All You Need")
        path = self.make_pdf("a.pdf", {"title": "attention is all you need", "doi": "10.1000/attn"})
        result = self.import_one(path)
        self.assertEqual(result["action"], "matched")
        self.assertEqual(result["id"], "p1")
        paper = db.get_paper("p1")
        self.assertTrue(paper["file_path"])
        self.assertTrue(Path(paper["file_path"]).is_file())

    def test_creates_new_paper_when_no_match(self):
        path = self.make_pdf("b.pdf", {"title": "A Brand New Paper About Whales", "doi": "10.1000/whale"})
        result = self.import_one(path)
        self.assertEqual(result["action"], "created")
        paper = db.get_paper(result["id"])
        self.assertEqual(paper["title"], "A Brand New Paper About Whales")
        self.assertTrue(paper["file_path"])

    def test_skips_when_existing_paper_already_has_pdf(self):
        existing = self.root / "already.pdf"
        existing.write_bytes(PDF)
        self.add_existing("p2", "Attention Is All You Need", file_path=str(existing))
        path = self.make_pdf("c.pdf", {"title": "Attention Is All You Need"})
        result = self.import_one(path)
        self.assertEqual(result["action"], "already_has_pdf")
        paper = db.get_paper("p2")
        self.assertEqual(paper["file_path"], str(existing))

    def test_applies_chosen_category_to_new_paper(self):
        path = self.make_pdf("d.pdf", {"title": "Categorized Paper Title", "doi": "10.1000/cat"})
        with patch.object(metadata, "extract_pdf_metadata", side_effect=self.fake_extract), patch.object(
            ingest.metadata, "extract_pdf_metadata", side_effect=self.fake_extract
        ), patch.object(ingest, "_enrich_from_doi", lambda merged: None), patch.object(
            ingest, "_enrich_from_arxiv", lambda merged, pdf: None
        ):
            result = ingest.import_pdf_file(path, source="folder", category_path=["光学工程", "偏振成像"])
        paper = db.get_paper(result["id"])
        self.assertEqual(json.loads(paper["category_path"]), ["光学工程", "偏振成像"])
        self.assertEqual(Path(paper["file_path"]).parent.parent.name, "光学工程")


if __name__ == "__main__":
    unittest.main()
