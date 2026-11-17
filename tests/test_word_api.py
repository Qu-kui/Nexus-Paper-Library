# -*- coding: utf-8 -*-
"""Word 模板管理接口：/api/word/status|install|uninstall。

测试全程用临时 APPDATA 与临时模板源（PAPERWHALE_WORD_SOURCE），不碰真实
%APPDATA%\\Microsoft\\Word\\STARTUP，也不依赖本机是否装了 Word。
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
from paperwhale_server.routers import word

TEMPLATE = "PaperWhaleMacros.dotm"


class WordApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-word-")
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
        db.init_db()
        self.appdata = self.root / "appdata"
        self.source = self.root / "src" / TEMPLATE
        self.source.parent.mkdir(parents=True, exist_ok=True)
        self.source.write_bytes(b"PK-fake-dotm")
        self.target = self.appdata / "Microsoft" / "Word" / "STARTUP" / TEMPLATE
        self.env = patch.dict(
            os.environ,
            {"APPDATA": str(self.appdata), "PAPERWHALE_WORD_SOURCE": str(self.source)},
        )
        self.env.start()
        # 本机是否装了 Word 不影响测试：默认按"没装 / 没在运行"处理，个别用例再覆盖
        self.registry = patch.object(word, "_registry_word_detected", return_value=False)
        self.registry.start()
        self.running = patch.object(word, "_word_running", return_value=False)
        self.running.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.running.stop()
        self.registry.stop()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def status(self):
        response = self.client.get("/api/word/status")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------------------ status
    def test_status_reports_paths_and_source_availability(self):
        data = self.status()
        self.assertEqual(
            set(data),
            {
                "word_detected",
                "installed",
                "target_path",
                "source_path",
                "source_available",
                "running",
            },
        )
        self.assertFalse(data["word_detected"], "临时 APPDATA 下没有 Word 目录，注册表探测被 mock 成 False")
        self.assertFalse(data["installed"])
        self.assertEqual(data["target_path"], str(self.target))
        self.assertEqual(data["source_path"], str(self.source))
        self.assertTrue(data["source_available"])
        self.assertFalse(data["running"])

    def test_status_without_source_sets_source_available_false(self):
        missing = self.root / "nope" / TEMPLATE
        with patch.dict(os.environ, {"PAPERWHALE_WORD_SOURCE": str(missing)}):
            data = self.status()
        self.assertFalse(data["source_available"])
        self.assertEqual(data["source_path"], str(missing))
        self.assertFalse(data["installed"])

    def test_word_detected_from_word_directory_or_registry(self):
        (self.appdata / "Microsoft" / "Word").mkdir(parents=True, exist_ok=True)
        self.assertTrue(self.status()["word_detected"], "有 %APPDATA%\\Microsoft\\Word 就算检测到 Word")
        # 目录不在时退回注册表探测
        (self.appdata / "Microsoft" / "Word").rmdir()
        with patch.object(word, "_registry_word_detected", return_value=True):
            self.assertTrue(self.status()["word_detected"])

    def test_status_reports_running_word(self):
        with patch.object(word, "_word_running", return_value=True):
            self.assertTrue(self.status()["running"])

    # ------------------------------------------------------------ install
    def test_install_copies_template_and_second_install_reports_replaced(self):
        first = self.client.post("/api/word/install")
        self.assertEqual(first.status_code, 200, first.text)
        payload = first.json()
        self.assertEqual(payload["ok"], True)
        self.assertEqual(payload["installed"], True)
        self.assertEqual(payload["replaced"], False, "第一次安装没有旧文件")
        self.assertEqual(payload["target_path"], str(self.target))
        self.assertEqual(payload["running"], False)
        self.assertTrue(self.target.is_file())
        self.assertEqual(self.target.read_bytes(), b"PK-fake-dotm")
        self.assertTrue(self.status()["installed"])

        second = self.client.post("/api/word/install")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json()["replaced"], True, "覆盖已有模板要报 replaced=true")

    def test_install_creates_startup_directory(self):
        self.assertFalse(self.appdata.exists())
        response = self.client.post("/api/word/install")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(self.target.parent.is_dir())

    def test_install_without_source_returns_400(self):
        missing = self.root / "nope" / TEMPLATE
        with patch.dict(os.environ, {"PAPERWHALE_WORD_SOURCE": str(missing)}):
            response = self.client.post("/api/word/install")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("找不到 Word 模板文件", response.json()["detail"])
        self.assertFalse(self.target.is_file())

    def test_install_when_word_locks_file_returns_400(self):
        self.assertEqual(self.client.post("/api/word/install").status_code, 200)
        with patch.object(word.shutil, "copy2", side_effect=OSError("Permission denied")), patch.object(
            word, "_word_running", return_value=True
        ):
            response = self.client.post("/api/word/install")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(response.json()["detail"], "请先关闭 Word 再安装")
        self.assertEqual(self.target.read_bytes(), b"PK-fake-dotm", "失败不能破坏已有模板")

    def test_install_copy_failure_without_word_running_returns_400(self):
        with patch.object(word.shutil, "copy2", side_effect=OSError("disk full")):
            response = self.client.post("/api/word/install")
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("安装 Word 模板失败", response.json()["detail"])

    # ------------------------------------------------------------ uninstall
    def test_uninstall_removes_template_and_is_idempotent(self):
        self.client.post("/api/word/install")
        first = self.client.post("/api/word/uninstall")
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), {"ok": True, "removed": True})
        self.assertFalse(self.target.is_file())
        self.assertFalse(self.status()["installed"])

        second = self.client.post("/api/word/uninstall")
        self.assertEqual(second.status_code, 200, second.text)
        self.assertEqual(second.json(), {"ok": True, "removed": False}, "没有模板也算卸载成功")


if __name__ == "__main__":
    unittest.main()
