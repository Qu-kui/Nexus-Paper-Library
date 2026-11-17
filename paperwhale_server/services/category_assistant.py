"""分类助手：对话确认分类偏好（支持任意层级），然后批量整理未分类论文。"""
from __future__ import annotations

import json
import queue
import re
import threading
from typing import Any

from .. import config, db, llm
from . import ai_context, batches, categories, skills
from .activity import serialized

_GUIDE_RE = re.compile(r"\[CATEGORY_GUIDE\](.*?)\[/CATEGORY_GUIDE\]", re.S)


def _paper_brief(paper: dict) -> str:
    parts = [str(paper.get("title") or "")]
    if paper.get("venue"):
        parts.append(f"期刊/会议：{paper['venue']}")
    if paper.get("year"):
        parts.append(f"年份：{paper['year']}")
    if paper.get("abstract"):
        parts.append(f"摘要：{str(paper['abstract'])[:600]}")
    if paper.get("summary"):
        parts.append(f"已有精读摘要：{str(paper['summary'])[:600]}")
    return "\n".join(parts)


def _context() -> str:
    paths = categories.taxonomy_paths()
    tree_lines = [
        f"- {item['label']}（{item['count']} 篇）" for item in paths[:80]
    ]
    tree_text = "\n".join(tree_lines) or "（还没有分类）"
    pending = db.query(
        "SELECT id, title, venue, year, summary FROM papers "
        "WHERE COALESCE(category_path,'') IN ('','[]') AND COALESCE(category_major,'') = '' "
        "ORDER BY updated_at DESC LIMIT 40"
    )
    pending_lines = [f"- {row['title']}" for row in pending[:20]]
    guide = str(config.get_settings().get("category_guide") or "").strip()
    total = db.query_one("SELECT COUNT(*) AS n FROM papers WHERE COALESCE(category_path,'') IN ('','[]') AND COALESCE(category_major,'') = ''")['n']
    return ai_context.profile_prior() + '\n\n' + (
        f"当前分类路径：\n{tree_text}\n\n"
        f"未分类论文（共 {total} 篇，以下最多 20 篇示例）：\n" + ("\n".join(pending_lines) or "无") + "\n\n"
        f"用户之前确认的偏好：\n{guide or '还没有'}"
    )


def chat(messages: list[dict]) -> dict:
    manage_skill = skills.task_prompt("manage", max_chars=2500)
    manage_block = (
        "\n自动启用的文献管理规范（只借鉴组织与命名原则，不改变对话目标）：\n" + manage_skill
        if manage_skill
        else ""
    )
    system = (
        "你是 NPL 的「文献分类助手」。目标：通过简短对话确认用户的研究领域和分类偏好，"
        "最终给出一套稳定可复用的分类路径，供程序自动归档论文。\n"
        "规则：\n"
        "1. 一次最多问 1-2 个问题；先理解用户方向，再给建议，不要长篇大论。\n"
        "2. 分类层级可以是 1-6 级；只追问尚未明确的层级和维度，不重复询问画像或已保存偏好已经说明的内容。\n"
        "3. 大类要宽泛（如 光学工程 / 人工智能 / 生物医学），中类和小类要具体但可复用。\n"
        "4. 分类数量克制：一级 3-8 个，每个一级下 2-8 个二级，更深层级按需增加；优先复用已有路径。\n"
        "5. 不要输出论文清单或代码。\n"
        "6. 当用户确认/基本认可后，在回答末尾输出最终偏好块：\n"
        "[CATEGORY_GUIDE]\n用自然语言写清分类原则、层级深度、各级命名偏好、边界情况\n[/CATEGORY_GUIDE]\n"
        "7. 分类助手也可以根据用户新的要求修改偏好；这里只输出草稿，不能声称已经保存或完成归档。\n\n"
        f"{manage_block}"
    ) + ai_context.RULES
    history = ai_context.history_messages((messages or [])[:-1])
    if messages and messages[-1].get('role') == 'user':
        history.append({'role':'user','content':str(messages[-1].get('content') or '')[:16000]})
    if not history:
        history = [{"role": "user", "content": "我想整理文献分类，先帮我确定分类方案。"}]
    history[-1] = {**history[-1], 'content': f'【参考资料区】\n{_context()}\n【资料区结束】\n\n用户当前要求：\n'+history[-1]['content']}
    answer = llm.chat([{"role": "system", "content": system}, *history], temperature=0.4)
    guide = ""
    match = _GUIDE_RE.search(answer or "")
    if match:
        guide = match.group(1).strip()
        answer = _GUIDE_RE.sub("", answer).strip()
    return {"answer": answer, "guide": guide}


