"""浏览器扩展 / Word 插件的安装位置与一键安装接口。"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/api/plugins", tags=["plugins"])

WORD_FILES = ("PaperWhaleMacros.dotm",)


def _base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[2]


def _dev_root() -> Path:
    return Path(__file__).resolve().parents[2]


def extension_dir() -> Path:
    base = _base_dir()
    candidate = base / "browser-extension"
    if candidate.exists():
        return candidate
    return _dev_root() / "extension"


def word_dir() -> Path:
    base = _base_dir()
    candidate = base / "word"
    if candidate.exists():
        return candidate
    return _dev_root() / "word"


def word_startup() -> Path:
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    return Path(appdata) / "Microsoft" / "Word" / "STARTUP"


class OpenIn(BaseModel):
    kind: str = "extension"


def _state() -> dict:
    ext = extension_dir()
    wdir = word_dir()
    startup = word_startup()
    installed = [name for name in WORD_FILES if (startup / name).exists()]
    return {
        "extension_dir": str(ext),
        "extension_exists": ext.exists(),
        "word_dir": str(wdir),
        "word_startup": str(startup),
        "word_templates": [str(wdir / name) for name in WORD_FILES if (wdir / name).exists()],
        "word_installed": installed,
        "word_ok": len(installed) == len(WORD_FILES),
    }


@router.get("")
def plugin_state() -> dict:
    return _state()


@router.post("/open")
def open_folder(body: OpenIn) -> dict:
    if body.kind == "word":
        target = word_startup() if word_startup().exists() else word_dir()
    else:
        target = extension_dir()
    if not target.exists():
        raise HTTPException(404, f"目录不存在：{target}")
    try:
        os.startfile(str(target))  # noqa: S606
    except OSError as exc:
        raise HTTPException(500, f"打开目录失败：{exc}") from exc
    return {"ok": True, "path": str(target)}


@router.post("/install-word")
def install_word() -> dict:
    """兼容旧前端入口：真正的安装逻辑在 routers/word.py（含 Word 占用检测）。"""
    from . import word as word_routes

    result = word_routes.install()
    return {"ok": True, "copied": [result["target_path"]], "restart_word": True}
