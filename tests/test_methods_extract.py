# -*- coding: utf-8 -*-
"""方法与实验参数库：提取落库、幂等、失败分流与 API 契约。

隔离方式对齐 tests/test_analyze_metadata.py：patch config 的数据路径与 _cache，
用 fixtures/attention.pdf 当真实 PDF，LLM 走 PAPERWHALE_LLM_MOCK=1 的固定返回。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db, llm
from paperwhale_server.main import app
from paperwhale_server.services import methods as methods_service

PDF_BYTES = (Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes()
REQUIRED_ITEM_KEYS = {
    "paper_id", "title", "year", "venue", "status", "method_summary",
    "experiment_type", "parameters", "materials", "equipment", "conditions",
    "error", "updated_at",
}


class MethodsBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-methods-")
        self.root = Path(self.temp.name)
        self.settings = patch.multiple(
            config,
            DATA_DIR=self.root,
            DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp",
            DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "api_key": "fake-test-key", "scholar_auto_sync": False},
        )
        self.settings.start()
        self.env = patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "1"})
        self.env.start()
        db.init_db()
        (self.root / "pdfs").mkdir(parents=True, exist_ok=True)
        self.pdf = self.root / "pdfs" / "p1.pdf"
        self.pdf.write_bytes(PDF_BYTES)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    # ---------------------------------------------------------------- 工具
    def add_paper(self, paper_id="p1", *, with_pdf=True, **fields):
        row = {
            "id": paper_id,
            "title": "Attention Is All You Need",
            "year": "2017",
            "venue": "NeurIPS",
            "file_path": str(self.pdf) if with_pdf else "",
            "summary": "测试摘要",
        }
        row.update(fields)
        db.execute(
            "INSERT INTO papers(id,title,year,venue,file_path,summary) VALUES(?,?,?,?,?,?)",
            (row["id"], row["title"], row["year"], row["venue"], row["file_path"], row["summary"]),
        )
        return row

    def add_chunks(self, paper_id, rows):
        for page, text in rows:
            db.execute(
                "INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)", (paper_id, page, text)
            )

    def wait(self, predicate, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False

    def wait_batch(self, timeout=20):
        """等批次进入 completed，避免 worker 还在写库时 tearDown 删掉临时库。"""
        return self.wait(
            lambda: self.client.get("/api/methods/status").json().get("state") == "completed",
            timeout,
        )

    def profile(self, paper_id="p1"):
        row = db.query_one("SELECT * FROM method_profiles WHERE paper_id = ?", (paper_id,))
        return dict(row) if row else None


class MethodsExtractTests(MethodsBase):
    # ------------------------------------------------------------ 主场景
    def test_extract_persists_profile_row_and_columns(self):
        self.add_paper()
        self.add_chunks("p1", [(3, "3 实验装置与方法：使用 1550 nm 激光器。")])
        result = methods_service.extract("p1")
        self.assertEqual(result["status"], "ok")
        row = self.profile()
        self.assertEqual(row["status"], "done")
        self.assertIn("Mock", row["method_summary"])
        self.assertEqual(row["experiment_type"], "数值仿真")
        payload = json.loads(row["data"])
        self.assertEqual(len(payload["parameters"]), 2)
        self.assertEqual(set(payload["parameters"][0]), {"name", "value", "unit", "note"})
        self.assertIsInstance(payload["materials"], list)
        self.assertIsInstance(payload["equipment"], list)
        self.assertIsInstance(payload["conditions"], list)
        self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM method_profiles")["c"], 1)

    def test_method_text_prefers_section_blocks_and_falls_back_to_first_pages(self):
        self.add_paper()
        self.add_chunks("p1", [(1, "Introduction 引言"), (2, "2 方法：实验装置 A"), (9, "实验数据与结果")])
        text = methods_service.method_text(db.get_paper("p1"))
        self.assertIn("第2页", text)
        self.assertIn("第9页", text)
        self.assertNotIn("第1页", text, "命中方法/实验关键词时，不该退化成前 8 页")

        self.add_paper("p2")
        self.add_chunks("p2", [(page, f"正文第{page}页") for page in range(1, 11)])
        fallback = methods_service.method_text(db.get_paper("p2"))
        self.assertIn("第8页", fallback)
        self.assertNotIn("第9页", fallback, "没命中关键词时只取前 8 页")

    def test_method_text_is_capped(self):
        self.add_paper()
        self.add_chunks("p1", [(1, "方法 " + "很长的正文" * 4000)])
        text = methods_service.method_text(db.get_paper("p1"))
        self.assertLessEqual(len(text), methods_service.MAX_TEXT + 20)

    def test_repeated_extract_overwrites_same_row(self):
        self.add_paper()
        self.add_chunks("p1", [(2, "2 实验方法")])
        first = methods_service.extract("p1")
        self.add_chunks("p1", [(4, "4 实验装置 B")])
        second = methods_service.extract("p1")
        self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM method_profiles")["c"], 1)
        self.assertEqual(self.profile()["status"], "done")
        self.assertEqual(first["profile"]["created_at"], second["profile"]["created_at"])
        self.assertEqual(
            json.loads(self.profile()["data"])["method_summary"],
            "【Mock】用数值仿真验证偏振成像方案。",
        )

    # ------------------------------------------------------------ 失败分流
    def test_paper_without_pdf_is_rejected(self):
        self.add_paper("nopdf", with_pdf=False, title="No PDF paper")
        with self.assertRaises(ValueError):
            methods_service.extract("nopdf")
        self.assertIsNone(self.profile("nopdf"))
        response = self.client.post("/api/methods/extract", json={"paper_ids": ["nopdf"]})
        self.assertEqual(response.status_code, 400, response.text)

    def test_paper_without_chunks_fails_in_queue(self):
        self.add_paper("empty")
        self.add_paper("p1")
        self.add_chunks("p1", [(2, "2 实验方法")])
        response = self.client.post(
            "/api/methods/extract", json={"paper_ids": ["empty", "p1"]}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["queued"], 2)
        self.assertTrue(
            self.wait(lambda: self.profile("empty") and self.profile("empty")["status"] == "failed")
        )
        self.assertIn("正文索引", self.profile("empty")["error"])
        self.assertTrue(
            self.wait(lambda: self.profile("p1") and self.profile("p1")["status"] == "done")
        )
        self.assertTrue(self.wait_batch(), "批次要跑完再结束测试")

    def test_failed_extraction_marks_profile_and_batch(self):
        self.add_paper()
        self.add_chunks("p1", [(2, "2 实验方法")])
        with patch.object(
            methods_service.llm, "chat_json", side_effect=llm.LLMError("模拟大模型失败")
        ):
            response = self.client.post("/api/methods/extract", json={"paper_ids": ["p1"]})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertTrue(
                self.wait(lambda: self.profile() and self.profile()["status"] == "failed")
            )
            self.assertTrue(self.wait_batch())
            status = self.client.get("/api/methods/status").json()
        self.assertEqual(status["kind"], "methods")
        self.assertEqual(status["failed"], 1)
        self.assertEqual(status["succeeded"], 0)
        self.assertIn("模拟大模型失败", self.profile()["error"])
        self.assertEqual(status["errors"][0]["paper_id"], "p1")
        self.assertEqual(status["errors"][0]["title"], "Attention Is All You Need")

    def test_error_text_scrubs_api_key(self):
        self.add_paper()
        self.add_chunks("p1", [(2, "2 实验方法")])
        with patch.object(
            methods_service.llm,
            "chat_json",
            side_effect=RuntimeError("boom fake-test-key 炸了"),
        ):
            self.client.post("/api/methods/extract", json={"paper_ids": ["p1"]})
            self.assertTrue(
                self.wait(lambda: self.profile() and self.profile()["status"] == "failed")
            )
            self.assertTrue(self.wait_batch())
        error = self.profile()["error"]
        self.assertNotIn("fake-test-key", error)
        self.assertIn("***", error)

    # ------------------------------------------------------------ 排队规则
    def test_only_pdf_papers_are_queued_and_only_new_skips_done(self):
        self.add_paper("p1")
        self.add_chunks("p1", [(2, "2 实验方法")])
        self.add_paper("p2", with_pdf=False, title="Second")
        response = self.client.post("/api/methods/extract", json={})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["queued"], 1)
        self.assertTrue(self.wait(lambda: (self.profile("p1") or {}).get("status") == "done"))
        self.assertTrue(self.wait_batch(), "批次要跑完再结束测试")
        self.assertIsNone(self.profile("p2"), "没有 PDF 的论文不该有 profile 行")
        self.assertEqual(response.json()["status"]["total"], 1)

        # only_new 默认跳过已 done；显式 only_new=false 才允许重新提取
        again = self.client.post("/api/methods/extract", json={"paper_ids": ["p1"]})
        self.assertEqual(again.status_code, 400, again.text)
        force = self.client.post(
            "/api/methods/extract", json={"paper_ids": ["p1"], "only_new": False}
        )
        self.assertEqual(force.status_code, 200, force.text)
        self.assertEqual(force.json()["queued"], 1)
        self.assertTrue(self.wait(lambda: (self.profile("p1") or {}).get("status") == "done"))
        self.assertTrue(self.wait_batch())

    def test_unconfigured_llm_returns_400(self):
        self.add_paper()
        self.add_chunks("p1", [(2, "2 实验方法")])
        with patch.object(
            config, "_cache", {**config.DEFAULT_SETTINGS, "scholar_auto_sync": False}
        ), patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "0"}):
            response = self.client.post("/api/methods/extract", json={"paper_ids": ["p1"]})
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("大模型未配置", response.json()["detail"])


class MethodsApiTests(MethodsBase):
    def test_list_filters_search_and_paper_detail(self):
        self.add_paper("p1")
        self.add_chunks("p1", [(2, "2 实验方法")])
        methods_service.extract("p1")
        self.add_paper("p2", with_pdf=False, title="Polarization Imaging Survey")
        self.add_paper("p3", with_pdf=False, title="Unrelated Topic")

        listing = self.client.get("/api/methods").json()
        self.assertGreaterEqual(listing["total"], 3)
        self.assertTrue(REQUIRED_ITEM_KEYS <= set(listing["items"][0]))
        bare = next(item for item in listing["items"] if item["paper_id"] == "p2")
        self.assertEqual(bare["status"], "")
        self.assertEqual(bare["parameters"], [])

        # q 在标题 / 方法摘要 / 参数 JSON 文本里模糊匹配，大小写不敏感
        self.assertEqual(self.client.get("/api/methods?q=polarization").json()["total"], 1)
        self.assertGreaterEqual(self.client.get("/api/methods?q=%E5%81%8F%E6%8C%AF").json()["total"], 1)
        self.assertEqual(self.client.get("/api/methods?q=1550").json()["total"], 1)

        done = self.client.get("/api/methods?state=done").json()
        self.assertEqual([item["paper_id"] for item in done["items"]], ["p1"])
        pending = self.client.get("/api/methods?state=pending").json()
        self.assertNotIn("p1", [item["paper_id"] for item in pending["items"]])
        self.assertIn("p2", [item["paper_id"] for item in pending["items"]])

        detail = self.client.get("/api/methods/paper/p1").json()
        self.assertEqual(detail["paper"]["title"], "Attention Is All You Need")
        self.assertEqual(detail["profile"]["status"], "done")
        self.assertEqual(len(detail["profile"]["parameters"]), 2)
        missing = self.client.get("/api/methods/paper/nope")
        self.assertEqual(missing.status_code, 404)
        self.assertIsNone(
            self.client.get("/api/methods/paper/p2").json()["profile"], "未提取的论文 profile 为 null"
        )

    def test_list_limit_returns_at_most_200_rows(self):
        for index in range(205):
            db.execute(
                "INSERT INTO papers(id,title,file_path) VALUES(?,?,?)",
                (f"bulk{index}", f"Bulk {index}", ""),
            )
        listing = self.client.get("/api/methods").json()
        self.assertEqual(listing["total"], 205)
        self.assertEqual(len(listing["items"]), 200)


if __name__ == "__main__":
    unittest.main()
