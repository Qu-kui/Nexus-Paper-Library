# PaperWhale · 论文鲸 — 架构与接口契约

本地优先的 AI 文献工作台。Python FastAPI + SQLite + 原生前端（ES Modules + PDF.js）。
所有数据保存在 `data/`，LLM 通过 OpenAI 兼容 API 调用。

## 目录与写入范围

```
paperwhale_server/main.py            Lead
paperwhale_server/config.py          Lead
paperwhale_server/db.py              Lead
paperwhale_server/models.py          Lead
paperwhale_server/routers/papers.py  Lead
paperwhale_server/routers/settings.py Lead
paperwhale_server/routers/annotations.py Lead
paperwhale_server/services/ingest.py T2-ai
paperwhale_server/services/rag.py    T2-ai
paperwhale_server/llm.py             T2-ai
paperwhale_server/routers/ai.py      T2-ai
paperwhale_server/services/metadata.py T1-import
paperwhale_server/services/importers.py T1-import
paperwhale_server/services/discover.py  T1-import
paperwhale_server/routers/import_.py    T1-import
paperwhale_server/routers/discover.py   T1-import
web/index.html            Lead
web/css/style.css         Lead
web/js/api.js             Lead
web/js/app.js             Lead
web/js/library.js         T3-ui
web/js/import.js          T3-ui
web/js/settings.js        T3-ui
web/js/chat.js            T3-ui
web/js/reader.js          T4-reader
web/vendor/pdfjs/*        Lead (vendor)
```

## 数据模型（SQLite）

```sql
papers(
  id TEXT PRIMARY KEY,           -- uuid4 hex
  title TEXT NOT NULL,
  authors TEXT,                  -- "A; B; C"
  year TEXT, venue TEXT, doi TEXT, arxiv_id TEXT, url TEXT,
  abstract TEXT,
  file_path TEXT,                -- 相对 data/ 的路径，如 pdfs/xxx.pdf
  source TEXT,                   -- upload|zotero|obsidian|arxiv|doi|manual
  tags TEXT,                     -- JSON array
  category TEXT,
  summary TEXT,                  -- LLM 结构化摘要（markdown）
  key_points TEXT,               -- JSON array of strings
  relevance TEXT,                -- LLM 与用户画像的关联分析（markdown）
  status TEXT DEFAULT 'new',     -- new|indexed|analyzed|failed
  created_at TEXT, updated_at TEXT
)
chunks(id INTEGER PK, paper_id TEXT, page INT, text TEXT)
chunks_fts USING fts5(text, tokenize='unicode61')  -- 通过触发器同步，rowid = chunks.id
annotations(id INTEGER PK, paper_id TEXT, page INT, color TEXT, rects TEXT, quote TEXT, note TEXT, kind TEXT, created_at TEXT)
settings(key TEXT PRIMARY KEY, value TEXT)
```

## 服务层契约

### paperwhale_server/llm.py
```python
class LLMError(Exception): ...

def is_configured() -> bool
def chat(messages: list[dict], temperature: float | None = None, json_mode: bool = False) -> str
    # messages: [{"role":"system"|"user"|"assistant","content":str}]
    # 读取 config.get_settings() 的 base_url/api_key/model；未配置抛 LLMError("LLM 未配置")
def chat_json(system: str, user: str) -> dict   # 去掉 ```json 围栏后 json.loads
```

### paperwhale_server/services/ingest.py
```python
def create_paper_from_metadata(meta: dict, pdf_path: Path | None = None, source: str = "manual") -> str
    # meta 可含 title,authors,year,venue,doi,arxiv_id,url,abstract,tags,category
    # 若给定 pdf_path：复制到 data/pdfs/<id>.pdf、抽文本分页入库（chunks_fts）、
    # 用 PDF 元数据补全空字段；返回 paper_id
def create_paper_from_pdf(pdf_path: Path, meta: dict | None = None, source: str = "upload") -> str
def reindex_paper(paper_id: str) -> int          # 重新抽文本，返回 chunk 数
def analyze_paper(paper_id: str) -> dict          # 调 LLM：summary/key_points/tags/category，
                                                  # 若用户画像非空再生成 relevance；写库并返回
                                                  # {"status":"ok","paper":<全文 paper dict>}