class _CategoryQueue:
    def __init__(self, workers: int = 1) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._queued: set[str] = set()
        self._started = False
        self._pending = 0
        self._active = 0
        self._done = 0
        self._succeeded = 0
        self._failed = 0
        self._errors: list[dict] = []
        self._paper_batches: dict[str, str] = {}
        self._paper_move: dict[str, bool] = {}

    @serialized
    def start(self, paper_ids: list[str], *, move: bool = True) -> int:
        fresh: list[str] = []
        batch_id = None
        with self._lock:
            for paper_id in paper_ids:
                if not paper_id or paper_id in self._queued:
                    continue
                self._queued.add(paper_id)
                self._pending += 1
                fresh.append(paper_id)
            if fresh:
                batch_id = batches.create("category", fresh)
                for paper_id in fresh:
                    self._paper_batches[paper_id] = batch_id
                    self._paper_move[paper_id] = bool(move)
            if fresh and not self._started:
                self._started = True
                for index in range(1):
                    threading.Thread(target=self._worker, name=f"pw-cat-{index}", daemon=True).start()
        for paper_id in fresh:
            self._queue.put(paper_id)
        return len(fresh)

    def status(self) -> dict:
        # 批次状态随任务中心一起持久化：关掉分类助手弹窗、甚至重启程序后，
        # 任务中心里还能看到这批整理做到哪了（对比原先只有前端内存里的计数）。
        batch_id = batches.current("category")
        if batch_id:
            return batches.status(batch_id)
        with self._lock:
            return {
                "pending": self._pending,
                "active": self._active,
                "done": self._done,
                "succeeded": self._succeeded,
                "failed": self._failed,
                "errors": self._errors[-5:],
            }

    def has_inflight(self) -> bool:
        with self._lock:
            return bool(self._queued or self._active)

    def resume(self) -> int:
        """程序重启后把没做完的分类批次接着排，并把残留的 queued/running 标成 interrupted。"""
        from .. import llm

        ids = [
            row["paper_id"]
            for row in db.query(
                "SELECT DISTINCT t.paper_id FROM task_batch_items t "
                "JOIN task_batches b ON b.id = t.batch_id "
                "WHERE b.kind='category' AND t.state IN ('queued','running')"
            )
        ]
        batches.interrupt_unfinished("category")
        if ids and (llm.is_configured() or llm.is_mock()):
            return self.start(ids)
        return 0

    def _worker(self) -> None:
        while True:
            paper_id = self._queue.get()
            batch_id = self._paper_batches.get(paper_id)
            with self._lock:
                self._active += 1
                self._pending = max(0, self._pending - 1)
            batches.finish(batch_id, paper_id, "running")
            try:
                self._classify(paper_id)
                batches.finish(batch_id, paper_id, "succeeded")
                with self._lock:
                    self._succeeded += 1
            except Exception as exc:  # noqa: BLE001
                error = str(exc)[:500]
                batches.finish(batch_id, paper_id, "failed", error)
                with self._lock:
                    self._failed += 1
                    self._errors.append({"paper_id": paper_id, "error": str(exc)[:200]})
                try:
                    db.update_paper(paper_id, {"category_error": f"分类失败：{str(exc)[:200]}"})
                except Exception:  # noqa: BLE001
                    pass
            finally:
                with self._lock:
                    self._active -= 1
                    self._done += 1
                    self._queued.discard(paper_id)
                self._queue.task_done()

    def _classify(self, paper_id: str) -> None:
        paper = db.get_paper(paper_id)
        if not paper:
            return
        guide = str(config.get_settings().get("category_guide") or "").strip()
        tree = categories.taxonomy_paths()
        tree_text = "\n".join(f"- {item['label']}" for item in tree[:100]) or "（暂无分类）"
        system = (
            "你是文献分类助手。根据用户确认的分类偏好，为这篇论文选出最合适的分类路径。\n"
            "路径可以是 1-6 级；优先复用已有路径；只有确实无法归入时才建议新路径。\n"
            "深度与用户偏好一致；新增路径需符合已确认偏好。还没有分类时，可以按偏好建立路径。\n"
            '只输出 JSON：{"path": ["一级", "二级", "三级"]}；路径元素不要包含 /'
        ) + ai_context.RULES
        prior = f'【已确认分类偏好】\n{guide or "未提供"}\n【已有分类路径】\n{tree_text}'
        data = llm.chat_json(system, f'【参考资料区】\n{ai_context.profile_prior()}\n{prior}\n{_paper_brief(paper)}\n【资料区结束】\n为当前论文选择路径。')
        levels = data.get("path") if isinstance(data, dict) else None
        if not isinstance(levels, list):
            levels = [
                data.get("major") or data.get("category_major") or "",
                data.get("minor") or data.get("category_minor") or "",
            ]
        levels = categories.clean_levels(levels)
        if not levels:
            raise ValueError("模型没有给出分类路径")
        categories.assign_path(paper_id, levels, move=self._paper_move.get(paper_id, True))
        db.update_paper(paper_id,{'category_error':''})


queue_manager = _CategoryQueue()

_UNCLASSIFIED_SQL = (
    "SELECT id FROM papers WHERE COALESCE(category_major,'') = '' "
    "AND COALESCE(category_path,'') IN ('','[]') ORDER BY updated_at DESC"
)
_ALL_SQL = "SELECT id FROM papers ORDER BY updated_at DESC"


def select_paper_ids(scope: str = "unclassified") -> list[str]:
    """scope=all：全部论文重新分类；其它值：只挑还没分类的（原行为）。"""
    sql = _ALL_SQL if str(scope or "").strip().lower() == "all" else _UNCLASSIFIED_SQL
    return [row["id"] for row in db.query(sql)]


def preview(scope: str = "unclassified") -> dict:
    """UI 确认框用：这次会处理多少篇。"""
    return {
        "scope": "all" if str(scope or "").strip().lower() == "all" else "unclassified",
        "count": len(select_paper_ids(scope)),
        "total": int((db.query_one("SELECT COUNT(*) AS c FROM papers") or {"c": 0})["c"]),
    }


def apply(guide: str = "", scope: str = "unclassified", move_files: bool = True) -> dict:
    guide = (guide or "").strip()
    if guide:
        config.update_settings({"category_guide": guide})
    ids = select_paper_ids(scope)
    queued = queue_manager.start(ids, move=move_files)
    return {
        "queued": queued,
        "scope": "all" if str(scope or "").strip().lower() == "all" else "unclassified",
        "status": queue_manager.status(),
    }
