# -*- coding: utf-8 -*-
"""卷期页：归一化、抓取映射（Crossref/OpenAlex/网页 meta）、五路导入解析、落库补全与批量补齐。

全部离线：网络函数只用构造 payload / mock，测试库在临时目录。
"""
from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app
from paperwhale_server.routers import papers as papers_router
from paperwhale_server.services import importers, ingest, metadata

PDF_BYTES = (Path(__file__).parent / "fixtures" / "attention.pdf").read_bytes()

CROSSREF_MESSAGE = {
    "DOI": "10.1000/example",
    "title": ["Paper One"],
    "author": [{"given": "Shane", "family": "Colburn"}, {"given": "Yi", "family": "Chu"}],
    "container-title": ["Applied Optics"],
    "volume": "12",
    "issue": "3",
    "page": "45-67",
    "issued": {"date-parts": [[2019]]},
    "URL": "https://doi.org/10.1000/example",
    "abstract": "Test abstract.",
}

OPENALEX_WORK = {
    "display_name": "Paper One",
    "publication_year": 2019,
    "doi": "https://doi.org/10.1000/example",
    "biblio": {"volume": "12", "issue": "3", "first_page": "45", "last_page": "67"},
    "primary_location": {"source": {"display_name": "Applied Optics"}},
    "authorships": [{"author": {"display_name": "Shane Colburn"}}],
}

HIGHWIRE_HTML = """<html><head>
<meta name="citation_title" content="Paper One">
<meta name="citation_author" content="Shane Colburn">
<meta name="citation_journal_title" content="Applied Optics">
<meta name="citation_publication_date" content="2019/01/01">
<meta name="citation_doi" content="10.1000/example">
<meta name="citation_volume" content="12">
<meta name="citation_issue" content="3">
<meta name="citation_firstpage" content="45">
<meta name="citation_lastpage" content="67">
</head></html>"""

PRISM_HTML = """<html><head>
<meta name="citation_title" content="Paper One">
<meta name="prism.publicationname" content="Applied Optics">
<meta name="prism.volume" content="12">
<meta name="prism.number" content="3">
<meta name="prism.startingpage" content="45">
<meta name="prism.endingpage" content="67">
<meta name="prism.doi" content="10.1000/example">
</head></html>"""


class BibliographicBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-bib-")
        self.root = Path(self.temp.name)
        self.settings = patch.multiple(
            config,
            DATA_DIR=self.root,
            DEFAULT_PDF_DIR=self.root / "pdfs",
            TMP_DIR=self.root / "tmp",
            DB_PATH=self.root / "test.db",
            SETTINGS_PATH=self.root / "settings.json",
            _cache={**config.DEFAULT_SETTINGS, "scholar_auto_sync": False},
        )
        self.settings.start()
        self.env = patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "1"})
        self.env.start()
        db.init_db()
        (self.root / "pdfs").mkdir(parents=True, exist_ok=True)
        self.files = self.root / "imports"
        self.files.mkdir(parents=True, exist_ok=True)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def write(self, name: str, text: str) -> Path:
        path = self.files / name
        path.write_text(text, encoding="utf-8")
        return path

    def wait(self, predicate, timeout=20):
        end = time.time() + timeout
        while time.time() < end:
            if predicate():
                return True
            time.sleep(0.05)
        return False


