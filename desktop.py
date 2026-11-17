"""Nexus Paper Library 桌面版入口：本地服务 + 原生窗口（WebView2），不依赖浏览器标签页。

开发：`.venv\\Scripts\\python.exe desktop.py`
打包：PyInstaller 见 build.ps1
"""
from __future__ import annotations

import ctypes
import logging
import os
import socket
import sys
import threading
import time
import traceback
import uuid
import webbrowser
from pathlib import Path

APP_TITLE = "Nexus Paper Library"
APP_USER_MODEL_ID = "NexusPaperLibrary.Desktop"
PREFERRED_PORT = 8765

# PyInstaller --windowed 下 stdout/stderr 是 None，部分库会因此崩溃
if sys.stdout is None:
    sys.stdout = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
if sys.stderr is None:
    sys.stderr = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115


def _bundle_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    return Path(__file__).resolve().parent


ROOT = _bundle_root()
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _maximize_in_work_area(window) -> None:
    """Constrain borderless WinForms maximization to the current monitor's work area."""
    if sys.platform != "win32":
        window.maximize()
        return
    from System import Action
    from System.Windows.Forms import FormWindowState, Screen
    from webview.platforms.winforms import BrowserView

    form = BrowserView.instances[window.uid]

    def maximize():
        form.MaximizedBounds = Screen.FromControl(form).WorkingArea
        form.WindowState = FormWindowState.Maximized

    form.Invoke(Action(maximize))


def _setup_logging(data_dir: Path) -> None:
    data_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.FileHandler(data_dir / "paperwhale.log", encoding="utf-8", delay=True),
        ],
    )


def _window_icon_path() -> str | None:
    for path in (ROOT / "assets" / "npl.ico", ROOT / "web" / "brand" / "npl.ico"):
        if path.is_file():
            return str(path)
    return None


def _set_windows_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        setter = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes = [ctypes.c_wchar_p]
        setter.restype = ctypes.c_long
        result = setter(APP_USER_MODEL_ID)
        if result != 0:
            logging.getLogger(__name__).warning("Cannot set NPL taskbar identity: %s", result)
    except Exception:
        logging.getLogger(__name__).exception("Cannot set NPL taskbar identity")


def _pick_port() -> int:
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 30):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _tray_image():
    """使用与安装包一致的 NPL 品牌图标。"""
    from PIL import Image

    for path in (ROOT / "assets" / "npl.png", ROOT / "web" / "brand" / "npl-app.png"):
        if path.is_file():
            with Image.open(path) as image:
                return image.convert("RGBA").resize((64, 64), Image.Resampling.LANCZOS)
    raise FileNotFoundError("缺少 NPL 托盘图标，请重新安装 Nexus Paper Library")


def _create_tray(window, http_server):
    """系统托盘：关闭窗口后继续后台常驻，只有托盘「退出」才结束进程。"""
    try:
        import pystray
    except Exception:  # noqa: BLE001 - 没有 pystray 就退回普通关闭行为
        return None

    def _show(icon=None, item=None) -> None:
        try:
            window.show()
            window.restore()
        except Exception:  # noqa: BLE001
            pass

    def _open_data(icon=None, item=None) -> None:
        try:
            os.startfile(str(config.DATA_DIR))  # noqa: S606
        except OSError:
            pass

    def _open_pdf(icon=None, item=None) -> None:
        try:
            os.startfile(str(config.get_pdf_dir()))  # noqa: S606
        except OSError:
            pass

    def _quit(icon=None, item=None) -> None:
        try:
            http_server.should_exit = True
        except Exception:  # noqa: BLE001
            pass
        try:
            if icon is not None:
                icon.stop()
        except Exception:  # noqa: BLE001
            pass
        os._exit(0)

    menu = pystray.Menu(
        pystray.MenuItem("显示主窗口", _show, default=True),
        pystray.MenuItem("打开数据目录", _open_data),
        pystray.MenuItem("打开 PDF 目录", _open_pdf),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出 NPL", _quit),
    )
    icon = pystray.Icon("NPL", _tray_image(), "Nexus Paper Library", menu)
    threading.Thread(target=icon.run, name="paperwhale-tray", daemon=True).start()
    logging.getLogger("paperwhale.desktop").info("tray icon started")
    return icon


def _wait_ready(port: int, timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.25)
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _message_box(text: str, title: str = APP_TITLE, error: bool = False) -> None:
    try:
        flags = 0x00000010 if error else 0x00000040
        ctypes.windll.user32.MessageBoxW(None, text, title, flags)
    except Exception:
        print(text)


