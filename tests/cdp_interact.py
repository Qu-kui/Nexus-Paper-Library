"""端到端交互验证：headless Edge + CDP 驱动真实页面操作。

用法：python tests/cdp_interact.py <paper_id> [base_url]
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
PORT = 9224


class CDP:
    def __init__(self, ws):
        self.ws = ws
        self.seq = 0
        self.errors: list[str] = []
        self.logs: list[str] = []

    def _next(self) -> int:
        self.seq += 1
        return self.seq

    def send(self, method: str, params: dict | None = None):
        self.ws.send(json.dumps({"id": self._next(), "method": method, "params": params or {}}))

    def eval(self, expression: str, await_promise: bool = True, timeout: float = 25.0):
        ident = self._next()
        self.ws.send(json.dumps({
            "id": ident, "method": "Runtime.evaluate",
            "params": {"expression": expression, "returnByValue": True,
                       "awaitPromise": await_promise},
        }))
        self.ws.settimeout(timeout)
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(str(msg["params"]).replace("\n", " ")[:300])
            if msg.get("method") == "Runtime.consoleAPICalled":
                args = " ".join(str(a.get("value", a.get("description", "")))[:200]
                                for a in msg["params"].get("args", []))
                self.logs.append(f"{msg['params'].get('type')}: {args}")
            if msg.get("id") == ident:
                result = msg.get("result", {})
                if result.get("exceptionDetails"):
                    raise RuntimeError(result["exceptionDetails"].get("text", "JS error") + " " +
                                       str(result["exceptionDetails"].get("exception", {}).get("description", ""))[:300])
                return (result.get("result") or {}).get("value")

    def drain_errors(self) -> list[str]:
        self.ws.settimeout(0.3)
        while True:
            try:
                msg = json.loads(self.ws.recv())
            except Exception:
                break
            if msg.get("method") == "Runtime.exceptionThrown":
                self.errors.append(str(msg["params"])[:400])
            if msg.get("method") == "Runtime.consoleAPICalled":
                args = " ".join(str(a.get("value", a.get("description", "")))[:200]
                                for a in msg["params"].get("args", []))
                self.logs.append(f"{msg['params'].get('type')}: {args}")
            if msg.get("method") == "Log.entryAdded":
                entry = msg["params"].get("entry", {})
                if entry.get("level") == "error":
                    self.errors.append(entry.get("text", ""))
        return self.errors


def main() -> int:
    paper_id = sys.argv[1] if len(sys.argv) > 1 else ""
    base = sys.argv[2] if len(sys.argv) > 2 else "http://127.0.0.1:8766"
    profile = Path(tempfile.mkdtemp(prefix="edge-cdp-int-"))
    proc = subprocess.Popen(
        [EDGE, "--headless=new", "--disable-gpu", "--no-first-run", "--hide-scrollbars",
         "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
         "--disable-backgrounding-occluded-windows",
         "--disable-features=CalculateNativeWinOcclusion",
         f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
         "--window-size=1500,950", base + "/"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    results = []
    try:
        ws_url = None
        for _ in range(60):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json/list", timeout=1) as resp:
                    pages = [t for t in json.load(resp) if t.get("type") == "page"]
                if pages:
                    ws_url = pages[0]["webSocketDebuggerUrl"]
                    break
            except Exception:
                pass
            time.sleep(0.25)
        if not ws_url:
            print("FAIL: CDP 连接失败")
            return 1
        ws = websocket.create_connection(ws_url, timeout=10)
        cdp = CDP(ws)
        for method in ("Runtime.enable", "Log.enable", "Page.enable", "Network.enable"):
            cdp.send(method)
        cdp.send("Page.bringToFront")
        cdp.send("Emulation.setFocusEmulationEnabled", {"enabled": True})
        time.sleep(4)

        def step(name, fn):
            try:
                detail = fn()
                results.append((name, True, detail))
                print(f"  PASS  {name}  [{detail}]")
            except Exception as exc:  # noqa: BLE001
                results.append((name, False, str(exc)))
                print(f"  FAIL  {name}: {exc}")

        def chat():
            cdp.eval("""(() => {
                document.querySelector('[data-page=chat]').click();
                const input = document.getElementById('chat-input');
                input.value = '这些论文的主要贡献是什么？';
                input.dispatchEvent(new Event('input', {bubbles:true}));
                document.getElementById('btn-chat-send').click();
                return 'sent';
            })()""", await_promise=False)
            time.sleep(5)
            state = cdp.eval("""JSON.stringify({
                msgs: document.querySelectorAll('#chat-messages .msg').length,
                last: (document.querySelector('#chat-messages .msg:last-child')?.innerText || '').slice(0,80),
                loading: document.querySelector('#chat-messages')?.innerText.includes('思考') || false
            })""")
            data = json.loads(state)
            assert data["msgs"] >= 2, f"消息数 {data['msgs']}"
            return data["last"].replace("\n", " ")[:50]

        def chat_source_click():
            count = cdp.eval("document.querySelectorAll('.src-item').length")
            if count:
                cdp.eval("document.querySelector('.src-item').click()")
                time.sleep(4)
                opened = cdp.eval("!document.getElementById('reader').classList.contains('hidden')")
                assert opened, "点击来源未打开阅读器"
                cdp.eval("document.getElementById('reader-close').click()")
                time.sleep(1)
                return f"{count} sources, click opens reader"
            return "无 sources（mock 检索为空），跳过"

        def discover():
            cdp.eval("""(() => {
                document.getElementById('reader-close')?.click();
                document.querySelector('[data-page=discover]').click();
                const input = document.getElementById('discover-input');
                input.value = 'attention transformer';
                input.dispatchEvent(new Event('input', {bubbles:true}));
                document.getElementById('btn-discover').click();
                return 'searching';
            })()""", await_promise=False)
            time.sleep(8)
            state = cdp.eval("""JSON.stringify({
                rows: document.querySelectorAll('#discover-results .paper-row').length,
                first: (document.querySelector('#discover-results .paper-row h3')?.innerText || '')
            })""")
            data = json.loads(state)
            assert data["rows"] >= 1, "arXiv 结果为空"
            return f"{data['rows']} rows, first={data['first'][:30]}"

        def reader():
            cdp.eval(f"window.PW.openPaper('{paper_id}')", await_promise=False)
            time.sleep(8)
            state = cdp.eval("""JSON.stringify({
                pages: document.querySelectorAll('.pdf-page').length,
                canvases: document.querySelectorAll('.pdf-page canvas').length,
                total: document.getElementById('reader-page-total')?.textContent,
                title: document.getElementById('reader-title')?.textContent,
                summary: (document.getElementById('reader-summary')?.innerText || '').length
            })""")
            data = json.loads(state)
            assert data["canvases"] >= 1, "canvas 未渲染"
            assert data["total"] and data["total"] != "…", f"总页数 {data['total']}"
            return f"pages={data['pages']} canvases={data['canvases']} total={data['total']}"

        def reader_analyze():
            cdp.eval("document.getElementById('btn-analyze').click()")
            time.sleep(6)
            state = cdp.eval("""JSON.stringify({
                summary: (document.getElementById('reader-summary')?.innerText || '').slice(0,60),
                keypoints: document.querySelectorAll('#reader-keypoints .kp-item').length,
                tags: document.querySelectorAll('#reader-tags .pc-tag').length
            })""")
            data = json.loads(state)
            assert data["summary"] and data["keypoints"] >= 1, f"精读结果异常 {data}"
            return f"kp={data['keypoints']} tags={data['tags']}"

        def reader_relevance():
            cdp.eval("document.querySelector('[data-pane=summary]')?.classList.remove('active'); document.querySelector('[data-pane=relevance]')?.classList.add('active');")
            cdp.eval("document.getElementById('btn-relevance').click()")
            time.sleep(5)
            text = cdp.eval("(document.getElementById('reader-relevance')?.innerText || '').slice(0,60)")
            assert text and "还没有" not in text, f"关联结果 {text}"
            return text.replace("\n", " ")[:50]

        def reader_zoom():
            def widths():
                return cdp.eval("JSON.stringify({w: Array.from(document.querySelectorAll('.pdf-page canvas')).map(c=>c.width), pages: document.querySelectorAll('.pdf-page').length, state: (()=>{const s=window.PW.readerState; return s?{zoom:s.zoom, fit:s.fitScale, cp:s.currentPage, np:s.numPages, wanted:[...s.wanted], rendering:[...s.rendering], queue:s.renderQueue.length, active:s.activeRenders, rendered:[...s.renderedPages.entries()]}:null})()})")
            before = json.loads(widths())
            raf = cdp.eval("new Promise(r => { let done=false; requestAnimationFrame(()=>{done=true;r('raf-ok')}); setTimeout(()=>{ if(!done) r('raf-timeout') }, 2000) })")
            print("      requestAnimationFrame:", raf)
            cdp.eval("document.getElementById('reader-zoom-in').click()")
            time.sleep(7)
            after = json.loads(widths())
            print("      before:", before)
            print("      after :", after)
            assert max(after["w"] or [0]) > max(before["w"] or [0]), f"缩放无效 {max(before['w'] or [0])}->{max(after['w'] or [0])}"
            return f"canvas {max(before['w'])}->{max(after['w'])}"

        def close_reader():
            cdp.eval("document.getElementById('reader-close').click()")
            time.sleep(1)
            hidden = cdp.eval("document.getElementById('reader').classList.contains('hidden')")
            assert hidden, "阅读器未关闭"
            return "closed"

        step("知识库对话（mock RAG）", chat)
        step("对话来源跳转", chat_source_click)
        step("发现论文（真实 arXiv）", discover)
        step("打开阅读器渲染 PDF", reader)
        step("AI 精读面板", reader_analyze)
        step("与我相关分析", reader_relevance)
        step("缩放重渲染", reader_zoom)
        step("关闭阅读器", close_reader)

        errors = cdp.drain_errors()
        print("\n=== JS ERRORS (%d) ===" % len(errors))
        for err in errors[:10]:
            print(" -", err[:300])
        print("\n=== CONSOLE LOG (tail 30) ===")
        for line in cdp.logs[-30:]:
            print(" -", line[:250])
        failed = [r for r in results if not r[1]]
        print("\nPASS %d / %d" % (len(results) - len(failed), len(results)))
        ws.close()
        return 1 if failed else 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()


if __name__ == "__main__":
    sys.exit(main())
