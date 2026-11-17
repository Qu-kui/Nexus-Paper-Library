"""PaperWhale 启动器：启动本地服务并打开浏览器。"""
from __future__ import annotations

import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path

try:  # Windows 控制台默认 GBK，避免中文/符号打印崩溃
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOST = "127.0.0.1"
PORT = 8765
URL = f"http://{HOST}:{PORT}"


def port_free(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.4)
        return sock.connect_ex((HOST, port)) != 0


def open_browser() -> None:
    for _ in range(40):
        if not port_free(PORT):
            break
        time.sleep(0.25)
    webbrowser.open(URL)


def main() -> None:
    import uvicorn

    print("=" * 56)
    print("  PaperWhale · 论文鲸")
    print(f"  本地地址: {URL}")
    print("  关闭本窗口即停止服务")
    print("=" * 56)
    threading.Thread(target=open_browser, daemon=True).start()
    uvicorn.run("paperwhale_server.main:app", host=HOST, port=PORT, log_level="info")


if __name__ == "__main__":
    main()
