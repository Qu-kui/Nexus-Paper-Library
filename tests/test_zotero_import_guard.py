"""Zotero 导入护栏：分类节点不能变成论文，没定位到真 PDF 的条目不能入库。

背景（真实库事故）：用户的 Zotero 导出里有 6 个分类（散射成像 / 叠层成像 / 偏振成像 /
衍射光学 / 超分辨 / 单像素成像），旧解析把 <z:Collection> 当成文献条目，dc:title
（分类名）直接变成论文标题入库，全是「未知作者 / 未获取 PDF」的空壳。

隔离要求：本文件自行 patch 到 tempfile，不碰真实 %APPDATA%\\PaperWhale。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import importers, ingest, metadata

PDF = (Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes()

RDF_HEAD = """<?xml version="1.0" encoding="UTF-8"?>
<rdf:RDF
  xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
  xmlns:dc="http://purl.org/dc/elements/1.1/"
  xmlns:dcterms="http://purl.org/dc/terms/"
  xmlns:z="http://www.zotero.org/namespaces/export#">
"""


class ZoteroImportGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-zotero-")
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
        self.folder = self.root / "export"
        self.folder.mkdir()
        self.patches = [
            patch.object(metadata, "extract_pdf_metadata", return_value={}),
            patch.object(ingest.metadata, "extract_pdf_metadata", return_value={}),
            patch.object(ingest, "_enrich_from_doi", lambda merged: None),
            patch.object(ingest, "_enrich_from_arxiv", lambda merged, pdf: None),
        ]
        for item in self.patches:
            item.start()

    def tearDown(self):
        for item in self.patches:
            item.stop()
        self.settings.stop()
        self.temp.cleanup()

    # ------------------------------------------------------------------ helpers
    def write_pdf(self, relative: str) -> Path:
        path = self.folder / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(PDF)
        return path

    def write_rdf(self, body: str) -> Path:
        path = self.folder / "zotero_export.rdf"
        path.write_text(RDF_HEAD + body + "</rdf:RDF>", encoding="utf-8")
        return path

    def titles(self) -> list[str]:
        rows = db.query("SELECT title FROM papers ORDER BY rowid")
        return [row["title"] for row in rows]

    # -------------------------------------------------------------------- tests
    def test_collection_nodes_never_become_papers(self):
        """<z:Collection> 只有分类名，没有 itemType，绝不能被当成文献导入。"""
        self.write_pdf("files/1/real-paper.pdf")
        self.write_rdf(
            """
  <z:Collection rdf:about="#collection_0">
    <dc:title>散射成像</dc:title>
    <dcterms:hasPart rdf:resource="#item_1"/>
  </z:Collection>
  <z:Collection rdf:about="#collection_1">
    <dc:title>衍射光学</dc:title>
  </z:Collection>
  <z:item rdf:about="#item_1">
    <z:itemType>journalArticle</z:itemType>
    <dc:title>Real Paper About Scattering</dc:title>
    <dc:date>2023</dc:date>
    <dc:source>Optics Express</dc:source>
    <dcterms:hasPart rdf:resource="#item_1_att"/>
  </z:item>
  <z:Attachment rdf:about="#item_1_att">
    <z:itemType>attachment</z:itemType>
    <dc:title>Full Text PDF</dc:title>
    <z:path>files/1/real-paper.pdf</z:path>
    <dcterms:isReferencedBy rdf:resource="#item_1"/>
  </z:Attachment>
"""
        )
        result = importers.import_zotero_folder(self.folder)
        titles = self.titles()
        self.assertEqual(len(result["imported"]), 1)
        self.assertEqual(titles, ["Real Paper About Scattering"])
        for collection_name in ("散射成像", "衍射光学"):
            self.assertNotIn(collection_name, titles)

    def test_item_without_pdf_is_skipped_not_created(self):
        """规矩：没定位到真实 PDF 文件，就不建条目（只记一条跳过原因）。"""
        self.write_rdf(
            """
  <z:item rdf:about="#item_1">
    <z:itemType>journalArticle</z:itemType>
    <dc:title>Paper With No Local PDF At All</dc:title>
    <dc:date>2021</dc:date>
  </z:item>
"""
        )
        result = importers.import_zotero_folder(self.folder)
        self.assertEqual(len(result["imported"]), 0)
        self.assertEqual(self.titles(), [])
        self.assertGreaterEqual(result["skipped"], 1)
        self.assertEqual(result["skipped_no_pdf"], 1)
        self.assertIn("Paper With No Local PDF At All", result["skipped_no_pdf_examples"])
        self.assertEqual(result["errors"], [])

    def test_pdf_named_file_that_is_not_a_pdf_is_skipped(self):
        """后缀是 .pdf 但内容不是 PDF（下载残片/改名文件）不算定位到 PDF。"""
        fake = self.folder / "files" / "1" / "broken.pdf"
        fake.parent.mkdir(parents=True, exist_ok=True)
        fake.write_bytes(b"<html>blocked by publisher</html>" * 50)
        self.write_rdf(
            """
  <z:item rdf:about="#item_1">
    <z:itemType>journalArticle</z:itemType>
    <dc:title>Blocked Paper</dc:title>
    <dcterms:hasPart rdf:resource="#item_1_att"/>
  </z:item>
  <z:Attachment rdf:about="#item_1_att">
    <z:itemType>attachment</z:itemType>
    <z:path>files/1/broken.pdf</z:path>
    <dcterms:isReferencedBy rdf:resource="#item_1"/>
  </z:Attachment>
"""
        )
        result = importers.import_zotero_folder(self.folder)
        self.assertEqual(len(result["imported"]), 0)
        self.assertEqual(self.titles(), [])
        self.assertGreaterEqual(result["skipped"], 1)

    def test_import_pdf_file_rejects_fake_pdf(self):
        fake = self.root / "fake.pdf"
        fake.write_bytes(b"not really a pdf, just text " * 40)
        with self.assertRaises(ValueError):
            ingest.import_pdf_file(fake, source="folder")
        self.assertEqual(self.titles(), [])

    def test_real_pdf_is_still_imported(self):
        real = self.root / "real.pdf"
        real.write_bytes(PDF)
        result = ingest.import_pdf_file(real, source="folder")
        self.assertEqual(result["action"], "created")
        self.assertTrue(db.get_paper(result["id"])["file_path"])

    # ------------------------------------------------------- DOI 归一化判重
    def test_doi_suffix_variant_is_same_paper(self):
        db.execute(
            "INSERT INTO papers(id,title,doi) VALUES(?,?,?)",
            ("p1", "Study On Optical Absorption", "10.1117/1.JBO.23.12.121609.full"),
        )
        found = db.find_duplicate("Study On Optical Absorption", "10.1117/1.JBO.23.12.121609")
        self.assertEqual(found, "p1")

    def test_doi_case_variant_is_same_paper(self):
        db.execute(
            "INSERT INTO papers(id,title,doi) VALUES(?,?,?)",
            ("p2", "Self-Evolving Ghost Imaging", "10.1364/optica.424980"),
        )
        found = db.find_duplicate("Self-evolving ghost imaging", "10.1364/OPTICA.424980")
        self.assertEqual(found, "p2")

    def test_different_dois_stay_separate(self):
        db.execute(
            "INSERT INTO papers(id,title,doi) VALUES(?,?,?)",
            ("p3", "Optics Express", "10.1364/OE.410191"),
        )
        found = db.find_duplicate("Optics Express", "10.1364/OE.381229")
        self.assertIsNone(found)


if __name__ == "__main__":
    unittest.main()
