"""自动走一遍安装器 GUI：选择自定义目录 → 确认 → 安装 → 验证。"""
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
IDOK = 1
IDYES = 6

ROOT = Path(r"D:\Harness APP\PaperWhale")
SETUP = ROOT / "release" / "PaperWhale-0.2.1-Setup.exe"
TARGET = Path(r"D:\PaperWhaleGuiTest")
SHOT = ROOT / "docs" / "screenshots" / "installer-path.png"


def window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, buf, 128)
    return buf.value


def children(hwnd: int):
    items = []
    child = user32.GetWindow(hwnd, 5)
    while child:
        items.append((child, class_name(child), window_text(child)))
        child = user32.GetWindow(child, 2)
    return items


def setup_pids() -> set[int]:
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "(Get-Process -Name PaperWhale-Setup -ErrorAction SilentlyContinue).Id"],
            capture_output=True, text=True, timeout=15,
        ).stdout
        return {int(x) for x in out.split() if x.strip().isdigit()}
    except Exception:
        return set()


def dialogs(pids: set[int]):
    found = []

    def callback(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and class_name(hwnd) == "#32770":
            found.append(hwnd)
        return True

    EnumWindows(EnumProc(callback), 0)
    return found


def click_button(hwnd: int, pred) -> bool:
    for child, cls, text in children(hwnd):
        if cls == "Button" and pred(text):
            user32.SendMessageW(child, BM_CLICK, 0, 0)
            return True
    return False


def main() -> int:
    subprocess.run(["taskkill", "/F", "/IM", "PaperWhale.exe"], capture_output=True)
    time.sleep(1)
    if TARGET.exists():
        subprocess.run(["cmd", "/c", "rmdir", "/s", "/q", str(TARGET)], capture_output=True)
    subprocess.run(["cmd", "/c", "reg", "delete",
                    r"HKCU\Software\Microsoft\Windows\CurrentVersion\Uninstall\PaperWhale", "/f"],
                   capture_output=True)
    for link in (Path(r"D:\DeskTop\PaperWhale.lnk"),
                 Path.home() / "Desktop" / "PaperWhale.lnk"):
        link.unlink(missing_ok=True)

    proc = subprocess.Popen([str(SETUP)])
    deadline = time.time() + 180
    picked = False
    installed = False
    while time.time() < deadline:
        pids = setup_pids()
        for hwnd in dialogs(pids):
            text = window_text(hwnd)
            kids = children(hwnd)
            body = " | ".join(t for _, _, t in kids)
            edits = [h for h, cls, _ in kids if cls == "Edit"]
            if "点「确定」可以自己选择安装文件夹" in body:
                print("intro dialog -> OK")
                click_button(hwnd, lambda t: t.startswith("确定"))
            elif edits and ("选择" in text or "浏览" in text or "文件夹" in text):
                print("folder dialog:", repr(text))
                target = str(TARGET)
                user32.SetWindowTextW(edits[0], ctypes.c_wchar_p(target))
                time.sleep(0.3)
                rect = wintypes.RECT()
                user32.GetWindowRect(hwnd, ctypes.byref(rect))
                try:
                    ImageGrab.grab(bbox=(rect.left, rect.top, rect.right, rect.bottom)).save(SHOT)
                    print("screenshot ->", SHOT)
                except Exception as exc:
                    print("screenshot failed", exc)
                if click_button(hwnd, lambda t: t.startswith("确定") or t.startswith("选择")):
                    picked = True
                    print("clicked folder OK")
                else:
                    print("no OK button in folder dialog:", body[:200])
            elif "即将安装 PaperWhale" in body:
                print("confirm dialog -> YES")
                click_button(hwnd, lambda t: t.startswith("是"))
            elif "安装完成" in body:
                print("done dialog -> OK")
                click_button(hwnd, lambda t: t.startswith("确定"))
                installed = True
                break
            elif "安装失败" in body or "没有权限" in body:
                print("FAILED dialog:", body[:300])
                click_button(hwnd, lambda t: t.startswith("确定"))
                return 1
        if installed:
            break
        time.sleep(1.2)

    print("picked path:", picked, "installed:", installed)
    time.sleep(2)
    exe = TARGET / "PaperWhale.exe"
    print("exe exists:", exe.exists())
    if exe.exists():
        health = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "try { (Invoke-RestMethod 'http://127.0.0.1:8765/api/health' -TimeoutSec 5).version } catch { 'ERR' }"],
            capture_output=True, text=True, timeout=20,
        ).stdout.strip()
        print("health:", health)
    try:
        proc.terminate()
    except Exception:
        pass
    return 0 if (picked and installed and exe.exists()) else 1


if __name__ == "__main__":
    sys.exit(main())
