"""PaperWhale 安装器：自解压 + 自动关闭旧进程 + 快捷方式 + 卸载程序 + Word 插件安装。

用法：
  PaperWhale-Setup.exe                 # 图形向导
  PaperWhale-Setup.exe --silent        # 静默安装到已安装目录（没有则默认目录）
  PaperWhale-Setup.exe --silent --dir "D:\\Apps\\PaperWhale"
"""
from __future__ import annotations

import argparse
import ctypes
import os
import shutil
import subprocess
import sys
import time
import zipfile
from ctypes import wintypes
from pathlib import Path

APP_NAME = "Nexus Paper Library"
EXE_NAME = "PaperWhale.exe"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall\PaperWhale"
DEFAULT_VERSION = "0.0.0"

#: 关闭旧进程时最多等多久（秒）
CLOSE_TIMEOUT = 12.0

WINWORD_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe"

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)

kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
kernel32.GetExitCodeProcess.restype = wintypes.BOOL
kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
user32.EnumWindows.argtypes = [ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM), wintypes.LPARAM]
user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]

WM_CLOSE = 0x0010
STILL_ACTIVE = 259
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000


class AppRunningError(RuntimeError):
    """旧进程关不掉（通常是管理员权限启动的托盘程序）。"""


# ---------------------------------------------------------------------------
# 日志
# ---------------------------------------------------------------------------

def log(message: str) -> None:
    try:
        line = time.strftime("%H:%M:%S") + " " + str(message)
        path = Path(os.environ.get("TEMP") or ".") / "PaperWhale-setup.log"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
    except Exception:  # noqa: BLE001 - 日志失败绝不能影响安装
        pass


# ---------------------------------------------------------------------------
# Win32 进程工具
# ---------------------------------------------------------------------------

def _pid_alive(pid: int) -> bool:
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _run_powershell(command: str) -> str:
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", command],
            capture_output=True,
            timeout=60,
            # The installer is a windowed PyInstaller build, so a plain child
            # process would pop a console window over the progress UI.
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        log(f"powershell failed: {exc}")
        return ""
    return completed.stdout.decode("utf-8", errors="ignore")


def list_running_instances() -> list[dict]:
    """列出正在运行的 PaperWhale 进程（跳过安装器自己与坏行）。"""
    command = (
        "(Get-CimInstance Win32_Process -Filter \"Name='PaperWhale.exe'\" "
        "| ForEach-Object { $_.ProcessId.ToString() + '|' + $_.ExecutablePath })"
    )
    output = _run_powershell(command)
    own = os.getpid()
    instances: list[dict] = []
    for raw in output.splitlines():
        line = raw.strip()
        if not line:
            continue
        pid_text, _, path = line.partition("|")
        try:
            pid = int(pid_text.strip())
        except ValueError:
            continue
        if pid == own:
            continue
        instances.append({"pid": pid, "path": path.strip()})
    return instances


def request_window_close(pid: int) -> bool:
    """给该进程的顶层窗口发 WM_CLOSE；找到至少一个窗口才算成功。"""
    target = wintypes.DWORD(int(pid))
    sent = {"ok": False}

    def callback(hwnd, _lparam):
        window_pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(window_pid))
        if window_pid.value == target.value:
            user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            sent["ok"] = True
        return True

    callback_ptr = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)(callback)
    user32.EnumWindows(callback_ptr, 0)
    return sent["ok"]


def terminate_process(pid: int) -> bool:
    """强制结束进程树，并等到它真的消失。"""
    try:
        subprocess.run(
            ["taskkill", "/T", "/F", "/PID", str(pid)],
            capture_output=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # noqa: BLE001
        log(f"taskkill failed: {exc}")
    deadline = time.monotonic() + CLOSE_TIMEOUT
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.25)
    return not _pid_alive(pid)


