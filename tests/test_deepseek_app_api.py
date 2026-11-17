"""DeepSeek 全家桶 App API：服务端联网工具、PDF 文件解析、上下文预算。

全部用假 httpx / 假模型，不访问网络，也不读写真实设置。
"""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db, llm
from paperwhale_server.services import deepseek_files, rag


class _FakeResponse:
    def __init__(self, data: dict, status: int = 200):
        self.status_code = status
        self.headers = {"content-type": "application/json"}
        self.text = json.dumps(data, ensure_ascii=False)
        self._data = data

    def json(self) -> dict:
        return self._data


class _FakeStream:
    def __init__(self, events: list[dict]):
        self.status_code = 200
        self.headers = {"content-type": "text/event-stream"}
        self._events = events

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def iter_lines(self):
        for event in self._events:
            yield "data: " + json.dumps(event, ensure_ascii=False)
            yield ""

    def read(self) -> bytes:
        return b""


class _FakeClient:
    payload: dict | None = None
    response: dict = {}
    events: list[dict] = []

    def __init__(self, *args, **kwargs):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, headers=None, json=None):
        _FakeClient.payload = json
        return _FakeResponse(_FakeClient.response)

    def stream(self, method, url, headers=None, json=None):
        _FakeClient.payload = json
        return _FakeStream(_FakeClient.events)


class AppApiTestBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-app-api-")
        root = Path(self.temp.name)
        self.root = root
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            _cache={
                **config.DEFAULT_SETTINGS,
                "base_url": "https://api.deepseek.com/v1",
                "api_key": "sk-test",
                "model": "deepseek-flash",
            },
        )
        self.paths.start()
        db.init_db()
        # 这些用例要真的走到 HTTP 客户端，关掉离线 mock
        self.env = patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "0"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.paths.stop()
        self.temp.cleanup()


class ConnectionDetectionTests(AppApiTestBase):
    def test_flash_on_deepseek_supports_app_api(self):
        self.assertTrue(llm.supports_app_api())

    def test_other_model_falls_back(self):
        config._cache["model"] = "deepseek-v4-pro"
        self.assertFalse(llm.supports_app_api())

    def test_other_provider_never_claims_support(self):
        config._cache["base_url"] = "https://api.moonshot.cn/v1"
        self.assertFalse(llm.supports_app_api())

    def test_master_switch_disables(self):
        config._cache["app_api_enabled"] = False
        self.assertFalse(llm.supports_app_api())


class ServerToolRequestTests(AppApiTestBase):
    def test_tools_are_declared_and_citations_collected(self):
        _FakeClient.response = {
            "choices": [{
                "message": {
                    "content": "答案见来源。",
                    "annotations": [
                        {"type": "url_citation", "url_citation": {"url": "https://b", "title": "B"}},
                    ],
                    "server_tool_calls": [{
                        "index": 0,
                        "calls": [{
                            "id": "srvtoolu_1",
                            "kind": "search",
                            "status": "completed",
                            "sources": [{"url": "https://a", "title": "A", "snippet": "..."}],
                        }],
                    }],
                },
            }],
        }
        with patch.object(llm.httpx, "Client", _FakeClient):
            meta = llm.chat(
                [{"role": "user", "content": "查一下"}],
                with_meta=True,
                server_tools=[llm.SERVER_TOOL_SEARCH, llm.SERVER_TOOL_READ_URL],
            )
        self.assertEqual(
            _FakeClient.payload["tools"],
            [{"type": llm.SERVER_TOOL_SEARCH}, {"type": llm.SERVER_TOOL_READ_URL}],
        )
        self.assertEqual([row["url"] for row in meta["citations"]], ["https://a", "https://b"])
        self.assertEqual(meta["server_tool_calls"][0]["index"], 0)
        self.assertEqual(meta["web_trace"][0]["type"], "search")
        self.assertEqual(meta["web_trace"][0]["sources"][0]["url"], "https://a")
        self.assertEqual(meta["tools"], [llm.SERVER_TOOL_SEARCH, llm.SERVER_TOOL_READ_URL])

    def test_tools_dropped_for_unsupported_model(self):
        config._cache["model"] = "deepseek-v4-pro"
        _FakeClient.response = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(llm.httpx, "Client", _FakeClient):
            llm.chat([{"role": "user", "content": "hi"}], server_tools=[llm.SERVER_TOOL_SEARCH])
        self.assertNotIn("tools", _FakeClient.payload)

    def test_json_mode_drops_tools(self):
        _FakeClient.response = {"choices": [{"message": {"content": "{}"}}]}
        with patch.object(llm.httpx, "Client", _FakeClient):
            llm.chat([{"role": "user", "content": "hi"}], json_mode=True,
                     server_tools=[llm.SERVER_TOOL_SEARCH])
        self.assertNotIn("tools", _FakeClient.payload)
        self.assertEqual(_FakeClient.payload["response_format"], {"type": "json_object"})

    def test_streaming_collects_tool_events(self):
        _FakeClient.events = [
            {"choices": [{"delta": {"reasoning_content": "想"}}]},
            {"choices": [{"server_tool_event": {"type": "call_started", "call": {"kind": "search"}}}]},
            {"choices": [{"server_tool_event": {"type": "call_completed", "call": {
                "kind": "search", "sources": [{"url": "https://a", "title": "A"}]}}}]},
            {"choices": [{"delta": {"content": "答案"}}]},
            {"choices": [{"delta": {
                "content": "",
                "annotations": [{"type": "url_citation", "url_citation": {"url": "https://b", "title": "B"}}],
                "server_tool_calls": [{"index": 0, "calls": [{"id": "x", "status": "completed"}]}],
            }, "finish_reason": "stop"}]},
        ]
        with patch.object(llm.httpx, "Client", _FakeClient):
            meta = llm.chat(
                [{"role": "user", "content": "查一下"}],
                with_meta=True,
                on_delta=lambda **_: None,
                server_tools=[llm.SERVER_TOOL_SEARCH],
            )
        self.assertEqual(meta["content"], "答案")
        self.assertEqual([row["url"] for row in meta["citations"]], ["https://a", "https://b"])
        self.assertTrue(meta["server_tool_calls"])


