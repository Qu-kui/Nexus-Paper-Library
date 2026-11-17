# -*- coding: utf-8 -*-
"""自定义引文格式：规格校验、确定性渲染、AI 编译、CSL 导入、存取与分发。

隔离方式照旧：patch config 的数据路径，用临时 SQLite；LLM 走 PAPERWHALE_LLM_MOCK=1。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db, llm
from paperwhale_server.services import citation, citation_style

PAPER = {
    "id": "p1",
    "title": "Paper One",
    "authors": "Shane Colburn; Yi Chu",
    "year": "2019",
    "venue": "Applied Optics",
    "doi": "10.1364/AO.58.003179",
    "url": "https://example.org/p1",
}

#: 内置 6 种格式的渲染基线：本任务不许改变它们的任何字节
BUILTIN_BASELINE = {
    "apa": (
        "(Colburn & Chu, 2019)",
        "Colburn, S., & Chu, Y. (2019). Paper One. *Applied Optics*. https://doi.org/10.1364/AO.58.003179",
    ),
    "mla": (
        "(Colburn & Chu, 2019)",
        'Colburn, Shane, and Yi Chu. "Paper One." Applied Optics, 2019. https://doi.org/10.1364/AO.58.003179.',
    ),
    "chicago": (
        "(Colburn & Chu, 2019)",
        'Colburn, Shane, and Yi Chu. "Paper One." Applied Optics (2019). https://doi.org/10.1364/AO.58.003179.',
    ),
    "ieee": ("[2]", 'S. Colburn and Y. Chu, "Paper One," Applied Optics, 2019. doi: 10.1364/AO.58.003179.'),
    "nature": ("[2]", "Colburn, S. & Chu, Y. Paper One. *Applied Optics*. (2019). https://doi.org/10.1364/AO.58.003179"),
    "gbt7714": ("[2]", "COLBURN S, CHU Y. Paper One[J]. Applied Optics, 2019. DOI:10.1364/AO.58.003179."),
}

CSL_IEEE_LIKE = """<?xml version="1.0" encoding="utf-8"?>
<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" default-locale="en-US" version="1.0">
  <info><title>IEEE-like</title></info>
  <citation><layout><text variable="citation-number" prefix="[" suffix="]"/></layout></citation>
  <bibliography><layout>
    <names variable="author"><name name-as-sort-order="all" initialize-with=". " et-al-min="6" et-al-use-first="3"/></names>
    <date variable="issued"><date-part name="year"/></date>
    <text variable="title" prefix="&quot;" suffix="&quot;"/>
    <text variable="container-title" font-style="italic"/>
    <text variable="DOI" prefix="https://doi.org/"/>
    <text variable="URL"/>
  </layout></bibliography>
</style>"""

CSL_APA_LIKE = """<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text" default-locale="zh-CN">
  <info><title>APA-like</title></info>
  <citation><layout><names variable="author"/><date variable="issued"/></layout></citation>
  <bibliography><layout>
    <names variable="author"><name et-al-use-first="20" initialize-with=". "/></names>
    <date variable="issued"/>
    <text variable="title"/>
    <text variable="container-title"/>
  </layout></bibliography>
</style>"""

CSL_WITH_CHOOSE = """<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text">
  <info><title>Choose-like</title></info>
  <citation><layout><text variable="citation-number"/></layout></citation>
  <bibliography><layout>
    <names variable="author"><name et-al-use-first="3"/></names>
    <choose><if type="article-journal"><text variable="title"/></if></choose>
    <text variable="volume"/>
    <text variable="publisher"/>
    <text variable="DOI"/>
  </layout></bibliography>
