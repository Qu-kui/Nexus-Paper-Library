# -*- coding: utf-8 -*-
"""自定义引文格式 API：列表/纯文本/详情/预览/AI 编译/CSL 导入/删除/分发。

隔离方式照旧：patch config 的数据路径，临时 SQLite，LLM 走 mock。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from paperwhale_server import config, db
from paperwhale_server.main import app

CSL_IEEE_LIKE = """<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" default-locale="en-US">
  <info><title>IEEE-like</title></info>
  <citation><layout><text variable="citation-number" prefix="[" suffix="]"/></layout></citation>
  <bibliography><layout>
    <names variable="author"><name name-as-sort-order="all" initialize-with=". " et-al-min="6" et-al-use-first="3"/></names>
    <date variable="issued"><date-part name="year"/></date>
    <text variable="title" prefix="&quot;" suffix="&quot;"/>
    <text variable="container-title" font-style="italic"/>
    <text variable="DOI" prefix="https://doi.org/"/>
  </layout></bibliography>
</style>"""

CSL_APA_LIKE = """<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" default-locale="en-US">
  <info><title>APA-like</title></info>
  <citation><layout><names variable="author"/><date variable="issued"/></layout></citation>
  <bibliography><layout>
    <names variable="author"><name initialize-with=". "/></names>
    <date variable="issued"/>
    <text variable="title"/>
    <text variable="container-title"/>
  </layout></bibliography>
