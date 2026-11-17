# -*- coding: utf-8 -*-
"""WebImportIn 校验回归：把会 422 的输入固定下来。

背景：2026-10-28 用户 Connector 保存报 `[提交到 PaperWhale] [object Object]`，
旧 popup.js `new Error(data.detail)` 遇到 FastAPI 422 的 detail 数组就会显示
`[object Object]`。本测试锁住「哪些字段类型会触发 422」，以及 422 detail 的形状，
避免以后改模型时把兼容性（例如 authors 同时接受字符串）弄丢。
"""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("PAPERWHALE_DATA", os.path.join(ROOT, "tests", ".tmp-data"))
os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from pydantic import ValidationError  # noqa: E402

from paperwhale_server.routers.webimport import WebImportIn  # noqa: E402

POPUP_PAYLOAD = {
    "title": "Deep learning",
    "authors": ["A B", "C D"],
    "year": "2016",
    "venue": "Nature",
    "doi": "10.1/x",
    "url": "https://example.org/1",
    "abstract": "abs",
    "tags": ["a", "b"],
    "item_type": "article",
    "pdf_url": "https://example.org/a.pdf",
    "pdf_candidates": ["https://example.org/a.pdf"],
    "pdf_cookies": "k=v",
    "pdf_referer": "https://example.org/1",
    "pdf_user_agent": "Mozilla/5.0",
    "pdf_attempts": [{"url": "https://example.org/a", "code": "access_denied", "status": 403}],
    "page_html": "<html>" + ("中文 <p>x</p> " * 5000) + "</html>",
    "download_pdf": True,
    "analyze": False,
}


class WebImportValidationTest(unittest.TestCase):
    def test_popup_payload_is_accepted(self):
        body = WebImportIn.model_validate(POPUP_PAYLOAD)
        self.assertEqual(body.title, "Deep learning")
        self.assertEqual(body.authors, ["A B", "C D"])
        self.assertEqual(body.year, "2016")
        self.assertEqual(len(body.pdf_attempts), 1)

    def test_string_authors_and_tags_accepted(self):
        body = WebImportIn.model_validate({"title": "t", "authors": "A; B", "tags": "x; y"})
        self.assertEqual(body.authors, "A; B")

    def test_object_in_authors_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            WebImportIn.model_validate({"title": "t", "authors": [{"name": "x"}]})
        locs = [tuple(err["loc"]) for err in ctx.exception.errors()]
        self.assertIn(("authors", "list[str]", 0), locs)

    def test_non_string_in_tags_rejected(self):
        for bad in ([{"k": "v"}], [1], [None]):
            with self.subTest(tags=bad), self.assertRaises(ValidationError):
                WebImportIn.model_validate({"title": "t", "tags": bad})

    def test_numeric_year_rejected(self):
        with self.assertRaises(ValidationError) as ctx:
            WebImportIn.model_validate({"title": "t", "year": 2024})
        self.assertEqual(ctx.exception.errors()[0]["loc"], ("year",))

    def test_string_fields_reject_numbers_objects_and_null(self):
        for field in ("title", "venue", "doi", "url", "abstract", "pdf_url", "page_html"):
            for bad in (123, {"a": 1}, None):
                with self.subTest(field=field, bad=bad), self.assertRaises(ValidationError):
                    WebImportIn.model_validate({"title": "t", field: bad})

    def test_pdf_candidates_must_be_list_of_strings(self):
        for bad in ([None], [7], "https://example.org/a.pdf"):
            with self.subTest(candidates=bad), self.assertRaises(ValidationError):
                WebImportIn.model_validate({"title": "t", "pdf_candidates": bad})

    def test_pdf_attempts_must_be_list_of_dicts(self):
        for bad in ("x", ["x"], [None], [[]]):
            with self.subTest(attempts=bad), self.assertRaises(ValidationError):
                WebImportIn.model_validate({"title": "t", "pdf_attempts": bad})
        # 每条 attempt 内部的值类型不限（插件会带 status 数字、url None 等）
        WebImportIn.model_validate(
            {"title": "t", "pdf_attempts": [{"url": None, "status": "403", "x": [1]}]}
        )

    def test_bool_fields_accept_common_coercions_only(self):
        WebImportIn.model_validate({"title": "t", "download_pdf": 1})
        WebImportIn.model_validate({"title": "t", "download_pdf": "true"})
        with self.assertRaises(ValidationError):
            WebImportIn.model_validate({"title": "t", "download_pdf": 2})

    def test_unknown_extra_fields_ignored(self):
        body = WebImportIn.model_validate({"title": "t", "site": "ScienceDirect", "user_agent": "x"})
        self.assertFalse(hasattr(body, "site"))

    def test_validation_detail_is_list_of_objects(self):
        """旧 popup.js `new Error(data.detail)` 把它变成 "[object Object]" 的原因。"""
        with self.assertRaises(ValidationError) as ctx:
            WebImportIn.model_validate({"title": "t", "authors": [{"name": "x"}]})
        errors = ctx.exception.errors()
        self.assertIsInstance(errors, list)
        self.assertTrue(all(isinstance(item, dict) for item in errors))
        self.assertIn("loc", errors[0])
        self.assertEqual(str(errors)[:2], "[{")


if __name__ == "__main__":
    unittest.main(verbosity=2)
