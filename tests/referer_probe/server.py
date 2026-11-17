# -*- coding: utf-8 -*-
"""最小 Referer 探针服务（只给实验用，不属于 PaperWhale 运行时）。

目的：验证 Edge/Chrome 的 MV3 `chrome.downloads.download({url, headers:[{name:'Referer',...}]})`
到底会不会把 Referer 发出来。浏览器那侧由 tests/referer_probe/extension 负责，
这里只做两件事：
  1. GET /page          -> 一个测试页（含手动点击的下载链接）
  2. GET /signed.pdf    -> Content-Disposition: attachment 的 PDF，并把收到的请求头打进日志

安全边界：只监听 127.0.0.1:8799，不读用户数据库，不访问外网，不保存任何 Cookie 值
（日志只记 Cookie 是否存在，不记内容）。

用法：
    .venv/Scripts/python.exe tests/referer_probe/server.py
然后按 tests/referer_probe/README.md 加载扩展、点「开始实验」。
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8799
LOG_PATH = Path(__file__).with_name("requests.log")

# 头部带 %PDF-1.4 且大于 1KB，足够让浏览器/插件当成 PDF 保存；这里不追求可渲染。
PDF_BODY = (
    b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    + b"%" + b"A" * 1200 + b"\n"
    + b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    + b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    + b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 200]>>endobj\n"
    + b"trailer<</Size 4/Root 1 0 R>>\n%%EOF\n"
)

PAGE = """<!doctype html>
<meta charset="utf-8">
<title>PW Referer Probe</title>
<h1>PaperWhale Referer 探针</h1>
<p>这个页面只是给下载请求提供 Referer 值。</p>
<p>可选：手动点一次下面的链接，比较“用户手势点击”和“扩展 downloads API”的请求头差异：</p>
<p><a id="manual" href="/signed.pdf" download="probe.pdf">手动下载 signed.pdf</a></p>
"""


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class Handler(BaseHTTPRequestHandler):
    server_version = "PWRefererProbe/1.0"

    def log_message(self, *args):  # 关掉默认的 stderr 噪音，用结构化日志代替
        pass

    def do_GET(self):  # noqa: N802 - BaseHTTPRequestHandler 的命名
        referer = self.headers.get("Referer", "")
        record = {
            "at": _now(),
            "path": self.path,
            "referer": referer,
            "has_cookie": bool(self.headers.get("Cookie")),
            "user_agent": (self.headers.get("User-Agent") or "")[:80],
        }
        line = json.dumps(record, ensure_ascii=False)
        print(line, flush=True)
        try:
            with LOG_PATH.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
        except OSError:
            pass

        if self.path.startswith("/page"):
            body = PAGE.encode("utf-8")
            content_type = "text/html; charset=utf-8"
            disposition = None
        else:
            body = PDF_BODY
            content_type = "application/pdf"
            disposition = 'attachment; filename="probe.pdf"'

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        if disposition:
            self.send_header("Content-Disposition", disposition)
        self.end_headers()
        self.wfile.write(body)


def main() -> int:
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"PW Referer Probe listening on http://127.0.0.1:{PORT}  (Ctrl+C 结束)")
    print(f"日志追加到: {LOG_PATH}")
    print("测试页: http://127.0.0.1:8799/page")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