class NormalizeTests(BibliographicBase):
    def test_normalize_pages_variants(self):
        cases = {
            "45-67": "45–67",
            "45–67": "45–67",
            "45—67": "45–67",
            "45 - 67": "45–67",
            "pp. 45-67": "45–67",
            "pp. 45–67": "45–67",
            "pages 45-67": "45–67",
            "45--67": "45–67",
            "e12345": "e12345",
            " 45–67 ": "45–67",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(metadata.normalize_pages(raw), expected)

    def test_normalize_volume_variants(self):
        cases = {
            "Vol. 12": "12",
            "volume 12": "12",
            "12": "12",
            "第12卷": "12",
            " Vol. 12 ": "12",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(metadata.normalize_volume(raw), expected)

    def test_normalize_issue_variants(self):
        cases = {
            "(3)": "3",
            "（3）": "3",
            "(3) ": "3",
            "No. 3": "3",
            "issue 3": "3",
            "第3期": "3",
            "3-4": "3-4",
            "": "",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(metadata.normalize_issue(raw), expected)

    def test_clean_meta_normalizes_and_drops_blank(self):
        cleaned = metadata.clean_meta(
            {"title": "Paper", "volume": "Vol. 12", "issue": "(3)", "pages": "pp. 45-67"}
        )
        self.assertEqual(
            {key: cleaned.get(key) for key in ("volume", "issue", "pages")},
            {"volume": "12", "issue": "3", "pages": "45–67"},
        )
        dropped = metadata.clean_meta({"title": "Paper", "volume": "Vol.", "pages": "pp."})
        self.assertNotIn("volume", dropped)
        self.assertNotIn("pages", dropped)


class MappingTests(BibliographicBase):
    def test_crossref_message_maps_volume_issue_pages(self):
        meta = metadata._crossref_message_to_meta(CROSSREF_MESSAGE)
        self.assertEqual(meta["volume"], "12")
        self.assertEqual(meta["issue"], "3")
        self.assertEqual(meta["pages"], "45–67")

    def test_openalex_maps_biblio_fields(self):
        meta = metadata._openalex_work_to_meta(OPENALEX_WORK)
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))
        only_first = metadata._openalex_work_to_meta(
            {**OPENALEX_WORK, "biblio": {"volume": "12", "first_page": "45", "last_page": "45"}}
        )
        self.assertEqual(only_first["pages"], "45")

    def test_html_highwire_volume_issue_pages(self):
        meta = metadata.metadata_from_html(HIGHWIRE_HTML, "https://example.org/p1")
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))

    def test_html_prism_volume_issue_pages(self):
        meta = metadata.metadata_from_html(PRISM_HTML, "https://example.org/p1")
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))


