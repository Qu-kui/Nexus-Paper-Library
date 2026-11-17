"""方法与实验参数库：从论文正文抽方法/材料/设备/条件，落到 method_profiles 表。

设计要点：
- 只处理有真实 PDF 的论文（db.pdf_available），没有 PDF 的条目不排队也不提取。
- 正文优先取「方法/实验/装置」相关块，取不到才退化成前 8 页；总长有上限，避免把整篇喂给模型。
- 每篇论文一行（paper_id 主键），重复提取覆盖同一行；失败只写 error，不丢上一次成功的数据。
"""
from __future__ import annotations

import json
import queue
import re
import threading

from .. import config, db, llm
from . import batches
from .activity import serialized, tracked

MAX_TEXT = 12_000
MAX_PARAMETERS = 40
MAX_LIST_ITEMS = 20
FALLBACK_PAGES = 8
# 方法/实验章节的中英文常见写法；命中这些块的正文优先喂给模型
SECTION_RE = re.compile(r"方法|实验|装置|Materials\s+and\s+Methods|Method|Experiment", re.IGNORECASE)

_SYSTEM_PROMPT = (
    "你是一名 AI 方法参数抽取器，负责从论文原文中抽取「方法与实验参数」结构化信息。\n"
    "硬性规则：\n"
    "1. 只依据用户提供的正文节选，严禁编造；原文没有写的内容就不要输出。\n"
    "2. parameters 里的数值必须能在正文里逐字找到（含单位），找不到逐字依据的一律不要列。\n"
    "3. parameters 只收可量化的实验/仿真参数：波长、功率、温度、厚度、尺寸、折射率、步长、迭代次数、"
    "扫描速度等；不要把普通名词当参数。\n"
    "4. method_summary 用中文 1-2 句概括方法与验证方式；experiment_type 取"
    "「数值仿真 / 实验 / 理论推导 / 混合验证」这类短语。\n"
    "5. materials / conditions 是字符串数组，equipment 是对象数组；没有就给空数组 []。\n"
    "只输出一个合法 JSON 对象，不要输出任何其他文字；字符串内不要出现真实换行（用 \\n 转义）：\n"
    "{\n"
    '  "method_summary": "中文 1-2 句话的方法概括",\n'
    '  "experiment_type": "数值仿真|实验|理论推导|混合验证",\n'
    '  "parameters": [{"name": "参数名", "value": "数值", "unit": "单位", "note": "原文依据/备注"}],\n'
    '  "materials": ["材料或样品"],\n'
    '  "equipment": [{"name": "设备名", "note": "型号/用途"}],\n'
    '  "conditions": ["环境或实验条件"]\n'
    "}"
)


def _as_text(value) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return "; ".join(str(item).strip() for item in value if str(item).strip())
    return str(value).strip()


def _as_list(value) -> list:
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [value]
    return []


def _scrub(error) -> str:
    """错误文本截 500 字，并抹掉设置的 API Key，避免密钥进数据库/日志。"""
    text = str(error or "")[:500]
    try:
        settings = config.get_settings()
    except Exception:  # noqa: BLE001 - 读设置失败时不做替换
        settings = {}
    for key in ("api_key", "openalex_api_key"):
        secret = str(settings.get(key) or "")
        if secret:
            text = text.replace(secret, "***")
    return text


def method_text(paper: dict) -> str:
    """拼出方法抽取用的正文节选：优先命中方法/实验关键词的块，取不到退化前 8 页。"""
    rows = db.query(
        "SELECT page, text FROM chunks WHERE paper_id = ? ORDER BY page ASC, id ASC",
        (paper.get("id"),),
    )
    blocks = [
        (int(row["page"] or 0), str(row["text"] or ""))
        for row in rows
        if str(row["text"] or "").strip()
    ]
    if not blocks:
        return ""

    picked: list[tuple[int, str]] = []
    length = 0
    for page, text in blocks:
        if SECTION_RE.search(text):
            picked.append((page, text))
            length += len(text)
            if length >= MAX_TEXT:
                break
    if not picked:
        for page, text in blocks:
            if page > FALLBACK_PAGES:
                continue
            picked.append((page, text))
            length += len(text)
            if length >= MAX_TEXT:
                break

    joined = "\n".join(f"【第{page}页】{text}" for page, text in picked)
    if len(joined) > MAX_TEXT:
        joined = joined[:MAX_TEXT] + "\n……（正文节选结束）"
    return joined


