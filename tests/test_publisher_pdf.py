"""Publisher resolver regressions; synthetic pages, no browser credentials."""
import json
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
import httpx
from paperwhale_server import config
from paperwhale_server.services import pdf_download
from paperwhale_server.services.pdf_download import landing_links

BASE='https://www.sciencedirect.com/science/article/pii/S0143816619313120'

class PublisherPdfTests(unittest.TestCase):
    def test_elsevier_linkinghub_refresh_without_pdf_suffix(self):
        start='https://linkinghub.elsevier.com/retrieve/pii/S0143816619313120'
        hop='/retrieve/articleSelectSinglePerm?Redirect=https%3A%2F%2Fwww.sciencedirect.com%2Fscience%2Farticle%2Fpii%2FS0143816619313120&key=TEST'
        html='<meta HTTP-EQUIV="REFRESH" content="2; url=\''+hop.replace('&','&amp;')+'\'"/>'
        self.assertIn('https://linkinghub.elsevier.com'+hop,landing_links(html,start))

    def test_doi_hub_refresh_does_not_spend_article_depth_budget(self):
        seen=[]
        def serve(request):
            seen.append(request)
            if request.url.host=='doi.org':return httpx.Response(302,headers={'location':'https://linkinghub.elsevier.com/retrieve/pii/S0143816619313120'})
            if request.url.host=='linkinghub.elsevier.com':
                if request.url.path.startswith('/retrieve/pii/'):
                    return httpx.Response(200,text='<meta http-equiv="refresh" content="2;url=/retrieve/articleSelectSinglePerm?key=TEST">')
                return httpx.Response(302,headers={'location':BASE})
            if request.url.path.endswith('/pdfft'):return httpx.Response(200,text='<meta http-equiv="refresh" content="0;url=https://cdn.example/main.pdf">')
            if request.url.host=='cdn.example':return httpx.Response(200,content=(Path(__file__).parent/'fixtures/attention.pdf').read_bytes())
            return httpx.Response(200,text='<a id="pdfLink" href="'+BASE+'/pdfft">PDF</a>')
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'TMP_DIR',Path(folder)),patch.object(config,'ensure_dirs'),httpx.Client(transport=httpx.MockTransport(serve)) as client:
            visited_cookie_hosts=[]
            def cookies(url):
                host=httpx.URL(url).host;visited_cookie_hosts.append(host)
                return 'publisher_session=TEST' if host=='www.sciencedirect.com' else ''
            result=pdf_download.download('https://doi.org/10.1016/j.optlaseng.2020.106055',client=client,cookie_provider=cookies)
            self.assertTrue(result.path.is_file());self.assertEqual(result.url,'https://cdn.example/main.pdf')
            self.assertIn('www.sciencedirect.com',visited_cookie_hosts)
            self.assertEqual(next(req for req in seen if req.url.host=='www.sciencedirect.com').headers.get('cookie'),'publisher_session=TEST')
            self.assertNotIn('publisher_session=TEST',seen[-1].headers.get('cookie',''))

    def test_hub_refresh_cycle_is_bounded_and_non_http_not_followed(self):
        base='https://linkinghub.elsevier.com/retrieve/pii/S0143816619313120'
        self.assertEqual(landing_links('<meta http-equiv="refresh" content="0;url=javascript:alert(1)">',base),[])
        self.assertEqual(landing_links('<meta http-equiv="refresh" content="0;url=https://example.org/next">','https://example.org/page'),[])
        seen=[]
        def serve(request):
            seen.append(request)
            return httpx.Response(200,text='<meta http-equiv="refresh" content="0;url='+base+'">')
        with tempfile.TemporaryDirectory() as folder,patch.object(config,'TMP_DIR',Path(folder)),patch.object(config,'ensure_dirs'),httpx.Client(transport=httpx.MockTransport(serve)) as client:
            with self.assertRaises(pdf_download.PdfDownloadError) as exc:pdf_download.download(base,client=client)
            self.assertEqual(exc.exception.code,'redirect_limit');self.assertEqual(len(seen),1)

    def test_proxy_canonical_same_pii_keeps_proxy_origin_and_signed_json(self):
        proxy='https://proxy.university.example/science/article/pii/S0143816619313120'
        payload={'article':{'pdfDownload':{'urlMetadata':{'path':'science/article/pii','pii':'S0143816619313120','pdfExtension':'/pdfft','queryParams':{'md5':'SIGNED','pid':'main.pdf'}}}}}
        html='<script type="application/json">'+json.dumps(payload)+'</script><link rel="canonical" href="'+BASE+'">'
        rows=landing_links(html,proxy)
        self.assertTrue(any('md5=SIGNED' in row for row in rows));self.assertTrue(all(row.startswith('https://proxy.university.example/') for row in rows))
        self.assertEqual(landing_links(html,proxy.replace('S0143816619313120','S999')),[])

    def test_supplementary_links_follow_main_pdf(self):
        rows=landing_links('<a href="/mmc1.pdf">Supplementary information</a><a href="/main.pdf">Full text PDF</a>',BASE)
        self.assertLess(rows.index('https://www.sciencedirect.com/main.pdf'),rows.index('https://www.sciencedirect.com/mmc1.pdf'))
        rows=landing_links('<a href="/attachment.pdf">Supporting Information</a><a href="/main.pdf">Full text PDF</a>',BASE)
        self.assertLess(rows.index('https://www.sciencedirect.com/main.pdf'),rows.index('https://www.sciencedirect.com/attachment.pdf'))

    def test_invalid_structured_download_parameters_do_not_block_fallback(self):
        for query in (None,[], 'invalid',7):
            payload={'article':{'pdfDownload':{'urlMetadata':{'path':'science/article/pii','pii':'S0143816619313120','pdfExtension':'/pdfft','queryParams':query}}}}
            with self.subTest(query=query):
                rows=landing_links('<script type="application/json">'+json.dumps(payload)+'</script>',BASE)
                self.assertEqual(rows,[BASE+'/pdfft?download=true'])
    def test_article_intermediate_pdf_chain_and_referer(self):
        requests=[]
        def serve(request):
            requests.append(request)
            if request.url.path.endswith('/pdfft'):
                return httpx.Response(200,text='<meta http-equiv="refresh" content="0;URL=https://cdn.example/full.pdf">')
            if request.url.host=='cdn.example':
                return httpx.Response(200,content=(Path(__file__).parent/'fixtures/attention.pdf').read_bytes())
            return httpx.Response(200,text='<a id="pdfLink" href="'+BASE+'/pdfft">PDF</a>')
        with tempfile.TemporaryDirectory() as folder, patch.object(config,'TMP_DIR',Path(folder)),patch.object(config,'ensure_dirs'),httpx.Client(transport=httpx.MockTransport(serve)) as client:
            result=pdf_download.download(BASE,client=client)
            self.assertEqual(result.url,'https://cdn.example/full.pdf')
            self.assertEqual(requests[-1].headers['referer'],'https://www.sciencedirect.com/')
            self.assertTrue(result.path.is_file())
    def test_intermediate_refresh_and_redirect_anchor(self):
        self.assertIn('https://pdf.sciencedirectassets.com/file.pdf?token=x',landing_links(
            '<meta HTTP-EQUIV="Refresh" CONTENT="0;URL=https://pdf.sciencedirectassets.com/file.pdf?token=x">',BASE+'/pdfft'))
        self.assertIn('https://cdn.example/download?id=1',landing_links(
            '<div id="redirect-message"><a href="https://cdn.example/download?id=1">Continue</a></div>',BASE+'/pdfft'))

    def test_signed_json_precedes_unsigned_fallback(self):
        value={'article':{'pdfDownload':{'urlMetadata':{'path':'science/article/pii','pii':'S0143816619313120','pdfExtension':'/pdfft','queryParams':{'md5':'abc','pid':'1-s2.0-id-main.pdf'}}}}}
        rows=landing_links('<script type="application/json">'+json.dumps(value)+'</script>',BASE)
        self.assertIn('md5=abc',rows[0]);self.assertIn('pid=1-s2.0-id-main.pdf',rows[0])
        self.assertEqual(rows[-1],BASE+'/pdfft?download=true')

    def test_button_link_without_pdf_suffix_and_nonpublisher_no_guess(self):
        self.assertIn('https://www.sciencedirect.com/token/123',landing_links('<a id="pdfLink" href="/token/123">View article</a>',BASE))
        self.assertEqual(landing_links('<a href="#">PDF</a>','https://example.org/article'),[])
        self.assertEqual(landing_links('', 'https://www.sciencedirect.com.evil.example/science/article/pii/S0143816619313120'),[])

    def test_malformed_json_does_not_stop_other_candidates(self):
        rows=landing_links('<script type="application/json">{bad</script><a href="/paper.pdf">PDF</a>',BASE)
        self.assertIn('https://www.sciencedirect.com/paper.pdf',rows)

if __name__=='__main__':unittest.main()
