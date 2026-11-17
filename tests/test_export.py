"""导出文件：WebView2 里下载不可靠，改由后端落盘。"""
from __future__ import annotations

import base64
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db


class ExportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-export-")
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            PROGRAM_DIR=root,
            _cache={**config.DEFAULT_SETTINGS},
        )
        self.paths.start()
        db.init_db()
        self.out = root / "out"
        self.out.mkdir()
        self.patch_dir = patch("paperwhale_server.routers.export.exports_dir", return_value=self.out)
        self.patch_dir.start()

    def tearDown(self):
        self.patch_dir.stop()
        self.paths.stop()
        self.temp.cleanup()

    def _client(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app
        return TestClient(app)

    def test_save_svg_and_png(self):
        client = self._client()
        svg = b"<svg xmlns='http://www.w3.org/2000/svg'><rect width='10' height='10'/></svg>"
        body = {"filename": "流程与关系图.svg", "data_base64": base64.b64encode(svg).decode()}
        response = client.post("/api/export/save", json=body)
        self.assertEqual(response.status_code, 200, response.text)
        path = Path(response.json()["path"])
        self.assertTrue(path.is_file())
        self.assertEqual(path.read_bytes(), svg)
        self.assertTrue(path.name.endswith(".svg"))

        png = b"\x89PNG\r\n\x1a\n" + b"0" * 32
        response = client.post("/api/export/save", json={
            "filename": "chart.png", "data_base64": base64.b64encode(png).decode()})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(Path(response.json()["path"]).is_file())

    def test_name_is_sanitized_and_deduplicated(self):
        client = self._client()
        data = base64.b64encode(b"x").decode()
        first = client.post("/api/export/save", json={"filename": "a:b?.png", "data_base64": data}).json()
        second = client.post("/api/export/save", json={"filename": "a:b?.png", "data_base64": data}).json()
        self.assertNotIn("?", first["name"])
        self.assertNotEqual(first["path"], second["path"])

    def test_open_folder_endpoint(self):
        client = self._client()
        with patch("paperwhale_server.routers.export.os.startfile", create=True) as start:
            response = client.post("/api/export/open")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["folder"], str(self.out))
        start.assert_called_once()

    def test_rejects_bad_base64(self):
        client = self._client()
        response = client.post("/api/export/save", json={"filename": "x.png", "data_base64": "not-base64!!"})
        self.assertEqual(response.status_code, 400)
