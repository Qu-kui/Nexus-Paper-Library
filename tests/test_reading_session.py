"""阅读会话：本次阅读期间共用一个对话，退出阅读器即删除。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db
from paperwhale_server.services import conversations


class ReadingScopeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-reading-")
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
        for pid in ("p1", "p2"):
            db.execute("INSERT INTO papers (id,title,created_at,updated_at) VALUES (?,?,?,?)",
                       (pid, f"Paper {pid}", db.now(), db.now()))

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_reading_scope_and_branch(self):
        scope = conversations.reading_scope("sessAB12")
        self.assertEqual(scope, "reading:sessAB12")
        self.assertEqual(conversations.split_scope("reading:sessAB12~b1")[0], "reading:sessAB12")
        built = conversations.create_branch_for_scope(scope, "", 0)
        self.assertTrue(built["scope"].startswith("reading:sessAB12~"))
        items = conversations.branches_for_scope(scope)
        self.assertEqual(len(items), 1)
        conversations.validate_scope(built["scope"])

    def test_turn_writes_into_reading_scope_and_purge_removes_it(self):
        scope = conversations.reading_scope("sessCD34")
        branch = conversations.create_branch_for_scope(scope, "", 0)
        for target in (scope, branch["scope"]):
            conversations.turn(target, "问题", lambda history, materials: {
                "answer": "回答", "sources": [], "reasoning": "",
            })
        self.assertEqual(len(conversations.snapshot(scope)["messages"]), 2)
        self.assertEqual(len(conversations.snapshot(branch["scope"])["messages"]), 2)
        removed = conversations.purge_scope(scope)
        self.assertEqual(removed["removed"], 2)
        scopes = db.query("SELECT scope FROM ai_conversations WHERE scope LIKE 'reading:sessCD34%'")
        self.assertEqual(scopes, [])
        self.assertEqual(db.query("SELECT * FROM ai_branch_meta WHERE scope LIKE 'reading:sessCD34%'"), [])

    def test_stale_reading_sessions_purged(self):
        conversations.turn(conversations.reading_scope("sessEF56"), "旧会话", lambda h, m: {
            "answer": "回答", "sources": [], "reasoning": "",
        })
        self.assertTrue(db.query("SELECT scope FROM ai_conversations WHERE scope LIKE 'reading:%'"))
        conversations.purge_stale_reading_sessions()
        self.assertEqual(db.query("SELECT scope FROM ai_conversations WHERE scope LIKE 'reading:%'"), [])

    def test_invalid_reading_id_rejected(self):
        with self.assertRaises(conversations.ConversationError):
            conversations.reading_scope("bad id!")


class ReadingApiTests(ReadingScopeTests):
    def _client(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app
        return TestClient(app)

    def test_chat_uses_reading_scope_and_session_can_be_closed(self):
        from paperwhale_server.routers import ai as ai_router

        client = self._client()
        scope = "reading:sessGH78"
        with patch.object(ai_router.rag, "chat", return_value={
            "answer": "回答 [1]", "sources": [], "reasoning": "", "thinking_seconds": 0.1,
        }):
            response = client.post("/api/ai/chat", json={
                "question": "这篇讲什么", "paper_id": "p1", "reading_session": "sessGH78",
            })
        self.assertEqual(response.status_code, 200, response.text)
        stored = conversations.snapshot(scope)
        self.assertEqual(len(stored["messages"]), 2)
        # 论文自己的历史对话不受影响
        self.assertEqual(db.query("SELECT scope FROM ai_conversations WHERE scope='paper:p1'"), [])

        closed = client.request("DELETE", "/api/ai/reading/session", json={"scope": scope})
        self.assertEqual(closed.status_code, 200)
        self.assertEqual(db.query("SELECT scope FROM ai_conversations WHERE scope LIKE 'reading:sessGH78%'"), [])


    def test_archive_and_restore_reading_session(self):
        scope = conversations.reading_scope("sessKL12")
        conversations.turn(scope, "问题", lambda history, materials: {
            "answer": "回答", "sources": [], "reasoning": "",
        })
        branch = conversations.create_branch_for_scope(scope, "", 0)
        conversations.turn(branch["scope"], "分支问题", lambda history, materials: {
            "answer": "分支回答", "sources": [], "reasoning": "",
        })
        archived = conversations.archive_session(scope, "p1")
        self.assertEqual(archived["saved"], 4)
        paper = conversations.snapshot("paper:p1")
        self.assertEqual(len(paper["messages"]), 4)
        self.assertEqual(db.query("SELECT scope FROM ai_conversations WHERE scope LIKE 'reading:sessKL12%'"), [])

        new_scope = conversations.reading_scope("sessKL34")
        restored = conversations.restore_archive(new_scope, "p1")
        self.assertEqual(len(restored["messages"]), 4)
        self.assertEqual(restored["messages"][0]["content"], "问题")

    def test_archive_api(self):
        client = self._client()
        scope = "reading:sessMN56"
        conversations.turn(scope, "问题", lambda history, materials: {
            "answer": "回答", "sources": [], "reasoning": "",
        })
        archived = client.post(f"/api/ai/conversations/{scope}/archive", json={"paper_id": "p1"})
        self.assertEqual(archived.status_code, 200, archived.text)
        self.assertEqual(archived.json()["saved"], 2)
        restored = client.post("/api/ai/conversations/reading:sessOP78/restore", json={"paper_id": "p1"})
        self.assertEqual(restored.status_code, 200, restored.text)
        self.assertEqual(len(restored.json()["conversation"]["messages"]), 2)


    def test_archive_twice_does_not_duplicate(self):
        scope = conversations.reading_scope("sessQR90")
        conversations.turn(scope, "问题", lambda history, materials: {
            "answer": "回答", "sources": [], "reasoning": "",
        })
        first = conversations.archive_session(scope, "p1")
        self.assertEqual(first["saved"], 2)
        again_scope = conversations.reading_scope("sessQR91")
        conversations.turn(again_scope, "问题", lambda history, materials: {
            "answer": "回答", "sources": [], "reasoning": "",
        })
        second = conversations.archive_session(again_scope, "p1")
        self.assertEqual(second["saved"], 0, "相同内容不重复存")
        self.assertEqual(len(conversations.snapshot("paper:p1")["messages"]), 2)


    def test_purge_refuses_non_reading_scope(self):
        conversations.snapshot("paper:p1")
        with self.assertRaises(conversations.ConversationError):
            conversations.purge_scope("paper:p1")
        self.assertEqual(len(conversations.snapshot("paper:p1")["messages"]), 0)
        self.assertTrue(db.query_one("SELECT id FROM papers WHERE id='p1'"))

    def test_archive_keeps_same_question_with_different_answer(self):
        scope = conversations.reading_scope("sessUV12")
        conversations.turn(scope, "同一个问题", lambda history, materials: {
            "answer": "第一版回答", "sources": [], "reasoning": "",
        })
        conversations.archive_session(scope, "p1")
        again = conversations.reading_scope("sessUV34")
        conversations.turn(again, "同一个问题", lambda history, materials: {
            "answer": "第二版回答", "sources": [], "reasoning": "",
        })
        result = conversations.archive_session(again, "p1")
        self.assertEqual(result["saved"], 2, "同样的问题不同答案要完整保留")
        contents = [row["content"] for row in conversations.snapshot("paper:p1")["messages"]]
        self.assertIn("第一版回答", contents)
        self.assertIn("第二版回答", contents)

    def test_reading_branches_api(self):
        client = self._client()
        scope = "reading:sessIJ90"
        created = client.post("/api/ai/reading/branches", json={"scope": scope, "from_branch": "", "upto_index": 0})
        self.assertEqual(created.status_code, 200, created.text)
        branch = created.json()["branch"]
        listed = client.get("/api/ai/reading/branches", params={"scope": scope})
        self.assertEqual(len(listed.json()["items"]), 1)
        deleted = client.request("DELETE", "/api/ai/reading/branches", json={"scope": scope, "branch": branch})
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(client.get("/api/ai/reading/branches", params={"scope": scope}).json()["items"], [])


if __name__ == "__main__":
    unittest.main()
