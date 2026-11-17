"""自动点击 PaperWhale 安装器对话框并验证安装结果。"""
from __future__ import annotations

import ctypes
import subprocess
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

user32 = ctypes.windll.user32
EnumWindows = user32.EnumWindows
EnumProc = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
WM_COMMAND = 0x0111
BM_CLICK = 0x00F5
IDOK, IDYES = 1, 6

SETUP = Path(r"D:\Harness APP\PaperWhale\dist\PaperWhale-Setup.exe")


def window_text(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(4096)
    user32.GetWindowTextW(hwnd, buf, 4096)
    return buf.value


def child_texts(hwnd: int):
    items = []
    child = user32.GetWindow(hwnd, 5)  # GW_CHILD
    while child:
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(child, cls, 64)
        items.append((child, cls.value, window_text(child)))
        child = user32.GetWindow(child, 2)  # GW_HWNDNEXT
    return items


def dialogs_for(pids: set[int]):
    found = []

    def cb(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if pid.value in pids and cls.value == "#32770":
            found.append(hwnd)
        return True

    EnumWindows(EnumProc(cb), 0)
    return found


def main() -> int:
    for proc in subprocess.run(["tasklist", "/FI", "IMAGENAME eq PaperWhale-Setup.exe"],
                               capture_output=True, text=True).stdout.splitlines():
        if "PaperWhale-Setup.exe" in proc:
            pid = int(proc.split()[-1]) if proc.split()[-1].isdigit() else 0
            if pid:
                subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    time.sleep(1)

    proc = subprocess.Popen([str(SETUP)])
    deadline = time.time() + 180
    clicked_install = False
    finished = False
    while time.time() < deadline:
        pids = {proc.pid}
        try:
            out = subprocess.run(
                ["powershell", "-NoProfile", "-Command",
                 "(Get-CimInstance Win32_Process -Filter \"Name='PaperWhale-Setup.exe'\").ProcessId"],
                capture_output=True, text=True, timeout=20,
            ).stdout
            pids |= {int(line) for line in out.split() if line.strip().isdigit()}
        except Exception:
            pass
        for hwnd in dialogs_for(pids):
            children = child_texts(hwnd)
            texts = " | ".join(t for _, _, t in children)
            buttons = [(h, t) for h, cls, t in children if cls == "Button"]
            if not buttons:
                continue
            if not clicked_install:
                print("DIALOG-1:", texts[:250])
                target = next((h for h, t in buttons if t.startswith("是")), buttons[0][0])
                user32.PostMessageW(hwnd, BM_CLICK, 0, 0) if False else user32.SendMessageW(target, BM_CLICK, 0, 0)
                clicked_install = True
            elif "完成" in texts or "失败" in texts or "损坏" in texts:
                print("DIALOG-2:", texts[:300])
                target = next((h for h, t in buttons if t.startswith("确定")), buttons[0][0])
                user32.SendMessageW(target, BM_CLICK, 0, 0)
                finished = True
                break
        if finished:
            break
        time.sleep(1.5)
    print("clicked_install:", clicked_install, "finished:", finished, "exit:", proc.poll())
    return 0 if finished else 1


if __name__ == "__main__":
    sys.exit(main())
