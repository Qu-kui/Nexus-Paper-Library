# -*- coding: utf-8 -*-
"""分类助手：全量重新分类 + 进度进任务中心。

覆盖点：
1. scope=all 选全部论文，默认只选未分类；
2. 队列跑完后写入 task_batches(kind='category')，任务中心能查到批次；
3. move_files=False 时只改分类、不动磁盘上的 PDF；True 时移动；
4. preview 给出会处理多少篇。

隔离要求：自行 patch 到 tempfile，不碰真实 %APPDATA%\\PaperWhale。
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import batches, categories, category_assistant


class CategoryBulkTests(unittest.TestCase):
    def setUp(self):
        # 分类队列跑在后台线程里：线程收尾可能还在写库，临时目录可能一时删不掉，
        # 因此忽略清理错误（临时目录留在 %TEMP%，不影响真实数据）。
        self.temp = tempfile.TemporaryDirectory(prefix="pw-cat-", ignore_cleanup_errors=True)
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

    def tearDown(self):
        # 给后台线程一点收尾时间，避免它还在写 test.db 时清理目录
        time.sleep(0.2)
        self.settings.stop()
        self.temp.cleanup()

    def add_paper(self, paper_id: str, title: str, category_major: str = "", category_path: str = "", file_path: str = ""):
        db.execute(
            "INSERT INTO papers(id,title,category_major,category_path,file_path) VALUES(?,?,?,?,?)",
            (paper_id, title, category_major, category_path, file_path),
        )

    def wait_batch(self, batch_id: str, timeout: float = 15.0) -> dict:
        deadline = time.time() + timeout
        status = {}
        while time.time() < deadline:
            status = batches.status(batch_id)
            if status.get("state") == "completed":
                return status
            time.sleep(0.05)
        return status

    # ---------------------------------------------------------------- 选择范围
    def test_unclassified_scope_is_default(self):
        self.add_paper("p1", "Needs Category")
        self.add_paper("p2", "Already Sorted", category_major="光学工程", category_path='["光学工程"]')
        self.assertEqual(category_assistant.select_paper_ids(), ["p1"])
        self.assertEqual(category_assistant.select_paper_ids("unclassified"), ["p1"])

    def test_all_scope_selects_every_paper(self):
        self.add_paper("p1", "Needs Category")
        self.add_paper("p2", "Already Sorted", category_major="光学工程", category_path='["光学工程"]')
        self.assertEqual(set(category_assistant.select_paper_ids("all")), {"p1", "p2"})
        preview = category_assistant.preview("all")
        self.assertEqual(preview["scope"], "all")
        self.assertEqual(preview["count"], 2)
        self.assertEqual(preview["total"], 2)

    # ------------------------------------------------------- 批次 + 任务中心可见
    def test_bulk_run_creates_category_batch_and_completes(self):
        self.add_paper("p1", "Paper One")
        self.add_paper("p2", "Paper Two")
        # 队列跑在后台线程里：mock 必须包住「入队 + 等批次跑完」整段，否则线程可能拿到真模型。
        with patch.object(category_assistant.llm, "chat_json", return_value={"path": ["光学工程", "偏振成像"]}):
            result = category_assistant.apply("按器件类型分", scope="all", move_files=False)
            self.assertEqual(result["queued"], 2)
            self.assertEqual(result["scope"], "all")
            batch_id = result["status"].get("batch_id")
            self.assertTrue(batch_id, f"分类批次必须写进任务中心，实际：{result['status']}")
            status = self.wait_batch(batch_id)

        self.assertEqual(status["state"], "completed")
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["succeeded"], 2)
        self.assertEqual(status["failed"], 0)

        kinds = [row["id"] for row in db.query("SELECT id FROM task_batches WHERE kind='category'")]
        self.assertIn(batch_id, kinds)
        # 任务中心历史里能看到这一批
        recent_ids = [item["batch_id"] for item in batches.recent()]
        self.assertIn(batch_id, recent_ids)
        # 分类结果真的写进了论文
        paper = db.get_paper("p1")
        self.assertEqual(paper["category_major"], "光学工程")
        self.assertIn("偏振成像", paper["category_path"])

    def test_failed_classification_marks_batch_item_failed(self):
        self.add_paper("p1", "Paper One")
        with patch.object(category_assistant.llm, "chat_json", side_effect=RuntimeError("模型不可用")):
            result = category_assistant.apply("guide", scope="all", move_files=False)
            status = self.wait_batch(result["status"]["batch_id"])
        self.assertEqual(status["failed"], 1, f"status={status}")
        self.assertEqual(status["succeeded"], 0, f"status={status}")
        self.assertTrue(status["errors"], "失败项要能在任务中心看到原因")
        self.assertIn("模型不可用", str(status["errors"][0].get("error")))

    # ------------------------------------------------------------- PDF 移动开关
    def test_move_files_false_keeps_pdf_in_place(self):
        source = config.DEFAULT_PDF_DIR / "未分类" / "p1.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.add_paper("p1", "Paper One", file_path=str(source))

        categories.assign_path("p1", ["光学工程", "偏振成像"], move=False)
        paper = db.get_paper("p1")
        self.assertEqual(paper["category_major"], "光学工程")
        self.assertEqual(Path(paper["file_path"]), source)
        self.assertTrue(source.is_file())

    def test_move_files_true_moves_pdf_into_category_dir(self):
        source = config.DEFAULT_PDF_DIR / "未分类" / "p2.pdf"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"%PDF-1.4\n%%EOF\n")
        self.add_paper("p2", "Paper Two", file_path=str(source))

        categories.assign_path("p2", ["光学工程", "偏振成像"], move=True)
        paper = db.get_paper("p2")
        moved = Path(paper["file_path"])
        self.assertFalse(source.exists())
        self.assertTrue(moved.is_file())
        self.assertEqual(moved.parent.parent.name, "光学工程")


if __name__ == "__main__":
    unittest.main()