def main() -> int:
    startup_token = uuid.uuid4().hex
    _set_windows_app_id()
    from paperwhale_server import config

    config.ensure_dirs()
    _setup_logging(config.DATA_DIR)
    log = logging.getLogger("paperwhale.desktop")

    try:
        import uvicorn

        from paperwhale_server.main import app
    except Exception:
        _message_box("NPL 启动失败：\n\n" + traceback.format_exc(), error=True)
        return 1

    port = _pick_port()
    url = f"http://127.0.0.1:{port}"
    http_server = uvicorn.Server(
        uvicorn.Config(
            app,
            host="127.0.0.1",
            port=port,
            log_level="warning",
            access_log=False,
            log_config=None,  # 不用 uvicorn 的 dictConfig（窗口模式下 stderr 为 None）
        )
    )

    def _run_server() -> None:
        try:
            http_server.run()
        except Exception:
            log.exception("本地服务启动失败")
            _message_box("本地服务启动失败：\n\n" + traceback.format_exc(), error=True)

    thread = threading.Thread(target=_run_server, name="paperwhale-server", daemon=True)
    thread.start()

    if not _wait_ready(port):
        _message_box("本地服务启动超时，请查看日志：\n" + str(config.DATA_DIR / "paperwhale.log"), error=True)
        return 1
    log.info("server ready at %s (data: %s)", url, config.DATA_DIR)

    try:
        import webview

        window = webview.create_window(
            APP_TITLE,
            f"{url}/?startup={startup_token}",
            width=1460,
            height=940,
            min_size=(1040, 700),
            background_color="#f4f6f9",
            text_select=True,
            frameless=True,
            # 不要开 easy_drag：pywebview 会在 window 上挂一个 mousedown 监听，
            # 整个窗口任何地方按下鼠标都开始拖窗口——结果在正文里划词选字时，
            # 整个程序框跟着鼠标跑。关掉它，只有标题栏里标了 .pywebview-drag-region
            # 的两块区域（品牌区 + 空白拖动条）能拖窗口，选文字恢复正常。
            easy_drag=False,
        )

        def resize_window(width, height):
            """前端打开详情侧栏时把窗口向右加宽，列表保持原宽度。"""
            try:
                target_w = max(1040, min(int(width), 3840))
                target_h = max(700, min(int(height), 2160))
                window.resize(target_w, target_h)
                return True
            except Exception:  # noqa: BLE001
                return False

        try:
            window.expose(resize_window)
        except Exception:  # noqa: BLE001
            pass

        tray_icon = _create_tray(window, http_server)
        notified = {"hidden": False}

        def _on_closing() -> bool:
            """点 × 时收进系统托盘，不退出；只有托盘菜单「退出」才结束进程。"""
            if tray_icon is None:
                return False
            try:
                window.hide()
            except Exception:  # noqa: BLE001
                pass
            if tray_icon is not None and not notified["hidden"]:
                notified["hidden"] = True
                try:
                    tray_icon.notify("NPL 已收进托盘，右键托盘图标可以退出", APP_TITLE)
                except Exception:  # noqa: BLE001
                    pass
            return False  # pywebview：返回 False 取消默认关闭

        window.events.closing += _on_closing
        # 注意：不要给 events.minimized 挂 hide()。
        # 以前点标题栏的「–」会跟着最小化事件收进托盘，用户看到的像「直接消失到右下角」；
        # 现在的约定是：– 就是最小化到任务栏（正常窗口行为），只有 × 才收进托盘。

        # ---- 自定义窗口框（前端标题栏）用到的窗口操作 ----
        window_state = {"maximized": False}

        def window_minimize() -> bool:
            try:
                window.minimize()
            except Exception:  # noqa: BLE001
                pass
            return True

        def window_toggle_maximize() -> bool:
            try:
                if window_state["maximized"]:
                    window.restore()
                    window_state["maximized"] = False
                else:
                    _maximize_in_work_area(window)
                    window_state["maximized"] = True
            except Exception:  # noqa: BLE001
                pass
            return window_state["maximized"]

        def window_close() -> bool:
            """标题栏的 ×：与原生关闭一致，收进托盘而不是退出。"""
            _on_closing()
            return True

        def window_quit() -> bool:
            try:
                window.destroy()
            except Exception:  # noqa: BLE001
                pass
            return True

        def window_toggle_fullscreen() -> bool:
            return window_toggle_maximize()

        def window_geometry() -> dict:
            """给前端标题栏的缩放/拖动手柄用：返回当前窗口位置与尺寸。"""
            try:
                return {
                    "x": int(window.x),
                    "y": int(window.y),
                    "width": int(window.width),
                    "height": int(window.height),
                }
            except Exception:  # noqa: BLE001
                return {"x": 0, "y": 0, "width": 1460, "height": 940}

        def window_move(x: int, y: int) -> bool:
            try:
                window.move(int(x), int(y))
                return True
            except Exception:  # noqa: BLE001
                return False

        def window_resize(width: int, height: int) -> bool:
            try:
                window.resize(max(1040, int(width)), max(700, int(height)))
                return True
            except Exception:  # noqa: BLE001
                return False

        def pick_folder() -> str:
            """桌面端原生「选择文件夹」对话框，给导入 PDF 文件夹用。"""
            try:
                import webview as webview_module

                result = window.create_file_dialog(webview_module.FOLDER_DIALOG)
                if result:
                    return str(result[0]) if isinstance(result, (list, tuple)) else str(result)
            except Exception:  # noqa: BLE001
                log.exception("选择文件夹失败")
            return ""

        def pick_file() -> str:
            """桌面端原生「选择文件」对话框，给 EndNote XML 这类迁移文件用。"""
            try:
                import webview as webview_module

                result = window.create_file_dialog(
                    webview_module.OPEN_DIALOG,
                    allow_multiple=False,
                    file_types=(
                        "EndNote 导出 (*.xml;*.enl;*.ris;*.bib)",
                        "所有文件 (*.*)",
                    ),
                )
                if result:
                    return str(result[0]) if isinstance(result, (list, tuple)) else str(result)
            except Exception:  # noqa: BLE001
                log.exception("选择文件失败")
            return ""

        def open_guide() -> bool:
            """打开随包的《使用说明.txt》，没有就退回 docs 目录。

            注意：PyInstaller onefile 下 ``ROOT`` 是临时解包目录（``_MEIPASS``），
            而安装器/便携版把 使用说明.txt 放在 **exe 同目录**，所以两个位置都要找，
            并且 exe 目录优先；都没有时再退回仓库布局的 docs/。找不到要记日志，
            否则前端只会「点了没反应」。
            """
            exe_dir = Path(sys.executable).resolve().parent
            candidates = (
                exe_dir / "使用说明.txt",
                ROOT / "使用说明.txt",
                exe_dir / "docs" / "使用说明.txt",
                ROOT / "docs" / "使用说明.txt",
            )
            for candidate in candidates:
                try:
                    if candidate.is_file():
                        os.startfile(str(candidate))  # noqa: S606 - 仅打开本机文档
                        return True
                except OSError:
                    log.exception("打开使用说明失败：%s", candidate)
                    continue
            log.warning("找不到使用说明文件，尝试过的位置：%s", [str(p) for p in candidates])
            return False

        for handler in (
            window_minimize,
            window_toggle_maximize,
            window_close,
            window_quit,
            window_toggle_fullscreen,
            window_geometry,
            window_move,
            window_resize,
            pick_folder,
            pick_file,
            open_guide,
        ):
            try:
                window.expose(handler)
            except Exception:  # noqa: BLE001
                log.exception("窗口操作暴露失败：%s", getattr(handler, "__name__", handler))

        def _restore_resize_borders() -> None:
            """frameless 会丢掉 Windows 的缩放边框，这里把 THICKFRAME/MAXIMIZEBOX 加回去；
            同时把 DWM 画的窗口边框/标题区颜色刷成标题栏的深色，避免顶部出现系统灰边。"""
            try:
                handle = window.native.Handle.ToInt32()
            except Exception:  # noqa: BLE001
                try:
                    handle = window.native.handle.ToInt32()
                except Exception:  # noqa: BLE001
                    log.info("找不到原生窗口句柄，跳过窗口边框处理")
                    return

            def set_attr(attribute: int, value: int) -> None:
                try:
                    number = ctypes.c_int(value)
                    ctypes.windll.dwmapi.DwmSetWindowAttribute(
                        ctypes.c_void_p(handle), attribute, ctypes.byref(number), ctypes.sizeof(number)
                    )
                except Exception:  # noqa: BLE001
                    log.debug("DwmSetWindowAttribute(%s) 失败", attribute)

            # 标题栏底色 #0a1a2b 的 COLORREF（0x00BBGGRR）
            dark = 0x002B1A0A
            set_attr(20, 1)   # DWMWA_USE_IMMERSIVE_DARK_MODE
            set_attr(34, dark)  # DWMWA_BORDER_COLOR：把窗口外框刷成同色，去掉系统灰边
            set_attr(35, dark)  # DWMWA_CAPTION_COLOR：标题区同色
            set_attr(33, 2)   # DWMWA_WINDOW_CORNER_PREFERENCE = ROUND

            # 不再给 frameless 窗口加 WS_THICKFRAME：WinForms 会把非客户区画成
            # 系统灰边（用户看到顶部灰框），而且边框点击命中也没生效。
            # 缩放改由前端标题栏的透明拖拽手柄 + window_resize/window_move 完成。

        try:
            window.events.shown += _restore_resize_borders
        except Exception:  # noqa: BLE001
            log.exception("注册窗口显示事件失败")

        webview.start(debug=False, icon=_window_icon_path())
        log.info("window closed")
    except Exception:
        log.exception("WebView 启动失败，回退到系统浏览器")
        _message_box(
            "没能打开应用窗口（可能缺少 WebView2 运行时）。\n"
            f"已尝试用浏览器打开：{url}\n\n"
            "安装 WebView2 Runtime 后可获得原生窗口体验。",
        )
        webbrowser.open(url)
        # 浏览器模式下保持服务运行，直到用户关闭控制台/进程
        try:
            while True:
                time.sleep(1)
        except KeyboardInterrupt:
            pass
    finally:
        http_server.should_exit = True
        thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