def close_running_app() -> tuple[bool, list[dict]]:
    """覆盖安装前自动关掉正在运行的 PaperWhale：先礼后兵。

    返回 (是否全部关掉, 已关闭进程列表)。窗口都找不到时（比如程序已经缩到托盘）
    直接强制结束；强制结束也失败就返回 False，让调用方提示用户。
    """
    instances = list_running_instances()
    if not instances:
        return True, []
    closed: list[dict] = []
    for item in instances:
        pid = int(item.get("pid") or 0)
        if not pid or not _pid_alive(pid):
            continue
        how = ""
        if request_window_close(pid):
            # 只要发出关闭消息就算走了「正常关闭」这条路；如果它只是缩到托盘，
            # 下次安装时会被 terminate 兜底（下面仍然验证一次存活）。
            if _wait_for_exit(pid, CLOSE_TIMEOUT):
                how = "window-close"
        if not how:
            if not terminate_process(pid):
                log(f"cannot close pid {pid}")
                return False, []
            how = "terminate"
        closed.append({**item, "how": how})
        log(f"closed pid {pid} via {how}")
    return True, closed


def _process_running(pid: int) -> bool:
    """不经过 _pid_alive 的存活检查（测试里 _pid_alive 会被 mock，这里保持真实）。"""
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return False
    try:
        code = wintypes.DWORD()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == STILL_ACTIVE
    finally:
        kernel32.CloseHandle(handle)


def _wait_for_exit(pid: int, timeout: float) -> bool:
    """等进程退出；返回 True 表示已经退出。"""
    attempts = max(1, int(timeout / 0.25))
    for _ in range(attempts):
        if not _process_running(pid):
            return True
        time.sleep(0.25)
    return not _process_running(pid)


# ---------------------------------------------------------------------------
# 安装目录 / 注册表
# ---------------------------------------------------------------------------

def read_installed_location() -> str:
    try:
        import winreg
    except ImportError:  # pragma: no cover
        return ""
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
            value, _ = winreg.QueryValueEx(key, "InstallLocation")
            return str(value or "")
    except OSError:
        return ""


def default_target() -> Path:
    detected = read_installed_location()
    if detected and Path(detected).exists():
        return Path(detected)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "Programs" / "PaperWhale"


def word_startup_dir() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Word" / "STARTUP"


def install_word_plugin(target: Path) -> list[str]:
    """安装包自带 Word 模板，安装时复制到 Word STARTUP（仅当检测到 Word）。"""
    source = target / "word"
    templates = ("PaperWhaleMacros.dotm",)
    if not source.exists():
        return []
    word_root = word_startup_dir().parent
    if not word_root.exists():
        log("word not detected, skip word plugin")
        return []
    startup = word_startup_dir()
    startup.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for name in templates:
        src = source / name
        if src.exists():
            try:
                shutil.copy2(src, startup / name)
                copied.append(name)
            except OSError as exc:
                log(f"word plugin copy failed: {exc}")
    if copied:
        log(f"word plugin installed: {', '.join(copied)}")
    return copied


def _ps_quote(value: str | Path) -> str:
    """PowerShell 字面量，支持安装目录中的撇号。"""
    return "'" + str(value).replace("'", "''") + "'"


# Same-named shortcuts belonging to another application must be preserved.
SHORTCUT_HELPERS = r"""
$ws=New-Object -ComObject WScript.Shell
function Test-OwnedLink($p) {
    if (!(Test-Path -LiteralPath $p)) { return $false }
    $s=$ws.CreateShortcut($p)
    return (($s.TargetPath -ieq $appExe) -or
        (($s.WorkingDirectory -ieq $appDir) -and
         ([IO.Path]::GetFileName($s.TargetPath) -ieq 'powershell.exe') -and
         ($s.Arguments -ceq $uninstallArgs)))
}
function Clear-OwnedLinks($folder) {
    Get-ChildItem -LiteralPath $folder -Filter '*.lnk' -ErrorAction SilentlyContinue | ForEach-Object {
        if (Test-OwnedLink $_.FullName) { Remove-Item -LiteralPath $_.FullName -Force }
    }
}
function New-LinkPath($folder,$name) {
    $p=Join-Path $folder ($name+'.lnk')
    $i=2
    while ((Test-Path -LiteralPath $p) -and !(Test-OwnedLink $p)) {
        $p=Join-Path $folder ($name+' ('+$i+').lnk'); $i++
    }
    return $p
}
"""


