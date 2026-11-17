# -*- coding: utf-8 -*-
"""卷期页排版：内置 6 种样式（有值 / 缺项 / 全空）与自定义规格 volume_pages。

全空输出必须与加卷期页之前逐字节一致，这里用基线字符串锁死。
"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import citation, citation_style

BASE_PAPER = {
    "id": "p1",
    "title": "Paper One",
    "authors": "Shane Colburn; Yi Chu",
    "year": "2019",
    "venue": "Applied Optics",
    "doi": "10.1000/example",
    "url": "https://example.org/p1",
}

#: 没有卷期页时的现有输出（加卷期页前的基线，逐字节不许变）
BASELINE = {
    "apa": "Colburn, S., & Chu, Y. (2019). Paper One. *Applied Optics*. https://doi.org/10.1000/example",
    "mla": 'Colburn, Shane, and Yi Chu. "Paper One." Applied Optics, 2019. https://doi.org/10.1000/example.',
    "chicago": 'Colburn, Shane, and Yi Chu. "Paper One." Applied Optics (2019). https://doi.org/10.1000/example.',
    "ieee": 'S. Colburn and Y. Chu, "Paper One," Applied Optics, 2019. doi: 10.1000/example.',
    #: Nature 有意的行为变更：作者段不再出现双句点（旧的 "Y.." → 新的 "Y."）
    "nature": "Colburn, S. & Chu, Y. Paper One. *Applied Optics*. (2019). https://doi.org/10.1000/example",
    "gbt7714": "COLBURN S, CHU Y. Paper One[J]. Applied Optics, 2019. DOI:10.1000/example.",
}

#: 卷 12、期 3、页 45–67 的完整输出
WITH_NUMBERS = {
    "apa": "Colburn, S., & Chu, Y. (2019). Paper One. *Applied Optics*, 12(3), 45–67. https://doi.org/10.1000/example",
    "mla": 'Colburn, Shane, and Yi Chu. "Paper One." Applied Optics, vol. 12, no. 3, 2019, pp. 45–67. https://doi.org/10.1000/example.',
    "chicago": 'Colburn, Shane, and Yi Chu. "Paper One." Applied Optics 12, no. 3 (2019): 45–67. https://doi.org/10.1000/example.',
    "ieee": 'S. Colburn and Y. Chu, "Paper One," Applied Optics, vol. 12, no. 3, pp. 45–67, 2019. doi: 10.1000/example.',
    #: Nature 只带卷、不带期（行为变更：12(3) → 12）
    "nature": "Colburn, S. & Chu, Y. Paper One. *Applied Optics* 12, 45–67 (2019). https://doi.org/10.1000/example",
    "gbt7714": "COLBURN S, CHU Y. Paper One[J]. Applied Optics, 2019, 12(3): 45–67. DOI:10.1000/example.",
}


class CitationVolumePagesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-cite-vol-")
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
        paper = dict(BASE_PAPER)
        paper.update(fields)
        return paper

    # ------------------------------------------------------------ 内置 6 种
    def test_nature_author_segment_has_no_double_period(self):
        """真机回归：作者缩写以 "." 结尾，拼标题时不能出现 "L.."。"""
        paper = self.paper(
            authors="Ding, Y.; Jiang, X.; Wang, C.; Jia, X.; Liu, L.",
            title="Femtosecond laser writing in glass",
            year="2026",
            venue="Opto-Electronic Science",
            volume="5",
            issue="4",
            pages="260004",
            doi="10.1000/nature.2026",
        )
        text = citation.reference(paper, "nature")
        self.assertNotIn("..", text, text)
        self.assertTrue(
            text.startswith("Ding, Y., Jiang, X., Wang, C., Jia, X., Liu, L. Femtosecond"), text
        )

    def test_nature_ignores_issue(self):
        """Nature 规范只用卷：有期号也只显示 5，不显示 5(4)。"""
        paper = self.paper(
            authors="Ding, Y.; Liu, L.",
            title="Femtosecond laser writing",
            year="2026",
            venue="Opto-Electronic Science",
            volume="5",
            issue="4",
            pages="260004",
        )
        text = citation.reference(paper, "nature")
        self.assertIn("*Opto-Electronic Science* 5, 260004 (2026)", text)
        self.assertNotIn("(4)", text)
        issue_only = citation.reference(
            self.paper(venue="Opto-Electronic Science", issue="4", pages="260004"), "nature"
        )
        self.assertNotIn("(4)", issue_only, "没有卷号时也不单独输出期号")

    def test_no_double_period_after_author_in_any_style(self):
        """六种内置样式都不许出现作者段 "L." 后接 "." 的双句点。"""
        paper = self.paper(
            authors="Ding, Y.; Jiang, X.; Wang, C.",
            title="Femtosecond laser writing",
            year="2026",
            venue="Opto-Electronic Science",
            volume="5",
            issue="4",
            pages="260004",
        )
        for style in ("apa", "mla", "chicago", "ieee", "nature", "gbt7714"):
            with self.subTest(style=style):
                self.assertNotIn("..", citation.reference(paper, style))

    def test_six_builtin_styles_with_volume_issue_pages(self):
        paper = self.paper(volume="12", issue="3", pages="45-67")
        for style, expected in WITH_NUMBERS.items():
            with self.subTest(style=style):
                self.assertEqual(citation.reference(paper, style), expected)

    def test_missing_issue_skips_no_segment(self):
        paper = self.paper(volume="12", pages="45-67")
        expected_fragments = {
            "apa": ", 12, 45–67.",
            "mla": "vol. 12, 2019, pp. 45–67.",
            "chicago": "Applied Optics 12 (2019): 45–67.",
            "ieee": "vol. 12, pp. 45–67,",
            "nature": "*Applied Optics* 12, 45–67 (2019)",
            "gbt7714": "2019, 12: 45–67.",
        }
        for style, fragment in expected_fragments.items():
            with self.subTest(style=style):
                text = citation.reference(paper, style)
                self.assertIn(fragment, text)
                self.assertNotIn("no.", text)
                self.assertNotIn("(3)", text)

    def test_only_pages(self):
        paper = self.paper(pages="45-67")
        expected_fragments = {
            "apa": "*, 45–67.",
            "mla": "2019, pp. 45–67.",
            "chicago": "(2019): 45–67.",
            "ieee": ", pp. 45–67,",
            "nature": "*Applied Optics*, 45–67 (2019)",
            "gbt7714": "2019: 45–67.",
        }
        for style, fragment in expected_fragments.items():
            with self.subTest(style=style):
                self.assertIn(fragment, citation.reference(paper, style))

    def test_empty_volume_issue_pages_keeps_baseline_bytes(self):
        paper = self.paper()  # 没有 volume/issue/pages
        for style, expected in BASELINE.items():
            with self.subTest(style=style):
                self.assertEqual(citation.reference(paper, style), expected)

    def test_page_dash_is_normalized_even_from_raw_field(self):
        for raw in ("45-67", "45–67", "45—67", "pp. 45-67"):
            with self.subTest(raw=raw):
                text = citation.reference(self.paper(volume="12", pages=raw), "ieee")
                self.assertIn("pp. 45–67", text)
                self.assertNotIn("45-67", text)

    # ------------------------------------------------------------ 自定义规格
    def test_custom_volume_pages_token_three_states(self):
        spec = citation_style.normalize_spec(
            {"bib_order": ["authors", "year", "title", "venue", "volume_pages", "doi"]}
        )
        full = citation_style.render_reference(self.paper(volume="12", issue="3", pages="45-67"), spec)
        self.assertIn("*Applied Optics*. 12(3): 45–67", full)
        no_issue = citation_style.render_reference(self.paper(volume="12", pages="45-67"), spec)
        self.assertIn("12: 45–67", no_issue)
        only_pages = citation_style.render_reference(self.paper(pages="45-67"), spec)
        self.assertIn("45–67", only_pages)
        self.assertNotIn(":", only_pages.replace("https://", "").split("45–67")[0])

    def test_custom_separate_volume_and_pages_tokens(self):
        spec = citation_style.normalize_spec(
            {"bib_order": ["authors", "year", "title", "venue", "volume", "pages"]}
        )
        text = citation_style.render_reference(self.paper(volume="12", pages="45-67"), spec)
        self.assertIn("12", text)
        self.assertIn("45–67", text)
        self.assertEqual(spec["italic"], ["venue"], "默认斜体不包含新 token")

    def test_old_spec_without_volume_pages_is_unchanged(self):
        """老 spec（bib_order 没有新 token）即使论文有卷期页也按原样渲染。"""
        spec = citation_style.normalize_spec(
            {"bib_order": ["authors", "year", "title", "venue", "doi"]}
        )
        paper = self.paper(volume="12", issue="3", pages="45–67")
        rendered = citation_style.render_reference(paper, spec)
        self.assertNotIn("12", rendered.replace("10.1000/example", ""))
        self.assertNotIn("45–67", rendered)

    def test_ai_compile_defaults_to_volume_pages_after_venue(self):
        style = citation_style.compile_style("只要前三个作者，多了写 et al.")
        order = style["spec"]["bib_order"]
        self.assertIn("volume_pages", order)
        self.assertEqual(order.index("venue") + 1, order.index("volume_pages"))

    def test_csl_import_gets_volume_pages_after_venue(self):
        csl = """<style xmlns="http://purl.org/net/xbiblio/csl" class="in-text">
          <info><title>imported</title></info>
          <citation><layout prefix="(" suffix=")"><names variable="author"/></layout></citation>
          <bibliography><layout>
            <names variable="author"/><date variable="issued"/>
            <text variable="title"/><text variable="container-title"/>
          </layout></bibliography>
        </style>"""
        spec = citation_style.import_csl(csl)["spec"]
        self.assertIn("volume_pages", spec["bib_order"])
        self.assertEqual(
            spec["bib_order"].index("venue") + 1, spec["bib_order"].index("volume_pages")
        )

    def test_bib_order_accepts_new_tokens(self):
        for token in ("volume", "pages", "volume_pages"):
            with self.subTest(token=token):
                spec = citation_style.normalize_spec({"bib_order": ["authors", token]})
                self.assertIn(token, spec["bib_order"])


if __name__ == "__main__":
    unittest.main()