class ThinkingModeTests(AppApiTestBase):
    def _payload_after_call(self) -> dict:
        _FakeClient.response = {"choices": [{"message": {"content": "ok"}}]}
        with patch.object(llm.httpx, "Client", _FakeClient):
            llm.chat([{"role": "user", "content": "hi"}])
        return _FakeClient.payload

    def test_thinking_enabled_and_temperature_dropped_by_default(self):
        payload = self._payload_after_call()
        self.assertEqual(payload["thinking"], {"type": "enabled"})
        self.assertNotIn("temperature", payload)

    def test_thinking_off_keeps_temperature(self):
        config._cache["thinking_mode"] = "off"
        payload = self._payload_after_call()
        self.assertEqual(payload["thinking"], {"type": "disabled"})
        self.assertIn("temperature", payload)

    def test_auto_lets_server_decide(self):
        config._cache["thinking_mode"] = "auto"
        self.assertNotIn("thinking", self._payload_after_call())

    def test_other_provider_gets_no_thinking_param(self):
        config._cache["base_url"] = "https://api.moonshot.cn/v1"
        self.assertNotIn("thinking", self._payload_after_call())


class FileParseTests(AppApiTestBase):
    def _pdf(self, name: str = "paper.pdf", data: bytes = b"%PDF-1.4 fake") -> Path:
        path = self.root / name
        path.write_bytes(data)
        return path

    def test_upload_parses_and_caches_file_id(self):
        path = self._pdf()
        calls: list[dict] = []

        def fake_post(url, headers=None, data=None, files=None, timeout=None):
            calls.append({"url": url, "data": data, "files": files})
            return _FakeResponse({"id": "file-api-1", "purpose": data["purpose"]})

        with patch.object(deepseek_files.httpx, "post", fake_post):
            first = deepseek_files.upload_pdf(path, deepseek_files.PURPOSE_TEXT)
            second = deepseek_files.upload_pdf(path, deepseek_files.PURPOSE_TEXT)

        self.assertEqual(first, "file-api-1")
        self.assertEqual(second, "file-api-1")
        self.assertEqual(len(calls), 1, "第二次应命中 file_id 缓存，不再上传")
        self.assertEqual(calls[0]["data"], {"purpose": deepseek_files.PURPOSE_TEXT})
        self.assertTrue(calls[0]["url"].endswith("/files"))
        self.assertTrue((self.root / "deepseek-files.json").is_file())

    def test_rich_and_text_are_cached_separately(self):
        path = self._pdf()
        with patch.object(deepseek_files.httpx, "post", lambda *a, **k: _FakeResponse({"id": "file-api-rich"})):
            rich = deepseek_files.upload_pdf(path, deepseek_files.PURPOSE_RICH)
        self.assertEqual(rich, "file-api-rich")
        self.assertEqual(deepseek_files.cached_file_id(path, deepseek_files.PURPOSE_TEXT), "")

    def test_server_error_becomes_readable_message(self):
        path = self._pdf()
        with patch.object(deepseek_files.httpx, "post", lambda *a, **k: _FakeResponse({"error": "bad"}, status=413)):
            with self.assertRaises(deepseek_files.FileParseError) as ctx:
                deepseek_files.upload_pdf(path, deepseek_files.PURPOSE_TEXT)
        self.assertIn("100 MB", str(ctx.exception))

    def test_unsupported_connection_refuses(self):
        config._cache["model"] = "deepseek-v4-pro"
        with self.assertRaises(deepseek_files.FileParseError):
            deepseek_files.upload_pdf(self._pdf(), deepseek_files.PURPOSE_TEXT)


