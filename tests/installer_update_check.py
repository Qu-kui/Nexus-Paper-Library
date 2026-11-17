"""验证安装器 GUI 能检测到旧版本并给出覆盖更新选择（点击取消，不做修改）。"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

from PIL import ImageGrab

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
BM_CLICK = 0x00F5

ROOT = Path(r"D:\Harness APP\PaperWhale")
SETUP = ROOT / "release" / "PaperWhale-0.4.1-Setup.exe"
SHOT = ROOT / "docs" / "screenshots" / "installer-update.png"


def text(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def cls(hwnd):
    buf = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, buf, 64)
    return buf.value


def pids():
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'PaperWhale*Setup*' } | ForEach-Object { $_.ProcessId }"],
        capture_output=True, text=True, timeout=20,
    ).stdout
    return {int(x) for x in out.split() if x.strip().isdigit()}


def dialogs(proc_ids):
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in proc_ids and cls(hwnd) == "#32770":
            found.append(hwnd)
        return True

    EnumWindows(EnumProc(cb), 0)
    return found


def main() -> int:
    for line in subprocess.run(["tasklist", "/FI", "IMAGENAME eq PaperWhale-Setup.exe"],
                               capture_output=True, text=True).stdout.splitlines():
        if "PaperWhale-Setup.exe" in line:
            subprocess.run(["taskkill", "/F", "/PID", line.split()[-1]], capture_output=True)
    proc = subprocess.Popen([str(SETUP)])
    deadline = time.time() + 60
    seen = False
    while time.time() < deadline:
        for hwnd in dialogs(pids()):
            body = " | ".join(text(c) for c in [hwnd])
            child = user32.GetWindow(hwnd, 5)
            texts = []
            while child:
                texts.append(text(child))
                child = user32.GetWindow(child, 2)
            joined = " | ".join(texts)
            if "检测到电脑上已经安装了" in joined:
                print("dialog:", joined[:160])
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                try:
                    ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(SHOT)
                    print("saved", SHOT)
                except Exception as exc:
                    print("screenshot failed", exc)
                for ch in range(100):
                    child = user32.GetWindow(hwnd, 5) if ch == 0 else None
                    break
                # 点击「取消」
                child = user32.GetWindow(hwnd, 5)
                while child:
                    if cls(child) == "Button" and text(child).startswith("取消"):
                        user32.SendMessageW(child, BM_CLICK, 0, 0)
                        seen = True
                        break
                    child = user32.GetWindow(child, 2)
                break
        if seen:
            break
        time.sleep(1)
    time.sleep(1)
    try:
        proc.terminate()
    except Exception:
        pass
    subprocess.run(["taskkill", "/F", "/IM", "PaperWhale-Setup.exe"], capture_output=True)
    print("detected:", seen)
    return 0 if seen else 1


if __name__ == "__main__":
    sys.exit(main())