class ImportParserTests(BibliographicBase):
    def test_endnote_xml(self):
        path = self.write(
            "endnote.xml",
            """<xml><records><record>
            <titles><title>Paper One</title><secondary-title>Applied Optics</secondary-title></titles>
            <dates><year>2019</year></dates>
            <volume>12</volume><number>3</number><pages>45-67</pages>
            <electronic-resource-num>10.1000/example</electronic-resource-num>
            </record></records></xml>""",
        )
        meta = importers.parse_endnote_xml(path)[0]["meta"]
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))

    def test_ris(self):
        path = self.write(
            "a.ris",
            "TY  - JOUR\nTI  - Paper One\nJO  - Applied Optics\nPY  - 2019\n"
            "VL  - 12\nIS  - 3\nSP  - 45\nEP  - 67\nDO  - 10.1000/example\nER  - \n",
        )
        meta = importers.parse_ris(path)[0]["meta"]
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))

    def test_ris_only_start_page(self):
        path = self.write(
            "only-sp.ris", "TY  - JOUR\nTI  - Paper One\nVL  - 12\nSP  - 45\nER  - \n"
        )
        meta = importers.parse_ris(path)[0]["meta"]
        self.assertEqual(meta["pages"], "45")

    def test_bibtex(self):
        path = self.write(
            "a.bib",
            "@article{key, title={Paper One}, journal={Applied Optics}, year={2019},"
            " volume={12}, number={3}, pages={45--67}, doi={10.1000/example}}",
        )
        meta = importers.parse_bibtex(path)[0]["meta"]
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))

    def test_zotero_csv(self):
        path = self.write(
            "a.csv",
            "Title,Publication Title,Publication Year,Volume,Issue,Pages,DOI\n"
            "Paper One,Applied Optics,2019,12,3,45-67,10.1000/example\n",
        )
        meta = importers.parse_zotero_csv(path)[0]["meta"]
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))

    def test_zotero_csv_lowercase_headers(self):
        path = self.write(
            "b.csv",
            "title,publication title,publication year,volume,issue,pages\n"
            "Paper One,Applied Optics,2019,12,3,45-67\n",
        )
        meta = importers.parse_zotero_csv(path)[0]["meta"]
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))

    def test_zotero_rdf(self):
        root = ET.fromstring(
            """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                xmlns:dc="http://purl.org/dc/elements/1.1/"
                xmlns:dcterms="http://purl.org/dc/terms/"
                xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/">
              <rdf:Description rdf:about="#item_1">
                <dc:title>Paper One</dc:title>
                <dc:date>2019</dc:date>
                <prism:publicationName>Applied Optics</prism:publicationName>
                <prism:volume>12</prism:volume>
                <prism:number>3</prism:number>
                <prism:startingPage>45</prism:startingPage>
                <prism:endingPage>67</prism:endingPage>
                <prism:doi>10.1000/example</prism:doi>
              </rdf:Description>
            </rdf:RDF>"""
        )
        item = root.find("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
        meta = importers._rdf_item_meta(item)
        self.assertEqual((meta["volume"], meta["issue"], meta["pages"]), ("12", "3", "45–67"))

    def test_zotero_rdf_page_range_wins(self):
        root = ET.fromstring(
            """<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
                xmlns:dc="http://purl.org/dc/elements/1.1/"
                xmlns:prism="http://prismstandard.org/namespaces/1.2/basic/">
              <rdf:Description rdf:about="#item_1">
                <dc:title>Paper One</dc:title>
                <prism:pageRange>45-67</prism:pageRange>
              </rdf:Description>
            </rdf:RDF>"""
        )
        item = root.find("{http://www.w3.org/1999/02/22-rdf-syntax-ns#}Description")
        self.assertEqual(importers._rdf_item_meta(item)["pages"], "45–67")


class PersistAndBackfillTests(BibliographicBase):
    def test_create_paper_from_metadata_persists_fields(self):
        meta = metadata._crossref_message_to_meta(CROSSREF_MESSAGE)
        paper_id = ingest.create_paper_from_metadata(meta)
        paper = db.get_paper(paper_id)
        self.assertEqual((paper["volume"], paper["issue"], paper["pages"]), ("12", "3", "45–67"))

    def test_enrich_existing_only_fills_empty_fields(self):
        meta = metadata._crossref_message_to_meta(CROSSREF_MESSAGE)
        paper_id = ingest.create_paper_from_metadata(meta)
        db.update_paper(paper_id, {"volume": "99"})
        ingest.create_paper_from_metadata(dict(meta, issue="4", pages="1-9"))
        paper = db.get_paper(paper_id)
        self.assertEqual(paper["volume"], "99", "已有卷号不能被覆盖")
        self.assertEqual(paper["issue"], "3")
        self.assertEqual(paper["pages"], "45–67")
        self.assertEqual(db.query_one("SELECT COUNT(*) AS c FROM papers")["c"], 1)

    def test_create_paper_from_pdf_merges_crossref_volume_pages(self):
        pdf = self.root / "pdfs" / "p.pdf"
        pdf.write_bytes(PDF_BYTES)
        meta = {
            "title": "Paper One",
            "authors": "Shane Colburn; Yi Chu",
            "abstract": "abs",
            "doi": "10.1000/example",
        }
        crossref = {"volume": "12", "issue": "3", "pages": "45–67"}
        with patch.object(metadata, "extract_pdf_metadata", return_value={}), patch.object(
            metadata, "enrich_from_doi", return_value=crossref
        ):
            paper_id = ingest.create_paper_from_pdf(pdf, meta=meta, source="upload")
        paper = db.get_paper(paper_id)
        self.assertEqual((paper["volume"], paper["issue"], paper["pages"]), ("12", "3", "45–67"))

    def test_patch_paper_updates_bibliographic_fields(self):
        db.execute("INSERT INTO papers(id,title) VALUES(?,?)", ("p1", "Paper"))
        response = self.client.patch(
            "/api/papers/p1", json={"volume": "20", "issue": "4", "pages": "9–10"}
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["volume"], body["issue"], body["pages"]), ("20", "4", "9–10"))
        paper = db.get_paper("p1")
        self.assertEqual((paper["volume"], paper["issue"], paper["pages"]), ("20", "4", "9–10"))

    def test_lookup_fills_only_empty_volume_and_pages(self):
        db.execute(
            "INSERT INTO papers(id,title,doi,volume,pages) VALUES(?,?,?,?,?)",
            ("p1", "Paper One", "10.1000/example", "5", ""),
        )
        found = {"volume": "12", "issue": "3", "pages": "45–67"}
        from paperwhale_server.services import pdf_fetch

        with patch.object(metadata, "enrich_from_doi", return_value=found), patch.object(
            pdf_fetch.manager, "start", return_value=1
        ):
            response = self.client.post("/api/papers/p1/lookup")
        self.assertEqual(response.status_code, 200, response.text)
        paper = db.get_paper("p1")
        self.assertEqual(paper["volume"], "5", "手工卷号不能被覆盖")
        self.assertEqual(paper["issue"], "3")
        self.assertEqual(paper["pages"], "45–67")

    def test_backfill_fills_missing_values_and_reports_failures(self):
        db.execute(
            "INSERT INTO papers(id,title,doi,volume,pages) VALUES(?,?,?,?,?)",
            ("b1", "B1", "10.9000/b1", "", ""),
        )
        db.execute(
            "INSERT INTO papers(id,title,doi,volume,pages) VALUES(?,?,?,?,?)",
            ("b2", "B2", "10.9000/b2", "5", "1-9"),
        )
        db.execute(
            "INSERT INTO papers(id,title,doi,volume,pages) VALUES(?,?,?,?,?)",
            ("b3", "B3", "10.9000/b3", "", ""),
        )

        def fake_crossref(doi):
            if doi.endswith("b3"):
                raise RuntimeError("boom")
            return {"volume": "12", "issue": "3", "pages": "45–67"}

        with patch.object(metadata, "crossref_by_doi", side_effect=fake_crossref):
            started = self.client.post(
                "/api/papers/backfill-bibliographic", json={"limit": 10}
            ).json()
            self.assertTrue(started["started"], started)
            self.assertEqual(started["total"], 2, "只处理有 DOI 且卷/页缺失的论文")
            self.assertTrue(
                self.wait(
                    lambda: not self.client.get(
                        "/api/papers/backfill-bibliographic/status"
                    ).json()["running"]
                )
            )
        status = self.client.get("/api/papers/backfill-bibliographic/status").json()
        self.assertFalse(status["running"])
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["done"], 2)
        self.assertEqual(status["updated"], 1)
        self.assertEqual(len(status["failed"]), 1)
        self.assertEqual(status["failed"][0]["paper_id"], "b3")
        self.assertIn("boom", status["failed"][0]["error"])
        self.assertTrue(status["finished_at"])
        self.assertEqual(
            {key: db.get_paper("b1")[key] for key in ("volume", "issue", "pages")},
            {"volume": "12", "issue": "3", "pages": "45–67"},
        )
        self.assertEqual(
            {key: db.get_paper("b2")[key] for key in ("volume", "pages")},
            {"volume": "5", "pages": "1-9"},
            "已有卷/页的论文不在补齐范围",
        )

    def test_backfill_reports_running_and_no_candidates(self):
        empty = self.client.post("/api/papers/backfill-bibliographic", json={"limit": 10}).json()
        self.assertEqual(empty, {"started": False, "total": 0})
        with patch.dict(papers_router._backfill_state, {"running": True, "total": 7}):
            busy = self.client.post(
                "/api/papers/backfill-bibliographic", json={"limit": 10}
            ).json()
            self.assertEqual(busy, {"started": False, "total": 7})
            status = self.client.get("/api/papers/backfill-bibliographic/status").json()
            self.assertTrue(status["running"])
            self.assertEqual(status["total"], 7)


if __name__ == "__main__":
    unittest.main()
