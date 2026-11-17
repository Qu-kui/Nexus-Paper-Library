"""Deterministic request coordination; no network or user data access."""
import concurrent.futures
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import patch

import httpx
from paperwhale_server import config
from paperwhale_server.services import pdf_domains, pdf_download, pdf_trace


class Clock:
    def __init__(self):
        self.value = 100.0
        self.lock = threading.Lock()
        self.sleeps = []

    def __call__(self):
        with self.lock:
            return self.value

    def sleep(self, seconds):
        with self.lock:
            self.sleeps.append(seconds)
            self.value += seconds


class DomainPolicyTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.policy = pdf_domains.RequestPolicy(clock=self.clock, sleep=self.clock.sleep, wall_clock=lambda: 1000)
        self.url = 'https://publisher.example/paper.pdf'
        self.context = self.policy.context({})

    def acquire(self, url=None, context=None, deadline=1000):
        return self.policy.acquire(url or self.url, context or self.context, deadline)

    def test_start_spacing_is_host_wide_and_other_host_immediate(self):
        self.assertEqual(self.acquire(), 100)
        self.assertEqual(self.acquire('https://other.example/pdf'), 100)
        self.assertEqual(self.acquire('https://publisher.example/other'), 101)
        self.assertEqual(self.acquire(), 102)

    def test_concurrent_requests_reserve_distinct_start_times(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            starts = list(pool.map(lambda _: self.acquire(), range(12)))
        self.assertEqual(sorted(starts), list(range(100, 112)))
        self.assertTrue(all(wait <= .25 for wait in self.clock.sleeps))

    def test_deadline_rejects_spacing_without_wait(self):
        self.acquire()
        with self.assertRaises(pdf_domains.DomainPaused) as error:
            self.acquire(deadline=100.5)
        self.assertEqual(error.exception.code, 'deadline')
        self.assertEqual(self.clock(), 100)

    def test_retry_after_seconds_date_and_invalid_values(self):
        future = format_datetime(datetime.fromtimestamp(1012, timezone.utc), usegmt=True)
        self.assertEqual(pdf_domains.retry_seconds(future, now=1000), 12)
        self.assertEqual(pdf_domains.retry_seconds('2.5'), 2.5)
        self.assertEqual(pdf_domains.retry_seconds('-3'), 0)
        for value in ('NaN', 'inf', 'invalid', ''):
            self.assertEqual(pdf_domains.retry_seconds(value), 1)

    def test_429_shared_across_credentials_without_blocking_other_host(self):
        self.policy.observe(self.url, self.context, 429, '15')
        authorized = self.policy.context({'Cookie': 'session=FAKE'})
        for context in (self.context, authorized):
            with self.assertRaises(pdf_domains.DomainPaused) as error:
                self.acquire(context=context)
            self.assertEqual(error.exception.code, 'rate_limited')
            self.assertEqual(error.exception.seconds, 15)
        self.assertEqual(self.acquire('https://other.example/a'), 100)
        self.clock.sleep(15)
        self.assertEqual(self.acquire(), 115)

    def test_consecutive_403_circuit_is_context_scoped_and_recovers(self):
        for _ in range(3):
            self.policy.observe(self.url, self.context, 403)
        with self.assertRaises(pdf_domains.DomainPaused) as error:
            self.acquire()
        self.assertEqual(error.exception.code, 'domain_paused')
        authorized = self.policy.context({'Authorization': 'Bearer FAKE'})
        self.assertEqual(self.acquire(context=authorized), 100)
        self.clock.sleep(60)
        self.assertEqual(self.acquire(), 160)
        self.policy.observe(self.url, self.context, 200)
        self.policy.observe(self.url, self.context, 403)
        self.assertEqual(self.acquire(), 161)
        self.assertNotIn('FAKE', repr(self.policy._hosts))

    def test_503_retry_after_cooldown_shared_across_sessions_and_expires(self):
        future = format_datetime(datetime.fromtimestamp(1060, timezone.utc), usegmt=True)
        for header in ('60', future):
            with self.subTest(header=header):
                self.setUp()
                self.policy.observe(self.url, self.context, 503, header)
                authenticated = self.policy.context({'Cookie': 'session=FAKE'})
                for context in (self.context, authenticated):
                    with self.assertRaises(pdf_domains.DomainPaused) as error:
                        self.acquire(context=context)
                    self.assertEqual(error.exception.code, 'server_error')
                    self.assertEqual(error.exception.seconds, 60)
                self.assertEqual(self.acquire('https://other.example/a'), 100)
                self.assertEqual(self.clock.sleeps, [])
                self.clock.sleep(60)
                self.assertEqual(self.acquire(), 160)
                self.policy.observe(self.url, self.context, 200)
                self.assertEqual(self.acquire(context=authenticated), 161)

    def test_503_without_retry_after_does_not_arm_shared_cooldown(self):
        self.policy.observe(self.url, self.context, 503)
        self.assertEqual(self.acquire(), 100)
        self.assertEqual(self.clock.sleeps, [])

    def test_shorter_503_does_not_override_longer_429_cooldown(self):
        self.policy.observe(self.url, self.context, 429, '60')
        self.policy.observe(self.url, self.context, 503, '10')
        with self.assertRaises(pdf_domains.DomainPaused) as error:
            self.acquire()
        self.assertEqual(error.exception.code, 'rate_limited')
        self.assertEqual(error.exception.seconds, 60)

    def test_state_is_bounded_and_expires(self):
        policy = pdf_domains.RequestPolicy(clock=self.clock, sleep=self.clock.sleep, max_hosts=3, ttl=10)
        for index in range(10):
            policy.acquire(f'https://host{index}.example/a', self.context, 1000)
        self.assertEqual(len(policy._hosts), 3)
        for index in range(30):
            policy.observe(self.url, bytes([index]), 403)
        self.assertEqual(len(policy._hosts['publisher.example'].contexts), 16)
        self.clock.sleep(11)
        policy.acquire('https://new.example/a', self.context, 1000)
        self.assertEqual(len(policy._hosts), 1)

    def test_referer_preserves_proxy_query_only_on_same_origin(self):
        source = 'https://proxy.example/article?institution=FAKE#part'
        self.assertEqual(pdf_trace.request_referer(source, 'https://proxy.example/pdf'), source.split('#')[0])
        self.assertEqual(pdf_trace.request_referer(source, 'https://cdn.example/pdf'), 'https://proxy.example/')
        self.assertEqual(pdf_trace.request_referer(source, 'http://proxy.example/pdf'), '')
        self.assertEqual(pdf_trace.request_referer('https://user:pass@proxy.example/a', source), '')
        self.assertEqual(pdf_trace.request_referer(source+'\r\nX: a', source), '')
        self.assertNotIn('FAKE', pdf_trace.safe_url(source))

    def test_real_download_pipeline_retries_429_and_records_pause(self):
        requests = []
        pdf = (Path(__file__).parent / 'fixtures/attention.pdf').read_bytes()
        def serve(request):
            requests.append(self.clock())
            return httpx.Response(429, headers={'Retry-After': '2'}) if len(requests) == 1 else httpx.Response(200, content=pdf)
        with tempfile.TemporaryDirectory() as folder, patch.object(config, 'TMP_DIR', Path(folder)), patch.object(config, 'ensure_dirs'), httpx.Client(transport=httpx.MockTransport(serve)) as client:
            result = pdf_download.download(self.url, client=client, request_policy=self.policy, deadline=1000)
            self.assertTrue(result.path.exists())
        self.assertEqual(requests, [100, 102])

    def test_download_403_circuit_and_credential_retry_trace(self):
        requests = []
        def serve(request):
            requests.append(request)
            return httpx.Response(403)
        trace = pdf_trace.Trace()
        token = pdf_trace.active.set(trace)
        try:
            with patch.object(config, 'ensure_dirs'), httpx.Client(transport=httpx.MockTransport(serve)) as client:
                for _ in range(3):
                    with self.assertRaises(pdf_download.PdfDownloadError):
                        pdf_download.download(self.url, client=client, request_policy=self.policy, deadline=1000)
                with self.assertRaises(pdf_download.PdfDownloadError) as error:
                    pdf_download.download(self.url, client=client, request_policy=self.policy, deadline=1000)
                self.assertEqual(error.exception.code, 'domain_paused')
                self.assertEqual(len(requests), 3)
                with self.assertRaises(pdf_download.PdfDownloadError):
                    pdf_download.download(self.url, client=client, request_policy=self.policy, deadline=1000,
                                          cookie_provider=lambda _: 'session=FAKE')
                self.assertEqual(len(requests), 4)
            self.assertTrue(any(row.get('cooldown_seconds') == 60 for row in trace.attempts))
            self.assertNotIn('FAKE', repr(trace.attempts))
        finally:
            pdf_trace.active.reset(token)

    def test_long_retry_after_never_sleeps_and_blocks_next_download(self):
        requests = []
        trace = pdf_trace.Trace()
        def serve(request):
            requests.append(request)
            return httpx.Response(429, headers={'Retry-After': format_datetime(datetime.fromtimestamp(1060, timezone.utc), usegmt=True)})
        token = pdf_trace.active.set(trace)
        try:
            with patch.object(config, 'ensure_dirs'), httpx.Client(transport=httpx.MockTransport(serve)) as client:
                for _ in range(2):
                    with self.assertRaises(pdf_download.PdfDownloadError):
                        pdf_download.download(self.url, client=client, request_policy=self.policy, deadline=1000)
            self.assertEqual(len(requests), 1)
            self.assertEqual(self.clock.sleeps, [])
            self.assertTrue(any(row.get('cooldown_seconds') == 60 for row in trace.attempts))
        finally:
            pdf_trace.active.reset(token)

    def test_injected_clients_bypass_global_policy_unless_explicit(self):
        with patch.object(config, 'ensure_dirs'), patch.object(pdf_domains.default_policy, 'acquire', side_effect=AssertionError('global policy used')), httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403))) as client:
            with self.assertRaises(pdf_download.PdfDownloadError) as error:
                pdf_download.download(self.url, client=client)
            self.assertEqual(error.exception.code, 'access_denied')

    def test_503_download_shared_cooldown_preserves_trace_code(self):
        requests = []
        trace = pdf_trace.Trace()
        token = pdf_trace.active.set(trace)
        def serve(request):
            requests.append(request)
            return httpx.Response(503, headers={'Retry-After': '60'})
        try:
            with patch.object(config, 'ensure_dirs'), httpx.Client(transport=httpx.MockTransport(serve)) as client:
                for cookie in ('session=FIRST', 'session=SECOND'):
                    with self.assertRaises(pdf_download.PdfDownloadError) as error:
                        pdf_download.download(self.url, client=client, request_policy=self.policy, deadline=1000,
                                              headers={'Cookie': cookie})
                    self.assertEqual(error.exception.code, 'server_error')
            self.assertEqual(len(requests), 1)
            self.assertEqual(self.clock.sleeps, [])
            self.assertTrue(any(row['code'] == 'server_error' and row.get('cooldown_seconds') == 60 for row in trace.attempts))
            self.assertNotIn('FIRST', repr(trace.attempts))
            self.assertNotIn('SECOND', repr(trace.attempts))
        finally:
            pdf_trace.active.reset(token)

    def test_owned_client_uses_default_policy_and_closes_client(self):
        client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(403)))
        with patch.object(config, 'ensure_dirs'), patch.object(pdf_download.httpx, 'Client', return_value=client), patch.object(pdf_domains, 'default_policy', self.policy):
            with self.assertRaises(pdf_download.PdfDownloadError):
                pdf_download.download(self.url, deadline=1000)
        self.assertTrue(client.is_closed)
        self.assertEqual(self.policy._hosts['publisher.example'].contexts[self.context][0], 1)

    def test_download_wait_deadline_sends_no_request(self):
        self.acquire()
        requests = []
        with patch.object(config, 'ensure_dirs'), httpx.Client(transport=httpx.MockTransport(lambda request: requests.append(request) or httpx.Response(403))) as client:
            with self.assertRaises(pdf_download.PdfDownloadError) as error:
                pdf_download.download(self.url, client=client, request_policy=self.policy, deadline=100.5)
        self.assertEqual(error.exception.code, 'deadline')
        self.assertEqual(requests, [])


if __name__ == '__main__':
    unittest.main()