def _shortcut_context(target: Path) -> str:
    arguments = f'-NoProfile -ExecutionPolicy Bypass -File "{target / "uninstall.ps1"}"'
    return (
        f"$appDir={_ps_quote(target)};$appExe={_ps_quote(target / EXE_NAME)};"
        f"$uninstallArgs={_ps_quote(arguments)};"
        "$desktop=[Environment]::GetFolderPath('Desktop');"
        "$sm=Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs';"
    )


def create_shortcuts(exe: Path, target: Path) -> None:
    icon_path = target / "_internal" / "assets" / "npl.ico"
    if not icon_path.is_file():
        icon_path = exe
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        + _shortcut_context(target) + SHORTCUT_HELPERS
        + "foreach($folder in @($desktop,$sm)){"
        + f"$p=New-LinkPath $folder {_ps_quote(APP_NAME)};"
        + f"$s=$ws.CreateShortcut($p);$s.TargetPath={_ps_quote(exe)};"
        + f"$s.WorkingDirectory={_ps_quote(target)};$s.IconLocation={_ps_quote(str(icon_path)+',0')};$s.Save()}}"
    )
    _run_powershell(script)


def register_uninstall(exe: Path, version: str) -> None:
    import winreg

    size_kb = 0
    try:
        size_kb = int(sum(file.stat().st_size for file in exe.parent.rglob("*") if file.is_file()) / 1024)
    except OSError:
        size_kb = 0
    uninstall_script = exe.parent / "uninstall.ps1"
    values = {
        "DisplayName": APP_NAME,
        "DisplayVersion": str(version),
        "Publisher": APP_NAME,
        "DisplayIcon": str(exe),
        "InstallLocation": str(exe.parent),
        "UninstallString": f'powershell -NoProfile -ExecutionPolicy Bypass -File "{uninstall_script}"',
        "QuietUninstallString": f'powershell -NoProfile -ExecutionPolicy Bypass -File "{uninstall_script}"',
        "NoModify": 1,
        "NoRepair": 1,
        "EstimatedSize": size_kb,
    }
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as key:
        for name, value in values.items():
            if isinstance(value, int):
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, value)
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ, str(value))


def write_uninstaller(target: Path) -> None:
    """在安装目录写卸载脚本：删快捷方式 / Word 插件 / 注册表 / 程序目录（保留用户数据）。"""
    script = r"""$ErrorActionPreference = 'SilentlyContinue'
$appDir=$PSScriptRoot
$appExe=Join-Path $appDir 'PaperWhale.exe'
$uninstallArgs='-NoProfile -ExecutionPolicy Bypass -File "'+(Join-Path $appDir 'uninstall.ps1')+'"'
$desktop=[Environment]::GetFolderPath('Desktop')
$sm=Join-Path ([Environment]::GetFolderPath('StartMenu')) 'Programs'
""" + SHORTCUT_HELPERS + r"""
Clear-OwnedLinks $desktop
Clear-OwnedLinks $sm
Remove-Item (Join-Path $env:APPDATA 'Microsoft\Word\STARTUP\PaperWhaleMacros.dotm') -Force
Remove-Item 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\PaperWhale' -Recurse -Force
Start-Sleep -Milliseconds 600
Remove-Item -LiteralPath $appDir -Recurse -Force
"""
    path = target / "uninstall.ps1"
    path.write_text(script, encoding="utf-8")
    create_uninstall_shortcut(target, path)


