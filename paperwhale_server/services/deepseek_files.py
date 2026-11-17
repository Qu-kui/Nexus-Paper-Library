"""DeepSeek Files API：把论文 PDF 交给服务端解析，供对话引用。

只用于 api.deepseek.com 的 deepseek-flash（全家桶能力）。解析结果不落盘，
这里缓存的是 ``file_id``，按「解析方式 + 文件内容指纹」复用，避免重复上传计费。
"""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

import httpx

from .. import config, llm

#: 解析方式：只要文字 / 连图一起解析（按位置还原图表）
PURPOSE_TEXT = "text_recognition"
PURPOSE_RICH = "rich_layout_recognition"
PURPOSES = {PURPOSE_TEXT, PURPOSE_RICH}

#: 上传 + 解析的服务端时限（文档：上传 120s、解析 120s）
UPLOAD_TIMEOUT_SECONDS = 300.0
#: 文件大小上限（文档：100 MiB）
MAX_BYTES = 100 * 1024 * 1024

_CACHE_NAME = "deepseek-files.json"
_lock = threading.RLock()


class FileParseError(Exception):
    """上传或解析失败（可读的中文提示）。"""


def available() -> bool:
    """当前连接是否支持文件输入。"""
    return llm.supports_app_api() and bool(llm.app_api_enabled())


def _cache_path() -> Path:
    return config.DATA_DIR / _CACHE_NAME


def _read_cache() -> dict:
    try:
        with _lock:
            raw = _cache_path().read_text(encoding="utf-8")
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write_cache(data: dict) -> None:
    try:
        with _lock:
            _cache_path().parent.mkdir(parents=True, exist_ok=True)
            _cache_path().write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def fingerprint(path: Path) -> str:
    """文件内容指纹：大小 + SHA-256 前 24 位。"""
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"{path.stat().st_size}-{digest.hexdigest()[:24]}"


def _cache_key(path: Path, purpose: str, base_url: str) -> str:
    return f"{base_url}|{purpose}|{fingerprint(path)}"


def cached_file_id(path: Path, purpose: str = PURPOSE_TEXT) -> str:
    """已解析过的文件直接复用 file_id；没有则返回空串。"""
    path = Path(path)
    if not path.is_file():
        return ""
    base_url = str(config.llm_config().get("base_url") or "").rstrip("/")
    row = _read_cache().get(_cache_key(path, purpose, base_url)) or {}
    return str(row.get("file_id") or "")


def upload_pdf(path: Path, purpose: str = PURPOSE_TEXT) -> str:
    """上传并解析 PDF，返回 file_id（命中缓存时不上传）。"""
    path = Path(path)
    if purpose not in PURPOSES:
        raise FileParseError("不支持的解析方式")
    if not path.is_file():
        raise FileParseError("找不到这篇论文的 PDF 文件")
    if path.stat().st_size > MAX_BYTES:
        raise FileParseError("PDF 超过 100 MB，无法交给模型解析")
    if not available():
        raise FileParseError("当前模型连接不支持文件解析（需要 api.deepseek.com + deepseek-flash）")

    cfg = config.llm_config()
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    key = _cache_key(path, purpose, base_url)
    cached = _read_cache().get(key) or {}
    if cached.get("file_id"):
        return str(cached["file_id"])

    url = f"{base_url}/files"
    headers = {"Authorization": f"Bearer {cfg.get('api_key') or ''}"}
    try:
        with open(path, "rb") as handle:
            # purpose 必须排在 file 之前，httpx 会先写 data 再写文件字段
            response = httpx.post(
                url,
                headers=headers,
                data={"purpose": purpose},
                files={"file": (path.name, handle, "application/pdf")},
                timeout=UPLOAD_TIMEOUT_SECONDS,
            )
    except httpx.TimeoutException as exc:
        raise FileParseError("上传解析超时，请稍后重试") from exc
    except httpx.HTTPError as exc:
        raise FileParseError(f"上传失败：{exc}") from exc

    if response.status_code >= 400:
        detail = ""
        try:
            detail = str((response.json() or {}).get("error") or "")
        except ValueError:
            detail = response.text[:200]
        hints = {
            413: "PDF 超过 100 MB",
            408: "上传超时",
            429: "同时解析的文件太多，请稍后重试",
            504: "服务端解析超时，请稍后重试",
        }
        hint = hints.get(response.status_code) or detail or "服务端拒绝解析"
        raise FileParseError(f"文件解析失败（{response.status_code}）：{hint}")

    try:
        file_id = str((response.json() or {}).get("id") or "")
    except ValueError as exc:
        raise FileParseError("文件解析返回无法解析") from exc
    if not file_id:
        raise FileParseError("文件解析没有返回 file_id")

    cache = _read_cache()
    cache[key] = {"file_id": file_id, "purpose": purpose, "name": path.name}
    _write_cache(cache)
    return file_id


def forget_all() -> None:
    """清空 file_id 缓存（换服务商或想重新解析时用）。"""
    _write_cache({})
