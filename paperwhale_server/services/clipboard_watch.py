"""剪贴板监听：复制 DOI / arXiv / 看起来像论文题目的文字时，提示一键入库。

隐私约定（重要）：
- 只在内存里保留最近几条候选项，不写日志、不落库；
- 设置里关掉 ``clipboard_watch`` 后连剪贴板都不读；
- 关闭 ``clipboard_title_guess`` 后只认 DOI / arXiv，不再猜标题。

测试不需要真剪贴板：``poll_once(text)`` 直接把文本喂进来。
"""
from __future__ import annotations

import hashlib
import os
import re
import threading
from typing import Any

from .. import config, db
from . import ingest, metadata

#: 轮询间隔（秒）。剪贴板是全局资源，读太快没必要。
POLL_SECONDS = 0.9

#: 最多保留几条待处理的候选
MAX_ITEMS = 3

#: 已处理 id 的 FIFO 记忆上限，避免重复提示同一段文字
MAX_SEEN = 300

#: 超过这个长度一律不当论文题目（聊天记录/代码块）
MAX_TEXT = 2000

_DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"'<>]+", re.IGNORECASE)
_ARXIV_URL_PATTERN = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
_ARXIV_TAG_PATTERN = re.compile(r"arxiv[:\s]+(\d{4}\.\d{4,5}(?:v\d+)?)", re.IGNORECASE)
_ARXIV_BARE_PATTERN = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?$")

#: 中文标题里常见的研究动词，用来把“一句话”和“论文题目”分开
_CJK_TITLE_HINTS = (
    "基于", "面向", "一种", "利用", "通过", "研究", "方法", "系统", "算法",
    "检测", "成像", "分析", "模型", "实验", "设计", "优化", "测量", "重建",
)
#: 英文标题里常见的小词，配合大写词判断“像标题”
_EN_TITLE_STOPWORDS = ("of", "for", "in", "with", "using", "and", "on", "to", "by", "from")

_lock = threading.RLock()
_items: list[dict[str, Any]] = []
_seen: list[str] = []
_last_hash = ""
_thread: threading.Thread | None = None
_stop = threading.Event()

_ctypes_state: dict[str, Any] | None = None


# ---------------------------------------------------------------------------
# Windows 剪贴板读取
# ---------------------------------------------------------------------------

def _clipboard_api() -> dict[str, Any] | None:
    """懒加载 Win32 剪贴板函数；非 Windows 或失败时返回 None。"""
    global _ctypes_state
    if os.name != "nt":
        return None
    if _ctypes_state is not None:
        return _ctypes_state or None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.WinDLL("user32", use_last_error=True)
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        user32.OpenClipboard.argtypes = [wintypes.HWND]
        user32.OpenClipboard.restype = wintypes.BOOL
        user32.GetClipboardData.argtypes = [wintypes.UINT]
        user32.GetClipboardData.restype = wintypes.HANDLE
        user32.CloseClipboard.restype = wintypes.BOOL
        kernel32.GlobalLock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalLock.restype = wintypes.LPVOID
        kernel32.GlobalUnlock.argtypes = [wintypes.HANDLE]
        kernel32.GlobalUnlock.restype = wintypes.BOOL
        _ctypes_state = {"user32": user32, "kernel32": kernel32}
    except Exception:  # noqa: BLE001 - 没有 Win32 就静默禁用
        _ctypes_state = {}
    return _ctypes_state or None


def read_clipboard_text() -> str:
    """读当前剪贴板文本；失败返回空串（不抛异常）。"""
    api = _clipboard_api()
    if not api:
        return ""
    user32, kernel32 = api["user32"], api["kernel32"]
    CF_UNICODETEXT = 13
    try:
        if not user32.OpenClipboard(None):
            return ""
        try:
            handle = user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return ""
            pointer = kernel32.GlobalLock(handle)
            if not pointer:
                return ""
            try:
                import ctypes

                return str(ctypes.wstring_at(pointer) or "")
            finally:
                kernel32.GlobalUnlock(handle)
        finally:
            user32.CloseClipboard()
    except Exception:  # noqa: BLE001 - 其他程序占着剪贴板时放弃这一轮
        return ""


# ---------------------------------------------------------------------------
# 识别
# ---------------------------------------------------------------------------