def create_uninstall_shortcut(target: Path, script: Path) -> None:
    # Without an explicit icon the shortcut inherits powershell.exe's console
    # icon, which makes "卸载 ..." look like a stray command prompt.
    icon_path = target / "_internal" / "assets" / "npl.ico"
    if not icon_path.is_file():
        icon_path = target / EXE_NAME
    command = (
        "$ErrorActionPreference='SilentlyContinue';"
        + _shortcut_context(target) + SHORTCUT_HELPERS
        + "Clear-OwnedLinks $desktop;Clear-OwnedLinks $sm;"
        + f"$p=New-LinkPath $sm {_ps_quote('卸载 ' + APP_NAME)};"
        + "$s=$ws.CreateShortcut($p);$s.TargetPath='powershell.exe';"
        + f"$s.Arguments=$uninstallArgs;$s.WorkingDirectory={_ps_quote(target)};"
        + f"$s.IconLocation={_ps_quote(str(icon_path) + ',0')};$s.Save()"
    )
    _run_powershell(command)


# ---------------------------------------------------------------------------
# 安装
# ---------------------------------------------------------------------------

def _payload_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


def load_version() -> str:
    try:
        return (_payload_root() / "version.txt").read_text(encoding="utf-8-sig").strip() or DEFAULT_VERSION
    except OSError:
        return DEFAULT_VERSION


def find_payload() -> Path:
    root = _payload_root()
    for candidate in (root / "PaperWhale.zip", root / "dist" / "PaperWhale.zip"):
        if candidate.is_file():
            return candidate
    raise RuntimeError(f"安装包内缺少 PaperWhale.zip（在 {root}）")


def install(payload: Path, target: Path, version: str, launch: bool = True) -> list[dict]:
    target.mkdir(parents=True, exist_ok=True)
    # 覆盖安装时 PaperWhale.exe 正在运行会锁住文件：先自动识别并关掉，再写文件。
    closed_ok, closed = close_running_app()
    if not closed_ok:
        pids = ", ".join(str(item.get("pid")) for item in closed) or "未知"
        raise AppRunningError(f"无法结束进程（PID {pids}）")
    # 关掉进程后文件句柄可能晚一拍才释放（实测偶发 PermissionError），重试几次再报错
    with zipfile.ZipFile(payload) as archive:
        last_error: Exception | None = None
        for attempt in range(6):
            try:
                archive.extractall(target)
                last_error = None
                break
            except PermissionError as exc:
                last_error = exc
                log(f"extract retry {attempt + 1}: {exc}")
                time.sleep(0.8)
        if last_error is not None:
            raise AppRunningError(
                "程序文件仍被占用（可能还有 NPL 进程没退出）。"
                "请先手动退出托盘里的 NPL，或右键安装包以管理员身份运行。"
            ) from last_error
    write_uninstaller(target)
    # Word 插件：装到 %APPDATA%\Microsoft\Word\STARTUP（只影响当前用户）
    try:
        copied = install_word_plugin(target)
        if copied:
            log(f"word plugin installed: {', '.join(copied)}")
    except Exception as exc:  # noqa: BLE001 - Word 安装失败不能挡住主程序安装
        log(f"word plugin skipped: {exc}")
    exe = target / EXE_NAME
    create_shortcuts(exe, target)
    register_uninstall(exe, version)
    log(f"installed {version} -> {target}")
    if launch and exe.exists():
        os.startfile(str(exe))  # noqa: S606
    return closed


# ---------------------------------------------------------------------------
# 图形向导
# ---------------------------------------------------------------------------

