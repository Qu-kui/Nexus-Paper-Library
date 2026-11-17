"""Import regressions on temporary databases; never use personal settings or files."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import ingest, metadata


class IngestAtomicTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-ingest-atomic-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        settings = patch.multiple(
            config, DATA_DIR=self.root, DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp", DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False,
                    "pdf_browser_session_enabled": False},
        )
        settings.start()
        self.addCleanup(settings.stop)
        db.init_db()

    def test_reimport_restores_missing_pdf_and_chunks(self):
        incoming = self.root / "incoming.pdf"
        incoming.write_bytes((Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes())
        meta = {"title": "Attention Is All You Need", "authors": "Authors", "abstract": "Abstract"}
        paper_id = ingest.create_paper_from_metadata(meta, pdf_path=incoming)
        archived = Path(db.get_paper(paper_id)["file_path"])
        archived.unlink()
        self.assertFalse(db.get_paper(paper_id)["has_pdf"])
        with patch.object(metadata, "extract_pdf_metadata", return_value=meta), patch.object(
            ingest, "_enrich_from_doi"
        ), patch.object(ingest, "_enrich_from_arxiv"):
            result = ingest.import_pdf_file(incoming)
        self.assertEqual(result["id"], paper_id)
        self.assertEqual(result["action"], "matched")
        self.assertTrue(db.get_paper(paper_id)["has_pdf"])
        self.assertTrue(archived.is_file())
        self.assertGreater(db.query_one("SELECT COUNT(*) AS n FROM chunks WHERE paper_id=?", (paper_id,))["n"], 0)
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM papers")["n"], 1)

    def concurrent_imports(self, first, second):
        start = threading.Barrier(2)
        looked_up = threading.Barrier(2)
        original = db.find_duplicate

        def synchronized_lookup(*args, **kwargs):
            result = original(*args, **kwargs)
            # Force the former independent read transactions to see the same snapshot.
            # A caller holding an SQLite write transaction already prevents this race.
            conn = kwargs.get("conn")
            if conn is None or not conn.in_transaction:
                looked_up.wait(timeout=5)
            return result

        def run(meta):
            start.wait(timeout=5)
            return ingest.create_paper_from_metadata(meta, strict_identifiers=True)

        with patch.object(db, "find_duplicate", side_effect=synchronized_lookup):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(run, meta) for meta in (first, second)]
                ids = [future.result(timeout=10) for future in futures]
        self.assertEqual(ids[0], ids[1])
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM papers")["n"], 1)

    def test_concurrent_normalized_doi_imports_share_one_record(self):
        self.concurrent_imports(
            {"title": "First title", "doi": "https://doi.org/10.1000/WHale.pdf"},
            {"title": "Different title", "doi": "10.1000/whale"},
        )

    def test_concurrent_normalized_arxiv_imports_share_one_record(self):
        self.concurrent_imports(
            {"title": "First preprint", "arxiv_id": "https://arxiv.org/abs/1706.03762v7"},
            {"title": "Revised preprint", "arxiv_id": "arXiv:1706.03762v1"},
        )

    def test_concurrent_identical_doi_imports_share_one_record(self):
        self.concurrent_imports(
            {"title": "Title one", "doi": "10.1000/exact"},
            {"title": "Title two", "doi": "10.1000/exact"},
        )

    def test_distinct_identifiers_with_same_title_remain_separate(self):
        first = ingest.create_paper_from_metadata({"title": "Same title", "doi": "10.1000/first"})
        second = ingest.create_paper_from_metadata({"title": "Same title", "doi": "10.1000/second"})
        self.assertNotEqual(first, second)
        third = ingest.create_paper_from_metadata({"title": "Same preprint title", "arxiv_id": "1706.03762v1"})
        fourth = ingest.create_paper_from_metadata({"title": "Same preprint title", "arxiv_id": "1706.03763v1"})
        self.assertNotEqual(third, fourth)

    def test_failed_insert_rolls_back_and_releases_writer(self):
        original = ingest._insert_paper

        def fail_after_insert(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("Interrupted import")

        with patch.object(ingest, "_insert_paper", side_effect=fail_after_insert):
            with self.assertRaisesRegex(RuntimeError, "Interrupted import"):
                ingest.create_paper_from_metadata({"title": "Failed paper", "doi": "10.1000/fail"})
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM papers")["n"], 0)
        paper_id = ingest.create_paper_from_metadata({"title": "Retry", "doi": "10.1000/fail"})
        self.assertIsNotNone(db.get_paper(paper_id))

    def test_identifier_enrichment_commits_with_duplicate_decision(self):
        paper_id = ingest.create_paper_from_metadata({"title": "Existing paper"})
        original = ingest._enrich_existing
        first_reached = threading.Event()
        second_finished = threading.Event()

        def pause_non_atomic_enrichment(pid, data, pdf_path, **kwargs):
            if data.get("doi"):
                first_reached.set()
                # Previously the row remained unidentified until after the lookup
                # transaction ended, so a competing importer could insert it.
                if kwargs.get("conn") is None:
                    if not second_finished.wait(timeout=5):
                        raise AssertionError("Competing import did not finish")
            return original(pid, data, pdf_path, **kwargs)

        def first():
            return ingest.create_paper_from_metadata({"title": "Existing paper", "doi": "10.1000/enriched"})

        def second():
            if not first_reached.wait(timeout=5):
                raise AssertionError("First import did not reach enrichment")
            try:
                return ingest.create_paper_from_metadata({"title": "Other title", "doi": "10.1000/enriched"})
            finally:
                second_finished.set()

        with patch.object(ingest, "_enrich_existing", side_effect=pause_non_atomic_enrichment):
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = [pool.submit(first), pool.submit(second)]
                self.assertEqual([f.result(timeout=10) for f in futures], [paper_id, paper_id])
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM papers")["n"], 1)

    def test_default_sort_uses_safe_nonunique_index(self):
        plan = db.query("EXPLAIN QUERY PLAN SELECT * FROM papers ORDER BY updated_at DESC, created_at DESC, id ASC LIMIT 50")
        self.assertTrue(any("idx_papers_updated_at" in row["detail"] for row in plan))
        self.assertFalse(any("TEMP B-TREE" in row["detail"] for row in plan))

    def test_historical_duplicates_are_preserved_and_reused(self):
        for paper_id, doi in (("old1", "10.1000/Whale"), ("old2", "https://doi.org/10.1000/whale")):
            db.execute("INSERT INTO papers(id,title,doi) VALUES(?,?,?)", (paper_id, paper_id, doi))
        db.init_db()
        result = ingest.create_paper_from_metadata({"title": "Third title", "doi": "DOI: 10.1000/WHALE.pdf"})
        self.assertIn(result, {"old1", "old2"})
        self.assertEqual(db.query_one("SELECT COUNT(*) AS n FROM papers")["n"], 2)


if __name__ == "__main__":
    unittest.main()
