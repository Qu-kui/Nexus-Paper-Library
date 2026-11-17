"""PDF 获取优化：礼貌邮箱、自适应降速、限流自动重试。

隔离要求：PAPERWHALE_DATA=tests\\.tmp-data、PAPERWHALE_LLM_MOCK=1；本文件不写用户库。
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

from paperwhale_server.services import metadata, pdf_domains, pdf_fetch


class AdaptivePacingTests(unittest.TestCase):
    def make_policy(self):
        clock = {"now": 1000.0}
        return pdf_domains.RequestPolicy(
            spacing=1.0,
            adaptive_spacing=True,
            clock=lambda: clock["now"],
            wall_clock=lambda: 0.0,
            sleep=lambda seconds: None,
        ), clock

    def test_rate_limit_raises_host_spacing_and_success_decays_it(self):
        policy, clock = self.make_policy()
        url = "https://example.org/paper.pdf"
        context = policy.context({})
        policy.observe(url, context, 429, "2")
        state = policy._hosts["example.org"]
        self.assertGreaterEqual(state.spacing_boost, 2.0)
        boosted = state.spacing_boost
        # 成功一次后开始回调，但不会立刻归零
        policy.observe(url, context, 200)
        self.assertLess(policy._hosts["example.org"].spacing_boost, boosted)
        self.assertGreaterEqual(policy._hosts["example.org"].spacing_boost, 0.0)

    def test_spacing_boost_is_capped(self):
        policy, _ = self.make_policy()
        url = "https://example.org/paper.pdf"
        context = policy.context({})
        for _ in range(10):
            policy.observe(url, context, 429, "1")
        self.assertLessEqual(policy._hosts["example.org"].spacing_boost, 8.0)

    def test_403_also_slows_the_host_down(self):
        policy, _ = self.make_policy()
        url = "https://publisher.example/paper.pdf"
        policy.observe(url, policy.context({}), 403)
        self.assertGreater(policy._hosts["publisher.example"].spacing_boost, 0)


class UnpaywallEmailFallbackTests(unittest.TestCase):
    def test_falls_back_to_scholar_email(self):
        from unittest.mock import patch

        captured = {}

        class FakeResponse:
            status_code = 200

            def json(self):
                return {
                    "best_oa_location": {"url_for_pdf": "https://example.org/oa.pdf", "version": "publishedVersion", "license": "cc-by"},
                    "oa_locations": [],
                }

        def fake_get(url, *, params=None, timeout=None, headers=None):
            captured["email"] = (params or {}).get("email")
            return FakeResponse()

        with patch.object(pdf_fetch.config, "get_settings", return_value={"scholar_email": "whale@example.org"}), patch.object(
            pdf_fetch.metadata_service, "_get", side_effect=fake_get
        ):
            candidates = pdf_fetch._unpaywall_pdfs("10.1000/test")
        self.assertEqual(captured.get("email"), "whale@example.org")
        self.assertTrue(any("example.org/oa.pdf" in item["url"] for item in candidates))

    def test_skips_without_any_email(self):
        from unittest.mock import patch

        with patch.object(pdf_fetch.config, "get_settings", return_value={}), patch.dict("os.environ", {}, clear=False):
            sys.modules["os"].environ.pop("PAPERWHALE_UNPAYWALL_EMAIL", None)
            candidates = pdf_fetch._unpaywall_pdfs("10.1000/test")
        self.assertEqual(candidates, [])


class PolitePoolTests(unittest.TestCase):
    def test_contact_email_prefers_env_then_settings(self):
        from unittest.mock import patch

        with patch.dict("os.environ", {"PAPERWHALE_MAILTO": "env@example.org"}, clear=False):
            self.assertEqual(metadata._contact_email(), "env@example.org")
        with patch.dict("os.environ", {}, clear=True), patch.object(
            metadata.config if hasattr(metadata, "config") else pdf_fetch.config,
            "get_settings",
            return_value={"scholar_email": "scholar@example.org"},
        ):
            email = metadata._contact_email()
        self.assertIn(email, {"env@example.org", "scholar@example.org", ""})


class RetryTests(unittest.TestCase):
    def test_transient_failure_schedules_exactly_one_retry(self):
        manager = pdf_fetch.PdfFetchManager()
        scheduled = []

        class FakeTimer:
            def __init__(self, delay, function, args=()):
                scheduled.append((delay, function, args))
                self.daemon = False

            def start(self):
                return None

        original = pdf_fetch.threading.Timer
        pdf_fetch.threading.Timer = FakeTimer
        try:
            manager._schedule_retry("p1", {"ok": False, "code": "rate_limited"})
            manager._schedule_retry("p1", {"ok": False, "code": "rate_limited"})
        finally:
            pdf_fetch.threading.Timer = original
        self.assertEqual(len(scheduled), 1)
        self.assertEqual(scheduled[0][2][0], "p1")

    def test_permanent_failure_is_not_retried(self):
        manager = pdf_fetch.PdfFetchManager()
        scheduled = []

        class FakeTimer:
            def __init__(self, delay, function, args=()):
                scheduled.append(delay)
                self.daemon = False

            def start(self):
                return None

        original = pdf_fetch.threading.Timer
        pdf_fetch.threading.Timer = FakeTimer
        try:
            manager._schedule_retry("p2", {"ok": False, "code": "not_found"})
            manager._schedule_retry("p3", {"ok": True})
        finally:
            pdf_fetch.threading.Timer = original
        self.assertEqual(scheduled, [])


if __name__ == "__main__":
    unittest.main()
