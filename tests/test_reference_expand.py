# -*- coding: utf-8 -*-
"""参考文献外扩：OpenAlex 引用关系 → 候选列表 → 勾选入库。

用户需求原话：「参考文献外扩」。数据源固定为 OpenAlex，网络层全部用假实现，
测试不联网。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app
from paperwhale_server.services import metadata, reference_expand
from paperwhale_server.services.pdf_fetch import manager as pdf_manager

SEED_TITLE = "An Optical Frontend for a Convolutional Neural Network"


def work(work_id: str, title: str, *, year: int = 2019, cited: int = 7, doi: str = "", venue: str = "Applied Optics") -> dict:
    return {
        "id": f"https://openalex.org/{work_id}",
        "display_name": title,
        "publication_year": year,
        "cited_by_count": cited,
        "doi": f"https://doi.org/{doi}" if doi else None,
        "authorships": [{"author": {"display_name": "A. Author"}}, {"author": {"display_name": "B. Author"}}],
        "primary_location": {"source": {"display_name": venue}},
        "best_oa_location": {},
    }


SEED = work(
    "W100",
    SEED_TITLE,
    year=2019,
    cited=42,
    doi="10.1364/AO.58.003179",
)
SEED["referenced_works"] = ["https://openalex.org/W200", "https://openalex.org/W300"]
REF_A = work("W200", "First Referenced Paper", year=2015, cited=3, doi="10.1000/ref-a")
REF_B = work("W300", "Second Referenced Paper", year=2016, cited=9, doi="10.1000/ref-b")
CITER = work("W400", "A Later Paper Citing It", year=2022, cited=1, doi="10.1000/citer")


def fake_json(path: str, params: dict | None = None) -> dict:
    params = dict(params or {})
    if path.startswith("/works/doi:"):
        return SEED
    filter_value = str(params.get("filter") or "")
    if filter_value.startswith("openalex_id:"):
        return {"results": [REF_A, REF_B]}
    if filter_value.startswith("cites:"):
        return {"results": [CITER]}
    if params.get("search"):
        return {"results": [SEED]}
    raise AssertionError(f"unexpected OpenAlex call: {path} {params}")


class ReferenceExpandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-ref-")
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
                "scholar_auto_sync": False,
                "pdf_browser_session_enabled": False,
            },
        )
        self.paths.start()
        db.init_db()
        self.client = TestClient(app)
        self.paper_id = self._insert_paper(SEED_TITLE, doi="10.1364/AO.58.003179")

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def _insert_paper(self, title: str, *, doi: str = "", year: str = "2019") -> str:
        paper_id = db.new_id()
        db.execute(
            "INSERT INTO papers(id,title,doi,year,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (paper_id, title, doi, year, db.now(), db.now()),
        )
        return paper_id

    # ---------- 拉候选 ----------

    def test_expand_references_returns_candidates(self):
        with patch.object(reference_expand, "_json", side_effect=fake_json):
            result = reference_expand.expand(self.paper_id, "references", 40)
        self.assertEqual(result["direction"], "references")
        self.assertEqual([item["title"] for item in result["items"]], ["First Referenced Paper", "Second Referenced Paper"])
        self.assertIn("ref-a", result["items"][0]["doi"])
        self.assertEqual(result["seed"]["cited_by_count"], 42)

    def test_expand_marks_items_already_in_library(self):
        existing = self._insert_paper("First Referenced Paper", doi="10.1000/ref-a")
        with patch.object(reference_expand, "_json", side_effect=fake_json):
            result = reference_expand.expand(self.paper_id, "references", 40)
        self.assertEqual(result["items"][0]["existing_paper_id"], existing)
        self.assertEqual(result["items"][1]["existing_paper_id"], "")

    def test_expand_citations_direction(self):
        with patch.object(reference_expand, "_json", side_effect=fake_json):
            result = reference_expand.expand(self.paper_id, "citations", 10)
        self.assertEqual(result["direction"], "citations")
        self.assertEqual([item["title"] for item in result["items"]], ["A Later Paper Citing It"])

    def test_expand_falls_back_to_title_search_without_doi(self):
        paper_id = self._insert_paper(SEED_TITLE)
        with patch.object(reference_expand, "_json", side_effect=fake_json):
            result = reference_expand.expand(paper_id, "references", 40)
        self.assertEqual(len(result["items"]), 2)

    def test_expand_rejects_unknown_paper(self):
        with self.assertRaises(LookupError):
            reference_expand.expand("does-not-exist", "references", 5)

    def test_expand_api_maps_metadata_error(self):
        with patch.object(
            reference_expand, "_json", side_effect=metadata.MetadataError("OpenAlex 挂了", 502)
        ):
            response = self.client.post(
                "/api/references/expand", json={"paper_id": self.paper_id, "direction": "references"}
            )
        self.assertEqual(response.status_code, 502)

    def test_expand_api_success(self):
        with patch.object(reference_expand, "_json", side_effect=fake_json):
            response = self.client.post(
                "/api/references/expand", json={"paper_id": self.paper_id, "direction": "references"}
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["items"]), 2)

    # ---------- 入库 ----------

    def test_import_creates_new_and_skips_existing(self):
        self._insert_paper("First Referenced Paper", doi="10.1000/ref-a", year="2015")
        items = [
            {"title": "First Referenced Paper", "doi": "10.1000/ref-a", "year": "2015"},
            {"title": "Brand New Paper", "doi": "10.1000/new", "year": "2021"},
        ]
        with patch.object(pdf_manager, "start", return_value=1) as queue:
            result = reference_expand.import_items(items, fetch_pdf=True)
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["skipped"], 1)
        self.assertEqual(result["queued"], 1)
        queue.assert_called_once()
        self.assertTrue(db.get_paper(result["paper_ids"][0]))

    def test_import_can_skip_pdf_queue(self):
        with patch.object(pdf_manager, "start", return_value=1) as queue:
            result = reference_expand.import_items(
                [{"title": "Another Fresh Paper", "doi": "10.1000/fresh"}], fetch_pdf=False
            )
        self.assertEqual(result["created"], 1)
        self.assertEqual(result["queued"], 0)
        queue.assert_not_called()

    def test_import_keeps_going_after_one_failure(self):
        items = [
            {"title": "Bad One", "doi": "10.1000/bad"},
            {"title": "Good One", "doi": "10.1000/good"},
        ]
        with patch.object(
            reference_expand.ingest, "create_paper_from_metadata", side_effect=[RuntimeError("boom"), "pid-ok"]
        ) as create, patch.object(pdf_manager, "start", return_value=0):
            result = reference_expand.import_items(items, fetch_pdf=False)
        self.assertEqual(result["created"], 1)
        self.assertEqual(len(result["failed"]), 1)
        self.assertEqual(create.call_count, 2)

    def test_import_api_rejects_empty_selection(self):
        response = self.client.post("/api/references/import", json={"items": []})
        self.assertEqual(response.status_code, 400)

    def test_import_api_success(self):
        with patch.object(pdf_manager, "start", return_value=0):
            response = self.client.post(
                "/api/references/import",
                json={"items": [{"title": "Via API Paper", "doi": "10.1000/api"}], "fetch_pdf": True},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["created"], 1)


if __name__ == "__main__":
    unittest.main()
