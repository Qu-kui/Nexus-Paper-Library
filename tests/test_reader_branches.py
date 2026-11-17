"""阅读器对话分支：从某一轮分出新线索，主对话不受影响。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db
from paperwhale_server.services import conversations


class BranchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-branch-")
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
        db.execute("INSERT INTO papers (id,title,created_at,updated_at) VALUES ('p','Paper',?,?)",
                   (db.now(), db.now()))
        for question in ("第一个问题", "第二个问题"):
            conversations.turn("paper:p", question, lambda history, materials: {
                "answer": "回答 [1]", "sources": [], "reasoning": "", "thinking_seconds": 0.1,
            })

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_main_conversation_listed(self):
        items = conversations.branches("p")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["branch"], "")
        self.assertEqual(items[0]["messages"], 4)
        self.assertEqual(items[0]["title"], "第一个问题")
        self.assertEqual(items[0]["preview"], "回答 [1]")
        self.assertEqual(items[0]["parent_branch"], "")

    def test_create_branch_starts_empty_and_remembers_parent(self):
        snapshot = conversations.create_branch("p", "", 1)
        self.assertTrue(snapshot["scope"].startswith("paper:p~"))
        self.assertEqual(snapshot["messages"], [], "新分支应是干净的新对话，不复刻历史")
        parent = conversations.branch_parent(snapshot["scope"])
        self.assertEqual(parent["scope"], "paper:p")
        self.assertEqual(parent["fork_index"], 1)
        items = conversations.branches("p")
        self.assertEqual(len(items), 2)
        child = [row for row in items if row["branch"]][0]
        self.assertEqual(child["parent_branch"], "", "父对话是主对话时 parent_branch 为空")
        self.assertEqual(child["fork_index"], 1)

    def test_branch_continues_independently(self):
        created = conversations.create_branch("p", "", 1)
        branch_scope = created["scope"]
        conversations.turn(branch_scope, "分支里的问题", lambda history, materials: {
            "answer": "分支回答", "sources": [], "reasoning": "",
        })
        branch = conversations.snapshot(branch_scope)
        main = conversations.snapshot("paper:p")
        self.assertEqual(len(branch["messages"]), 2)
        self.assertEqual(len(main["messages"]), 4, "主对话不应被分支影响")
        self.assertEqual(branch["messages"][-1]["content"], "分支回答")

    def test_branch_from_branch(self):
        first = conversations.create_branch("p", "", 3)
        first_branch = conversations.split_scope(first["scope"])[1]
        second = conversations.create_branch("p", first_branch, 1)
        self.assertEqual(second["messages"], [])
        child = [row for row in conversations.branches("p") if row["branch"] == conversations.split_scope(second["scope"])[1]][0]
        self.assertEqual(child["parent_branch"], first_branch)
        self.assertEqual(conversations.branch_parent(second["scope"])["scope"], first["scope"])
        self.assertEqual(len(conversations.branches("p")), 3)

    def test_parent_digest_only_uses_messages_before_fork(self):
        branch = conversations.create_branch("p", "", 1)
        digest = conversations.parent_digest(branch["scope"])
        self.assertEqual([row["role"] for row in digest], ["user", "assistant"])
        self.assertEqual(digest[0]["content"], "第一个问题")

    def test_parent_meta_survives_turns(self):
        created = conversations.create_branch("p", "", 1)
        scope = created["scope"]
        conversations.turn(scope, "分支里第一问", lambda history, materials: {
            "answer": "分支回答", "sources": [], "reasoning": "",
        })
        parent = conversations.branch_parent(scope)
        self.assertEqual(parent["scope"], "paper:p", "每轮问答不能把分支的父对话记录冲掉")
        self.assertTrue(conversations.parent_digest(scope))

    def test_parent_digest_empty_for_main(self):
        self.assertEqual(conversations.parent_digest("paper:p"), [])

    def test_parent_context_reaches_paper_prompt(self):
        from paperwhale_server.services import rag

        captured = {}

        def fake_chat(messages, **kwargs):
            captured["messages"] = messages
            return {"content": "分支回答", "reasoning": "", "model": "deepseek-flash",
                    "citations": [], "server_tool_calls": [], "web_trace": []}

        paper = {"id": "p", "title": "Paper", "abstract": "摘要", "file_path": ""}
        with patch.object(rag.db, "get_paper", return_value=paper), \
                patch.object(rag, "_paper_fulltext_rows", return_value=[]), \
                patch.object(rag, "_search_fts", return_value=[]), \
                patch.object(rag, "_search_like", return_value=[]), \
                patch.object(rag.llm, "chat", side_effect=fake_chat):
            rag.chat("为什么这样", paper_id="p", parent_context=[
                {"role": "user", "content": "之前问过的问题"},
                {"role": "assistant", "content": "之前的回答"},
            ])
        prompt = captured["messages"][-1]["content"]
        self.assertIn("父对话上下文", prompt)
        self.assertIn("之前问过的问题", prompt)
        self.assertIn("之前的回答", prompt)

    def test_delete_branch(self):
        created = conversations.create_branch("p", "", 1)
        branch = conversations.split_scope(created["scope"])[1]
        conversations.delete_branch("p", branch)
        self.assertEqual(len(conversations.branches("p")), 1)
        with self.assertRaises(conversations.ConversationError):
            conversations.delete_branch("p", branch)

    def test_cannot_delete_main(self):
        with self.assertRaises(conversations.ConversationError):
            conversations.delete_branch("p", "")

    def test_bad_branch_identifier_rejected(self):
        with self.assertRaises(conversations.ConversationError):
            conversations.validate_scope("paper:p~bad/name")


class BranchApiTests(BranchTests):
    def _client(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app
        return TestClient(app)

    def test_branch_api_roundtrip(self):
        client = self._client()
        listed = client.get("/api/ai/branches/p")
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(len(listed.json()["items"]), 1)

        created = client.post("/api/ai/branches", json={"paper_id": "p", "from_branch": "", "upto_index": 1})
        self.assertEqual(created.status_code, 200)
        branch = created.json()["branch"]
        self.assertTrue(branch)
        self.assertEqual(created.json()["conversation"]["messages"], [])

        listed = client.get("/api/ai/branches/p").json()["items"]
        self.assertEqual(len(listed), 2)

        deleted = client.request("DELETE", "/api/ai/branches",
                                 json={"paper_id": "p", "branch": branch})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(len(client.get("/api/ai/branches/p").json()["items"]), 1)

    def test_chat_with_branch_writes_to_branch_scope(self):
        from paperwhale_server.routers import ai as ai_router

        client = self._client()
        branch_scope = conversations.create_branch("p", "", 1)["scope"]
        branch = conversations.split_scope(branch_scope)[1]
        conv = client.get(f"/api/ai/conversations/{branch_scope.replace(':', '%3A')}").json()
        with patch.object(ai_router.rag, "chat", return_value={
            "answer": "分支回答 [1]", "sources": [], "reasoning": "", "thinking_seconds": 0.2,
        }):
            response = client.post("/api/ai/chat", json={
                "question": "分支里再问一句", "paper_id": "p", "branch": branch,
                "conversation_id": conv["id"], "revision": conv["revision"],
            })
        self.assertEqual(response.status_code, 200, response.text)
        stored = conversations.snapshot(branch_scope)
        self.assertEqual(stored["messages"][-1]["content"], "分支回答 [1]")
        self.assertEqual(len(conversations.snapshot("paper:p")["messages"]), 4)


if __name__ == "__main__":
    unittest.main()