def _normalize(data: dict) -> dict:
    """把模型输出清洗成固定结构：类型统一、条数有上限、空项丢掉。"""
    parameters: list[dict] = []
    for item in _as_list(data.get("parameters")):
        if not isinstance(item, dict):
            continue
        name = _as_text(item.get("name"))
        value = _as_text(item.get("value"))
        if not name and not value:
            continue
        parameters.append(
            {
                "name": name,
                "value": value,
                "unit": _as_text(item.get("unit")),
                "note": _as_text(item.get("note")),
            }
        )
        if len(parameters) >= MAX_PARAMETERS:
            break

    materials = [
        _as_text(item) for item in _as_list(data.get("materials")) if _as_text(item)
    ][:MAX_LIST_ITEMS]

    equipment: list[dict] = []
    for item in _as_list(data.get("equipment")):
        if isinstance(item, dict):
            name = _as_text(item.get("name"))
            if not name:
                continue
            equipment.append({"name": name, "note": _as_text(item.get("note"))})
        else:
            text = _as_text(item)
            if text:
                equipment.append({"name": text, "note": ""})
        if len(equipment) >= MAX_LIST_ITEMS:
            break

    conditions = [
        _as_text(item) for item in _as_list(data.get("conditions")) if _as_text(item)
    ][:MAX_LIST_ITEMS]

    return {
        "method_summary": _as_text(data.get("method_summary")),
        "experiment_type": _as_text(data.get("experiment_type")),
        "parameters": parameters,
        "materials": materials,
        "equipment": equipment,
        "conditions": conditions,
    }


def _save(paper_id: str, payload: dict, status: str, error: str = "") -> None:
    """按 paper_id 覆盖写（唯一行），created_at 保留首次写入时间。"""
    stamp = db.now()
    db.execute(
        "INSERT INTO method_profiles"
        "(paper_id,status,method_summary,experiment_type,data,error,created_at,updated_at)"
        " VALUES(?,?,?,?,?,?,?,?)"
        " ON CONFLICT(paper_id) DO UPDATE SET"
        " status=excluded.status,method_summary=excluded.method_summary,"
        " experiment_type=excluded.experiment_type,data=excluded.data,"
        " error=excluded.error,updated_at=excluded.updated_at",
        (
            paper_id,
            status,
            _as_text(payload.get("method_summary")),
            _as_text(payload.get("experiment_type")),
            json.dumps(payload, ensure_ascii=False),
            _scrub(error),
            stamp,
            stamp,
        ),
    )


def profile_dict(row) -> dict | None:
    """数据库行 → 接口结构；data 列解析失败时退回空数组而不是报错。"""
    if row is None:
        return None
    item = dict(row)
    try:
        payload = json.loads(item.get("data") or "{}")
    except Exception:  # noqa: BLE001 - 坏数据不能挡住列表
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    return {
        "paper_id": item.get("paper_id", ""),
        "status": item.get("status", ""),
        "method_summary": item.get("method_summary", ""),
        "experiment_type": item.get("experiment_type", ""),
        "parameters": payload.get("parameters") or [],
        "materials": payload.get("materials") or [],
        "equipment": payload.get("equipment") or [],
        "conditions": payload.get("conditions") or [],
        "error": item.get("error", ""),
        "created_at": item.get("created_at", ""),
        "updated_at": item.get("updated_at", ""),
    }


@tracked
def extract(paper_id: str) -> dict:
    """抽取单篇论文的方法参数并落库；调用方负责批次状态。失败抛异常。"""
    paper = db.get_paper(paper_id)
    if not paper:
        raise ValueError("论文不存在")
    if not db.pdf_available(paper.get("file_path")):
        raise ValueError("这篇论文还没有 PDF，先补齐 PDF 再提取方法参数")
    if not llm.is_configured() and not llm.is_mock():
        raise llm.LLMError(llm.NOT_CONFIGURED_MESSAGE)

    text = method_text(paper)
    if not text:
        raise ValueError("这篇论文还没有可用的正文索引，请先重建索引再提取方法参数")

    user = (
        f"论文标题：{_as_text(paper.get('title'))}\n"
        f"发表年份/来源：{_as_text(paper.get('year'))} {_as_text(paper.get('venue'))}".strip()
        + f"\n\n正文节选（可能截断）：\n{text}\n\n"
        "请抽取这篇论文的方法与实验参数，输出约定 JSON。"
    )
    data = llm.chat_json(_SYSTEM_PROMPT, user)
    if not isinstance(data, dict):
        raise llm.LLMError("大模型没有返回结构化方法参数，请重试")
    payload = _normalize(data)
    _save(paper_id, payload, "done")
    row = db.query_one("SELECT * FROM method_profiles WHERE paper_id = ?", (paper_id,))
    return {"status": "ok", "profile": profile_dict(row)}