```

### paperwhale_server/services/metadata.py
```python
def extract_pdf_metadata(pdf_path: Path) -> dict   # title/authors/year/doi/abstract（尽力而为）
def crossref_by_doi(doi: str) -> dict
def arxiv_meta(arxiv_id: str) -> dict
```

### paperwhale_server/services/importers.py
```python
def import_zotero_file(path: Path) -> dict   # 支持 .csv/.bib/.ris/.rdf；返回 {"imported":[...],"skipped":n,"errors":[...]}
def import_zotero_folder(folder: Path) -> dict  # 找 *.rdf/*.csv + files/ 附件
def import_obsidian_vault(folder: Path) -> dict # 扫 .md frontmatter + 关联 PDF
```
全部通过 `ingest.create_paper_from_metadata` 落库，去重规则：doi 或 arXiv id 或（title+year）。

### paperwhale_server/services/discover.py
```python
def arxiv_search(query: str, limit: int = 10) -> list[dict]
    # [{"arxiv_id","title","authors","abstract","published","pdf_url","abs_url"}]
def download_arxiv(arxiv_id: str) -> tuple[Path, dict]   # 下载 PDF 到临时目录并返回元数据
```

### paperwhale_server/services/rag.py
```python
def chat(question: str, paper_id: str | None = None) -> dict
    # 有 paper_id：检索该论文 chunks；否则跨全库。返回
    # {"answer": str, "sources":[{"paper_id","title","page","snippet"}]}
```

## HTTP API（全部 /api 前缀）

设置
- `GET /api/settings` → 全部设置对象（含 `api_key_configured`）
- `PUT /api/settings` body 任意字段 → 合并保存后返回同 GET

论文
- `GET /api/papers?q=&tag=&category=&status=&limit=50&offset=0` → `{items, total}`
- `GET /api/papers/{id}` → paper
- `PATCH /api/papers/{id}` body `{title?,authors?,year?,tags?,category?,notes?}` → paper
- `DELETE /api/papers/{id}` → `{ok:true}`
- `GET /api/papers/{id}/pdf` → PDF 文件（inline）
- `POST /api/papers/upload` multipart `files`（可多个）→ `{papers:[...]}`
- `POST /api/papers/{id}/reindex` → `{chunks:n}`
- 论文对象字段同数据库列，另附 `has_pdf`,`annotation_count`

标注
- `GET /api/papers/{id}/annotations` → `{items:[...]}`
- `POST /api/papers/{id}/annotations` body `{page,color,rects,quote,note?,kind}` → annotation
- `DELETE /api/annotations/{id}` → `{ok:true}`

导入
- `POST /api/import/zotero` multipart `file`（.csv/.bib/.ris/.rdf）→ 结果
- `POST /api/import/zotero-folder` json `{path}` → 结果
- `POST /api/import/obsidian` json `{path}` → 结果
- `POST /api/import/identifier` json `{identifier}`（DOI / arXiv id / URL）→ `{paper}`

发现
- `GET /api/discover/arxiv?q=&limit=10` → `{items}`
- `POST /api/discover/download` json `{arxiv_id}` → `{paper}`
- `GET /api/tags` → `{tags:[{name,count}]}`；`GET /api/categories` 同理
- `GET /api/stats` → `{papers,chunks,annotations,tags,categories}`

AI（T2）
- `POST /api/ai/summarize/{paper_id}` → analyze_paper 结果
- `POST /api/ai/chat` json `{question,paper_id?}` → rag.chat 结果
- `POST /api/ai/translate` json `{text,target_lang?}` → `{text}`
- `POST /api/ai/explain` json `{text,paper_id?}` → `{text}`
- `POST /api/ai/relevance/{paper_id}` → `{text}`（基于 profile 重新生成）
- `GET /api/ai/profile` → `{profile}`；`PUT /api/ai/profile` json `{profile}`

## 前端约定
- `api.js` 暴露 `api.get/post/put/patch/del` 与 `toast(msg,type)`、`fmt*` 工具。
- `app.js` 负责导航切换（`.page[data-page]` 显隐）、初始化各模块 `initLibrary()/initImport()/initSettings()/initChat()/initReader()`。
- DOM 结构与 id 以 `web/index.html` 为准；模块不得改写 index.html 结构，只能填内容。
- PDF.js 从 `/vendor/pdfjs/pdf.mjs` 与 `/vendor/pdfjs/pdf.worker.mjs` 导入。
- 无构建步骤：浏览器直接跑 ES Modules。

## 启动
`start.cmd` → `.venv\Scripts\python.exe -m uvicorn paperwhale_server.main:app --host 127.0.0.1 --port 8765`