def _clean_text(text: str) -> str:
    return " ".join(str(text or "").replace("\r", "\n").split())


def _trim_value(value: str) -> str:
    return str(value or "").strip().strip(".,;:)]}>\"'").strip()


def _looks_like_title(text: str) -> bool:
    """标题启发式：宁可漏，不要动不动就弹提示。"""
    text = _clean_text(text)
    length = len(text)
    if length < 16 or length > 220:
        return False
    if "://" in text or "www." in text.lower():
        return False
    if text[-1] in "。？！?!：" or "？" in text or "！" in text:
        return False
    if re.search(r"[\r\n]", str(text)):
        return False
    has_cjk = bool(re.search(r"[\u4e00-\u9fff]", text))
    if has_cjk:
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text))
        if cjk_chars < 10:
            return False
        hits = sum(1 for hint in _CJK_TITLE_HINTS if hint in text)
        return hits >= 1 or cjk_chars >= 18
    # 英文：至少 4 个词、至少 2 个首字母大写的词，或含标题小词
    words = re.findall(r"[A-Za-z][A-Za-z'\-]*", text)
    if len(words) < 4 or len(words) > 30:
        return False
    capitalized = sum(1 for word in words if word[:1].isupper())
    lowered = {word.lower() for word in words}
    stop_hits = sum(1 for word in _EN_TITLE_STOPWORDS if word in lowered)
    return capitalized >= 2 or stop_hits >= 2


def _candidate_id(kind: str, value: str) -> str:
    return hashlib.sha1(f"{kind}|{value}".encode("utf-8")).hexdigest()[:12]


def detect(text: str, *, title_guess: bool = True) -> dict[str, Any] | None:
    """把一段剪贴板文本识别成候选；不像论文就返回 None。"""
    raw = str(text or "").strip()
    if not raw or len(raw) > MAX_TEXT:
        return None
    flat = _clean_text(raw)
    if not flat:
        return None

    match = _ARXIV_URL_PATTERN.search(flat) or _ARXIV_TAG_PATTERN.search(flat)
    if match:
        arxiv_id = metadata.normalize_arxiv_id(match.group(1))
        if arxiv_id:
            return {"kind": "arxiv", "value": arxiv_id}
    bare = _ARXIV_BARE_PATTERN.match(flat)
    if bare:
        arxiv_id = metadata.normalize_arxiv_id(bare.group(1))
        if arxiv_id:
            return {"kind": "arxiv", "value": arxiv_id}

    doi_match = _DOI_PATTERN.search(flat)
    if doi_match:
        doi = metadata.normalize_doi(_trim_value(doi_match.group(0)))
        if doi:
            return {"kind": "doi", "value": doi}

    if not title_guess or not _looks_like_title(flat):
        return None
    # 复制整段文字（聊天记录、摘要段落）时，标题只会是一两行；超过两行不当题目
    if len([line for line in raw.splitlines() if line.strip()]) > 2:
        return None
    return {"kind": "title", "value": flat}


def _already_in_library(kind: str, value: str) -> str | None:
    """已经在库里的就直接不提示（避免从自己库里复制标题时反复弹窗）。"""
    try:
        if kind == "doi":
            return db.find_duplicate("", value)
        if kind == "arxiv":
            return db.find_duplicate("", "", value)
        return db.find_duplicate(value, "")
    except Exception:  # noqa: BLE001 - 查重失败不要挡住提示
        return None


def _remember(cid: str) -> None:
    _seen.append(cid)
    if len(_seen) > MAX_SEEN:
        del _seen[: len(_seen) - MAX_SEEN]


def _push(item: dict[str, Any]) -> bool:
    """把候选放进列表；同 id 已在列表里就不重复。"""
    if any(existing["id"] == item["id"] for existing in _items):
        return False
    _items.insert(0, item)
    del _items[MAX_ITEMS:]
    return True


def _handle_text(text: str) -> dict[str, Any] | None:
    """处理一段新的剪贴板文本，返回新加入的候选（没有就 None）。"""
    settings = config.get_settings()
    if not settings.get("clipboard_watch", True):
        return None
    title_guess = bool(settings.get("clipboard_title_guess", True))
    found = detect(text, title_guess=title_guess)
    if not found:
        return None
    cid = _candidate_id(found["kind"], found["value"])
    with _lock:
        if cid in _seen or any(existing["id"] == cid for existing in _items):
            return None
    if _already_in_library(found["kind"], found["value"]):
        with _lock:
            _remember(cid)
        return None
    item = {
        "id": cid,
        "kind": found["kind"],
        "value": found["value"],
        "detected_at": db.now(),
    }
    with _lock:
        if not _push(item):
            return None
        _remember(cid)
    return item


