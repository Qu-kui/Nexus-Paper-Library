"""Browser PDF reports use a temporary library and mocked acquisition only."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from paperwhale_server import config, db
from paperwhale_server.main import app
from paperwhale_server.routers import webimport
from paperwhale_server.services import pdf_download, pdf_fetch, pdf_trace


class BrowserPdfReportTests(unittest.TestCase):
    def test_current_pdf_availability_is_separate_from_previous_backend_failure(self):
        self.manager._save('p','failed',pdf_trace.Trace(),code='access_denied',error='previous backend failure')
        path=Path(self.temp.name)/'attached.pdf'
        path.write_bytes((Path(__file__).parent/'fixtures/attention.pdf').read_bytes())
        db.update_paper('p',{'file_path':str(path)})
        self.manager.record_browser_attempts('p',[self.attempt(code='ok',status=200)],has_pdf=True)
        report=self.client.get('/api/pdf-fetch/report/p').json()
        self.assertTrue(report['pdf_available']);self.assertEqual(report['state'],'failed')
        self.assertEqual(report['error'],'previous backend failure')
        path.unlink()
        self.assertFalse(self.client.get('/api/pdf-fetch/report/p').json()['pdf_available'])

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pw-browser-report-')
        root=Path(self.temp.name)
        self.settings=patch.multiple(config,DATA_DIR=root,DEFAULT_PDF_DIR=root/'pdfs',TMP_DIR=root/'tmp',
            DB_PATH=root/'test.db',SETTINGS_PATH=root/'settings.json',_cache=dict(config.DEFAULT_SETTINGS))
        self.settings.start()
        db.init_db()
        db.execute('INSERT INTO papers(id,title,doi) VALUES(?,?,?)',('p','Report fixture','10.5555/report'))
        self.manager=pdf_fetch.PdfFetchManager()
        self.manager_patch=patch.object(pdf_fetch,'manager',self.manager)
        self.manager_patch.start()
        self.client=TestClient(app)

    def tearDown(self):
        self.client.close()
        self.manager_patch.stop()
        self.settings.stop()
        self.temp.cleanup()

    def attempt(self,**values):
        return {'url':'https://user:PASS@example.org/article?token=SECRET#private',
                'route':'browser-page','code':'access_denied','status':403,**values}

    def test_sanitization_bounds_and_ignores_untrusted_fields(self):
        raw=self.attempt(cookies='SECRET',message='SECRET',error='SECRET',profile='SECRET')
        attempts=pdf_fetch.sanitize_browser_attempts([raw]*30)
        self.assertEqual(len(attempts),24)
        self.assertEqual(attempts[0]['url'],'https://example.org/article')
        self.assertNotIn('SECRET',json.dumps(attempts))
        self.assertEqual(set(attempts[0]),{'url','route','code','status','message'})
        bad=[self.attempt(code='SECRET'),self.attempt(code=['bad']),self.attempt(route='SECRET'),
             self.attempt(route='browser-validation')]
        self.assertEqual(pdf_fetch.sanitize_browser_attempts(bad),[])
        for status in (-1,600,True,'403',None):
            self.assertIsNone(pdf_fetch.sanitize_browser_attempts([self.attempt(status=status)])[0]['status'])
        for url in ('javascript:SECRET','https://example.org/\nSECRET','https://example.org:bad/x'):
            self.assertEqual(pdf_fetch.sanitize_browser_attempts([self.attempt(url=url)])[0]['url'],'')

    def test_import_and_get_report_expose_redacted_browser_attempts(self):
        with patch.object(self.manager,'start',return_value=1):
            result=self.client.post('/api/import/web',json={'title':'Report fixture','doi':'10.5555/report',
                'pdf_attempts':[self.attempt()]}).json()
        self.assertEqual(result['pdf'],'queued')
        report=self.client.get('/api/pdf-fetch/report/p').json()
        self.assertEqual(report['browser_attempts'],result['browser_attempts'])
        self.assertEqual(report['browser_attempts'][0]['url'],'https://example.org/article')

    def test_worker_saves_retain_latest_browser_report_at_sql_write(self):
        self.manager.record_browser_attempts('p',[self.attempt()])
        execute=db.execute
        injected=False
        def interleave(sql,params=()):
            nonlocal injected
            if 'state=excluded.state' in sql and not injected:
                injected=True
                self.manager.record_browser_attempts('p',[self.attempt(code='timeout')])
            return execute(sql,params)
        with patch.object(db,'execute',side_effect=interleave):
            self.manager._save('p','running',pdf_trace.Trace())
        self.assertTrue(injected)
        self.assertEqual(self.manager.report('p')['browser_attempts'][0]['code'],'timeout')
        self.manager._save('p','failed',pdf_trace.Trace(),code='no_candidate')
        report=self.manager.report('p')
        self.assertEqual(report['browser_attempts'][0]['code'],'timeout')
        self.assertEqual(report['code'],'no_candidate')

    def test_only_explicit_new_attempt_replaces_browser_trace_and_preserves_backend(self):
        trace=pdf_trace.Trace()
        trace.add('browser-session',code='no_session',browser='Edge',profile='Default')
        self.manager._save('p','failed',trace,code='no_candidate')
        self.manager.record_browser_attempts('p',[self.attempt()])
        for payload in ({'download_pdf':False},{'download_pdf':False,'pdf_attempts':[]}):
            result=self.client.post('/api/import/web',json={'title':'Report fixture',**payload})
            self.assertEqual(result.status_code,200)
        report=self.manager.report('p')
        self.assertEqual(report['browser_attempts'][0]['code'],'access_denied')
        self.manager.record_browser_attempts('p',[self.attempt(code='timeout')])
        report=self.manager.report('p')
        self.assertEqual(report['state'],'failed')
        self.assertEqual(report['code'],'no_candidate')
        self.assertEqual(report['attempts'],trace.attempts)
        self.assertEqual(report['browser_attempts'][0]['code'],'timeout')

    def test_browser_write_retains_worker_report_committed_immediately_before_it(self):
        self.manager.record_browser_attempts('p',[self.attempt()])
        trace=pdf_trace.Trace()
        trace.add('browser-session',code='no_session',browser='Edge',profile='Default')
        execute=db.execute
        injected=False
        def interleave(sql,params=()):
            nonlocal injected
            if 'report=json_set(pdf_fetch_jobs.report' in sql and not injected:
                injected=True
                self.manager._save('p','failed',trace,code='no_candidate')
            return execute(sql,params)
        with patch.object(db,'execute',side_effect=interleave):
            self.manager.record_browser_attempts('p',[self.attempt(code='timeout')])
        report=self.manager.report('p')
        self.assertTrue(injected)
        self.assertEqual(report['attempts'],trace.attempts)
        self.assertEqual(report['code'],'no_candidate')
        self.assertEqual(report['state'],'failed')
        self.assertEqual(report['browser_attempts'][0]['code'],'timeout')

    def test_invalid_base64_returns_warning_and_keeps_existing_pdf(self):
        existing=Path(self.temp.name)/'existing.pdf'
        existing.write_bytes(b'original contents')
        db.update_paper('p',{'file_path':str(existing)})
        result=self.client.post('/api/import/web',json={'title':'Report fixture','pdf_base64':'not base64!'}).json()
        self.assertEqual(result['pdf'],'attached')
        self.assertEqual(result['pdf_warning'],pdf_trace.MESSAGES['invalid_pdf'])
        self.assertEqual(existing.read_bytes(),b'original contents')
        self.assertEqual(self.manager.report('p')['state'],'skipped')
        self.assertEqual(result['browser_attempts'][-1]['code'],'invalid_pdf')

    def test_identity_mismatch_keeps_backend_queue_and_reports_browser_validation(self):
        path=Path(self.temp.name)/'wrong.pdf'
        path.write_bytes(b'wrong pdf')
        with patch.object(webimport,'_pdf_from_base64',return_value=path), \
             patch.object(pdf_download,'verify_identity',side_effect=pdf_download.PdfDownloadError('identity_mismatch')), \
             patch.object(self.manager,'start',return_value=1) as start:
            result=self.client.post('/api/import/web',json={'title':'Report fixture','pdf_base64':'mock',
                'pdf_attempts':[self.attempt(code='ok',status=200)]}).json()
        start.assert_called_once_with(['p'])
        self.assertFalse(path.exists())
        self.assertEqual(result['pdf'],'queued')
        self.assertEqual(result['pdf_warning'],pdf_trace.MESSAGES['identity_mismatch'])
        self.assertEqual(result['browser_attempts'][-1]['code'],'identity_mismatch')
        self.assertEqual(self.manager.report('p')['browser_attempts'],result['browser_attempts'])

    def test_new_report_without_job_is_safe_and_empty_client_does_not_create_job(self):
        self.assertEqual(self.manager.report('p')['browser_attempts'],[])
        self.manager.record_browser_attempts('p',[])
        self.assertIsNone(db.query_one('SELECT * FROM pdf_fetch_jobs WHERE paper_id=?',('p',)))
        self.manager.record_browser_attempts('p',[self.attempt()],has_pdf=False)
        self.assertEqual(self.manager.report('p')['state'],'failed')


if __name__=='__main__':
    unittest.main()
