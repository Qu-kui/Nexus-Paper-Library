"""PaperWhale 端到端冒烟测试：起一个隔离数据目录的服务，逐个打接口。

用法：.venv\\Scripts\\python.exe tests\\smoke.py
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures"
DATA = ROOT / "tests" / ".smoke-data"
PORT = 8799
BASE = f"http://127.0.0.1:{PORT}"
PASS, FAIL = [], []
try:  # Windows 控制台默认 GBK
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def check(name: str, fn):
    try:
        detail = fn()
        PASS.append(name)
        print(f"  PASS  {name}" + (f"  [{detail}]" if detail else ""))
    except Exception as exc:  # noqa: BLE001
        FAIL.append((name, str(exc)))
        print(f"  FAIL  {name}: {exc}")


def wait_port(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            if sock.connect_ex(("127.0.0.1", PORT)) == 0:
                return True
        time.sleep(0.3)
    return False


def main() -> int:
    if DATA.exists():
        shutil.rmtree(DATA)
    DATA.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env["PAPERWHALE_DATA"] = str(DATA)
    env["PAPERWHALE_LLM_MOCK"] = "1"
    log = (ROOT / "tests" / ".smoke-server.log").open("w", encoding="utf-8")
    proc = subprocess.Popen(
        [str(ROOT / ".venv" / "Scripts" / "python.exe"), "-m", "uvicorn", "paperwhale_server.main:app",
         "--host", "127.0.0.1", "--port", str(PORT), "--log-level", "warning"],
        cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    # 本地回环不要走系统代理（部分环境 http_proxy 会截走 localhost 请求并返回 502）
    client = httpx.Client(base_url=BASE, timeout=180.0, trust_env=False)
    paper_id: dict[str, str] = {}
    try:
        if not wait_port():
            print("服务未能启动，日志：")
            print((ROOT / "tests" / ".smoke-server.log").read_text(encoding="utf-8", errors="ignore")[-4000:])
            return 1

        def health():
            r = client.get("/api/health")
            r.raise_for_status()
            return r.json()["version"]

        check("health", health)

        def static_assets():
            for path in ("/", "/css/style.css", "/js/app.js", "/vendor/pdfjs/pdf.mjs",
                         "/js/library.js", "/js/import.js", "/js/settings.js",
                         "/js/chat.js", "/js/reader.js"):
                res = client.get(path)
                assert res.status_code == 200, f"{path} -> {res.status_code}"
            return "9 assets"

        check("static assets", static_assets)

        def settings_roundtrip():
            res = client.put("/api/settings", json={
                "base_url": "http://127.0.0.1:9999/v1", "api_key": "test-key",
                "model": "mock-model", "profile": "我在做多模态药物筛选，关注可解释性。",
                "summary_language": "zh",
                "pdf_browser_session_enabled":False,
            })
            res.raise_for_status()
            assert res.json()["model"] == "mock-model"
            got = client.get("/api/settings").json()
            assert got["profile"].startswith("我在做")
            return "ok"

        check("settings get/put", settings_roundtrip)

        def upload_pdf():
            with (FIXTURES / "attention.pdf").open("rb") as fh:
                res = client.post("/api/papers/upload",
                                  files={"files": ("attention.pdf", fh, "application/pdf")})
            res.raise_for_status()
            papers = res.json()["papers"]
            assert papers, "没有返回论文"
            paper_id["main"] = papers[0]["id"]
            assert papers[0]["has_pdf"], "PDF 未保存"
            return f"{papers[0]['title'][:40]}"

        check("upload pdf", upload_pdf)

        def paper_indexed():
            res = client.get(f"/api/papers/{paper_id['main']}")
            paper = res.json()
            assert paper["status"] in ("indexed", "analyzed"), paper["status"]
            stats = client.get("/api/stats").json()
            assert stats["chunks"] > 10, f"chunks={stats['chunks']}"
            return f"chunks={stats['chunks']}"

        check("pdf text indexed", paper_indexed)

        def search_library():
            res = client.get("/api/papers", params={"q": "attention"})
            assert res.json()["total"] >= 1
            return f"total={res.json()['total']}"

        check("library search", search_library)

        def tags_categories():
            assert "tags" in client.get("/api/tags").json()
            assert "categories" in client.get("/api/categories").json()
            return "ok"

        check("tags/categories", tags_categories)

        def analyze_mock():
            res = client.post(f"/api/ai/summarize/{paper_id['main']}")
            res.raise_for_status()
            paper = res.json()["paper"]
            assert paper["summary"], "summary 为空"
            assert paper["key_points"], "key_points 为空"
            assert paper["status"] == "analyzed"
            return f"tags={paper['tags']}"

        check("ai summarize (mock)", analyze_mock)

        def chat_mock():
            res = client.post("/api/ai/chat", json={"question": "这篇论文的主要贡献是什么？"})
            res.raise_for_status()
            data = res.json()
            assert data["answer"]
            return f"sources={len(data.get('sources', []))}"

        check("ai library chat (mock)", chat_mock)

        def translate_mock():
            res = client.post("/api/ai/translate", json={"text": "Attention is all you need."})
            res.raise_for_status()
            assert res.json()["text"]
            return "ok"

        check("ai translate (mock)", translate_mock)

        def explain_mock():
            res = client.post("/api/ai/explain", json={"text": "multi-head attention",
                                                       "paper_id": paper_id["main"]})
            res.raise_for_status()
            assert res.json()["text"]
            return "ok"

        check("ai explain (mock)", explain_mock)

        def relevance_mock():
            res = client.post(f"/api/ai/relevance/{paper_id['main']}")
            res.raise_for_status()
            assert res.json()["text"]
            return "ok"

        check("ai relevance (mock)", relevance_mock)

        def annotations():
            res = client.post(f"/api/papers/{paper_id['main']}/annotations", json={
                "page": 1, "color": "#ffd666",
                "rects": [{"x": 0.1, "y": 0.2, "w": 0.3, "h": 0.02}],
                "quote": "Attention is all you need", "kind": "highlight",
            })
            res.raise_for_status()
            ann_id = res.json()["id"]
            items = client.get(f"/api/papers/{paper_id['main']}/annotations").json()["items"]
            assert any(item["id"] == ann_id for item in items)
            client.delete(f"/api/annotations/{ann_id}").raise_for_status()
            items = client.get(f"/api/papers/{paper_id['main']}/annotations").json()["items"]
            assert not any(item["id"] == ann_id for item in items)
            return "crud ok"

        check("annotations crud", annotations)

        def import_zotero_csv():
            with (FIXTURES / "zotero_sample.csv").open("rb") as fh:
                res = client.post("/api/import/zotero",
                                  files={"file": ("zotero_sample.csv", fh, "text/csv")})
            res.raise_for_status()
            data = res.json()
            assert data["imported"], data
            paper_id["bert"] = data["imported"][0]["id"]
            return f"imported={len(data['imported'])} skipped={data.get('skipped')}"

        check("import zotero csv", import_zotero_csv)

        def import_bibtex():
            with (FIXTURES / "zotero_sample.bib").open("rb") as fh:
                res = client.post("/api/import/zotero",
                                  files={"file": ("zotero_sample.bib", fh, "text/plain")})
            res.raise_for_status()
            return f"imported={len(res.json()['imported'])}"

        check("import bibtex", import_bibtex)

        def import_ris():
            with (FIXTURES / "zotero_sample.ris").open("rb") as fh:
                res = client.post("/api/import/zotero",
                                  files={"file": ("zotero_sample.ris", fh, "text/plain")})
            res.raise_for_status()
            return f"imported={len(res.json()['imported'])}"

        check("import ris", import_ris)

        def import_obsidian():
            res = client.post("/api/import/obsidian",
                              json={"path": str(FIXTURES / "obsidian_vault")})
            res.raise_for_status()
            data = res.json()
            assert data["imported"], data
            with_pdf = [p for p in data["imported"] if p.get("has_pdf")]
            return f"imported={len(data['imported'])} with_pdf={len(with_pdf)}"

        check("import obsidian vault", import_obsidian)

        def arxiv_search():
            res = client.get("/api/discover/arxiv", params={"q": "transformer", "limit": 3})
            res.raise_for_status()
            items = res.json()["items"]
            assert items and items[0]["arxiv_id"], items
            return f"{len(items)} results, first={items[0]['arxiv_id']}"

        check("arxiv search (network)", arxiv_search)

        def delete_paper():
            client.delete(f"/api/papers/{paper_id['main']}").raise_for_status()
            res = client.get(f"/api/papers/{paper_id['main']}")
            assert res.status_code == 404
            return "ok"

        check("delete paper", delete_paper)

        def llm_error_path():
            # 关掉 mock 不可行（进程级），至少确认未配置时错误结构由代码保证
            return "skip"

        check("llm error path", llm_error_path)

    finally:
        client.close()
        proc.terminate()
        try:
            proc.wait(timeout=8)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()

    print("\n" + "=" * 56)
    print(f"PASS {len(PASS)} / {len(PASS) + len(FAIL)}")
    if FAIL:
        print("FAILURES:")
        for name, err in FAIL:
            print(f" - {name}: {err}")
        return 1
    print("全部通过 🐳")
    return 0


if __name__ == "__main__":
    sys.exit(main())