class MethodQueue:
    """后台批量提取队列；写法对齐 services/analysis_queue.py。"""

    def __init__(self, workers: int = 1) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._queued: set[str] = set()
        self._started = False
        self._workers = max(1, workers)
        self._paper_batches: dict[str, str] = {}

    @serialized
    def start(self, paper_ids: list[str] | None = None, only_new: bool = True) -> int:
        """入队提取；only_new=True 时跳过已经有 done 记录的论文。"""
        if paper_ids is not None and not paper_ids:
            return 0
        done = {
            row["paper_id"]
            for row in db.query("SELECT paper_id FROM method_profiles WHERE status = 'done'")
        }
        wanted = set(paper_ids or [])
        ids: list[str] = []
        for row in db.query("SELECT id, file_path FROM papers ORDER BY created_at ASC"):
            if not db.pdf_available(row["file_path"]):
                continue  # 没有真实 PDF 不提取
            if wanted and row["id"] not in wanted:
                continue
            if only_new and row["id"] in done:
                continue
            ids.append(row["id"])

        fresh: list[str] = []
        with self._lock:
            for paper_id in ids:
                if not paper_id or paper_id in self._queued:
                    continue
                self._queued.add(paper_id)
                fresh.append(paper_id)
            batch_id = batches.create("methods", fresh)
            for paper_id in fresh:
                self._paper_batches[paper_id] = batch_id

        if fresh:
            stamp = db.now()
            for paper_id in fresh:
                db.execute(
                    "INSERT INTO method_profiles(paper_id,status,created_at,updated_at)"
                    " VALUES(?,'queued',?,?)"
                    " ON CONFLICT(paper_id) DO UPDATE SET status='queued',error='',updated_at=excluded.updated_at",
                    (paper_id, stamp, stamp),
                )
        for paper_id in fresh:
            self._queue.put(paper_id)
        if fresh:
            with self._lock:
                if not self._started:
                    self._started = True
                    for index in range(self._workers):
                        threading.Thread(
                            target=self._worker, name=f"pw-methods-{index}", daemon=True
                        ).start()
        return len(fresh)

    def status(self) -> dict:
        return batches.status(batches.current("methods"))

    def has_inflight(self) -> bool:
        with self._lock:
            return bool(self._queued)

    def resume(self) -> int:
        ids = [
            row["paper_id"]
            for row in db.query(
                "SELECT DISTINCT t.paper_id FROM task_batch_items t"
                " JOIN task_batches b ON b.id = t.batch_id"
                " WHERE b.kind = 'methods' AND t.state IN ('queued','running')"
            )
        ]
        batches.interrupt_unfinished("methods")
        if ids and (llm.is_configured() or llm.is_mock()):
            return self.start(ids, only_new=False)
        return 0

    def _worker(self) -> None:
        while True:
            paper_id = self._queue.get()
            try:
                batches.finish(self._paper_batches.get(paper_id), paper_id, "running")
                db.execute(
                    "UPDATE method_profiles SET status = 'running', updated_at = ? WHERE paper_id = ?",
                    (db.now(), paper_id),
                )
                result = extract(paper_id)
                batches.finish(
                    self._paper_batches.get(paper_id),
                    paper_id,
                    "succeeded" if result.get("status") == "ok" else "failed",
                )
            except Exception as exc:  # noqa: BLE001 - 单篇失败不能拖垮队列
                error = _scrub(exc)
                batches.finish(self._paper_batches.get(paper_id), paper_id, "failed", error)
                # 失败只覆盖状态与 error，保留上一次成功抽取的数据
                stamp = db.now()
                db.execute(
                    "INSERT INTO method_profiles(paper_id,status,error,created_at,updated_at)"
                    " VALUES(?,'failed',?,?,?)"
                    " ON CONFLICT(paper_id) DO UPDATE SET status='failed',"
                    " error=excluded.error,updated_at=excluded.updated_at",
                    (paper_id, error, stamp, stamp),
                )
            finally:
                with self._lock:
                    self._queued.discard(paper_id)
                self._queue.task_done()


queue_manager = MethodQueue()
