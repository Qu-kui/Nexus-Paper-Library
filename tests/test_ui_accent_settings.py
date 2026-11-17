# -*- coding: utf-8 -*-
"""设置项 ui_accent（强调色）：默认值、合法/非法校验、持久化与隔离。

临时数据目录 + 内存缓存 patch，绝不碰真实 %APPDATA%\\PaperWhale\\settings.json。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app

DEFAULT_ACCENT = "#0e7c86"
DEFAULT_THEME = "midnight"


class UiAccentSettingsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-ui-accent-")
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

    def get_accent(self) -> str:
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["ui_accent"]

    def get_theme(self) -> str:
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["ui_theme"]

    def put(self, payload):
        return self.client.put("/api/settings", json=payload)

    # ------------------------------------------------------------ 默认值
    def test_default_accent_is_returned(self):
        response = self.client.get("/api/settings")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ui_accent"], DEFAULT_ACCENT)

    # ------------------------------------------------------------ 合法值
    def test_valid_accent_is_saved_and_returned(self):
        response = self.put({"ui_accent": "#2563eb"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ui_accent"], "#2563eb")
        self.assertEqual(self.get_accent(), "#2563eb", "再次 GET 仍是新颜色")
        self.assertEqual(config.get_settings()["ui_accent"], "#2563eb")
        stored = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["ui_accent"], "#2563eb", "落盘到临时 settings.json")

    def test_uppercase_hex_is_accepted(self):
        response = self.put({"ui_accent": "#2563EB"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.get_accent(), "#2563EB")

    # ------------------------------------------------------------ 非法值
    def test_invalid_accent_returns_400_and_keeps_value(self):
        self.assertEqual(self.put({"ui_accent": "#2563eb"}).status_code, 200)
        for bad in ("blue", "#12345", "#1234567", "#GGGGGG", "# 2563eb"):
            with self.subTest(value=bad):
                response = self.put({"ui_accent": bad})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "强调色应为 #RRGGBB 格式")
                self.assertEqual(self.get_accent(), "#2563eb", "非法输入不能改动已保存颜色")

    # ------------------------------------------------------------ 不传就不覆盖
    def test_other_updates_do_not_touch_accent(self):
        self.assertEqual(self.put({"ui_accent": "#2563eb"}).status_code, 200)
        response = self.put({"temperature": 0.5})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ui_accent"], "#2563eb")
        self.assertEqual(response.json()["temperature"], 0.5)
        self.assertEqual(self.get_accent(), "#2563eb")

    # ------------------------------------------------------------ 隔离
    def test_uses_isolated_settings_file(self):
        self.assertTrue(
            str(config.SETTINGS_PATH).startswith(str(self.root)),
            f"SETTINGS_PATH 必须指向临时目录：{config.SETTINGS_PATH}",
        )
        self.assertEqual(self.put({"ui_accent": "#2563eb"}).status_code, 200)
        self.assertTrue((self.root / "settings.json").is_file())
        # 真实用户设置目录不能被写：这里只断言当前路径，不读取真实文件
        real_suffix = Path("AppData") / "Roaming" / "PaperWhale"
        self.assertNotIn(str(real_suffix), str(config.SETTINGS_PATH))

    # ------------------------------------------------------------ 主题预设 ui_theme
    def test_default_theme_is_returned(self):
        self.assertEqual(self.get_theme(), DEFAULT_THEME)

    def test_valid_theme_is_saved_and_returned(self):
        response = self.put({"ui_theme": "celadon"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["ui_theme"], "celadon")
        self.assertEqual(self.get_theme(), "celadon", "再次 GET 仍是新主题")
        self.assertEqual(config.get_settings()["ui_theme"], "celadon")
        stored = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(stored["ui_theme"], "celadon", "落盘到临时 settings.json")
        # 合法边界：小写字母开头、含数字/连字符、总长 32
        longest = "a" + "b1-" * 10 + "x"  # 32 字符
        self.assertEqual(len(longest), 32)
        self.assertEqual(self.put({"ui_theme": longest}).status_code, 200, "32 字符应合法")

    def test_invalid_theme_returns_400_and_keeps_value(self):
        self.assertEqual(self.put({"ui_theme": "celadon"}).status_code, 200)
        for bad in ("Paper", "纸白", "a b", "", "1abc", "-abc", "a" * 33, "celadon!", "A"):
            with self.subTest(value=bad):
                response = self.put({"ui_theme": bad})
                self.assertEqual(response.status_code, 400, response.text)
                self.assertEqual(response.json()["detail"], "主题预设标识不合法")
                self.assertEqual(self.get_theme(), "celadon", "非法输入不能改动已保存主题")

    def test_other_updates_do_not_touch_theme(self):
        self.assertEqual(self.put({"ui_theme": "celadon"}).status_code, 200)
        for payload in ({"ui_accent": "#2563eb"}, {"temperature": 0.5}):
            with self.subTest(payload=payload):
                response = self.put(payload)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["ui_theme"], "celadon")
                self.assertEqual(self.get_theme(), "celadon")

    def test_accent_and_theme_are_saved_together(self):
        response = self.put({"ui_accent": "#2563eb", "ui_theme": "celadon"})
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["ui_accent"], "#2563eb")
        self.assertEqual(body["ui_theme"], "celadon")
        settings = self.client.get("/api/settings").json()
        self.assertEqual(settings["ui_accent"], "#2563eb")
        self.assertEqual(settings["ui_theme"], "celadon")
        stored = json.loads((self.root / "settings.json").read_text(encoding="utf-8"))
        self.assertEqual(
            (stored["ui_accent"], stored["ui_theme"]), ("#2563eb", "celadon"), "两者都落盘"
        )


if __name__ == "__main__":
    unittest.main()
