"""Task polling uses only a temporary database and never starts workers."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from paperwhale_server import config, db
from paperwhale_server.routers.tasks import router
from paperwhale_server.services import batches


class TaskPollingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pw-task-poll-')
        root = Path(self.temp.name)
        self.paths = patch.multiple(config, DATA_DIR=root, DEFAULT_PDF_DIR=root/'pdfs',
                                    TMP_DIR=root/'tmp', DB_PATH=root/'test.db',
                                    SETTINGS_PATH=root/'settings.json', _cache=dict(config.DEFAULT_SETTINGS))
        self.paths.start()
        db.init_db()
        db.execute('INSERT INTO papers(id,title,created_at,updated_at) VALUES(?,?,?,?)',
                   ('paper', 'Test paper', db.now(), db.now()))
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.paths.stop()
        self.temp.cleanup()

    def batch(self, kind, state='queued'):
        bid = batches.create(kind, ['paper'])
        batches.finish(bid, 'paper', state)
        return bid

    def test_old_batches_remain_pollable_through_completion(self):
        old = [self.batch('analyze', 'running'), self.batch('pdf')]
        for _ in range(35):
            self.batch('cards', 'succeeded')
        initial = self.client.get('/api/tasks/batches').json()['items']
        self.assertTrue(set(old).issubset({b['batch_id'] for b in initial}))
        batches.finish(old[0], 'paper', 'succeeded')
        batches.finish(old[1], 'paper', 'failed', 'isolated error')
        result = self.client.get('/api/tasks/batches', params=[('batch_id', b) for b in old])
        self.assertEqual(result.status_code, 200)
        items = {b['batch_id']: b for b in result.json()['items']}
        self.assertEqual(items[old[0]]['succeeded'], 1)
        self.assertEqual(items[old[1]]['failed'], 1)
        self.assertEqual(items[old[1]]['errors'][0]['error'], 'isolated error')
        self.assertEqual(items[old[0]]['failed'], 0)
        self.assertEqual(len(items), 32)

    def test_latest_status_contract_and_history_bound_are_preserved(self):
        old = self.batch('analyze', 'succeeded')
        for _ in range(31):
            latest = self.batch('analyze', 'succeeded')
        self.assertEqual(batches.current('analyze'), latest)
        self.assertEqual(batches.status(latest)['total'], 1)
        result = self.client.get('/api/tasks/batches').json()['items']
        self.assertEqual(len(result), 30)
        self.assertNotIn(old, {b['batch_id'] for b in result})
        result = self.client.get('/api/tasks/batches', params={'batch_id': "unknown' OR 1=1"})
        self.assertEqual(len(result.json()['items']), 30)


if __name__ == '__main__':
    unittest.main()
