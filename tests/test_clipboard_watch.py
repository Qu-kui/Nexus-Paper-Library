# -*- coding: utf-8 -*-
"""剪贴板监听：识别 DOI / arXiv / 标题，去重，入库，开关。

用户需求原话：「盯剪贴板里的 DOI 或者看着像论文题目的」。
这里的测试不碰真剪贴板：``poll_once`` 直接喂文本。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app
from paperwhale_server.services import clipboard_watch, metadata
from paperwhale_server.services.pdf_fetch import manager as pdf_manager

TITLE = "An Optical Frontend for a Convolutional Neural Network"
CJK_TITLE = "基于深度学习的偏振成像系统设计与实验验证"


class ClipboardWatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-clip-")
        self.root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=self.root,
            DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp",
            DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            _cache={
                **config.DEFAULT_SETTINGS,
                "clipboard_watch": True,
                "clipboard_title_guess": True,
                "scholar_auto_sync": False,
                "pdf_browser_session_enabled": False,
            },
        )
        self.paths.start()
        db.init_db()
        clipboard_watch.reset()
        self.client = TestClient(app)

    def tearDown(self):
        clipboard_watch.reset()
        self.paths.stop()
        self.temp.cleanup()

    # ---------- 识别 ----------

    def test_detects_doi_inside_pasted_citation(self):
        found = clipboard_watch.detect("Optics Letters, doi:10.1364/OL.420000.")
        self.assertEqual(found["kind"], "doi")
        self.assertEqual(found["value"].lower(), "10.1364/ol.420000")

    def test_detects_arxiv_url_tag_and_bare_id(self):
        for text in (
            "https://arxiv.org/abs/2401.12345",
            "arXiv:2401.12345v2",
            "2401.12345",
        ):
            found = clipboard_watch.detect(text)
            self.assertIsNotNone(found, text)
            self.assertEqual(found["kind"], "arxiv", text)
            self.assertTrue(found["value"].startswith("2401.12345"), text)

    def test_detects_english_and_chinese_titles(self):
        for text in (TITLE, CJK_TITLE):
            found = clipboard_watch.detect(text)
            self.assertIsNotNone(found, text)
            self.assertEqual(found["kind"], "title", text)

    def test_ignores_sentences_urls_and_short_noise(self):
        for text in (
            "我们明天下午三点开会，记得带上材料。",
            "https://example.com/some/page",
            "ok",
            "你好",
        ):
            self.assertIsNone(clipboard_watch.detect(text), text)

    def test_ignores_pasted_paragraphs(self):
        paragraph = (
            "Recent work on optical computing has shown that diffractive layers can\n"
            "implement linear transforms, but the alignment tolerance of the whole\n"
            "system remains a practical bottleneck in experiments and simulations."
        )
        self.assertIsNone(clipboard_watch.detect(paragraph))
        long_prose = (
            "The authors report that the proposed frontend reduces the number of "
            "optical components required for convolution while keeping accuracy, and "
            "they validate it with both numerical simulation and a free-space "
            "experiment in the visible band."
        )
        self.assertIsNone(clipboard_watch.detect(long_prose))

    def test_title_does_not_match_when_already_in_library(self):
        db.execute(
            "INSERT INTO papers(id,title,created_at,updated_at) VALUES(?,?,?,?)",
            ("p1", TITLE, db.now(), db.now()),
        )
        self.assertIsNone(clipboard_watch.poll_once(TITLE))

    # ---------- 去重与开关 ----------

    def test_same_clipboard_text_only_prompts_once(self):
        first = clipboard_watch.poll_once("10.1364/OL.420000")
        second = clipboard_watch.poll_once("10.1364/OL.420000")
        self.assertIsNotNone(first)
        self.assertIsNone(second)
        self.assertEqual(len(clipboard_watch.snapshot()["items"]), 1)

    def test_watch_can_be_switched_off(self):
        config.update_settings({"clipboard_watch": False})
        self.assertIsNone(clipboard_watch.poll_once("10.1364/OL.420000"))
        self.assertFalse(clipboard_watch.snapshot()["enabled"])

    def test_title_guessing_can_be_switched_off(self):
        config.update_settings({"clipboard_title_guess": False})
        self.assertIsNone(clipboard_watch.poll_once(TITLE))
        self.assertEqual(clipboard_watch.poll_once("10.1364/OL.420000")["kind"], "doi")

    def test_candidates_are_capped(self):
        for index in range(6):
            clipboard_watch.poll_once(f"10.1364/OL.42000{index}")
        self.assertLessEqual(len(clipboard_watch.snapshot()["items"]), clipboard_watch.MAX_ITEMS)

    # ---------- 入库 ----------

    def test_accept_doi_creates_paper_and_queues_pdf(self):
        item = clipboard_watch.poll_once("10.1364/OL.420000")
        with patch.object(
            metadata,
            "crossref_by_doi",
            return_value={"title": "Optical paper", "doi": "10.1364/OL.420000"},
        ), patch.object(pdf_manager, "start", return_value=1):
            result = clipboard_watch.accept(item["id"])
        self.assertEqual(result["paper"]["title"], "Optical paper")
        self.assertEqual(result["pdf_fetch_queued"], 1)
        self.assertTrue(db.get_paper(result["paper"]["id"]))

    def test_accept_title_without_metadata_still_creates_entry(self):
        item = clipboard_watch.poll_once(TITLE)
        with patch.object(metadata, "find_metadata_by_title", return_value={}), patch.object(
            pdf_manager, "start", return_value=0
        ):
            result = clipboard_watch.accept(item["id"])
        self.assertEqual(result["paper"]["title"], TITLE)
        self.assertEqual(result["paper"]["source"], "clipboard")

    def test_accept_twice_is_rejected(self):
        item = clipboard_watch.poll_once("10.1364/OL.420000")
        clipboard_watch.dismiss(item["id"])
        with self.assertRaises(KeyError):
            clipboard_watch.accept(item["id"])

    # ---------- API ----------

    def test_candidates_api_lists_and_dismisses(self):
        item = clipboard_watch.poll_once("10.1364/OL.420000")
        payload = self.client.get("/api/clipboard/candidates").json()
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["items"][0]["id"], item["id"])
        self.assertEqual(
            self.client.post(f"/api/clipboard/{item['id']}/dismiss").status_code, 200
        )
        self.assertEqual(
            self.client.post(f"/api/clipboard/{item['id']}/dismiss").status_code, 404
        )
        self.assertEqual(self.client.get("/api/clipboard/candidates").json()["items"], [])

    def test_accept_api_returns_paper(self):
        item = clipboard_watch.poll_once("10.1364/OL.420000")
        with patch.object(
            metadata,
            "crossref_by_doi",
            return_value={"title": "Optical paper", "doi": "10.1364/OL.420000"},
        ), patch.object(pdf_manager, "start", return_value=0):
            response = self.client.post(f"/api/clipboard/{item['id']}/accept")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["paper"]["title"], "Optical paper")

    def test_accept_api_maps_metadata_error_to_status(self):
        item = clipboard_watch.poll_once("10.1364/OL.420000")
        with patch.object(
            metadata, "crossref_by_doi", side_effect=metadata.MetadataError("没查到", 404)
        ):
            response = self.client.post(f"/api/clipboard/{item['id']}/accept")
        self.assertEqual(response.status_code, 404)

    def test_accept_api_unknown_id_is_404(self):
        self.assertEqual(self.client.post("/api/clipboard/deadbeefdead/accept").status_code, 404)


if __name__ == "__main__":
    unittest.main()
