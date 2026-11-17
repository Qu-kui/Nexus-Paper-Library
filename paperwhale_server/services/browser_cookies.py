"""从本机 Chrome / Edge 读取已登录 Cookie（无需打开浏览器），供出版社 PDF 下载复用。"""
from __future__ import annotations

import base64
import ctypes
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import threading
from pathlib import Path
from urllib.parse import urlparse

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

_CACHE: dict[str, object] = {"loaded": False, "cookies": [], "at": 0.0, 'selection':None}
CACHE_SECONDS = 1800
_COOKIE_LOCK=threading.RLock()


def invalidate_cache():
    with _COOKIE_LOCK:
        _CACHE['loaded']=False

BROWSERS = [
    ("Edge", Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data"),
    ("Chrome", Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data"),
]
PROFILES = ["Default", "Profile 1", "Profile 2", "Profile 3", "Profile 4"]


def _browser_process_names(browser: str) -> set[str]:
    name = str(browser or "Edge").lower()
    if "chrome" in name:
        return {"chrome.exe"}
    return {"msedge.exe"}


def _browser_running(browser: str) -> bool:
    """浏览器开着时 Cookie 库会被锁，rookie 还会额外拉起浏览器，直接跳过。"""
    try:
        import psutil

        names = _browser_process_names(browser)
        for proc in psutil.process_iter(["name"]):
            if (proc.info.get("name") or "").lower() in names:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", ctypes.c_uint32), ("pbData", ctypes.POINTER(ctypes.c_char))]


def _dpapi_unprotect(blob: bytes) -> bytes:
    if not blob:
        return b""
    buffer = ctypes.create_string_buffer(blob, len(blob))
    data_in = _DataBlob(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    data_out = _DataBlob()
    crypt32 = ctypes.windll.crypt32
    if not crypt32.CryptUnprotectData(
        ctypes.byref(data_in), None, None, None, None, 0, ctypes.byref(data_out)
    ):
        return b""
    try:
        return ctypes.string_at(data_out.pbData, data_out.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(data_out.pbData)


def _master_key(user_data: Path) -> bytes:
    state_path = user_data / "Local State"
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
        encoded = state["os_crypt"]["encrypted_key"]
    except Exception:  # noqa: BLE001
        return b""
    try:
        raw = base64.b64decode(encoded)
    except Exception:  # noqa: BLE001
        return b""
    if raw.startswith(b"DPAPI"):
        raw = raw[5:]
    return _dpapi_unprotect(raw)


def _decrypt_value(value: bytes, key: bytes, host_key: str) -> str:
    if not value:
        return ""
    try:
        if value[:3] in {b"v10", b"v11"}:
            if not key:
                return ""
            nonce, ciphertext = value[3:15], value[15:]
            plain = AESGCM(key).decrypt(nonce, ciphertext, None)
            # 新版本 Chrome 会在明文前加 32 字节 host hash
            digest = hashlib.sha256(host_key.encode("utf-8")).digest()
            if plain.startswith(digest):
                plain = plain[32:]
            return plain.decode("utf-8", errors="ignore")
        # 老版本 DPAPI 直接加密值
        return _dpapi_unprotect(value).decode("utf-8", errors="ignore")
    except Exception:  # noqa: BLE001
        return ""


def _copy_db(path: Path) -> Path | None:
    if not path.exists():
        return None
    try:
        handle = tempfile.NamedTemporaryFile(delete=False, suffix=".sqlite")
        handle.close()
        target = Path(handle.name)
        target.write_bytes(path.read_bytes())
        for suffix in ("-wal", "-shm"):
            side = path.with_name(path.name + suffix)
            if side.exists():
                target.with_name(target.name + suffix).write_bytes(side.read_bytes())
        return target
    except Exception:  # noqa: BLE001
        return None


def _load_cookies(browser: str='Edge', profile: str='Default') -> list[dict]:
    cookies: list[dict] = []
    now = time.time()
    for _name, user_data in BROWSERS:
        if _name.lower()!=browser.lower():
            continue
        if not user_data.exists():
            continue
        key = _master_key(user_data)
        for profile in [profile]:
            if profile not in available_profiles(browser):
                continue
            db_path = user_data / profile / "Network" / "Cookies"
            if not db_path.exists():
                db_path = user_data / profile / "Cookies"
            if not db_path.exists():
                continue
            copied = _copy_db(db_path)
            if not copied:
                continue
            try:
                conn = sqlite3.connect(str(copied))
                conn.row_factory = sqlite3.Row
                for row in conn.execute(
                    "SELECT host_key, name, value, encrypted_value, path, expires_utc, is_secure "
                    "FROM cookies"
                ):
                    host = str(row["host_key"] or "")
                    plain = str(row["value"] or "")
                    if not plain:
                        plain = _decrypt_value(row["encrypted_value"] or b"", key, host)
                    if not plain:
                        continue
                    expires = int(row["expires_utc"] or 0)
                    if expires and expires / 1000000 - 11644473600 < now:
                        continue
                    cookies.append(
                        {
                            "domain": host.lstrip(".").lower(),
                            "host": host.lower(),
                            "name": str(row["name"] or ""),
                            "value": plain,
                            "path": str(row["path"] or "/"),
                            "secure": bool(row["is_secure"]),
                        }
                    )
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            finally:
                copied.unlink(missing_ok=True)
                for suffix in ("-wal", "-shm"):
                    copied.with_name(copied.name + suffix).unlink(missing_ok=True)
    return cookies



def _rookie_cookies(browser: str='Edge', profile: str='Default') -> list[dict]:
    """用 rookie-cookies 解 App-Bound v20；只允许在浏览器关闭时调用。"""
    if _browser_running(browser):
        return []
    try:
        import psutil
        import rookie_cookies as rookie
    except Exception:  # noqa: BLE001
        return []
    before = {
        proc.pid
        for proc in psutil.process_iter(["pid", "name"])
        if (proc.info.get("name") or "").lower() in _browser_process_names(browser)
    }
    rows: list[dict] = []
    try:
        jar = rookie.jar(
            browser=browser.lower(),
            profile=profile,
            include_session=True,
            app_bound="injection_only",
            timeout=10,
        )
        for cookie in jar:
            domain = str(getattr(cookie, "domain", "") or "").lstrip(".").lower()
            name = str(getattr(cookie, "name", "") or "")
            value = str(getattr(cookie, "value", "") or "")
            if not domain or not name or not value:
                continue
            rows.append(
                {
                    "domain": domain,
                    "host": ('.' if getattr(cookie, 'domain_specified', False) else '') + domain,
                    "name": name,
                    "value": value,
                    "path": str(getattr(cookie, "path", "/") or "/"),
                    "secure": bool(getattr(cookie, "secure", False)),
                }
            )
    except Exception:  # noqa: BLE001
        return []
    finally:
        # rookie 为了注入会拉起一个临时浏览器；读完/失败后都只关闭这次新起的进程。
        try:
            for proc in psutil.process_iter(["pid", "name"]):
                if (proc.info.get("name") or "").lower() not in _browser_process_names(browser):
                    continue
                if proc.pid in before:
                    continue
                try:
                    proc.terminate()
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            pass
    return rows


def available_profiles(browser: str='Edge') -> list[str]:
    profiles=[]
    for name,root in BROWSERS:
        if name.lower()!=browser.lower() or not root.is_dir():
            continue
        for path in root.iterdir():
            if path.is_dir() and (path.name=='Default' or re.fullmatch(r'Profile \d+',path.name)):
                profiles.append(path.name)
    return sorted(profiles)


def _cookies(force: bool = False, browser: str | None=None, profile: str | None=None) -> list[dict]:
    with _COOKIE_LOCK:
        return _selected_cookies(force,browser,profile)


def _selected_cookies(force: bool = False, browser: str | None=None, profile: str | None=None) -> list[dict]:
    from .. import config

    settings=config.get_settings()
    browser=browser or str(settings.get('pdf_browser') or 'Edge')
    profile=profile or str(settings.get('pdf_browser_profile') or 'Default')
    selection=(browser,profile)
    now = time.time()
    if force or not _CACHE["loaded"] or _CACHE.get('selection')!=selection or now - float(_CACHE["at"] or 0) > CACHE_SECONDS:
        rows = _load_cookies(browser,profile)
        try:
            rows.extend(_rookie_cookies(browser,profile))
        except Exception:
            pass
        # 同域名+名字去重
        seen: set[tuple[str, str]] = set()
        unique: list[dict] = []
        for row in rows:
            key = (row.get("host", ""), row.get("path",'/'), row.get("name", ""))
            if key in seen:
                continue
            seen.add(key)
            unique.append(row)
        _CACHE["cookies"] = unique
        _CACHE["loaded"] = True
        _CACHE["at"] = now
        _CACHE['selection']=selection
    return list(_CACHE["cookies"] or [])


def cookie_header(url: str, force: bool = False, browser: str | None=None, profile: str | None=None) -> str:
    """返回适合该 URL 的 Cookie 头；没有就返回空字符串。"""
    try:
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = parsed.path or "/"
        secure = parsed.scheme.lower() == "https"
    except Exception:  # noqa: BLE001
        return ""
    if not host:
        return ""
    pairs = []
    for cookie in _cookies(force=force,browser=browser,profile=profile):
        domain = cookie["domain"]
        host_only=not str(cookie.get('host') or '').startswith('.')
        if not (host == domain or (not host_only and host.endswith("." + domain))):
            continue
        if cookie["secure"] and not secure:
            continue
        cookie_path = cookie["path"] or "/"
        if not (path==cookie_path or (path.startswith(cookie_path) and (cookie_path.endswith('/') or path[len(cookie_path):].startswith('/')))):
            continue
        pairs.append(f"{cookie['name']}={cookie['value']}")
    return "; ".join(pairs)