class RagBudgetTests(AppApiTestBase):
    def test_budget_follows_setting_and_clamps(self):
        config._cache["answer_context_chars"] = 50000
        self.assertEqual(rag._context_budget(), 50000)
        config._cache["answer_context_chars"] = 100
        self.assertEqual(rag._context_budget(), 8000)
        config._cache["answer_context_chars"] = 9_000_000
        self.assertEqual(rag._context_budget(), 400000)
        config._cache["answer_context_chars"] = 0
        self.assertEqual(rag._context_budget(), rag.FULLTEXT_LIMIT)

    def test_builtin_search_respects_mode(self):
        config._cache["web_search_mode"] = "auto"
        self.assertTrue(rag._use_builtin_search(True, False))
        self.assertFalse(rag._use_builtin_search(False, False))
        self.assertFalse(rag._use_builtin_search(True, True))
        config._cache["web_search_mode"] = "local"
        self.assertFalse(rag._use_builtin_search(True, False))
        config._cache["web_search_mode"] = "builtin"
        self.assertTrue(rag._use_builtin_search(True, False))
        config._cache["model"] = "deepseek-v4-pro"
        self.assertFalse(rag._use_builtin_search(True, False))

    def test_reference_block_renders_markdown_links(self):
        block = rag._reference_block([
            {"url": "https://a", "title": "A 论文"},
            {"url": "https://a", "title": "重复的 A"},
            {"url": "https://b", "title": "B] 带括号"},
        ])
        self.assertIn("[A 论文](https://a)", block)
        self.assertIn("B） 带括号", block)
        self.assertEqual(rag._reference_block([]), "")

    def test_user_content_uses_file_parts(self):
        self.assertEqual(rag._user_content("问题"), "问题")
        parts = rag._user_content("问题", ["file-api-1"])
        self.assertEqual(parts[0], {"type": "file", "file_id": "file-api-1"})
        self.assertEqual(parts[1], {"type": "text", "text": "问题"})


class SettingsApiTests(AppApiTestBase):
    def _client(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app
        return TestClient(app)

    def test_settings_roundtrip_and_validation(self):
        client = self._client()
        ok = client.put("/api/settings", json={
            "app_api_enabled": True,
            "web_search_mode": "builtin",
            "paper_file_mode": "always",
            "answer_context_chars": 120000,
        })
        self.assertEqual(ok.status_code, 200, ok.text)
        data = client.get("/api/settings").json()
        self.assertEqual(data["web_search_mode"], "builtin")
        self.assertEqual(data["paper_file_mode"], "always")
        self.assertEqual(data["answer_context_chars"], 120000)

        bad_mode = client.put("/api/settings", json={"web_search_mode": "nope"})
        self.assertEqual(bad_mode.status_code, 400)
        bad_file = client.put("/api/settings", json={"paper_file_mode": "nope"})
        self.assertEqual(bad_file.status_code, 400)
        bad_budget = client.put("/api/settings", json={"answer_context_chars": 1})
        self.assertEqual(bad_budget.status_code, 400)
        bad_thinking = client.put("/api/settings", json={"thinking_mode": "sometimes"})
        self.assertEqual(bad_thinking.status_code, 400)


class PaperDocumentIntegrationTests(AppApiTestBase):
    def test_scanned_paper_attaches_pdf_file_id(self):
        pdf = self.root / "scan.pdf"
        pdf.write_bytes(b"%PDF-1.4 scanned")
        paper = {"id": "p1", "title": "扫描论文", "file_path": str(pdf)}
        config._cache["paper_file_mode"] = "scanned"
        captured: dict = {}

        def fake_chat(messages, **kwargs):
            captured.update({"messages": messages, "kwargs": kwargs})
            return {"content": "看图后回答", "reasoning": "", "model": "deepseek-flash",
                    "citations": [], "server_tool_calls": []}

        with patch.object(rag.db, "get_paper", return_value=paper), \
                patch.object(rag, "_paper_fulltext_rows", return_value=[]), \
                patch.object(rag, "_search_fts", return_value=[]), \
                patch.object(rag, "_search_like", return_value=[]), \
                patch.object(deepseek_files, "available", return_value=True), \
                patch.object(deepseek_files, "upload_pdf", return_value="file-api-9") as upload, \
                patch.object(rag.llm, "chat", side_effect=fake_chat):
            result = rag.chat("这篇的图说了什么", paper_id="p1")

        upload.assert_called_once()
        user_message = captured["messages"][-1]
        self.assertIsInstance(user_message["content"], list)
        self.assertEqual(user_message["content"][0], {"type": "file", "file_id": "file-api-9"})
        self.assertEqual(user_message["content"][-1]["type"], "text")
        self.assertNotIn("PDF 在线解析失败", result["answer"])

    def test_mode_off_never_uploads(self):
        pdf = self.root / "paper.pdf"
        pdf.write_bytes(b"%PDF-1.4")
        paper = {"id": "p1", "title": "普通论文", "file_path": str(pdf), "abstract": "摘要"}
        config._cache["paper_file_mode"] = "off"
        with patch.object(rag.db, "get_paper", return_value=paper), \
                patch.object(rag, "_paper_fulltext_rows", return_value=[]), \
                patch.object(rag, "_search_fts", return_value=[]), \
                patch.object(rag, "_search_like", return_value=[]), \
                patch.object(deepseek_files, "upload_pdf") as upload, \
                patch.object(rag.llm, "chat", return_value={
                    "content": "回答", "reasoning": "", "model": "deepseek-flash",
                    "citations": [], "server_tool_calls": []}):
            rag.chat("问题", paper_id="p1")
        upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