def run_gui(version: str, payload: Path, initial_dir: Path) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox

    root = tk.Tk()
    root.title(f"NPL 安装程序 {version}")
    icon_path = _payload_root() / "assets" / "npl.png"
    if not icon_path.is_file():
        icon_path = Path(__file__).resolve().parent.parent / "web" / "brand" / "npl-app.png"
    if icon_path.is_file():
        root._npl_icon = tk.PhotoImage(file=str(icon_path))
        root.iconphoto(True, root._npl_icon)
    root.resizable(False, False)
    root.geometry("560x300")

    tk.Label(root, text="Nexus Paper Library", font=("Microsoft YaHei UI", 15, "bold")).pack(pady=(18, 4))
    tk.Label(root, text="本地优先的 AI 文献工作台").pack()

    frame = tk.Frame(root)
    frame.pack(fill="x", padx=24, pady=18)
    tk.Label(frame, text="安装到：").pack(anchor="w")
    row = tk.Frame(frame)
    row.pack(fill="x", pady=4)
    target_var = tk.StringVar(value=str(initial_dir))
    entry = tk.Entry(row, textvariable=target_var)
    entry.pack(side="left", fill="x", expand=True, ipady=4)

    def browse() -> None:
        chosen = filedialog.askdirectory(initialdir=target_var.get() or str(Path.home()))
        if chosen:
            target_var.set(chosen)

    tk.Button(row, text="选择…", command=browse).pack(side="left", padx=(8, 0))
    status = tk.Label(frame, text="", fg="#555")
    status.pack(anchor="w", pady=(8, 0))

    buttons = tk.Frame(root)
    buttons.pack(side="bottom", fill="x", padx=24, pady=18)
    start = tk.Button(buttons, text="安装", width=14)
    start.pack(side="right")

    def quit_app() -> None:
        root.destroy()

    tk.Button(buttons, text="退出", width=10, command=quit_app).pack(side="right", padx=(0, 8))

    def do_install() -> None:
        target = Path(target_var.get().strip() or str(initial_dir)).expanduser()
        confirm = messagebox.askyesno(
            "确认安装",
            f"把 Nexus Paper Library 安装到：\n{target}\n\n"
            "如果程序正在运行，安装器会自动关掉它再继续。",
        )
        if not confirm:
            return
        start.config(state="disabled")
        status.config(text="正在安装…")

        def worker() -> None:
            try:
                install(payload, target, version, launch=True)
            except AppRunningError:
                root.after(0, lambda: fail(
                    "旧版本的 NPL 自动关闭它失败。\n\n"
                    "请先手动退出托盘里的 NPL；仍然不行就右键安装包，选择「以管理员身份运行」。"
                ))
            except Exception as exc:  # noqa: BLE001
                log(f"install failed: {exc}")
                root.after(0, lambda: fail(
                    f"安装失败：{exc}\n\n"
                    "可以先手动退出 NPL；仍失败就右键安装包，选择「以管理员身份运行」。"
                ))
            else:
                root.after(0, done)

        import threading

        threading.Thread(target=worker, daemon=True).start()

    def done() -> None:
        messagebox.showinfo("安装完成", "NPL 已安装，正在启动。\n\n桌面和开始菜单都有快捷方式。")
        root.destroy()

    def fail(message: str) -> None:
        start.config(state="normal")
        status.config(text="")
        messagebox.showerror("安装未完成", message)

    start.config(command=do_install)
    root.mainloop()
    return 0


def run_silent(version: str, payload: Path, target: Path) -> int:
    log(f"silent install {version} -> {target}")
    try:
        install(payload, target, version, launch=True)
    except AppRunningError:
        log("cannot close running app")
        return 2
    except Exception as exc:  # noqa: BLE001
        log(f"install failed: {exc}")
        return 1
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="NPL 安装程序")
    parser.add_argument("--silent", action="store_true", help="不显示向导，直接安装")
    parser.add_argument("--dir", default="", help="安装目录")
    parser.add_argument("--no-launch", action="store_true", help="安装完成后不自动启动")
    args = parser.parse_args(argv)

    version = load_version()
    payload = find_payload()
    initial = Path(args.dir).expanduser() if args.dir else default_target()

    if args.silent:
        if args.no_launch:
            rc = 0
            try:
                install(payload, initial, version, launch=False)
            except AppRunningError:
                rc = 2
            except Exception:  # noqa: BLE001
                rc = 1
            return rc
        return run_silent(version, payload, initial)
    return run_gui(version, payload, initial)


if __name__ == "__main__":
    sys.exit(main())