def poll_once(text: str) -> dict[str, Any] | None:
    """测试/手动喂入剪贴板内容。"""
    global _last_hash
    flat = str(text or "")
    digest = hashlib.sha1(flat.encode("utf-8", "ignore")).hexdigest()
    with _lock:
        if digest == _last_hash and flat.strip():
            return None
        _last_hash = digest
    return _handle_text(flat)


# ---------------------------------------------------------------------------
# 对外操作
# ---------------------------------------------------------------------------

def snapshot() -> dict[str, Any]:
    settings = config.get_settings()
    with _lock:
        items = [dict(item) for item in _items]
    return {
        "enabled": bool(settings.get("clipboard_watch", True)),
        "title_guess": bool(settings.get("clipboard_title_guess", True)),
        "items": items,
    }


def _take(candidate_id: str) -> dict[str, Any] | None:
    with _lock:
        for index, item in enumerate(_items):
            if item["id"] == candidate_id:
                return _items.pop(index)
    return None


def peek(candidate_id: str) -> dict[str, Any] | None:
    with _lock:
        for item in _items:
            if item["id"] == candidate_id:
                return dict(item)
    return None


def dismiss(candidate_id: str) -> bool:
    return _take(candidate_id) is not None


def accept(candidate_id: str) -> dict[str, Any]:
    """把候选做成论文；返回 {paper, pdf_fetch_queued}。"""
    item = _take(candidate_id)
    if not item:
        raise KeyError("这条提示已经处理过了")
    kind, value = item["kind"], item["value"]
    if kind == "doi":
        meta = metadata.crossref_by_doi(value)
        meta.pop("pdf_url", None)
        paper_id = ingest.create_paper_from_metadata(meta, source="clipboard")
    elif kind == "arxiv":
        meta = metadata.arxiv_meta(value)
        meta.pop("pdf_url", None)
        paper_id = ingest.create_paper_from_metadata(meta, source="clipboard")
    else:
        meta: dict[str, Any] = {}
        try:
            meta = metadata.find_metadata_by_title(value)
        except metadata.MetadataError:
            meta = {}
        if not str(meta.get("title") or "").strip():
            meta = {"title": value}
        paper_id = ingest.create_paper_from_metadata(meta, source="clipboard")
    paper = db.get_paper(paper_id) or {"id": paper_id}
    queued = 0
    if not paper.get("has_pdf"):
        try:
            from .pdf_fetch import manager as pdf_fetch_manager

            queued = pdf_fetch_manager.start([paper_id])
        except Exception:  # noqa: BLE001 - 排队失败不影响入库
            queued = 0
    return {"paper": paper, "pdf_fetch_queued": queued}


# ---------------------------------------------------------------------------
# 后台线程
# ---------------------------------------------------------------------------

def _loop() -> None:
    global _last_hash
    while not _stop.is_set():
        try:
            settings = config.get_settings()
            if not settings.get("clipboard_watch", True):
                _stop.wait(POLL_SECONDS * 2)
                continue
            text = read_clipboard_text()
            digest = hashlib.sha1(text.encode("utf-8", "ignore")).hexdigest()
            with _lock:
                fresh = bool(text.strip()) and digest != _last_hash
                if fresh:
                    _last_hash = digest
            if fresh:
                _handle_text(text)
        except Exception:  # noqa: BLE001 - 监听线程不能因为一次读取失败就退出
            pass
        _stop.wait(POLL_SECONDS)


def start() -> None:
    """启动监听线程（幂等）。"""
    global _thread
    if os.name != "nt":
        return
    with _lock:
        if _thread and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_loop, name="pw-clipboard", daemon=True)
        _thread.start()


def stop() -> None:
    global _thread
    _stop.set()
    _thread = None


def reset() -> None:
    """测试用：清空状态。"""
    global _last_hash, _thread
    with _lock:
        _items.clear()
        _seen.clear()
        _last_hash = ""
    _thread = None
