"""PaperWhale 入口。"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .routers import conversations as conversation_routes
from .routers import maintenance,tasks,ai_progress as progress_routes
from .services.conversations import ConversationError
from .routers import (
    ai_branches,
    ai,
    analyze,
    annotations,
    terms,
    export,
    paper_figures,
    web_images,
    categories,
    cite,
    clipboard,
    discover,
    import_,
    methods,
    newsfeed,
    papers,
    pdf_fetch,
    plugins,
    references,
    scholar,
    settings,
    skills,
    webimport,
    word,
)

APP_NAME = "Nexus Paper Library"
APP_VERSION = "2.10.1"


@asynccontextmanager
async def lifespan(app: FastAPI):
    config.ensure_dirs()
    db.init_db()
    try:
        from .services import conversations as conversation_store

        # 阅读会话只在本次阅读期间存在，应用重启后清理上次残留
        conversation_store.purge_stale_reading_sessions()
    except Exception:  # noqa: BLE001 - 清理失败不能挡住启动
        pass
    from .services.pdf_fetch import manager as pdf_fetch_manager

    pdf_fetch_manager.resume()
    from .services.analysis_queue import queue_manager as analysis_manager

    analysis_manager.resume()
    try:
        from .services.category_assistant import queue_manager as category_manager

        category_manager.resume()
    except Exception:  # noqa: BLE001 - 分类恢复失败不能挡住启动
        pass
    try:
        from .services import tagging

        tagging.cleanup_all_paper_tags()
    except Exception:  # noqa: BLE001
        pass
    try:
        import threading

        from .services import metadata

        threading.Thread(
            target=metadata.repair_bad_titles, name="pw-title-repair", daemon=True
        ).start()
    except Exception:  # noqa: BLE001
        pass
    try:
        from .services import scholar

        scholar.start_auto_sync()
    except Exception:  # noqa: BLE001
        pass
    for module_name, label in (("methods", "method-extract"),):
        try:
            module = __import__(f".services.{module_name}", fromlist=["queue_manager"])
            module.queue_manager.resume()
        except Exception:  # noqa: BLE001 - 新功能恢复失败不能挡住启动
            pass
    try:
        from .services import clipboard_watch

        clipboard_watch.start()
    except Exception:  # noqa: BLE001 - 剪贴板监听失败不能挡住启动
        pass
    yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)


@app.exception_handler(ConversationError)
async def conversation_error_handler(request, exc):
    return JSONResponse(status_code=exc.status, content={'detail': str(exc)})


app.include_router(conversation_routes.router)
app.include_router(maintenance.router)
app.include_router(tasks.router)
app.include_router(progress_routes.router)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(papers.router)
app.include_router(annotations.router)
app.include_router(settings.router)
app.include_router(import_.router)
app.include_router(discover.router)
app.include_router(pdf_fetch.router)
app.include_router(analyze.router)
app.include_router(categories.router)
app.include_router(skills.router)
app.include_router(webimport.router)
app.include_router(cite.router)
app.include_router(plugins.router)
app.include_router(scholar.router)
app.include_router(methods.router)
app.include_router(terms.router)
app.include_router(export.router)
app.include_router(paper_figures.router)
app.include_router(web_images.router)
app.include_router(newsfeed.router)
app.include_router(clipboard.router)
app.include_router(references.router)
app.include_router(word.router)
app.include_router(ai.router)
app.include_router(ai_branches.router)


@app.get("/api/health")
def health() -> dict:
    # Existing Connectors use this stable protocol identifier for discovery.
    return {"ok": True, "app": "PaperWhale", "product_name": APP_NAME, "version": APP_VERSION}


@app.post("/api/app/quit")
def quit_app() -> dict:
    """桌面端「退出程序」：强制结束进程。"""
    import os
    import threading

    def _quit() -> None:
        os._exit(0)

    threading.Timer(0.25, _quit).start()
    return {"ok": True}


@app.get("/api/stats")
def stats() -> dict:
    def scalar(sql: str) -> int:
        row = db.query_one(sql)
        return int(row[0]) if row else 0

    return {
        "papers": scalar("SELECT COUNT(*) FROM papers"),
        "missing_pdf": scalar("SELECT COUNT(*) FROM papers WHERE pdf_available(file_path)=0"),
        "chunks": scalar("SELECT COUNT(*) FROM chunks"),
        "annotations": scalar("SELECT COUNT(*) FROM annotations"),
        "categories": scalar("SELECT COUNT(DISTINCT category) FROM papers WHERE category != ''"),
    }


web_dir = config.WEB_DIR
if web_dir.exists():
    if (web_dir / "brand").exists():
        app.mount("/brand", StaticFiles(directory=str(web_dir / "brand")), name="brand")
    if (web_dir / "vendor").exists():
        app.mount("/vendor", StaticFiles(directory=str(web_dir / "vendor")), name="vendor")
    app.mount("/css", StaticFiles(directory=str(web_dir / "css")), name="css")
    app.mount("/js", StaticFiles(directory=str(web_dir / "js")), name="js")


@app.get("/")
def index() -> FileResponse:
    index_path = config.WEB_DIR / "index.html"
    if not index_path.exists():
        return JSONResponse({"ok": False, "message": "web/index.html 不存在"}, status_code=500)
    return FileResponse(index_path)


@app.get("/word-picker")
def word_picker_page():
    """Word 引文选择窗口（独立网页，Zotero 式）；页面文件缺失时回 404 JSON。"""
    page = config.WEB_DIR / "word-picker.html"
    if not page.exists():
        return JSONResponse({"ok": False, "message": "web/word-picker.html 不存在"}, status_code=404)
    return FileResponse(page)


@app.exception_handler(Exception)
async def unhandled_exception(request, exc):  # pragma: no cover - 兜底
    import logging
    import traceback

    logging.getLogger("paperwhale").error(
        "Unhandled error on %s %s\n%s", request.method, request.url.path, traceback.format_exc()
    )
    return JSONResponse(
        {"ok": False, "message": f"{type(exc).__name__}: {exc}"},
        status_code=500,
    )