</style>"""


class CitationStyleApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-cstyle-api-")
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
        self.add_paper("p1", "Paper One", "Shane Colburn; Yi Chu", "2019", "Applied Optics", "10.1/a")
        self.add_paper("p2", "Paper Two", "Sharma", "2020", "Nature", "10.1/b")
        self.add_paper("p3", "Paper Three", "Zhu", "2021", "Optics Express", "")
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def add_paper(self, paper_id, title, authors, year, venue, doi):
        db.execute(
            "INSERT INTO papers(id,title,authors,year,venue,doi) VALUES(?,?,?,?,?,?)",
            (paper_id, title, authors, year, venue, doi),
        )

    def create_ai_style(self, description="只保留前三个作者，多了就写 et al.", name="我的格式"):
        response = self.client.post(
            "/api/cite/styles/ai", json={"description": description, "name": name}
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def create_import_style(self, csl=CSL_IEEE_LIKE, name="导入格式"):
        response = self.client.post("/api/cite/styles/import", json={"csl": csl, "name": name})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    # ------------------------------------------------------------ 列表
    def test_styles_list_has_builtin_and_custom_with_numeric_flags(self):
        created = self.create_ai_style()
        items = self.client.get("/api/cite/styles").json()["items"]
        builtin = [item for item in items if item["builtin"]]
        self.assertEqual([item["id"] for item in builtin], ["apa", "mla", "chicago", "ieee", "nature", "gbt7714"])
        flags = {item["id"]: item["numeric"] for item in builtin}
        self.assertEqual(flags, {"apa": False, "mla": False, "chicago": False, "ieee": True, "nature": True, "gbt7714": True})
        custom = next(item for item in items if item["id"] == created["style"]["id"])
        self.assertFalse(custom["builtin"])
        self.assertFalse(custom["numeric"])
        self.assertTrue({"id", "name", "builtin", "numeric"} <= set(custom))

    def test_styles_txt_is_id_name_and_numeric_flag_per_line(self):
        created = self.create_ai_style(name="AI格式")
        response = self.client.get("/api/cite/styles.txt")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["content-type"].startswith("text/plain"))
        lines = response.text.split("\n")
        self.assertEqual(lines[0], "apa\tAPA 7th\t0")
        self.assertEqual(lines[3], "ieee\tIEEE\t1")
        parsed = [line.split("\t") for line in lines]
        self.assertTrue(all(len(part) == 3 for part in parsed), parsed)
        self.assertEqual(parsed[-1], [created["style"]["id"], "AI格式", "0"], "自定义在后")
        self.assertNotIn("\r", response.text)

    def test_style_detail_builtin_and_missing(self):
        detail = self.client.get("/api/cite/styles/apa").json()
        self.assertEqual(
            detail["style"],
            {"id": "apa", "name": "APA 7th", "source": "builtin", "builtin": True},
        )
        self.assertIsNone(detail["spec"], "内置格式没有可编辑规格")
        self.assertTrue(detail["preview"]["inline"])
        self.assertTrue(detail["preview"]["reference"])
        ieee = self.client.get("/api/cite/styles/ieee").json()
        self.assertEqual(ieee["preview"]["inline"], "[1]")
        self.assertTrue(ieee["preview"]["reference"].startswith("[1] "))
        self.assertEqual(self.client.get("/api/cite/styles/nope").status_code, 404)

    # ------------------------------------------------------------ AI 编译
    def test_ai_compile_creates_style_with_preview(self):
        payload = self.create_ai_style()
        style = payload["style"]
        self.assertTrue(style["id"].startswith("custom-"))
        self.assertFalse(style["builtin"])
        self.assertEqual(style["source"], "ai")
        self.assertEqual(style["name"], "我的格式")
        self.assertEqual(payload["spec"]["author_limit"], 3)
        self.assertIn("et al", payload["spec"]["et_al"])
        self.assertTrue(payload["preview"]["inline"])
        self.assertTrue(payload["preview"]["reference"])

        detail = self.client.get(f"/api/cite/styles/{style['id']}").json()
        self.assertEqual(detail["spec"]["author_limit"], 3)
        self.assertEqual(detail["preview"]["reference"], payload["preview"]["reference"])
        listed = [item["id"] for item in self.client.get("/api/cite/styles").json()["items"]]
        self.assertIn(style["id"], listed)

    def test_ai_compile_empty_description_returns_400(self):
        for description in ("", "   "):
            with self.subTest(description=description):
                response = self.client.post(
                    "/api/cite/styles/ai", json={"description": description}
                )
                self.assertEqual(response.status_code, 400, response.text)
                self.assertIn("描述", response.json()["detail"])

    def test_ai_compile_unconfigured_returns_400(self):
        with patch.object(
            config, "_cache", {**config.DEFAULT_SETTINGS, "scholar_auto_sync": False}
        ), patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "0"}):
            response = self.client.post(
                "/api/cite/styles/ai", json={"description": "前三个作者"}
            )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertIn("大模型未配置", response.json()["detail"])

    # ------------------------------------------------------------ CSL 导入
    def test_import_numeric_csl_and_errors(self):
        payload = self.create_import_style()
        self.assertTrue(payload["spec"]["numeric"])
        self.assertEqual(payload["spec"]["author_limit"], 3)
        self.assertEqual(payload["spec"]["inline_wrapper"], "bracket")
        self.assertEqual(payload["style"]["source"], "import")
        self.assertEqual(payload["style"]["name"], "导入格式")
        self.assertIsInstance(payload["warnings"], list)
        # numeric 样式预览：正文 [1]、条目带 [1] 前缀（只加在预览里，不写进 spec）
        self.assertTrue(payload["preview"]["inline"].startswith("["), payload["preview"])
        self.assertTrue(payload["preview"]["reference"].startswith("[1] "), payload["preview"])
        self.assertNotIn("[1]", str(payload["spec"]))

        style_id = payload["style"]["id"]
        detail = self.client.get(f"/api/cite/styles/{style_id}").json()
        self.assertTrue(detail["preview"]["reference"].startswith("[1] "))
        self.assertNotIn("[1]", str(detail["spec"]))

        bad_xml = self.client.post("/api/cite/styles/import", json={"csl": "not xml"})
        self.assertEqual(bad_xml.status_code, 400, bad_xml.text)
        wrong_root = self.client.post("/api/cite/styles/import", json={"csl": "<foo/>"})
        self.assertEqual(wrong_root.status_code, 400, wrong_root.text)

    # ------------------------------------------------------------ 预览与删除
    def test_preview_with_custom_spec_and_validation(self):
        created = self.create_ai_style()
        style_id = created["style"]["id"]
        preview = self.client.post(
            f"/api/cite/styles/{style_id}/preview",
            json={"spec": {"author_limit": 1, "last_author_join": ", "}},
        )
        self.assertEqual(preview.status_code, 200, preview.text)
        self.assertTrue(preview.json()["reference"])
        bad = self.client.post(
            f"/api/cite/styles/{style_id}/preview", json={"spec": {"author_limit": 100}}
        )
        self.assertEqual(bad.status_code, 400, bad.text)
        self.assertEqual(
            self.client.post("/api/cite/styles/nope/preview", json={}).status_code, 404
        )

    def test_delete_custom_style_and_guards(self):
        created = self.create_ai_style()
        style_id = created["style"]["id"]
        removed = self.client.delete(f"/api/cite/styles/{style_id}")
        self.assertEqual(removed.status_code, 200, removed.text)
        self.assertEqual(removed.json(), {"ok": True})
        self.assertEqual(self.client.get(f"/api/cite/styles/{style_id}").status_code, 404)
        self.assertEqual(self.client.delete("/api/cite/styles/apa").status_code, 400)
        self.assertEqual(self.client.delete("/api/cite/styles/custom-missing").status_code, 404)

    # ------------------------------------------------------------ 分发
    def test_document_txt_uses_custom_numeric_style(self):
        style_id = self.create_import_style()["style"]["id"]
        response = self.client.post(
            "/api/cite/document.txt", json={"ids": ["p2", "p1"], "style": style_id}
        )
        self.assertEqual(response.status_code, 200, response.text)
        lines = response.text.split("\n")
        self.assertEqual(lines[0], "p2\t1\t[1]")
        self.assertEqual(lines[1], "p1\t2\t[2]")
        self.assertEqual(lines[2], "--BIBLIOGRAPHY--")
        entries = "\n".join(lines[3:]).split("\n\n")
        self.assertEqual(len(entries), 2)
        self.assertTrue(entries[0].startswith("[1] "))
        self.assertTrue(entries[1].startswith("[2] "))
        self.assertIn("Paper Two", entries[0])

    def test_builtin_document_still_unmodified_and_custom_used_elsewhere(self):
        apa = self.client.post(
            "/api/cite/document.txt", json={"ids": ["p1", "p2"], "style": "apa"}
        ).text
        self.assertIn("p1\t1\t(Colburn & Chu, 2019)", apa)
        self.assertNotIn("[1]", apa)

        style_id = self.create_ai_style(name="自定义")["style"]["id"]
        paper = self.client.get(f"/api/cite/paper/p1?style={style_id}").json()
        self.assertTrue(paper["citation"])
        self.assertIn("Paper One", paper["reference"])
        bibliography = self.client.post(
            "/api/cite/bibliography", json={"ids": ["p1", "p2"], "style": style_id}
        ).json()["items"]
        self.assertEqual(len(bibliography), 2)
        self.assertEqual(bibliography[0]["index"], 1)
        self.assertTrue(bibliography[0]["reference"])

        numeric_id = self.create_import_style()["style"]["id"]
        items = self.client.post(
            "/api/cite/bibliography", json={"ids": ["p1", "p2"], "style": numeric_id}
        ).json()["items"]
        self.assertEqual(items[0]["citation"], "[1]")
        self.assertEqual(items[1]["citation"], "[2]")

    def test_unknown_style_document_txt_returns_400_but_paper_endpoint_falls_back(self):
        response = self.client.post(
            "/api/cite/document.txt", json={"ids": ["p1"], "style": "no-such-style"}
        )
        self.assertEqual(response.status_code, 400, response.text)
        # 老行为保留：单篇引文接口遇到未知 style 仍退回 APA 默认渲染
        paper = self.client.get("/api/cite/paper/p1?style=no-such-style").json()
        self.assertTrue(paper["reference"])


if __name__ == "__main__":
    unittest.main()
