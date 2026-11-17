# -*- coding: utf-8 -*-
"""Word 引文选择窗口的回传桥：/api/word/pick 与 /word-picker 页面。

Word 侧提交 token 结果，Word VBA 轮询 GET 取回（一次性）。
"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app
from paperwhale_server.routers import word


class WordPickerBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-word-picker-")
        self.root = Path(self.temp.name)
        self.web = self.root / "web"
        self.web.mkdir(parents=True, exist_ok=True)
        (self.web / "word-picker.html").write_text(
            "<!doctype html><html><body>picker</body></html>", encoding="utf-8"
        )
        self.settings = patch.multiple(
            config,
            DATA_DIR=self.root,
            DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp",
            DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            WEB_DIR=self.web,
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False},
        )
        self.settings.start()
        self.env = patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "1"})
        self.env.start()
        db.init_db()
        with word._pick_lock:
            word._pick_tokens.clear()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        with word._pick_lock:
            word._pick_tokens.clear()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def submit(self, token, ids):
        return self.client.post("/api/word/pick", json={"token": token, "ids": ids})

    def fetch(self, token):
        return self.client.get("/api/word/pick", params={"token": token})

    # ------------------------------------------------------------ 正常回传
    def test_roundtrip_dedup_order_and_one_shot(self):
        response = self.submit("tok-1", ["a", "", "a", "b", " ", "c"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {"ok": True})
        first = self.fetch("tok-1")
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.text, "a,b,c")
        second = self.fetch("tok-1")
        self.assertEqual(second.text, "", "取过一次就删除")

    def test_non_string_ids_are_skipped(self):
        self.submit("tok-2", ["a", 123, None, "b"])
        self.assertEqual(self.fetch("tok-2").text, "a,b")

    def test_cancel_marker_is_one_shot(self):
        self.assertEqual(self.submit("tok-cancel", []).status_code, 200)
        first = self.fetch("tok-cancel")
        self.assertEqual(first.text, "__CANCELLED__")
        self.assertEqual(self.fetch("tok-cancel").text, "")

    def test_same_token_last_post_wins(self):
        self.submit("tok-3", ["a"])
        self.submit("tok-3", ["b", "c"])
        self.assertEqual(self.fetch("tok-3").text, "b,c")

    def test_ids_capped_at_500(self):
        self.submit("tok-4", [f"p{index}" for index in range(520)])
        text = self.fetch("tok-4").text
        parts = text.split(",")
        self.assertEqual(len(parts), 500)
        self.assertEqual(parts[0], "p0")
        self.assertEqual(parts[-1], "p499")

    # ------------------------------------------------------------ 校验与边界
    def test_token_validation_returns_400(self):
        for body in (
            {"token": "", "ids": ["a"]},
            {"token": "   ", "ids": ["a"]},
            {"token": "x" * 65, "ids": ["a"]},
            {"token": 123, "ids": ["a"]},
            {"token": None, "ids": ["a"]},
            {"ids": ["a"]},
            {"token": "t", "ids": "not-a-list"},
        ):
            with self.subTest(body=body):
                response = self.client.post("/api/word/pick", json=body)
                self.assertEqual(response.status_code, 400, response.text)

    def test_unknown_or_empty_token_returns_empty_text(self):
        for params in ({"token": "missing"}, {}):
            with self.subTest(params=params):
                response = self.client.get("/api/word/pick", params=params)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.text, "")
                self.assertTrue(
                    response.headers["content-type"].startswith("text/plain"),
                    response.headers["content-type"],
                )

    def test_expired_token_is_purged(self):
        self.submit("tok-old", ["a"])
        with word._pick_lock:
            word._pick_tokens["tok-old"]["ts"] = time.time() - word.PICK_TTL_SECONDS - 10
        self.assertEqual(self.fetch("tok-old").text, "")
        with word._pick_lock:
            self.assertNotIn("tok-old", word._pick_tokens, "过期项要被清理掉")

    def test_result_is_plain_text_without_bom_or_trailing_newline(self):
        self.submit("tok-5", ["id1", "id2"])
        response = self.fetch("tok-5")
        self.assertEqual(response.content, b"id1,id2")
        self.assertFalse(response.content.startswith(b"\xef\xbb\xbf"))
        self.assertNotIn(b"\r", response.content)
        self.assertFalse(response.content.endswith(b"\n"))
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))

    # ------------------------------------------------------------ 页面
    def test_word_picker_page_is_served(self):
        response = self.client.get("/word-picker")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("text/html", response.headers["content-type"])
        self.assertIn(b"<html", response.content)

    def test_word_picker_page_missing_returns_404(self):
        empty = self.root / "empty-web"
        empty.mkdir()
        with patch.object(config, "WEB_DIR", empty):
            response = self.client.get("/word-picker")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertIn("word-picker.html", response.json()["message"])


if __name__ == "__main__":
    unittest.main()
