"""只验证安装器 GUI 的目录选择对话框：打开 → 截图 → 取消，不实际安装。"""
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
WM_COMMAND = 0x0111
BM_CLICK = 0x00F5
WM_CLOSE = 0x0010

ROOT = Path(r"D:\Harness APP\PaperWhale")
SETUP = ROOT / "release" / "PaperWhale-0.2.1-Setup.exe"
SHOT = ROOT / "docs" / "screenshots" / "installer-path.png"


def text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def cls(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def kids(hwnd: int):
    out = []
    child = user32.GetWindow(hwnd, 5)
    while child:
        out.append((child, cls(child), text(child)))
        child = user32.GetWindow(child, 2)
    return out


def click(hwnd: int, prefixes) -> bool:
    for child, c, t in kids(hwnd):
        if c == "Button" and any(t.startswith(p) for p in prefixes):
            user32.SendMessageW(child, BM_CLICK, 0, 0)
            return True
    return False


def pids() -> set[int]:
    out = subprocess.run(
        ["powershell", "-NoProfile", "-Command",
         "Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'PaperWhale*Setup*' } | ForEach-Object { $_.ProcessId }"],
        capture_output=True, text=True, timeout=20,
    ).stdout
    return {int(x) for x in out.split() if x.strip().isdigit()}


def windows(proc_ids: set[int]):
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
    for stale in subprocess.run(["tasklist", "/FI", "IMAGENAME eq PaperWhale-Setup.exe"],
                                capture_output=True, text=True).stdout.splitlines():
        if "PaperWhale-Setup.exe" in stale:
            try:
                subprocess.run(["taskkill", "/F", "/PID", stale.split()[-1]], capture_output=True)
            except Exception:
                pass
    proc = subprocess.Popen([str(SETUP)])
    deadline = time.time() + 90
    saw_intro = saw_folder = saw_confirm = False
    while time.time() < deadline:
        for hwnd in windows(pids()):
            title = text(hwnd)
            body = " | ".join(t for _, _, t in kids(hwnd))
            if "点「确定」可以自己选择安装文件夹" in body and not saw_intro:
                print("intro ok")
                saw_intro = True
                click(hwnd, ("确定",))
            elif ("选择" in title or "文件夹" in title) and not saw_folder:
                saw_folder = True
                print("folder dialog:", repr(title))
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                try:
                    ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(SHOT)
                    print("saved", SHOT)
                except Exception as exc:
                    print("screenshot failed", exc)
                user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            elif "即将安装 PaperWhale" in body and not saw_confirm:
                saw_confirm = True
                print("confirm dialog -> cancel")
                click(hwnd, ("否",))
                break
        if saw_confirm:
            break
        time.sleep(1.0)
    time.sleep(1)
    try:
        proc.terminate()
    except Exception:
        pass
    subprocess.run(["taskkill", "/F", "/IM", "PaperWhale-Setup.exe"], capture_output=True)
    print("intro:", saw_intro, "folder:", saw_folder, "cancelled:", saw_confirm)
    return 0 if (saw_intro and saw_folder) else 1


if __name__ == "__main__":
    sys.exit(main())
