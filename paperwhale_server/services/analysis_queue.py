"""后台批量精读队列：一键把未精读的论文交给 LLM 生成摘要/标签/关联。"""
from __future__ import annotations

import queue
import threading

from .. import db, config, llm
from . import ingest, batches
from .activity import serialized


class AnalysisQueue:
    def __init__(self, workers: int = 1) -> None:
        self._queue: queue.Queue[str] = queue.Queue()
        self._lock = threading.Lock()
        self._queued: set[str] = set()
        self._started = False
        self._workers = max(1, workers)
        self._active = 0
        self._done = 0
        self._succeeded = 0
        self._failed = 0
        self._errors: list[dict] = []
        self._paper_batches = {}

    @serialized
    def start(self, paper_ids: list[str] | None = None, only_new: bool = True) -> int:
        """入队精读任务；only_new=False 时支持对已精读论文重复精读。"""
        if paper_ids is not None and not paper_ids:
            return 0
        rows = db.query("SELECT id, status, summary, file_path FROM papers")
        wanted = set(paper_ids or [])
        ids: list[str] = []
        for row in rows:
            if not db.pdf_available(row['file_path']):
                continue  # 没有 PDF 不精读
            if wanted and row["id"] not in wanted:
                continue
            if only_new and (row["status"] or "") == "analyzed" and (row["summary"] or "").strip():
                continue
            ids.append(row["id"])
        if wanted and not ids:
            # 显式指定的论文都没有 PDF / 都已精读：保持原有行为交给路由判断
            ids = []
        fresh: list[str] = []
        with self._lock:
            for paper_id in ids:
                if not paper_id or paper_id in self._queued:
                    continue
                self._queued.add(paper_id)
                fresh.append(paper_id)
            batch_id=batches.create('analyze',fresh)
            for paper_id in fresh:
                self._paper_batches[paper_id]=batch_id
        for paper_id in fresh:
            self._queue.put(paper_id)
        if fresh:
            with self._lock:
                if not self._started:
                    self._started = True
                    for index in range(self._workers):
                        threading.Thread(
                            target=self._worker, name=f"pw-analyze-{index}", daemon=True
                        ).start()
        return len(fresh)

    def status(self) -> dict:
        return batches.status(batches.current('analyze'))

    def has_inflight(self) -> bool:
        with self._lock:
            return bool(self._queued or self._active)

    def resume(self):
        ids=[row['paper_id'] for row in db.query("SELECT DISTINCT t.paper_id FROM task_batch_items t JOIN task_batches b ON b.id=t.batch_id WHERE b.kind='analyze' AND t.state IN ('queued','running')")]
        batches.interrupt_unfinished('analyze')
        if ids and (llm.is_configured() or llm.is_mock()):
            return self.start(ids,only_new=False)
        return 0

    def _worker(self) -> None:
        while True:
            paper_id = self._queue.get()
            try:
                with self._lock:
                    self._active += 1
                batches.finish(self._paper_batches.get(paper_id),paper_id,'running')
                result = ingest.analyze_paper(paper_id)
                batches.finish(self._paper_batches.get(paper_id),paper_id,'succeeded' if result.get('status')=='ok' else 'failed')
                db.update_paper(paper_id,{'analysis_error':''})
                with self._lock:
                    self._succeeded += 1 if result.get("status") == "ok" else 0
            except Exception as exc:  # noqa: BLE001
                error=str(exc)[:500]
                for key in ('api_key','openalex_api_key'):
                    secret=str(config.get_settings().get(key) or '')
                    if secret:
                        error=error.replace(secret,'***')
                batches.finish(self._paper_batches.get(paper_id),paper_id,'failed',error)
                db.update_paper(paper_id,{'analysis_error':error})
                with self._lock:
                    self._failed += 1
                    self._errors.append({"paper_id": paper_id, "error": str(exc)[:200]})
            finally:
                with self._lock:
                    self._active -= 1
                    self._done += 1
                    self._queued.discard(paper_id)
                self._queue.task_done()


queue_manager = AnalysisQueue()
