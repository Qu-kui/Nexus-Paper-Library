"""Window identity regression without starting servers or touching user data."""
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import desktop


class DesktopIdentityTests(unittest.TestCase):
    def test_startup_passes_npl_ico_to_native_window(self):
        window = MagicMock()
        webview = SimpleNamespace(create_window=MagicMock(return_value=window), start=MagicMock())
        server = MagicMock()
        uvicorn = SimpleNamespace(Server=MagicMock(return_value=server), Config=MagicMock())
        config = SimpleNamespace(ensure_dirs=MagicMock(), DATA_DIR=Path('unused'))
        modules = {'webview': webview, 'uvicorn': uvicorn,
                   'paperwhale_server': SimpleNamespace(config=config),
                   'paperwhale_server.main': SimpleNamespace(app=object())}
        with patch.dict(sys.modules, modules), patch.object(desktop, '_setup_logging'), \
             patch.object(desktop, '_wait_ready', return_value=True), \
             patch.object(desktop, '_create_tray', return_value=None), \
             patch.object(desktop, '_pick_port', return_value=1), \
             patch.object(desktop.threading, 'Thread') as thread, \
             patch.object(desktop.logging, 'getLogger') as get_logger, \
             patch.object(desktop, '_set_windows_app_id') as identity:
            self.assertEqual(desktop.main(), 0)
            self.assertEqual(desktop.main(), 0)
        self.assertEqual(identity.call_count, 2)
        self.assertEqual(thread.call_count, 2)
        launch_urls = [call.args[1] for call in webview.create_window.call_args_list]
        tokens = []
        for url in launch_urls:
            parsed = urlsplit(url)
            self.assertEqual((parsed.scheme, parsed.netloc, parsed.path), ('http', '127.0.0.1:1', '/'))
            token = parse_qs(parsed.query)['startup'][0]
            self.assertRegex(token, r'^[a-f0-9]{32}$')
            tokens.append(token)
        self.assertNotEqual(tokens[0], tokens[1])
        get_logger.return_value.info.assert_any_call(
            'server ready at %s (data: %s)', 'http://127.0.0.1:1', config.DATA_DIR)
        self.assertEqual(webview.create_window.call_args.args[0], 'Nexus Paper Library')
        self.assertEqual(Path(webview.start.call_args.kwargs['icon']).name, 'npl.ico')
        self.assertTrue(Path(webview.start.call_args.kwargs['icon']).is_file())

    def test_windows_identity_uses_dedicated_npl_id(self):
        setter = MagicMock(return_value=0)
        with patch.object(desktop.sys, 'platform', 'win32'), \
             patch.object(desktop.ctypes.windll.shell32, 'SetCurrentProcessExplicitAppUserModelID', setter):
            desktop._set_windows_app_id()
        setter.assert_called_once_with('NexusPaperLibrary.Desktop')


if __name__ == '__main__':
    unittest.main()
