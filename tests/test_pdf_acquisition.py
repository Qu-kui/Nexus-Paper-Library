"""PDF acquisition tests: temporary data, real PDF fixture, simulated HTTP only."""
from __future__ import annotations

import importlib
import json
import time
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient
from paperwhale_server import config, db
from paperwhale_server.main import app
from paperwhale_server.services import browser_cookies, discover, metadata, pdf_fetch

PDF = (Path(__file__).parent/'fixtures/attention.pdf').read_bytes()


class PdfAcquisitionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='pw-pdf-')
        root = Path(self.temp.name)
        self.settings = patch.multiple(config, DATA_DIR=root, DEFAULT_PDF_DIR=root/'pdfs', TMP_DIR=root/'tmp',
                                      DB_PATH=root/'test.db', SETTINGS_PATH=root/'settings.json',
                                      _cache={**config.DEFAULT_SETTINGS,'scholar_auto_sync':False,'pdf_browser_session_enabled':False})
        self.settings.start()
        db.init_db()
        db.execute('INSERT INTO papers(id,title,doi) VALUES(?,?,?)', ('p','Attention Is All You Need','10.5555/attention'))

    def tearDown(self):
        self.settings.stop()
        self.temp.cleanup()

    def download_module(self):
        try:
            return importlib.import_module('paperwhale_server.services.pdf_download')
        except ImportError:
            self.fail('Shared PDF resolver has not been implemented')

    def test_openalex_keeps_non_pdf_suffix_and_landing_candidates(self):
        payload = {'id':'https://openalex.org/W1','has_content':{'pdf':True},
                   'content_urls':{'pdf':'https://content.openalex.org/works/W1.pdf'},
                   'locations':[{'pdf_url':'https://repo.example/download?id=123',
                                 'landing_page_url':'https://repo.example/item/123','version':'acceptedVersion'}]}
        with patch.object(metadata, '_get', return_value=httpx.Response(200,json=payload)):
            result = metadata.openalex_by_doi('10.5555/attention')
        self.assertIn('https://repo.example/download?id=123', result['pdf_urls'])
        self.assertIn('https://repo.example/item/123', [c['url'] for c in result['candidates']])
        self.assertEqual(result['content_url'], 'https://content.openalex.org/works/W1.pdf')

    def test_resolver_follows_landing_page_and_relative_download_link(self):
        module = self.download_module()
        def serve(request):
            if request.url.path == '/item/1':
                return httpx.Response(200,text='<html><a href="../download?id=1">Download PDF</a></html>',headers={'content-type':'text/html'})
            return httpx.Response(200,content=PDF,headers={'content-type':'application/octet-stream'})
        with httpx.Client(transport=httpx.MockTransport(serve)) as client:
            result = module.download('https://repo.example/item/1',client=client)
        self.assertTrue(result.path.is_file())
        self.assertEqual(result.url, 'https://repo.example/download?id=1')

    def test_redirect_uses_destination_cookies_and_does_not_leak_auth(self):
        module = self.download_module()
        seen = []
        def serve(request):
            seen.append((str(request.url),request.headers.get('cookie',''),request.headers.get('authorization','')))
            if request.url.host == 'doi.org':
                return httpx.Response(302,headers={'location':'https://publisher.example/article'})
            return httpx.Response(200,content=PDF)
        provider = lambda url: 'publisher_session=OK' if httpx.URL(url).host=='publisher.example' else ''
        with httpx.Client(transport=httpx.MockTransport(serve)) as client:
            module.download('https://doi.org/10.5555/a',client=client,cookie_provider=provider,
                            headers={'Authorization':'Bearer SECRET','Cookie':'doi_only=TOKEN'})
        self.assertNotIn('doi_only', seen[-1][1])
        self.assertEqual(seen[-1][1], 'publisher_session=OK')
        self.assertEqual(seen[-1][2], '')

    def test_invalid_pdf_is_rejected_and_partial_file_removed(self):
        module = self.download_module()
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,content=b'%PDF-'+b'x'*2000))) as client:
            with self.assertRaises(module.PdfDownloadError) as err:
                module.download('https://example.org/file', client=client)
        self.assertEqual(err.exception.code, 'invalid_pdf')
        self.assertEqual(list(config.TMP_DIR.glob('*')), [])

    def test_oversized_response_is_stopped(self):
        module = self.download_module()
        with httpx.Client(transport=httpx.MockTransport(lambda req: httpx.Response(200,content=PDF))) as client:
            with self.assertRaises(module.PdfDownloadError) as err:
                module.download('https://example.org/file',client=client,max_bytes=1000)
        self.assertEqual(err.exception.code, 'too_large')

    def test_transient_error_retries_but_403_does_not(self):
        module = self.download_module()
        count = []
        def serve(request):
            count.append(1)
            return httpx.Response(503) if len(count)==1 else httpx.Response(200,content=PDF)
        with httpx.Client(transport=httpx.MockTransport(serve)) as client, patch.object(module.time,'sleep'):
            module.download('https://example.org/a',client=client)
        self.assertEqual(len(count),2)
        count.clear()
        def forbidden(request):
            count.append(1)
            return httpx.Response(403,text='Access denied')
        with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
            with self.assertRaises(module.PdfDownloadError) as err:
                module.download('https://example.org/a',client=client)
        self.assertEqual(len(count),1)
        self.assertEqual(err.exception.code,'access_denied')

    def test_host_only_and_cookie_path_boundary(self):
        rows=[{'domain':'publisher.example','host':'publisher.example','name':'host','value':'yes','path':'/article','secure':True}]
        with patch.object(browser_cookies, '_cookies', return_value=rows):
            self.assertEqual(browser_cookies.cookie_header('https://sub.publisher.example/article'), '')
            self.assertEqual(browser_cookies.cookie_header('https://publisher.example/articles'), '')
            self.assertIn('host=yes', browser_cookies.cookie_header('https://publisher.example/article/1'))

    def test_failure_report_does_not_guess_paywall_and_redacts_query(self):
        module = self.download_module()
        with httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(429,headers={'retry-after':'120'}))) as client:
            with self.assertRaises(module.PdfDownloadError) as err:
                module.download('https://example.org/file?api_key=SECRET',client=client)
        self.assertEqual(err.exception.code,'rate_limited')
        self.assertNotIn('SECRET', str(err.exception))

    def test_report_api_exists_without_running_fetch(self):
        client=TestClient(app,raise_server_exceptions=False)
        try:
            res=client.get('/api/pdf-fetch/report/p')
        finally:
            client.close()
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(res.json()['state'],'not_attempted')

    def test_unrelated_pdf_identity_is_rejected(self):
        module=self.download_module()
        path=config.DATA_DIR/'check.pdf'
        path.write_bytes(PDF)
        wrong={'title':'Enzymatic digestion in marine bacteria','doi':'10.0000/wrong','authors':'Nobody'}
        with self.assertRaises(module.PdfDownloadError) as err:
            module.verify_identity(path,wrong)
        self.assertEqual(err.exception.code,'identity_mismatch')

    def test_hosted_pdf_requires_opt_in_and_key(self):
        result={'content_url':'https://content.openalex.org/works/W1.pdf','candidates':[]}
        with patch.dict('os.environ',{'OPENALEX_API_KEY':''}):
            self.assertEqual(metadata.openalex_candidates(result),[])
            config.update_settings({'openalex_api_key':'SECRET','openalex_content_enabled':True})
            self.assertEqual(metadata.openalex_candidates(result)[0]['source'],'openalex-content')
        self.assertEqual(metadata.openalex_content_headers('https://other.example/works/W1.pdf'),{})

    def test_manager_archives_hosted_pdf_and_retains_safe_provenance(self):
        module=self.download_module()
        config.update_settings({'openalex_api_key':'VERY_SECRET_KEY','openalex_content_enabled':True})
        seen=[]
        real_client=httpx.Client
        def serve(request):
            seen.append(request)
            return httpx.Response(200,content=PDF)
        def client(**kwargs):
            return real_client(transport=httpx.MockTransport(serve),**kwargs)
        result={'content_url':'https://content.openalex.org/works/W1.pdf','candidates':[]}
        manager=pdf_fetch.PdfFetchManager()
        with patch.object(metadata,'openalex_by_doi',return_value=result), patch.object(module.httpx,'Client',side_effect=client):
            response=manager.fetch_one('p')
        self.assertTrue(response['ok'],response)
        self.assertEqual(seen[0].headers['authorization'],'Bearer VERY_SECRET_KEY')
        paper=db.get_paper('p')
        self.assertTrue(Path(paper['file_path']).is_file())
        self.assertEqual(json.loads(paper['pdf_acquisition'])['source'],'openalex-content')
        report=pdf_fetch.PdfFetchManager().report('p')
        self.assertEqual(report['state'],'succeeded')
        self.assertNotIn('VERY_SECRET_KEY',json.dumps(report))
        self.assertTrue(report['attempts'])

    def test_report_explains_actual_failure_after_manager_restart(self):
        from paperwhale_server.services import pdf_trace
        def timeout(*args):
            pdf_trace.record('openalex','https://example.org/a?token=SECRET',code='timeout')
            return None
        with patch.object(pdf_fetch,'_find_doi_pdf',side_effect=timeout), patch.object(pdf_fetch,'_search_arxiv_by_title',return_value=None),patch.object(pdf_fetch,'_browser_session_pdf',return_value=None):
            response=pdf_fetch.PdfFetchManager().fetch_one('p')
        self.assertEqual(response['code'],'timeout')
        self.assertNotIn('订阅',response['error'])
        report=pdf_fetch.PdfFetchManager().report('p')
        self.assertEqual(report['state'],'failed')
        self.assertNotIn('SECRET',json.dumps(report))

    def test_resume_recovers_only_unfinished_jobs(self):
        db.execute("INSERT INTO papers(id,title) VALUES('q','other')")
        db.execute("INSERT INTO pdf_fetch_jobs(paper_id,state) VALUES('p','running')")
        db.execute("INSERT INTO pdf_fetch_jobs(paper_id,state) VALUES('q','failed')")
        manager=pdf_fetch.PdfFetchManager()
        with patch.object(manager,'_ensure_workers'):
            queued=manager.resume()
        self.assertEqual(queued,1)
        self.assertEqual(manager._queue.get_nowait(),'p')
        self.assertEqual(manager.report('p')['state'],'queued')
        self.assertEqual(manager.report('q')['state'],'failed')

    def test_cookie_profiles_are_not_merged(self):
        def load(browser,profile):
            return [{'domain':'publisher.example','host':'.publisher.example','path':'/', 'secure':True,
                     'name':'session','value':browser+'_'+profile}]
        with patch.dict(browser_cookies._CACHE,{'loaded':False,'cookies':[],'at':0,'selection':None},clear=True),patch.object(browser_cookies,'_load_cookies',side_effect=load),patch.object(browser_cookies,'_rookie_cookies',return_value=[]):
            one=browser_cookies.cookie_header('https://publisher.example/a',browser='Edge',profile='Default')
            two=browser_cookies.cookie_header('https://publisher.example/a',browser='Chrome',profile='Profile 2')
        self.assertEqual(one,'session=Edge_Default')
        self.assertEqual(two,'session=Chrome_Profile 2')

    def test_server_rotated_cookie_takes_precedence_over_cached_cookie(self):
        module=self.download_module()
        seen=[]
        def serve(request):
            seen.append(request.headers.get('cookie',''))
            if request.url.path=='/start':
                return httpx.Response(302,headers={'location':'/file','set-cookie':'session=FRESH; Path=/; Secure'})
            return httpx.Response(200,content=PDF)
        with httpx.Client(transport=httpx.MockTransport(serve)) as client:
            module.download('https://publisher.example/start',client=client,cookie_provider=lambda url:'session=OLD')
        self.assertIn('session=FRESH',seen[-1])
        self.assertNotIn('session=OLD',seen[-1])

    def test_openalex_setting_does_not_expose_saved_key(self):
        client=TestClient(app)
        try:
            res=client.put('/api/settings',json={'openalex_api_key':'SECRET','openalex_content_enabled':True})
            self.assertEqual(res.status_code,200,res.text)
            self.assertEqual(res.json()['openalex_api_key'],'')
            self.assertTrue(res.json()['openalex_api_key_configured'])
        finally:
            client.close()

    def test_metadata_reads_only_the_first_two_pages(self):
        from paperwhale_server.services import pdftext
        original=pdftext._read_page_text
        read=[]
        def page(document,index):
            read.append(index)
            return original(document,index)
        with patch.object(pdftext,'_read_page_text',side_effect=page):
            metadata.extract_pdf_metadata(Path(__file__).parent/'fixtures/attention.pdf')
        self.assertEqual(read,[0,1])

    def test_fetch_does_not_replace_pdf_attached_while_request_was_running(self):
        existing=config.DATA_DIR/'manual.pdf'
        existing.write_bytes(PDF)
        fetched=config.DATA_DIR/'download.pdf'
        fetched.write_bytes(PDF)
        def acquire(*args):
            db.update_paper('p',{'file_path':str(existing)})
            return fetched
        with patch.object(pdf_fetch,'_find_doi_pdf',side_effect=acquire),patch.object(pdf_fetch.ingest,'attach_pdf') as attach:
            result=pdf_fetch.PdfFetchManager().fetch_one('p')
        self.assertTrue(result['skipped'])
        attach.assert_not_called()
        self.assertEqual(Path(db.get_paper('p')['file_path']),existing)

    def test_base64_import_rejects_a_fake_pdf(self):
        import base64
        from paperwhale_server.routers.webimport import _pdf_from_base64
        payload=base64.b64encode(b'%PDF-'+b'x'*2000).decode()
        self.assertIsNone(_pdf_from_base64(payload))


if __name__=='__main__':
    unittest.main()
