"""用 Edge headless 在真实论文页面上测试浏览器扩展的元数据提取函数。"""
from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websocket

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
PORT = 9224
ROOT = Path(__file__).resolve().parents[1]
POPUP = ROOT / "extension" / "popup.js"


def extractor_source() -> str:
    text = POPUP.read_text(encoding="utf-8")
    start = text.index("async function extractPageMetadata(")
    end = text.index("\nasync function probeBase(")
    return text[start:end]


class CDP:
    def __init__(self) -> None:
        self.ws = websocket.create_connection(
            json.loads(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/version", timeout=5).read())["webSocketDebuggerUrl"]
            if False
            else self._page_ws(),
            timeout=10,
        )
        self.seq = 0

    def _page_ws(self) -> str:
        for _ in range(60):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1))
                pages = [t for t in targets if t.get("type") == "page"]
                if pages:
                    return pages[0]["webSocketDebuggerUrl"]
            except Exception:
                time.sleep(0.2)
        raise RuntimeError("no page target")

    def call(self, method: str, params: dict | None = None):
        self.seq += 1
        self.ws.send(json.dumps({"id": self.seq, "method": method, "params": params or {}}))
        while True:
            message = json.loads(self.ws.recv())
            if message.get("id") == self.seq:
                if "error" in message:
                    raise RuntimeError(message["error"])
                return message.get("result")


def main() -> int:
    url = sys.argv[1] if len(sys.argv) > 1 else "https://arxiv.org/abs/1706.03762"
    profile = Path(tempfile.mkdtemp(prefix="edge-ext-"))
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         url],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        time.sleep(3)
        cdp = CDP()
        cdp.call("Runtime.enable")
        time.sleep(3)
        code = f"JSON.stringify(({extractor_source()})())"
        result = cdp.call("Runtime.evaluate", {"expression": code, "returnByValue": True, "awaitPromise": True})
        value = result.get("result", {}).get("value")
        if not value:
            print("RAW:", result)
            return 1
        meta = json.loads(value)
        print(json.dumps(meta, ensure_ascii=False, indent=2))
        ok = bool(meta.get("title"))
        print("TITLE_OK:", ok)
        return 0 if ok else 1
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
