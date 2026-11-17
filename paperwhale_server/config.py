"""PaperWhale 全局配置与设置持久化。"""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

FROZEN = bool(getattr(sys, "frozen", False))

if FROZEN:
    # PyInstaller：静态资源在 _MEIPASS，用户数据放 %APPDATA%\PaperWhale
    BUNDLE_DIR = Path(getattr(sys, "_MEIPASS", Path(sys.executable).resolve().parent))
    EXE_DIR = Path(sys.executable).resolve().parent
    # 便携版：exe 旁边放一个 portable.flag，数据就存 exe 同目录的 data/
    if (EXE_DIR / "portable.flag").exists():
        DEFAULT_DATA_DIR = EXE_DIR / "data"
    else:
        DEFAULT_DATA_DIR = Path(os.environ.get("APPDATA") or Path.home()) / "PaperWhale"
else:
    BUNDLE_DIR = Path(__file__).resolve().parent.parent
    DEFAULT_DATA_DIR = BUNDLE_DIR / "data"

BASE_DIR = BUNDLE_DIR
DATA_DIR = Path(os.environ.get("PAPERWHALE_DATA", DEFAULT_DATA_DIR)).resolve()

#: 程序根目录（打包后=exe 所在目录；源码运行时=仓库根目录）
PROGRAM_DIR = EXE_DIR if FROZEN else Path(__file__).resolve().parents[1]
DEFAULT_PDF_DIR = DATA_DIR / "pdfs"
TMP_DIR = DATA_DIR / "tmp"
DB_PATH = DATA_DIR / "paperwhale.db"
SETTINGS_PATH = DATA_DIR / "settings.json"
WEB_DIR = BUNDLE_DIR / "web"
SKILLS_DIR = BUNDLE_DIR / "skills"

DEFAULT_SETTINGS = {
    "base_url": "https://api.deepseek.com/v1",
    "api_key": "",
    "model": "deepseek-chat",
    "reasoning_model": "",
    "vision_model": "",
    "temperature": 0.3,
    "language": "zh",
    "ui_accent": "#0e7c86",
    "ui_theme": "midnight",
    "profile": "",
    "profile_name": "",
    "summary_language": "zh",
    "pdf_dir": "",
    "category_guide": "",
    "openalex_api_key": "",
    "openalex_content_enabled": False,
    "unpaywall_email": "",
    "pdf_browser": "Edge",
    "pdf_browser_profile": "Default",
    "pdf_browser_session_enabled": True,
    "scholar_email": "",
    "scholar_imap_host": "imap.gmail.com",
    "scholar_password": "",
    "scholar_folder": "INBOX",
    "scholar_auto_sync": True,
    "scholar_last_sync": "",
    "clipboard_watch": True,
    "clipboard_title_guess": True,
    # DeepSeek 全家桶（App API）：联网工具、PDF 文件解析、更长的上下文预算
    "thinking_mode": "on",
    "app_api_enabled": True,
    "web_search_mode": "auto",
    "paper_file_mode": "scanned",
    "answer_context_chars": 60000,
    "auto_summary": True,
}

_lock = threading.RLock()
_cache: dict | None = None


def get_pdf_dir() -> Path:
    """论文 PDF 的保存目录：设置里的 pdf_dir，空则用 data/pdfs。"""
    raw = str(get_settings().get("pdf_dir") or "").strip()
    if not raw:
        DEFAULT_PDF_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_PDF_DIR
    try:
        path = Path(os.path.expandvars(os.path.expanduser(raw))).resolve()
        path.mkdir(parents=True, exist_ok=True)
        return path
    except OSError:
        DEFAULT_PDF_DIR.mkdir(parents=True, exist_ok=True)
        return DEFAULT_PDF_DIR


def resolve_pdf_path(file_path: str) -> Path:
    """把数据库里的 file_path 解析成绝对路径（兼容旧版相对路径）。"""
    raw = str(file_path or "").strip()
    if not raw:
        # 空路径绝不能解析成 DATA_DIR，否则移动文件时可能把整个数据目录搬走
        return DATA_DIR / "__missing_pdf__.pdf"
    path = Path(raw)
    if path.is_absolute():
        return path
    return (DATA_DIR / path).resolve()


_PDF_BAD_CHARS = set('\\/:*?"<>|\r\n\t')


def sanitize_segment(text: str, fallback: str = "未分类") -> str:
    """分类名 → 安全的文件夹名。"""
    cleaned = "".join(ch for ch in str(text or "").strip() if ch not in _PDF_BAD_CHARS)
    cleaned = " ".join(cleaned.split())
    return (cleaned or fallback)[:40]


def paper_files_root() -> Path:
    """所有论文 PDF 的根目录：<PDF目录>/论文文件/大类/小类/。"""
    root = get_pdf_dir() / "论文文件"
    root.mkdir(parents=True, exist_ok=True)
    return root


def paper_pdf_dir(*segments: str) -> Path:
    """任意层级分类目录：论文文件/一级/二级/三级/...。"""
    parts = [sanitize_segment(str(item)) for item in segments if str(item or "").strip()]
    if not parts:
        parts = ["未分类"]
    target = paper_files_root()
    for part in parts:
        target = target / part
    target.mkdir(parents=True, exist_ok=True)
    return target


def dictionary_dir() -> Path:
    """用户单词本目录：优先程序根目录下的「单词本」，不可写时退到数据目录。"""
    preferred = PROGRAM_DIR / "单词本"
    for candidate in (preferred, DATA_DIR / "单词本"):
        try:
            candidate.mkdir(parents=True, exist_ok=True)
            probe = candidate / ".write-test"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return candidate
        except OSError:
            continue
    return DATA_DIR / "单词本"


def ensure_dirs() -> None:
    for d in (DATA_DIR, get_pdf_dir(), TMP_DIR):
        d.mkdir(parents=True, exist_ok=True)


def get_settings() -> dict:
    global _cache
    with _lock:
        if _cache is None:
            data = dict(DEFAULT_SETTINGS)
            if SETTINGS_PATH.exists():
                try:
                    stored = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
                    if isinstance(stored, dict):
                        data.update({k: v for k, v in stored.items() if v is not None})
                except Exception:
                    pass
            _cache = data
        return dict(_cache)


def update_settings(patch: dict) -> dict:
    global _cache
    with _lock:
        current = dict(_cache) if _cache is not None else get_settings()
        for key, value in (patch or {}).items():
            if key in DEFAULT_SETTINGS:
                current[key] = value
        _cache = current
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
        return dict(current)


def public_settings() -> dict:
    s = get_settings()
    s['openalex_api_key_configured']=bool(s.get('openalex_api_key') or os.environ.get('OPENALEX_API_KEY'))
    s['openalex_api_key']=''
    s["api_key_configured"] = bool(s.get("api_key"))
    s["api_key"] = s.get("api_key", "")
    return s


def llm_config() -> dict:
    s = get_settings()
    return {
        "base_url": (s.get("base_url") or "").rstrip("/"),
        "api_key": s.get("api_key") or "",
        "model": s.get("model") or "",
        "temperature": float(s.get("temperature") or 0.3),
        "language": s.get("summary_language") or s.get("language") or "zh",
    }