</style>"""


class CitationStyleBase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-cstyle-")
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

    def tearDown(self):
        self.env.stop()
        self.settings.stop()
        self.temp.cleanup()

    def paper(self, **fields):
        paper = dict(PAPER)
        paper.update(fields)
        return paper


class SpecValidationTests(CitationStyleBase):
    def test_missing_fields_are_filled_with_defaults(self):
        self.assertEqual(citation_style.normalize_spec({}), citation_style.DEFAULT_SPEC)
        spec = citation_style.normalize_spec({"numeric": True, "author_limit": 5})
        self.assertTrue(spec["numeric"])
        self.assertEqual(spec["author_limit"], 5)
        self.assertEqual(spec["et_al"], "et al.")
        self.assertEqual(spec["bib_order"], list(citation_style.DEFAULT_SPEC["bib_order"]))

    def test_invalid_enums_and_bounds_raise_value_error(self):
        bad_specs = [
            {"name_format": "wrong"},
            {"inline_wrapper": "round"},
            {"bib_year_style": "brackets"},
            {"doi_style": "doi"},
            {"url_style": "yes"},
            {"numeric": "yes"},
            {"author_limit": -1},
            {"author_limit": 100},
            {"author_limit": "abc"},
            {"inline_author_limit": 3.5},
            {"bib_order": "authors"},
            {"bib_order": []},
            {"bib_order": ["publisher"]},
            {"bib_order": ["authors", "publisher"]},
            {"italic": "venue"},
            {"italic": ["url"]},
            {"italic": ["venue"], "bib_order": ["authors", "title"]},
        ]
        for bad in bad_specs:
            with self.subTest(spec=bad), self.assertRaises(ValueError):
                citation_style.normalize_spec(bad)

    def test_non_dict_spec_raises(self):
        for bad in ([], "x", 3, None):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                citation_style.normalize_spec(bad)

    def test_extra_fields_ignored_and_strings_truncated(self):
        spec = citation_style.normalize_spec(
            {
                "unknown": "ignored",
                "name": "格式名" * 40,
                "et_al": "e" * 30,
                "inline_et_al": "等" * 30,
                "author_delimiter": "|" * 20,
                "last_author_join": "/" * 20,
                "inline_year_sep": "-" * 20,
                "bib_field_sep": ":" * 20,
            }
        )
        self.assertEqual(set(spec), set(citation_style.DEFAULT_SPEC))
        self.assertEqual(len(spec["name"]), 60)
        self.assertEqual(len(spec["et_al"]), 20)
        self.assertEqual(len(spec["inline_et_al"]), 20)
        for field in ("author_delimiter", "last_author_join", "inline_year_sep", "bib_field_sep"):
            self.assertEqual(len(spec[field]), 10)

    def test_bib_order_deduped_and_italic_default_tracks_order(self):
        spec = citation_style.normalize_spec({"bib_order": ["title", "authors", "title"]})
        self.assertEqual(spec["bib_order"], ["title", "authors"])
        self.assertEqual(spec["italic"], [], "默认斜体只保留 bib_order 里存在的字段")


class RenderTests(CitationStyleBase):
    def test_user_example_three_authors_et_al(self):
        paper = self.paper(authors="王; A.; B.; C.")
        spec = citation_style.normalize_spec(
            {"author_limit": 3, "et_al": "et al.", "last_author_join": ", "}
        )
        reference = citation_style.render_reference(paper, spec)
        self.assertTrue(reference.startswith("王, A, B, et al."), reference)
        self.assertNotIn("C.", reference, "第 4 位作者必须被省略")

        spec_all = citation_style.normalize_spec({"author_limit": 0})
        reference_all = citation_style.render_reference(paper, spec_all)
        self.assertIn("C.", reference_all)
        self.assertNotIn("et al.", reference_all)

    def test_name_formats(self):
        paper = self.paper(authors="Wei Zhang; Na Li")
        base = {"bib_order": ["authors"], "trailing_period": False, "last_author_join": ", "}
        cases = {
            "family-initials": "Zhang, W., Li, N.",
            "initials-family": "W. Zhang, N. Li",
            "family": "Zhang, Li",
        }
        for name_format, expected in cases.items():
            with self.subTest(name_format=name_format):
                spec = citation_style.normalize_spec({**base, "name_format": name_format})
                self.assertEqual(citation_style.render_reference(paper, spec), expected)

        no_dots = citation_style.normalize_spec(
            {**base, "name_format": "family-initials", "initials_dots": False}
        )
        self.assertEqual(citation_style.render_reference(paper, no_dots), "Zhang, W, Li, N")

    def test_numeric_inline_uses_index(self):
        spec = citation_style.normalize_spec({"numeric": True})
        self.assertEqual(citation_style.render_inline(self.paper(), spec, 3), "[3]")
        # numeric 时作者/年份不参与正文引文
        self.assertEqual(citation_style.render_inline({}, spec, 7), "[7]")

    def test_missing_fields_are_skipped_without_empty_separators(self):
        paper = {"title": "Only Title", "year": "2020"}
        spec = citation_style.normalize_spec({})
        reference = citation_style.render_reference(paper, spec)
        self.assertEqual(reference, "(2020). Only Title.")
        self.assertNotIn(", ,", reference)
        self.assertNotIn(" ..", reference)

    def test_trailing_period_toggle(self):
        paper = self.paper()
        base = {"bib_order": ["title"]}
        with_period = citation_style.render_reference(
            paper, citation_style.normalize_spec(base)
        )
        self.assertTrue(with_period.endswith("."))
        without = citation_style.render_reference(
            paper, citation_style.normalize_spec({**base, "trailing_period": False})
        )
        self.assertEqual(without, "Paper One")

    def test_italic_and_title_quotes(self):
        paper = self.paper()
        spec = citation_style.normalize_spec(
            {"bib_order": ["title", "venue"], "italic": ["venue"], "title_quotes": True}
        )
        reference = citation_style.render_reference(paper, spec)
        self.assertIn('"Paper One"', reference)
        self.assertIn("*Applied Optics*", reference)

        both = citation_style.normalize_spec(
            {
                "bib_order": ["title"],
                "italic": ["title"],
                "title_quotes": True,
                "trailing_period": False,
            }
        )
        self.assertEqual(citation_style.render_reference(paper, both), '*"Paper One"*')

    def test_doi_and_url_switches(self):
        paper = self.paper()
        base = {"bib_order": ["doi"], "trailing_period": False}
        url_spec = citation_style.normalize_spec({**base, "doi_style": "url"})
        self.assertEqual(
            citation_style.render_reference(paper, url_spec), "https://doi.org/10.1364/AO.58.003179"
        )
        plain_spec = citation_style.normalize_spec({**base, "doi_style": "plain"})
        self.assertEqual(citation_style.render_reference(paper, plain_spec), "10.1364/AO.58.003179")
        off_spec = citation_style.normalize_spec({**base, "doi_style": "off"})
        self.assertEqual(citation_style.render_reference(paper, off_spec), "")
        url_on = citation_style.normalize_spec(
            {"bib_order": ["url"], "url_style": "on", "trailing_period": False}
        )
        self.assertEqual(citation_style.render_reference(paper, url_on), "https://example.org/p1")
        url_off = citation_style.normalize_spec(
            {"bib_order": ["url"], "url_style": "off", "trailing_period": False}
        )
        self.assertEqual(citation_style.render_reference(paper, url_off), "")

    def test_inline_wrappers_and_truncation(self):
        paper = self.paper(authors="王; 李; 张")
        base = {"inline_author_limit": 0, "last_author_join": " & "}
        # 3 人：前两位用 author_delimiter，最后一位前用 last_author_join
        self.assertEqual(
            citation_style.render_inline(paper, citation_style.normalize_spec(base), 1),
            "(王, 李 & 张, 2019)",
        )
        bracket = citation_style.normalize_spec({**base, "inline_wrapper": "bracket"})
        self.assertEqual(citation_style.render_inline(paper, bracket, 1), "[王, 李 & 张, 2019]")
        bare = citation_style.normalize_spec({**base, "inline_wrapper": "none"})
        self.assertEqual(citation_style.render_inline(paper, bare, 1), "王, 李 & 张, 2019")

        limited = citation_style.normalize_spec(
            {"inline_author_limit": 2, "inline_et_al": "等", "inline_wrapper": "paren"}
        )
        self.assertEqual(citation_style.render_inline(paper, limited, 1), "(王 & 李 等, 2019)")
        one = citation_style.normalize_spec({"inline_author_limit": 1})
        self.assertEqual(citation_style.render_inline(paper, one, 1), "(王 et al., 2019)")

    def test_inline_truncation_has_no_extra_comma(self):
        """E2E 回归：正文截断后是 (Xiang et al., 2023)，不能出现 (Xiang, et al., 2023)。"""
        paper = self.paper(authors="Xiang; Shi; Zhang", year="2023")
        spec = citation_style.normalize_spec({"inline_author_limit": 1})
        self.assertEqual(citation_style.render_inline(paper, spec, 1), "(Xiang et al., 2023)")
        two = citation_style.normalize_spec({"inline_author_limit": 2, "inline_et_al": "等"})
        self.assertEqual(citation_style.render_inline(paper, two, 1), "(Xiang & Shi 等, 2023)")

    def test_reference_truncation_uses_delimiter_not_last_join(self):
        """E2E 回归：17 位作者 + limit 3 → 前三位用逗号，不要再冒 "&"。"""
        authors = ["Xiang, S.", "Shi, Y.", "Zhang, Y."] + [f"Author{index}, A." for index in range(4, 18)]
        self.assertEqual(len(authors), 17)
        paper = self.paper(authors="; ".join(authors), year="2023")
        spec = citation_style.normalize_spec({"author_limit": 3})
        reference = citation_style.render_reference(paper, spec)
        self.assertTrue(reference.startswith("Xiang, S., Shi, Y., Zhang, Y., et al."), reference)
        self.assertNotIn(" & ", reference, "截断时不能用 last_author_join")
        self.assertNotIn("Author4", reference, "第 4 位作者必须被省略")
        self.assertNotIn(",  et al.", reference)

    def test_no_authors_inline_only_year(self):
        spec = citation_style.normalize_spec({})
        self.assertEqual(citation_style.render_inline({"year": "2020"}, spec, 1), "(2020)")


class StorageAndDispatchTests(CitationStyleBase):
    def test_save_get_list_delete(self):
        style = citation_style.save_style({"author_limit": 2}, "manual", "两位作者")
        self.assertTrue(style["id"].startswith("custom-"))
        self.assertEqual(style["name"], "两位作者")
        self.assertFalse(style["builtin"])
        self.assertEqual(citation_style.get_spec(style["id"])["author_limit"], 2)

        items = citation_style.list_styles()
        builtin_ids = [item["id"] for item in items if item["builtin"]]
        self.assertEqual(builtin_ids, [item["id"] for item in citation.STYLES])
        self.assertEqual(items[-1]["id"], style["id"])
        self.assertFalse(citation.is_numeric(style["id"]))

        self.assertTrue(citation_style.delete_style(style["id"]))
        self.assertIsNone(citation_style.get_style(style["id"]))
        self.assertFalse(citation_style.delete_style(style["id"]))
        self.assertFalse(citation_style.delete_style("apa"), "内置格式不能删")

    def test_custom_styles_are_listed_in_creation_order(self):
        first = citation_style.save_style({"author_limit": 1}, "manual", "第一个")
        second = citation_style.save_style({"author_limit": 2}, "manual", "第二个")
        custom = [item["id"] for item in citation_style.list_styles() if not item["builtin"]]
        self.assertEqual(custom, [first["id"], second["id"]])

    def test_builtin_spec_is_none_and_dispatch_falls_through(self):
        self.assertIsNone(citation_style.get_spec("apa"))
        self.assertIsNone(citation_style.get_spec("ieee"))
        self.assertIsNone(citation_style.get_spec("nope"))
        self.assertTrue(citation_style.is_builtin("gbt7714"))
        self.assertTrue(citation_style.is_builtin("apa"))
        self.assertFalse(citation_style.is_builtin("custom-deadbeef"))

    def test_citation_dispatch_uses_custom_spec(self):
        paper = self.paper(authors="王; 李; 张; 赵")
        style = citation_style.save_style(
            {"author_limit": 2, "et_al": "等", "last_author_join": ", "}, "manual", "截断"
        )
        self.assertNotIn("赵", citation.reference(paper, style["id"]))
        self.assertIn("等", citation.reference(paper, style["id"]))
        self.assertIn("王", citation.inline(paper, style["id"]))

        numeric = citation_style.save_style({"numeric": True}, "manual", "数字编号")
        paper["_index"] = 4
        self.assertEqual(citation.inline(paper, numeric["id"]), "[4]")
        self.assertTrue(citation.is_numeric(numeric["id"]))
        self.assertTrue(citation.is_numeric("ieee"))
        self.assertFalse(citation.is_numeric("apa"))

    def test_builtin_rendering_is_byte_identical(self):
        paper = self.paper()
        paper["_index"] = 2
        for style, (expected_inline, expected_reference) in BUILTIN_BASELINE.items():
            with self.subTest(style=style):
                self.assertEqual(citation.inline(paper, style), expected_inline)
                self.assertEqual(citation.reference(paper, style), expected_reference)


class CslImportTests(CitationStyleBase):
    def test_ieee_like_csl_maps_to_spec(self):
        result = citation_style.import_csl(CSL_IEEE_LIKE)
        spec = result["spec"]
        self.assertTrue(spec["numeric"], "citation-number 必须映射成 numeric")
        self.assertEqual(spec["inline_wrapper"], "bracket", "citation layout 的 [ ] 要映射成 bracket")
        self.assertEqual(spec["author_limit"], 3)
        self.assertEqual(spec["et_al"], "et al.")
        self.assertEqual(spec["name_format"], "family-initials")
        self.assertTrue(spec["initials_dots"])
        self.assertTrue(spec["title_quotes"])
        self.assertIn("venue", spec["italic"])
        self.assertEqual(spec["doi_style"], "url")
        self.assertEqual(spec["url_style"], "on")
        self.assertEqual(
            spec["bib_order"], ["authors", "year", "title", "venue", "volume_pages", "doi", "url"]
        )
        self.assertEqual(spec["name"], "IEEE-like", "没给 name 时用 CSL 的 info/title")
        self.assertEqual(result["warnings"], [])

    def test_apa_like_csl_zh_locale(self):
        result = citation_style.import_csl(CSL_APA_LIKE)
        spec = result["spec"]
        self.assertFalse(spec["numeric"])
        self.assertEqual(spec["inline_wrapper"], "none", "citation layout 没写包围符就是 none")
        self.assertEqual(spec["author_limit"], 20)
        self.assertEqual(spec["et_al"], "等")
        self.assertEqual(spec["inline_et_al"], "等")
        self.assertEqual(spec["name_format"], "initials-family")
        self.assertEqual(spec["bib_order"][:4], ["authors", "year", "title", "venue"])

    def test_csl_inline_wrapper_inferred_from_prefix_suffix(self):
        """E2E 回归：citation layout 的 [ ] / ( ) / 无 分别映射 bracket/paren/none。"""
        cases = {
            "bracket": '<citation><layout prefix="[" suffix="]"><text variable="citation-number"/></layout></citation>',
            "paren": '<citation><layout prefix="(" suffix=")"><names variable="author"/><date variable="issued"/></layout></citation>',
            "none": '<citation><layout><names variable="author"/></layout></citation>',
        }
        for expected, citation_xml in cases.items():
            csl = (
                '<style xmlns="http://purl.org/net/xbiblio/csl"><info><title>wrap</title></info>'
                + citation_xml
                + "</style>"
            )
            with self.subTest(expected=expected):
                self.assertEqual(
                    citation_style.import_csl(csl)["spec"]["inline_wrapper"], expected
                )

    def test_import_rejects_invalid_input(self):
        for bad in ("", "  ", "not xml at all", "<foo><bar/></foo>", "<style>"):
            with self.subTest(csl=bad), self.assertRaises(ValueError):
                citation_style.import_csl(bad)

    def test_import_degrades_on_choose_and_unknown_fields(self):
        result = citation_style.import_csl(CSL_WITH_CHOOSE)
        warnings = "\n".join(result["warnings"])
        self.assertIn("choose", warnings)
        self.assertIn("volume", warnings)
        self.assertIn("publisher", warnings)
        self.assertTrue(result["spec"]["numeric"])

    def test_import_doi_without_link_prefix_warns(self):
        csl = """<style xmlns="http://purl.org/net/xbiblio/csl">
          <info><title>plain-doi</title></info>
          <bibliography><layout><text variable="DOI"/></layout></bibliography>
        </style>"""
        result = citation_style.import_csl(csl)
        self.assertEqual(result["spec"]["doi_style"], "plain")
        self.assertTrue(any("裸 DOI" in item for item in result["warnings"]))

    def test_imported_spec_can_render(self):
        style = citation_style.save_style(
            citation_style.import_csl(CSL_IEEE_LIKE)["spec"], "import", None
        )
        reference = citation.reference(self.paper(), style["id"])
        self.assertIn("Paper One", reference)
        self.assertTrue(reference.startswith("Colburn, S."), reference)


class AiCompileTests(CitationStyleBase):
    def test_compile_with_mock_saves_valid_spec(self):
        style = citation_style.compile_style("只保留前三个作者，多了就写 et al.", "我的格式")
        self.assertEqual(style["source"], "ai")
        self.assertEqual(style["name"], "我的格式")
        self.assertEqual(style["spec"]["author_limit"], 3)
        self.assertIn("et al", style["spec"]["et_al"])
        reference = citation.reference(self.paper(), style["id"])
        self.assertTrue(reference, "编译出的规格必须能渲染")

    def test_compile_without_model_name_uses_spec_name(self):
        style = citation_style.compile_style("数字编号格式")
        self.assertTrue(style["name"])
        self.assertEqual(style["name"], style["spec"]["name"])

    def test_empty_description_raises(self):
        for bad in ("", "   "):
            with self.subTest(description=bad), self.assertRaises(ValueError):
                citation_style.compile_style(bad)

    def test_unconfigured_llm_raises(self):
        with patch.object(
            config, "_cache", {**config.DEFAULT_SETTINGS, "scholar_auto_sync": False}
        ), patch.dict(os.environ, {"PAPERWHALE_LLM_MOCK": "0"}):
            with self.assertRaises(llm.LLMError):
                citation_style.compile_style("前三个作者")


if __name__ == "__main__":
    unittest.main()
