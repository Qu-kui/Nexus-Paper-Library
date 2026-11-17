"""用 Edge headless + CDP 检查页面运行状态与 JS 错误。

用法：python tests/cdp_check.py <url> [wait_ms]
"""
from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9223


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8766/"
    wait_ms = int(sys.argv[2]) if len(sys.argv) > 2 else 9000
    profile = Path(tempfile.mkdtemp(prefix="edge-cdp-"))
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--window-size=1500,950", url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1) as resp:
                    targets = json.load(resp)
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not ws_url:
            print("FAIL: 无法连接 CDP")
            return 1

        ws = websocket.create_connection(ws_url, timeout=5)
        counter = {"id": 0}

        def send(method, params=None):
            counter["id"] += 1
            ws.send(json.dumps({"id": counter["id"], "method": method, "params": params or {}}))

        for method in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
            send(method)
        pre_js = sys.argv[4] if len(sys.argv) > 4 else ""
        time.sleep(3.0)
        if pre_js:
            counter["id"] += 1
            ws.send(json.dumps({"id": counter["id"], "method": "Runtime.evaluate",
                                "params": {"expression": pre_js, "returnByValue": True}}))
            time.sleep(2.0)
        time.sleep(max(1.0, wait_ms / 1000 - 5))
        ws.settimeout(0.5)
        events = []
        while True:
            try:
                events.append(json.loads(ws.recv()))
            except Exception:
                break

        errors = []
        logs = []
        failed = []
        for event in events:
            method = event.get("method")
            params = event.get("params", {})
            if method == "Runtime.exceptionThrown":
                errors.append(params.get("exceptionDetails", {}).get("text", "") + " " +
                              str(params.get("exceptionDetails", {}).get("exception", {}).get("description", ""))[:400])
            elif method == "Log.entryAdded":
                entry = params.get("entry", {})
                if entry.get("level") in ("error", "warning"):
                    errors.append(f"{entry.get('level')}: {entry.get('text')} {entry.get('url','')}")
            elif method == "Runtime.consoleAPICalled":
                args = " ".join(str(a.get("value", a.get("description", "")))[:200]
                                for a in params.get("args", []))
                logs.append(f"{params.get('type')}: {args}")
            elif method == "Network.loadingFailed":
                failed.append(f"{params.get('errorText')} {params.get('blockedReason','')}")

        # 查询页面状态
        counter["id"] += 1
        probe = {
            "id": counter["id"], "method": "Runtime.evaluate",
            "params": {"expression": """JSON.stringify({
                pages: document.querySelectorAll('.pdf-page').length,
                canvases: document.querySelectorAll('.pdf-page canvas').length,
                textSpans: document.querySelectorAll('.textLayer span').length,
                pageTotal: document.getElementById('reader-page-total')?.textContent,
                readerHidden: document.getElementById('reader')?.classList.contains('hidden'),
                papers: document.querySelectorAll('.paper-card').length,
                setupBanner: !document.getElementById('setup-banner')?.classList.contains('hidden'),
                bodyText: document.body.innerText.slice(0, 200)
            })""", "returnByValue": True},
        }
        ws.send(json.dumps(probe))
        time.sleep(1.0)
        state = None
        ws.settimeout(3)
        while True:
            try:
                msg = json.loads(ws.recv())
            except Exception:
                break
            if msg.get("id") == counter["id"]:
                state = msg.get("result", {}).get("result", {}).get("value")
                break

        print("=== STATE ===")
        print(state)
        print("=== ERRORS (%d) ===" % len(errors))
        for line in errors[:25]:
            print(" -", line)
        print("=== FAILED REQUESTS (%d) ===" % len(failed))
        for line in failed[:15]:
            print(" -", line)
        print("=== CONSOLE (tail) ===")
        for line in logs[-15:]:
            print(" -", line)

        shot_path = sys.argv[3] if len(sys.argv) > 3 else ""
        if shot_path:
            import base64

            counter["id"] += 1
            ws.send(json.dumps({"id": counter["id"], "method": "Page.captureScreenshot",
                                "params": {"format": "png", "captureBeyondViewport": False}}))
            ws.settimeout(10)
            while True:
                msg = json.loads(ws.recv())
                if msg.get("id") == counter["id"]:
                    data = msg.get("result", {}).get("data")
                    if data:
                        Path(shot_path).write_bytes(base64.b64decode(data))
                        print("=== SCREENSHOT ===", shot_path)
                    break
        ws.close()
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
