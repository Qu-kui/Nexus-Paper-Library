"""Word 插件模板管理 API：查看 / 安装 / 卸载宏模板。

模板固定叫 PaperWhaleMacros.dotm，装到 ``%APPDATA%\\Microsoft\\Word\\STARTUP``，
Word 启动时会自动加载该目录里的模板。源文件解析顺序：
``PAPERWHALE_WORD_SOURCE`` 环境变量 → ``config.BUNDLE_DIR/word/PaperWhaleMacros.dotm``。

路径与环境变量都在调用时解析（不在 import 时固化），测试才能用临时 APPDATA。
"""
from __future__ import annotations

import os
import shutil
import sys
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel

from .. import config, db
from ..services import citation

router = APIRouter(prefix="/api/word", tags=["word"])

TEMPLATE_NAME = "PaperWhaleMacros.dotm"
WORD_RUNNING_MESSAGE = "请先关闭 Word 再安装"
#: Word「插入引文」弹窗列表：字段长度上限与返回上限
AUTHORS_MAX = 40
TITLE_MAX = 200
VENUE_MAX = 80
PAPERS_LIMIT_MAX = 500
#: scope 取这个值表示只看未分类
SCOPE_UNCATEGORIZED = "__none__"

# ---------- 引文选择窗口回传（Word 侧轮询） ---------- #
#: token 结果保留时长（秒），过期由 GET 顺手清理
PICK_TTL_SECONDS = 300.0
PICK_TOKEN_MAX = 64
PICK_IDS_MAX = 500
#: ids 为空时的「取消」标记，让 Word 立刻退出等待
PICK_CANCELLED = "__CANCELLED__"

_pick_tokens: dict[str, dict] = {}
_pick_lock = threading.Lock()


class PickIn(BaseModel):
    """用 Any 接住请求体，类型不对时统一转 400 而不是 FastAPI 的 422。"""

    token: Any = None
    ids: Any = None


