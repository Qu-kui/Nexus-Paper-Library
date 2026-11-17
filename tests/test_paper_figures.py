"""论文 PDF 配图：抽图、过滤、缓存与服务。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from PIL import Image

from paperwhale_server import config, db
from paperwhale_server.services import paper_figures


class PaperFigureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pw-paperfig-')
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
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('p','Paper','missing.pdf',?,?)",
                   (db.now(), db.now()))

    def tearDown(self):
        self.paths.stop()
        self.temp.cleanup()

    def test_missing_paper_or_file_returns_empty(self):
        self.assertEqual(paper_figures.extract('nope', [1]), [])
        self.assertEqual(paper_figures.extract('p', [1]), [])

    def test_save_image_filters_and_caches(self):
        self.assertIsNone(paper_figures._save_image(Image.new('RGB', (100, 80)), 'tiny'))
        self.assertIsNone(paper_figures._save_image(Image.new('RGB', (900, 100)), 'wide'))
        saved = paper_figures._save_image(Image.new('RGB', (900, 600)), 'ok')
        self.assertIsNotNone(saved)
        target = Path(paper_figures.cache_dir()) / saved['url'].rsplit('/', 1)[-1]
        self.assertTrue(target.is_file())
        self.assertEqual((saved['width'], saved['height']), (900, 600))

    def test_attach_paper_figures_appends_from_cited_pages(self):
        from paperwhale_server.services import rag

        result = {'sources': [{'paper_id': 'p', 'page': 3}, {'paper_id': 'p', 'page': 5}]}
        with patch.object(paper_figures, 'extract', return_value=[{'url': '/api/paper-figures/a.jpg'}]):
            rag._attach_paper_figures(result)
        self.assertEqual(result['web_images'][0]['url'], '/api/paper-figures/a.jpg')

    def test_serve_endpoint(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app

        name = 'b' * 20 + '.jpg'
        (Path(paper_figures.cache_dir()) / name).write_bytes(b'\xff\xd8\xff\xd9')
        client = TestClient(app)
        ok = client.get(f'/api/paper-figures/{name}')
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(ok.headers['content-type'], 'image/jpeg')
        self.assertEqual(client.get('/api/paper-figures/..%2Fevil.jpg').status_code, 404)


if __name__ == '__main__':
    unittest.main()
