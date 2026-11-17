"""论文库 API。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import threading
from pathlib import Path

from fastapi import APIRouter, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import config, db
from ..models import PaperPatch

router = APIRouter(prefix="/api", tags=["papers"])


class NoteAppend(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


@router.get('/papers/{paper_id}/notes')
def get_notes(paper_id: str):
    from ..services import imported_notes
    try:
        result = imported_notes.read(paper_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc)) from exc
    if result is None:
        raise HTTPException(404, '论文不存在')
    return result


@router.post('/papers/{paper_id}/notes/append')
def append_notes(paper_id: str, body: NoteAppend):
    from contextlib import closing

    with closing(db.connect()) as conn, conn:
        changed = conn.execute("UPDATE papers SET notes=CASE WHEN COALESCE(notes,'')='' THEN ? ELSE notes || char(10) || char(10) || ? END,updated_at=? WHERE id=?",
                               (body.text, body.text, db.now(), paper_id))
        if not changed.rowcount:
            raise HTTPException(404, '论文不存在')
    return db.get_paper(paper_id)


@router.get("/papers")
def list_papers(
    q: str = "",
    tag: str = "",
    category: str = "",
    status: str = "",
    missing_pdf: bool = False,
    limit: int = 100,
    offset: int = 0,
    sort: str = 'updated',
) -> dict:
    where, params = [], []
    if missing_pdf:
        where.append('pdf_available(file_path)=0')
    if q:
        like = f"%{q}%"
        where.append(
            "(title LIKE ? OR authors LIKE ? OR abstract LIKE ? OR summary LIKE ? OR tags LIKE ? OR category LIKE ?)"
        )
        params.extend([like] * 6)
    if tag:
        where.append("tags LIKE ?")
        params.append(f'%"{tag}"%')
    if category:
        # 分类按「文件夹」语义筛选：选中一级就把它的所有子级也算进来，
        # 否则点了「人工智能」会一篇都筛不出来（数据其实都在二级/三级里）。
        escaped = category.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        where.append("(category = ? OR category LIKE ? ESCAPE '\\')")
        params.extend([category, escaped + " / %"])
    if status:
        where.append("status = ?")
        params.append(status)
    clause = (" WHERE " + " AND ".join(where)) if where else ""
    total_row = db.query_one(f"SELECT COUNT(*) AS c FROM papers{clause}", params)
    total = int(total_row["c"]) if total_row else 0
    orders={'updated':'updated_at DESC, created_at DESC, id ASC','created':'created_at DESC, id ASC',
            'year':"CAST(COALESCE(year,'0') AS INTEGER) DESC,title COLLATE NOCASE ASC,id ASC",
            'venue':"CASE WHEN COALESCE(venue,'')='' THEN 1 ELSE 0 END,venue COLLATE NOCASE ASC,title COLLATE NOCASE ASC,id ASC",
            'title':'title COLLATE NOCASE ASC,id ASC'}
    if sort not in orders:
        raise HTTPException(400,'不支持的排序方式')
    page_size=max(1,min(limit,500))
    offset=max(0,offset)
    rows = db.query(
        f"SELECT *, (SELECT COUNT(*) FROM annotations a WHERE a.paper_id=papers.id) AS annotation_count FROM papers{clause} ORDER BY {orders[sort]} LIMIT ? OFFSET ?",
        params + [page_size,offset],
    )
    items = []
    for row in rows:
        paper = db.paper_to_dict(row)
        paper['annotation_count']=int(row['annotation_count'])
        items.append(paper)
    return {"items": items, "total": total,'limit':page_size,'offset':offset}


@router.get("/tags")
def list_tags() -> dict:
    from ..services import tagging

    counts: dict[str, int] = {}
    display: dict[str, str] = {}
    for row in db.query("SELECT tags FROM papers"):
        try:
            tags = json.loads(row["tags"] or "[]")
        except Exception:
            tags = []
        for raw in tags if isinstance(tags, list) else []:
            tag = tagging.clean_tag(raw)
            if not tag:
                continue
            key = tag.lower()
            counts[key] = counts.get(key, 0) + 1
            display.setdefault(key, tag)
    tags = sorted(
        ({"name": display[key], "count": counts[key]} for key in counts),
        key=lambda x: (-x["count"], x["name"]),
    )
    return {"tags": tags}


@router.get("/categories")
def list_categories() -> dict:
    rows = db.query(
        "SELECT category, COUNT(*) AS c FROM papers WHERE category != '' GROUP BY category ORDER BY c DESC"
    )
    return {"categories": [{"name": r["category"], "count": r["c"]} for r in rows]}


@router.get("/papers/{paper_id}")
def get_paper(paper_id: str) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    return paper


@router.patch("/papers/{paper_id}")
def patch_paper(paper_id: str, body: PaperPatch) -> dict:
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    fields = {k: v for k, v in body.model_dump().items() if v is not None}
    if "notes" not in fields:
        fields.pop("notes", None)
    category = fields.pop("category", None)
    db.update_paper(paper_id, fields)
    if category is not None:
        from ..services import categories

        try:
            categories.apply_category_string(paper_id, category)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
    return db.get_paper(paper_id) or {}


@router.post("/papers/{paper_id}/reveal")
def reveal_paper(paper_id: str) -> dict:
    """在资源管理器里定位这篇论文的 PDF。"""
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    rel = paper.get("file_path") or ""
    target = config.resolve_pdf_path(rel) if rel else None
    try:
        if target and target.exists():
            subprocess.Popen(["explorer", "/select,", str(target)])
            return {"ok": True, "path": str(target), "selected": True}
        folder = config.get_pdf_dir()
        folder.mkdir(parents=True, exist_ok=True)
        os.startfile(str(folder))  # noqa: S606
        return {"ok": True, "path": str(folder), "selected": False}
    except OSError as exc:
        raise HTTPException(500, f"打开文件夹失败：{exc}") from exc


@router.post("/papers/{paper_id}/enrich")
def enrich_paper(paper_id: str) -> dict:
    """按 DOI 从 Crossref 重新补全标题/作者/期刊/年份。"""
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    doi = (paper.get("doi") or "").strip()
    if not doi:
        raise HTTPException(400, "这篇论文没有 DOI，无法从 Crossref 补全")
    from ..services import metadata

    extra = metadata.enrich_from_doi(doi)
    if not extra:
        raise HTTPException(502, "Crossref 查询失败或没有返回内容")
    updates: dict = {}
    for key in ("title", "authors", "venue", "year", "abstract", "volume", "issue", "pages"):
        value = str(extra.get(key) or "").strip()
        if value:
            updates[key] = value[:4000] if key == "abstract" else value[:500]
    db.update_paper(paper_id, updates)
    return {"paper": db.get_paper(paper_id), "updated": updates}


@router.post("/papers/{paper_id}/lookup")
def lookup_paper(paper_id: str) -> dict:
    """联网按标题/DOI 找原文信息，并加入 PDF 补齐队列。"""
    paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(404, "论文不存在")
    from ..services import metadata, pdf_fetch

    updates: dict = {}
    doi = (paper.get("doi") or "").strip()
    if doi:
        found = metadata.enrich_from_doi(doi)
    else:
        found = metadata.find_metadata_by_title(paper.get("title") or "")
    for key in ("title", "authors", "venue", "year", "doi", "url"):
        if found.get(key):
            updates[key] = found[key]
    # 卷期页只补空值：用户手工填过的不覆盖
    for key in ("volume", "issue", "pages"):
        if found.get(key) and not str(paper.get(key) or "").strip():
            updates[key] = found[key]
    if updates:
        db.update_paper(paper_id, updates)
    queued = pdf_fetch.manager.start([paper_id])
    return {
        "paper": db.get_paper(paper_id),
        "updated": updates,
        "queued": queued,
        "matched": bool(updates),
    }


class BackfillIn(BaseModel):
    limit: int = 200


#: 卷期页批量补齐的进程内状态（单实例本地应用，够用）
_backfill_state: dict = {
    "running": False,
    "total": 0,
    "done": 0,
    "updated": 0,
    "failed": [],
    "finished_at": "",
}
_backfill_lock = threading.Lock()


def _backfill_snapshot() -> dict:
    with _backfill_lock:
        return {
            "running": bool(_backfill_state["running"]),
            "total": int(_backfill_state["total"]),
            "done": int(_backfill_state["done"]),
            "updated": int(_backfill_state["updated"]),
            "failed": list(_backfill_state["failed"]),
            "finished_at": str(_backfill_state["finished_at"]),
        }


@router.post("/papers/backfill-bibliographic")
def backfill_bibliographic(body: BackfillIn | None = None) -> dict:
    """后台按 DOI 补齐空着的卷/期/页；已在跑时 started=false。"""
    from ..services import metadata

    limit = 200 if body is None else body.limit
    limit = max(1, min(int(limit or 200), 500))
    if _backfill_snapshot()["running"]:
        return {"started": False, "total": _backfill_snapshot()["total"]}
    rows = db.query(
        "SELECT id, doi, volume, issue, pages FROM papers"
        " WHERE COALESCE(doi, '') <> ''"
        " AND (COALESCE(volume, '') = '' OR COALESCE(pages, '') = '')"
        " ORDER BY rowid LIMIT ?",
        (limit,),
    )
    candidates = [dict(row) for row in rows]
    if not candidates:
        return {"started": False, "total": 0}
    with _backfill_lock:
        if _backfill_state["running"]:
            return {"started": False, "total": int(_backfill_state["total"])}
        _backfill_state.update(
            {
                "running": True,
                "total": len(candidates),
                "done": 0,
                "updated": 0,
                "failed": [],
                "finished_at": "",
            }
        )

    def worker() -> None:
        import time

        try:
            for index, candidate in enumerate(candidates):
                if index:
                    time.sleep(0.2)  # 单篇限速，别把 Crossref 打急
                paper_id = str(candidate.get("id") or "")
                try:
                    meta = metadata.crossref_by_doi(str(candidate.get("doi") or ""))
                except Exception as exc:  # noqa: BLE001 - 单篇失败不中断整批
                    with _backfill_lock:
                        _backfill_state["failed"].append(
                            {"paper_id": paper_id, "error": str(exc)[:200]}
                        )
                        _backfill_state["done"] += 1
                    continue
                updates: dict = {}
                for key in ("volume", "issue", "pages"):
                    value = str((meta or {}).get(key) or "").strip()
                    if value and not str(candidate.get(key) or "").strip():
                        updates[key] = value[:50]
                if updates:
                    try:
                        db.update_paper(paper_id, updates)
                        with _backfill_lock:
                            _backfill_state["updated"] += 1
                    except Exception as exc:  # noqa: BLE001
                        with _backfill_lock:
                            _backfill_state["failed"].append(
                                {"paper_id": paper_id, "error": str(exc)[:200]}
                            )
                with _backfill_lock:
                    _backfill_state["done"] += 1
        finally:
            with _backfill_lock:
                _backfill_state["running"] = False
                _backfill_state["finished_at"] = db.now()

    threading.Thread(target=worker, name="pw-bib-backfill", daemon=True).start()
    return {"started": True, "total": len(candidates)}


@router.get("/papers/backfill-bibliographic/status")
def backfill_bibliographic_status() -> dict:
    return _backfill_snapshot()


@router.delete("/papers/{paper_id}")
def delete_paper(paper_id: str) -> dict:
    from ..services import recovery

    return recovery.archive_paper(paper_id)


@router.get("/papers/{paper_id}/pdf")
def paper_pdf(paper_id: str):
    paper = db.query_one("SELECT file_path FROM papers WHERE id = ?", (paper_id,))
    if not paper or not paper["file_path"]:
        raise HTTPException(404, "没有 PDF 文件")
    path = config.resolve_pdf_path(paper["file_path"])
    if not path.exists():
        raise HTTPException(404, "PDF 文件丢失")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@router.post("/papers/{paper_id}/attach-pdf")
async def attach_pdf(paper_id: str, file: UploadFile = File(...)) -> dict:
    """手动补 PDF：把用户拖入/上传的 PDF 挂到已有论文条目上。"""
    from ..services import ingest

    if not db.get_paper(paper_id):
        raise HTTPException(404, "论文不存在")
    suffix = Path(file.filename or "paper.pdf").suffix.lower()
    if suffix != ".pdf":
        raise HTTPException(400, "只支持 PDF 文件")
    tmp_fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
    os.close(tmp_fd)
    tmp = Path(tmp_name)
    try:
        with tmp.open("wb") as handle:
            shutil.copyfileobj(file.file, handle)
        chunks = ingest.attach_pdf(paper_id, tmp)
        if chunks <= 0:
            raise HTTPException(400, "PDF 没有可抽取的文本（可能是扫描版，需要先 OCR）")
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return {"paper": db.get_paper(paper_id), "chunks": chunks}


@router.post("/papers/upload")
async def upload_papers(files: list[UploadFile] = File(...)) -> dict:
    from ..services import ingest

    created, errors = [], []
    for upload in files:
        suffix = Path(upload.filename or "paper.pdf").suffix.lower() or ".pdf"
        if suffix != ".pdf":
            errors.append(f"{upload.filename}: 只支持 PDF")
            continue
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".pdf")
        os.close(tmp_fd)
        tmp = Path(tmp_name)
        try:
            with tmp.open("wb") as fh:
                shutil.copyfileobj(upload.file, fh)
            paper_id = ingest.create_paper_from_pdf(tmp, meta=None, source="upload")
            paper = db.get_paper(paper_id)
            if paper:
                created.append(paper)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{upload.filename}: {exc}")
        finally:
            try:
                tmp.unlink(missing_ok=True)
            except OSError:
                pass
    if not created and errors:
        raise HTTPException(400, "; ".join(errors))
    return {"papers": created, "errors": errors}


@router.post("/papers/{paper_id}/reindex")
def reindex(paper_id: str) -> dict:
    from ..services import ingest

    if not db.get_paper(paper_id):
        raise HTTPException(404, "论文不存在")
    try:
        chunks = ingest.reindex_paper(paper_id)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, str(exc)) from exc
    return {"ok": True, "chunks": chunks}
