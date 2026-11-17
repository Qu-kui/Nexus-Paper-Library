"""深度思考计时：进度快照与落库的「用时 N 秒」不能凭空出现，也不能算错。"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db
from paperwhale_server.services import ai_progress, conversations


class ProgressTimingTests(unittest.TestCase):
    def test_thinking_seconds_ends_at_first_answer(self):
        token = ai_progress.begin("paper:timing", "conv", 1)
        try:
            ai_progress.emit(reasoning_delta="先想一想")
            time.sleep(0.06)
            ai_progress.emit(answer_delta="答案")
            time.sleep(0.04)
        finally:
            ai_progress.finish(token)
        row = ai_progress.snapshot("paper:timing", "conv", 1)
        self.assertEqual(row["state"], "completed")
        self.assertGreaterEqual(row["thinking_seconds"], 0.05)
        self.assertLessEqual(row["thinking_seconds"], row["elapsed_seconds"] + 0.001)

    def test_web_trace_round_trips_through_snapshot(self):
        token = ai_progress.begin("paper:trace", "conv", 1)
        try:
            ai_progress.emit(reasoning_delta="查一下")
            ai_progress.emit(web_trace=[{"type": "search", "sources": [{"url": "https://x", "title": "X"}]}])
        finally:
            ai_progress.finish(token)
        row = ai_progress.snapshot("paper:trace", "conv", 1)
        self.assertEqual(row["web_trace"][0]["type"], "search")
        self.assertEqual(row["web_trace"][0]["sources"][0]["url"], "https://x")

    def test_idle_snapshot_has_zero_timing(self):
        row = ai_progress.snapshot("paper:none", "conv", 1)
        self.assertEqual(row["state"], "idle")
        self.assertEqual(row["thinking_seconds"], 0.0)
        self.assertEqual(row["elapsed_seconds"], 0.0)


class ConversationTimingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-thinking-")
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            _cache={**config.DEFAULT_SETTINGS},
        )
        self.paths.start()
        db.init_db()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_turn_persists_thinking_seconds(self):
        def generate(history, materials):
            ai_progress.emit(reasoning_delta="先读资料")
            time.sleep(0.06)
            ai_progress.emit(answer_delta="结论")
            return {"answer": "结论 [1]", "sources": [], "reasoning": "先读资料"}

        conversations.turn("library", "这个问题怎么答", generate)
        snapshot = conversations.snapshot("library")
        assistant = [row for row in snapshot["messages"] if row["role"] == "assistant"][-1]
        self.assertIn("thinking_seconds", assistant)
        self.assertGreaterEqual(float(assistant["thinking_seconds"]), 0.05)


class ProgressStreamTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-stream-")
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            _cache={**config.DEFAULT_SETTINGS},
        )
        self.paths.start()
        db.init_db()
        db.execute("INSERT INTO papers (id,title,created_at,updated_at) VALUES ('p','Paper',?,?)", (db.now(), db.now()))

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_stream_emits_final_state_then_done(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app

        token = ai_progress.begin("paper:p", "conv", 1)
        try:
            ai_progress.emit(reasoning_delta="先想")
            ai_progress.emit(answer_delta="答案")
        finally:
            ai_progress.finish(token)

        with TestClient(app) as client:
            with client.stream("GET", "/api/ai/progress/paper:p/stream",
                               params={"conversation_id": "conv", "revision": 1}) as response:
                self.assertEqual(response.status_code, 200)
                self.assertIn("text/event-stream", response.headers.get("content-type", ""))
                body = "".join(response.iter_text())
        self.assertIn('"state": "completed"', body)
        self.assertIn("event: done", body)
        self.assertIn("thinking_seconds", body)


if __name__ == "__main__":
    unittest.main()
