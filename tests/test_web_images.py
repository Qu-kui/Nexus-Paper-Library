"""联网配图：来源页取图中转、缓存、静态服务。"""
from __future__ import annotations

import io
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from PIL import Image

from paperwhale_server import config, db
from paperwhale_server.services import web_images


def _png_bytes(width=640, height=480) -> bytes:
    buffer = io.BytesIO()
    Image.new('RGB', (width, height), (180, 60, 90)).save(buffer, format='PNG')
    return buffer.getvalue()


class WebImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pw-webimg-')
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / 'settings.json',
            DB_PATH=root / 'test.db',
            TMP_DIR=root / 'tmp',
            DEFAULT_PDF_DIR=root / 'pdfs',
            PROGRAM_DIR=root,
            _cache={**config.DEFAULT_SETTINGS},
        )
        self.paths.start()
        db.init_db()

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_parse_meta_and_relative_images(self):
        html = ('<html><head><meta property="og:image" content="/img/a.png">'
                '<title> A PPLN page </title></head></html>')
        urls = web_images._page_images('https://example.com/x', html)
        self.assertEqual(urls, ['https://example.com/img/a.png'])

    def test_reject_private_hosts(self):
        self.assertFalse(web_images._public_url('http://127.0.0.1/a.png'))
        self.assertFalse(web_images._public_url('http://192.168.1.5/a.png'))
        self.assertFalse(web_images._public_url('ftp://example.com/a.png'))

    def test_collect_caches_images(self):
        def fake_page(url):
            return 'PPLN 示意图', [f'https://img.example.com/{url.split("/")[-1]}.png']

        def fake_download(url):
            return _png_bytes(), 640, 480, 'png'

        with patch.object(web_images, '_public_url', return_value=True), \
                patch.object(web_images, '_fetch_page', side_effect=fake_page), \
                patch.object(web_images, '_download', side_effect=fake_download):
            images = web_images.collect([{'url': 'https://a.example/1'}, {'url': 'https://b.example/2'}])
        self.assertEqual(len(images), 2)
        first = images[0]
        self.assertTrue(first['url'].startswith('/api/web-images/'))
        cached = Path(web_images.cache_dir()) / first['url'].rsplit('/', 1)[-1]
        self.assertTrue(cached.is_file())
        self.assertEqual(first['title'], 'PPLN 示意图')

    def test_serve_endpoint(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app

        cached = Path(web_images.cache_dir()) / ('a' * 20 + '.png')
        cached.write_bytes(_png_bytes())
        client = TestClient(app)
        ok = client.get(f'/api/web-images/{cached.name}')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers['content-type'], 'image/png')
        self.assertEqual(client.get('/api/web-images/..%2Fevil.png').status_code, 404)
        self.assertEqual(client.get('/api/web-images/nope.png').status_code, 404)

    def test_prompt_mentions_search_and_images(self):
        from paperwhale_server.services import rag

        prompt = rag._answer_prompt()
        self.assertIn('联网搜索能力', prompt)
        self.assertIn('配图', prompt)


if __name__ == '__main__':
    unittest.main()
