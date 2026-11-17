# -*- coding: utf-8 -*-
"""EndNote 论文库迁移：读 EndNote XML 导出 + .Data/PDF 附件。

EndNote 的 .enl 是它自己的数据库格式，跨版本不稳；官方迁移路线是
File → Export → XML（勾选 File Attachments），本文件钉住这条路线。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import importers

PDF_BYTES = (Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes()

ENDNOTE_XML = """<?xml version="1.0" encoding="UTF-8"?>
<xml><records>
  <record>
    <ref-type name="Journal Article">17</ref-type>
    <contributors>
      <authors>
        <author><style face="normal" font="default" size="100%">Colburn, Shane</style></author>
        <author><style face="normal" font="default" size="100%">Chu, Yi</style></author>
        <author><style face="normal" font="default" size="100%">Shlizerman, Eli</style></author>
      </authors>
    </contributors>
    <titles>
      <title>An Optical Frontend for a Convolutional Neural Network</title>
      <secondary-title>Applied Optics</secondary-title>
    </titles>
    <dates><year>2019</year></dates>
    <electronic-resource-num>10.1364/AO.58.003179</electronic-resource-num>
    <urls>
      <related-urls><url>https://doi.org/10.1364/AO.58.003179</url></related-urls>
      <pdf-urls><url>internal-pdf://1234567890/Colburn - 2019 - An Optical Frontend.pdf</url></pdf-urls>
    </urls>
    <abstract><p>We demonstrate a hybrid optical-electronic convolutional neural network.</p></abstract>
    <keyword><keyword>optical computing</keyword><keyword>CNN</keyword></keyword>
  </record>
  <record>
    <ref-type name="Journal Article">17</ref-type>
    <contributors><authors><author><style>Nobody, Some</style></author></authors></contributors>
    <titles><title>Metadata Only Entry Without Any Attachment</title></titles>
    <dates><year>2021</year></dates>
  </record>
</records></xml>
"""


class EndnoteMigrationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-endnote-", ignore_cleanup_errors=True)
        self.root = Path(self.temp.name)
        self.settings = patch.multiple(
            config,
            DATA_DIR=self.root / "data",
            DEFAULT_PDF_DIR=self.root / "data" / "pdfs",
            TMP_DIR=self.root / "data" / "tmp",
            DB_PATH=self.root / "data" / "test.db",
            SETTINGS_PATH=self.root / "data" / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False, "pdf_browser_session_enabled": False},
        )
        self.settings.start()
        db.init_db()
        self.library = self.root / "library"
        self.library.mkdir()
        self.xml = self.library / "My Library.xml"
        self.xml.write_text(ENDNOTE_XML, encoding="utf-8")
        pdf_dir = self.library / "My Library.Data" / "PDF" / "1234567890"
        pdf_dir.mkdir(parents=True)
        self.attached = pdf_dir / "Colburn - 2019 - An Optical Frontend.pdf"
        self.attached.write_bytes(PDF_BYTES)

    def tearDown(self):
        self.settings.stop()
        self.temp.cleanup()

    def test_parse_endnote_xml_reads_metadata_and_resolves_attachments(self):
        items = importers.parse_endnote_xml(self.xml)
        self.assertEqual(len(items), 2)
        first = items[0]
        self.assertEqual(first["meta"]["title"], "An Optical Frontend for a Convolutional Neural Network")
        self.assertEqual(first["meta"]["authors"], "Shane Colburn; Yi Chu; Eli Shlizerman")
        self.assertEqual(first["meta"]["venue"], "Applied Optics")
        self.assertEqual(first["meta"]["year"], "2019")
        self.assertEqual(first["meta"]["doi"], "10.1364/AO.58.003179")
        self.assertEqual(first["meta"]["tags"], ["optical computing", "CNN"])
        self.assertIn("hybrid optical-electronic", first["meta"]["abstract"])
        self.assertEqual(first["pdf_hints"], [str(self.attached)])
        self.assertEqual(items[1]["meta"]["title"], "Metadata Only Entry Without Any Attachment")
        self.assertEqual(items[1]["pdf_hints"], [])

    def test_migration_imports_entries_and_attaches_the_pdf(self):
        result = importers.import_endnote_path(self.library)
        self.assertEqual(len(result["imported"]), 2, "两条元数据都该进来（迁移默认允许没有 PDF）")
        with_pdf = [paper for paper in result["imported"] if paper.get("file_path")]
        self.assertEqual(len(with_pdf), 1, "有附件的条目要把 PDF 一起迁入")
        self.assertTrue(Path(with_pdf[0]["file_path"]).is_file())
        self.assertGreaterEqual(result["pdf_files"], 1)

    def test_include_without_pdf_false_only_keeps_entries_with_pdf(self):
        result = importers.import_endnote_path(self.library, include_without_pdf=False)
        self.assertEqual(len(result["imported"]), 1)
        self.assertEqual(result["imported"][0]["title"], "An Optical Frontend for a Convolutional Neural Network")
        self.assertGreaterEqual(result["skipped_no_pdf"], 1)

    def test_migrating_twice_does_not_duplicate(self):
        first = importers.import_endnote_path(self.library)
        self.assertTrue(first["imported"])
        second = importers.import_endnote_path(self.library)
        self.assertEqual(len(second["imported"]), 0, "第二次迁移必须全部判重跳过")
        self.assertGreaterEqual(second["skipped"], 2)
        count = db.query_one("SELECT COUNT(*) AS c FROM papers")["c"]
        self.assertEqual(count, 2)

    def test_enl_files_get_actionable_guidance(self):
        enl = self.library / "My Library.enl"
        enl.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
        with self.assertRaises(ValueError) as ctx:
            importers.import_endnote_path(enl)
        message = str(ctx.exception)
        self.assertIn(".enl", message)
        self.assertIn("XML", message, "错误信息必须告诉用户导出 XML 的具体步骤")

    def test_api_endpoint_migrates_a_library(self):
        from fastapi.testclient import TestClient

        from paperwhale_server.main import app

        client = TestClient(app)
        try:
            # The API contract is under test, not real background downloads after fixture teardown.
            with patch('paperwhale_server.routers.import_.pdf_fetch_manager.start', return_value=0):
                response = client.post("/api/import/endnote", json={"path": str(self.library), "include_without_pdf": True})
            self.assertEqual(response.status_code, 200, response.text)
            payload = response.json()
            self.assertEqual(len(payload.get("imported") or []), 2)
            self.assertIn("pdf_files", payload)
        finally:
            client.close()

    def test_api_endpoint_reports_enl_with_guidance(self):
        from fastapi.testclient import TestClient

        from paperwhale_server.main import app

        enl = self.library / "Lib.enl"
        enl.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
        client = TestClient(app)
        try:
            response = client.post("/api/import/endnote", json={"path": str(enl)})
            self.assertEqual(response.status_code, 400, response.text)
            self.assertIn("XML", response.json().get("detail", ""))
        finally:
            client.close()

    def test_folder_without_endnote_files_is_rejected(self):
        empty = self.root / "empty"
        empty.mkdir()
        with self.assertRaises(ValueError):
            importers.import_endnote_path(empty)


if __name__ == "__main__":
    unittest.main()