def _appdata() -> Path:
    raw = os.environ.get("APPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Roaming"


def _word_dir() -> Path:
    """``%APPDATA%\\Microsoft\\Word``：Word 的用户配置目录。"""
    return _appdata() / "Microsoft" / "Word"


def _target_path() -> Path:
    return _word_dir() / "STARTUP" / TEMPLATE_NAME


def _source_path() -> Path:
    raw = (os.environ.get("PAPERWHALE_WORD_SOURCE") or "").strip()
    if raw:
        return Path(raw)
    # 打包后 word/ 跟 PaperWhale.exe 同目录（PyInstaller onedir）；开发态在仓库根
    if getattr(sys, "frozen", False):
        base = Path(sys.executable).resolve().parent
    else:
        base = Path(__file__).resolve().parents[2]
    return base / "word" / TEMPLATE_NAME


def _registry_word_detected() -> bool:
    """注册表 App Paths 里有 Winword.exe 就算装了 Word；非 Windows / 读不到返回 False。"""
    if sys.platform != "win32":
        return False
    try:
        import winreg
    except ImportError:  # pragma: no cover - 非 Windows
        return False
    relative = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\Winword.exe"
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(root, relative):
                return True
        except OSError:
            continue
        except Exception:  # noqa: BLE001 - 权限异常等一律按未检测到处理
            continue
    return False


def _word_detected() -> bool:
    try:
        if _word_dir().is_dir():
            return True
    except OSError:
        pass
    return _registry_word_detected()


def _word_running() -> bool:
    """进程里有 WINWORD.EXE 就算 Word 正在运行。"""
    try:
        import psutil
    except ImportError:  # pragma: no cover - 仓库已依赖 psutil
        return False
    try:
        for process in psutil.process_iter(["name"]):
            try:
                if str(process.info.get("name") or "").upper() == "WINWORD.EXE":
                    return True
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:  # noqa: BLE001 - 进程枚举失败不阻塞接口
        return False
    return False


@router.get("/status")
def status() -> dict:
    source = _source_path()
    target = _target_path()
    return {
        "word_detected": _word_detected(),
        "installed": target.is_file(),
        "target_path": str(target),
        "source_path": str(source),
        "source_available": source.is_file(),
        "running": _word_running(),
    }


@router.post("/install")
def install() -> dict:
    source = _source_path()
    if not source.is_file():
        raise HTTPException(400, "找不到 Word 模板文件，请重新安装 NPL")
    target = _target_path()
    replaced = target.is_file()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
    except OSError as exc:
        # Word 打开着同名模板时是独占锁，复制会 PermissionError
        if _word_running():
            raise HTTPException(400, WORD_RUNNING_MESSAGE) from exc
        raise HTTPException(400, f"安装 Word 模板失败：{exc}") from exc
    return {
        "ok": True,
        "installed": True,
        "target_path": str(target),
        "replaced": replaced,
        "running": _word_running(),
    }


@router.post("/uninstall")
def uninstall() -> dict:
    target = _target_path()
    if not target.is_file():
        return {"ok": True, "removed": False}
    try:
        target.unlink()
    except OSError as exc:
        raise HTTPException(400, "请先关闭 Word 再卸载") from exc
    return {"ok": True, "removed": True}


# ---------- Word「插入引文」弹窗用的全库列表（纯文本，VBA 解析） ---------- #


def _cell(value) -> str:
    """纯文本单元格：制表符/换行一律换成空格，避免 VBA 按行列解析错位。"""
    return str(value or "").replace("\t", " ").replace("\n", " ").replace("\r", " ")


def _truncate(text: str, limit: int, ellipsis: bool = False) -> str:
    if len(text) <= limit:
        return text
    if ellipsis and limit > 0:
        return text[: limit - 1] + "…"
    return text[:limit]


def _escape_like(value: str) -> str:
    """LIKE 模式转义：与 /api/papers 的 category 过滤保持同一套语义。"""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _short_authors(raw: str) -> str:
    """短作者串：第一作者姓，多位作者加 `` et al.``，最长 40 字符。"""
    names = citation._authors({"authors": raw})
    if not names:
        return ""
    family = citation._split_name(names[0])[0]
    text = f"{family} et al." if len(names) > 1 else family
    return _truncate(_cell(text), AUTHORS_MAX)


@router.get("/papers.txt", response_class=PlainTextResponse)
def papers_text(q: str = "", limit: int = 500, scope: str = "") -> str:
    """插入引文弹窗的数据源：第一行统计，其后每行一篇（``\\t`` 分列、``\\n`` 分行）。

    ``scope``：空=全部；``__none__``=未分类；其它值=该分类完整路径（含所有子类，
    与 /api/papers?category= 同一套语义）；与 ``q`` 是 AND 关系。
    """
    keyword = (q or "").strip()
    scope_path = (scope or "").strip()
    try:
        limit_value = int(limit) if limit is not None else PAPERS_LIMIT_MAX
        limit = max(1, min(limit_value, PAPERS_LIMIT_MAX))
        conditions: list[str] = []
        params: list = []
        if scope_path:
            if scope_path == SCOPE_UNCATEGORIZED:
                conditions.append("COALESCE(category,'') = ''")
            else:
                conditions.append("(category = ? OR category LIKE ? ESCAPE '\\')")
                params.extend([scope_path, _escape_like(scope_path) + " / %"])
        if keyword:
            conditions.append(
                "(lower(COALESCE(title,'')) LIKE ?"
                " OR lower(COALESCE(authors,'')) LIKE ?"
                " OR lower(COALESCE(venue,'')) LIKE ?"
                " OR lower(COALESCE(doi,'')) LIKE ?)"
            )
            like = f"%{keyword.lower()}%"
            params.extend([like, like, like, like])
        where = (" WHERE " + " AND ".join(conditions)) if conditions else ""
        total_row = db.query_one(f"SELECT COUNT(*) AS c FROM papers{where}", params)
        total = int(total_row["c"]) if total_row else 0
        rows = db.query(
            "SELECT id, title, authors, year, venue, pdf_available(file_path) AS has_pdf"
            " FROM papers"
            + where
            + " ORDER BY CASE WHEN COALESCE(year,'') = '' THEN 1 ELSE 0 END,"
            " CAST(COALESCE(year,'0') AS INTEGER) DESC,"
            " title COLLATE NOCASE ASC, id ASC LIMIT ?",
            params + [limit],
        )
    except Exception:  # noqa: BLE001 - Word 弹窗不能因为查询异常拿 500
        return "#count\t0\t0"

    lines = [f"#count\t{total}\t{len(rows)}"]
    for row in rows:
        lines.append(
            "\t".join(
                (
                    _cell(row["id"]),
                    _cell(row["year"]),
                    _short_authors(row["authors"] or ""),
                    _truncate(_cell(row["title"] or ""), TITLE_MAX, ellipsis=True),
                    _truncate(_cell(row["venue"] or ""), VENUE_MAX),
                    "1" if int(row["has_pdf"] or 0) else "0",
                )
            )
        )
    return "\n".join(lines)


@router.get("/categories.txt", response_class=PlainTextResponse)
def categories_text() -> str:
    """分类下拉的数据源：``#stats`` 行 + 前序遍历的 ``<depth> <path> <count>``。

    count 是「该分类及所有子类」的论文数；父节点即使没有直接挂论文，只要子树里有
    也会输出一行，保证 VBA 按 depth 缩进时不会跳级。
    """
    try:
        total_row = db.query_one("SELECT COUNT(*) AS c FROM papers")
        papers_total = int(total_row["c"]) if total_row else 0
        uncategorized_row = db.query_one(
            "SELECT COUNT(*) AS c FROM papers WHERE COALESCE(category,'') = ''"
        )
        uncategorized = int(uncategorized_row["c"]) if uncategorized_row else 0
        counts = db.query(
            "SELECT category, COUNT(*) AS c FROM papers"
            " WHERE COALESCE(category,'') <> '' GROUP BY category"
        )
        subtree: dict[str, int] = {}
        for row in counts:
            pieces = [piece.strip() for piece in str(row["category"] or "").split(" / ")]
            pieces = [piece for piece in pieces if piece]
            if not pieces:
                continue
            count = int(row["c"] or 0)
            for index in range(1, len(pieces) + 1):
                node = " / ".join(pieces[:index])
                subtree[node] = subtree.get(node, 0) + count
    except Exception:  # noqa: BLE001 - Word 弹窗不能因为查询异常拿 500
        return "#stats\t0\t0\t0"

    rows = sorted(subtree.items(), key=lambda item: tuple(item[0].split(" / ")))
    lines = [f"#stats\t{papers_total}\t{uncategorized}\t{len(rows)}"]
    for path, count in rows:
        depth = len(path.split(" / ")) - 1
        lines.append("\t".join((str(depth), _cell(path), str(count))))
    return "\n".join(lines)


# ---------- 引文选择窗口回传：Word 提交结果，Word 轮询取回 ---------- #


def _purge_expired_picks(now: float) -> None:
    """清掉超过 TTL 的 token（调用方需持有 _pick_lock）。"""
    for key, item in list(_pick_tokens.items()):
        if now - float(item.get("ts") or 0.0) > PICK_TTL_SECONDS:
            _pick_tokens.pop(key, None)


@router.post("/pick")
def pick_submit(body: PickIn) -> dict:
    """网页选择窗口提交结果；同一 token 重复提交以最后一次为准。"""
    token = body.token
    if not isinstance(token, str) or not token.strip() or len(token) > PICK_TOKEN_MAX:
        raise HTTPException(400, "token 不合法")
    raw_ids = body.ids if body.ids is not None else []
    if not isinstance(raw_ids, list):
        raise HTTPException(400, "ids 必须是字符串数组")
    ids: list[str] = []
    seen: set[str] = set()
    for item in raw_ids:
        if not isinstance(item, str):
            continue
        paper_id = item.strip()
        if not paper_id or paper_id in seen:
            continue
        seen.add(paper_id)
        ids.append(paper_id)
        if len(ids) >= PICK_IDS_MAX:
            break
    with _pick_lock:
        _pick_tokens[token] = {"ids": ids, "ts": time.time()}
    return {"ok": True}


@router.get("/pick", response_class=PlainTextResponse)
def pick_result(token: str = "") -> str:
    """Word 轮询取结果（一次性）：``id1,id2`` / ``__CANCELLED__`` / 空串。"""
    now = time.time()
    with _pick_lock:
        _purge_expired_picks(now)
        entry = _pick_tokens.pop(token, None) if token else None
    if not entry:
        return ""
    ids = entry.get("ids") or []
    if not ids:
        return PICK_CANCELLED
    return ",".join(str(item) for item in ids)
