"""Inspect a hidden native WebView2 Form icon with an isolated temporary profile."""
import ctypes
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import desktop
import webview
from PIL import Image

output = ROOT / 'pw-debug' / 'npl-2.7.57'
output.mkdir(parents=True, exist_ok=True)
desktop._set_windows_app_id()
window = webview.create_window(desktop.APP_TITLE, html='<html><body>Native icon probe</body></html>', hidden=True)
result = {}


def inspect():
    try:
        assert window.events.loaded.wait(20), 'Hidden WebView2 did not load'
        from System import Action
        from System.Drawing import Icon
        from System.Drawing.Imaging import ImageFormat

        def capture():
            try:
                window.native.Icon.ToBitmap().Save(str(output / 'native-icon.png'), ImageFormat.Png)
                Icon(desktop._window_icon_path()).ToBitmap().Save(str(output / 'expected-icon.png'), ImageFormat.Png)
                result['title'] = str(window.native.Text)
            except Exception as error:
                result['error'] = str(error)
        window.native.Invoke(Action(capture))
        assert 'error' not in result, result
        with Image.open(output / 'native-icon.png') as actual, Image.open(output / 'expected-icon.png') as expected:
            assert actual.size == expected.size
            assert actual.convert('RGBA').tobytes() == expected.convert('RGBA').tobytes(), 'Native Form still uses another icon'
            result['native_icon_matches_npl'] = True
            result['icon_size'] = list(actual.size)
        pointer = ctypes.c_void_p()
        getter = ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID
        getter.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        getter.restype = ctypes.c_long
        assert getter(ctypes.byref(pointer)) == 0
        try:
            result['app_id'] = ctypes.wstring_at(pointer.value)
        finally:
            free = ctypes.windll.ole32.CoTaskMemFree
            free.argtypes = [ctypes.c_void_p]
            free(pointer)
        assert result['app_id'] == desktop.APP_USER_MODEL_ID
        assert result['title'] == 'Nexus Paper Library'
    except Exception as error:
        result['error'] = str(error)
    finally:
        window.destroy()


with tempfile.TemporaryDirectory(prefix='npl-icon-profile-') as profile:
    webview.start(inspect, gui='edgechromium', private_mode=True, storage_path=profile, icon=desktop._window_icon_path())
(output / 'native-icon.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(result, ensure_ascii=False))
if 'error' in result or not result.get('native_icon_matches_npl'):
    raise SystemExit(1)
