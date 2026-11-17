"""Browser acquisition retains actual proxy context without leaking it cross-origin."""
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path
from paperwhale_server.routers import webimport

class WebImportContextTests(unittest.TestCase):
    def test_same_origin_proxy_query_and_browser_user_agent(self):
        url='https://proxy.university.example/science/article/pii/S1/pdfft'
        with patch.object(webimport.pdf_download,'download',return_value=SimpleNamespace(path=Path('fixture.pdf'))) as download:
            webimport._download_pdf(url,'https://proxy.university.example/science/article/pii/S1?institution=42#pdf','session=private','Browser Agent')
        headers=download.call_args.kwargs['headers']
        self.assertEqual(headers['Referer'],'https://proxy.university.example/science/article/pii/S1?institution=42')
        self.assertEqual(headers['User-Agent'],'Browser Agent')
        self.assertEqual(headers['Cookie'],'session=private')

    def test_cross_origin_referrer_and_header_injection(self):
        with patch.object(webimport.pdf_download,'download',return_value=SimpleNamespace(path=Path('fixture.pdf'))) as download:
            webimport._download_pdf('https://cdn.example/main.pdf','https://proxy.example/article?ticket=SECRET','', 'fake\r\nInjected: value')
        headers=download.call_args.kwargs['headers']
        self.assertEqual(headers['Referer'],'https://proxy.example/')
        self.assertNotIn('User-Agent',headers)

if __name__=='__main__':unittest.main()
