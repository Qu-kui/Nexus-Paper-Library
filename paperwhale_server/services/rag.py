"""检索增强问答（RAG）：FTS 检索 chunks + LLM 依据片段作答。

对外契约：

``chat(question: str, paper_id: str | None = None) -> dict``
返回 ``{"answer": str, "sources": [{"paper_id","title","page","snippet"}]}``。

检索策略：先用 ``chunks_fts MATCH``（中文按 2-4 字滑窗生成 token，用 OR 连接），
因为 unicode61 对中文按连续片段分词，纯 FTS 召回有限，所以不足时再用 LIKE 补召回。
"""
from __future__ import annotations

import base64
import binascii
import re
import sqlite3

from .. import config, db, llm
from . import ai_context, skills, websearch, ai_progress, deepseek_files

#: 单篇论文对话时喂给模型的全文上限（字符）
FULLTEXT_LIMIT = 32000
#: 最多送进 prompt 的片段数
TOP_K = 10
#: 每条片段在 prompt 中的最大字符数
CONTEXT_CHUNK_LEN = 1200
#: sources 里 snippet 的最大字符数
SNIPPET_LEN = 200
#: 问题 token 上限，避免 FTS 查询过长
MAX_TOKENS = 24

_ASCII_WORD_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+\-]*")
_CJK_RUN_RE = re.compile(r"[\u4e00-\u9fff]+")
_CITATION_RE = re.compile(r"\[(\d+)\]")

#: 参考来源小节的抬头（内置联网工具返回的 citation 没有 [n] 角标，附在回答末尾）
_REFERENCE_HEADER = "\n\n---\n**参考来源**\n"


_WEB_INTENT_RE = re.compile(
    r"上网|联网|网上查|上网查|搜一下|搜一搜|搜搜|帮我搜|查一下最新|查查最新|最新进展|新进展|"
    r"最近的进展|近期进展|检索一下|查文献|查一下文献|找找文献|web\s*search|search\s+the\s+web",
    re.IGNORECASE,
)


def _wants_web_search(*questions: str) -> bool:
    """用户是不是明确要求上网查（只有这种情况下才强制模型必须调用联网工具）。"""
    return any(_WEB_INTENT_RE.search(str(q or "")) for q in questions)


def _context_budget() -> int:
    """喂给模型的字符预算：设置里的 answer_context_chars，限制在 8k–400k。"""
    try:
        value = int(config.get_settings().get("answer_context_chars") or 0)
    except (TypeError, ValueError):
        value = 0
    if value <= 0:
        return FULLTEXT_LIMIT
    return max(8000, min(value, 400000))


def _use_builtin_search(search_web: bool, has_image_action: bool) -> bool:
    """是否用 DeepSeek 服务端联网工具替换本地 ddgs/Bing 抓取。"""
    if not search_web or has_image_action:
        return False
    mode = str(config.get_settings().get("web_search_mode") or "auto").strip().lower()
    if mode == "local":
        return False
    return llm.supports_app_api()


def _paper_file_mode() -> str:
    mode = str(config.get_settings().get("paper_file_mode") or "scanned").strip().lower()
    return mode if mode in {"off", "scanned", "always"} else "scanned"


def _paper_documents(paper: dict, hint: str) -> tuple[list[str], str]:
    """把论文 PDF 交给服务端解析，返回 (file_id 列表, 失败原因)。

    只在首次解析时慢（上传 + 服务端解析），之后按内容指纹复用 file_id。
    """
    if _paper_file_mode() == "off" or not deepseek_files.available():
        return [], ""
    rel = str(paper.get("file_path") or "").strip()
    if not rel:
        return [], ""
    path = config.resolve_pdf_path(rel)
    if not path.is_file():
        return [], ""
    purpose = deepseek_files.PURPOSE_RICH if hint == "rich" else deepseek_files.PURPOSE_TEXT
    ai_progress.emit(stage="解析论文 PDF（首次较慢）")
    try:
        return [deepseek_files.upload_pdf(path, purpose)], ""
    except deepseek_files.FileParseError as exc:
        return [], str(exc)


def _user_content(text: str, documents: list[str] | None = None):
    """用户消息内容：附带 file_id 时用多部分内容块，否则仍是纯文本。"""
    file_ids = [fid for fid in (documents or []) if fid]
    if not file_ids:
        return text
    parts = [{"type": "file", "file_id": fid} for fid in file_ids]
    parts.append({"type": "text", "text": text})
    return parts


def _reference_block(citations: list[dict] | None) -> str:
    """内置联网工具返回的引用没有 [n] 角标，统一附一个可点的参考来源列表。"""
    rows = [row for row in (citations or []) if str((row or {}).get("url") or "").strip()][:8]
    if not rows:
        return ""
    lines = []
    for index, row in enumerate(rows, 1):
        title = str(row.get("title") or row["url"]).replace("]", "）")[:80]
        lines.append(f"{index}. [{title}]({row['url']})")
    return _REFERENCE_HEADER + "\n".join(lines)


NO_RESULT_ANSWER = (
    "没有在文献库里检索到与这个问题相关的内容。\n"
    "可以试试换个关键词，或者先把相关论文导入并完成索引（论文详情页可点「重新索引」）。"
)


# --------------------------------------------------------------------------- #
# 查询词处理
# --------------------------------------------------------------------------- #
def _query_tokens(question: str) -> list[str]:
    """把问题切成 FTS token：英文按词，中文按 2-4 字滑窗，去重保序。"""
    tokens: list[str] = []
    for match in _ASCII_WORD_RE.finditer(question or ""):
        word = match.group(0).strip("._+-").lower()
        if len(word) >= 2:
            tokens.append(word)
    for match in _CJK_RUN_RE.finditer(question or ""):
        run = match.group(0)
        for size in (2, 3, 4):
            for start in range(0, len(run) - size + 1):
                tokens.append(run[start:start + size])
    seen: set[str] = set()
    unique: list[str] = []
    for token in tokens:
        if token in seen:
            continue
        seen.add(token)
        unique.append(token)
    return unique[:MAX_TOKENS]


def _escaped_fts_token(token: str) -> str:
    """把单个 token 转成安全的 FTS5 短语，并决定是否加前缀通配。"""
    token = token.replace('"', '""')
    has_cjk = any("\u4e00" <= ch <= "\u9fff" for ch in token)
    # unicode61 会把一整段中文当一个 token，只能靠前缀匹配；英文长词同样适度前缀匹配
    prefix = has_cjk or len(token) >= 4
    return f'"{token}"' + ("*" if prefix else "")


def _fts_query(tokens: list[str]) -> str:
    parts = [_escaped_fts_token(token) for token in tokens if token]
    return " OR ".join(parts)


# --------------------------------------------------------------------------- #
# 检索
# --------------------------------------------------------------------------- #
def _search_fts(tokens: list[str], paper_id: str | None, limit: int) -> list[sqlite3.Row]:
    query = _fts_query(tokens)
    if not query:
        return []
    sql = (
        "SELECT c.id, c.paper_id, c.page, c.text "
        "FROM chunks_fts JOIN chunks c ON c.id = chunks_fts.rowid "
        "WHERE chunks_fts MATCH ?"
    )
    params: list = [query]
    if paper_id:
        sql += " AND c.paper_id = ?"
        params.append(paper_id)
    sql += " ORDER BY bm25(chunks_fts) ASC LIMIT ?"
    params.append(limit)
    try:
        return list(db.query(sql, params))
    except sqlite3.OperationalError:
        return []


def _search_like(tokens: list[str], paper_id: str | None, limit: int) -> list[sqlite3.Row]:
    """FTS 召回不足时用 LIKE 兜底（中文子串匹配）。"""
    # 优先用更长的窗口（更有区分度），最多取 10 个
    keywords = sorted({token for token in tokens if len(token) >= 2}, key=len, reverse=True)[:10]
    if not keywords:
        return []
    where = " OR ".join("text LIKE ?" for _ in keywords)
    sql = f"SELECT id, paper_id, page, text FROM chunks WHERE ({where})"
    params: list = [f"%{keyword}%" for keyword in keywords]
    if paper_id:
        sql += " AND paper_id = ?"
        params.append(paper_id)
    sql += " ORDER BY page ASC, id ASC LIMIT ?"
    params.append(limit)
    try:
        return list(db.query(sql, params))
    except sqlite3.OperationalError:
        return []


def _fallback_summary_rows(paper_id: str | None, limit: int = 5) -> list[dict]:
    """词法检索颗粒无收时的兜底：用已生成摘要/摘要原文的论文概括作上下文。

    中文提问与英文论文存在天然词法鸿沟，FTS/LIKE 都可能召回 0；
    此时用最近更新论文的结构化摘要回答「这批论文讲了什么」这类问题。
    """
    sql = (
        "SELECT id AS paper_id, title, abstract, summary, updated_at FROM papers "
        "WHERE ((summary IS NOT NULL AND summary != '') OR (abstract IS NOT NULL AND abstract != ''))"
    )
    params: list = []
    if paper_id:
        sql += " AND id = ?"
        params.append(paper_id)
    sql += " ORDER BY updated_at DESC LIMIT ?"
    params.append(max(1, limit))
    try:
        rows = db.query(sql, params)
    except sqlite3.OperationalError:
        return []
    result: list[dict] = []
    for row in rows:
        body = (row["summary"] or "").strip() or (row["abstract"] or "").strip()
        if not body:
            continue
        result.append(
            {
                "id": -1,
                "paper_id": row["paper_id"],
                "page": None,
                "source_kind": "generated_summary" if (row['summary'] or '').strip() else "abstract",
                "text": f"《{row['title'] or '未命名论文'}》摘要：{body[:2000]}",
            }
        )
    return result


def _retrieve(question: str, paper_id: str | None) -> list[sqlite3.Row]:
    tokens = _query_tokens(question)
    rows = _search_fts(tokens, paper_id, TOP_K) if tokens else []

    if len(rows) < TOP_K:
        seen = {row["id"] for row in rows}
        for row in _search_like(tokens, paper_id, TOP_K):
            if row["id"] in seen:
                continue
            seen.add(row["id"])
            rows.append(row)
            if len(rows) >= TOP_K:
                break
    if not rows:
        return _fallback_summary_rows(paper_id)
    return rows[:TOP_K]


def _paper_titles(paper_ids: list[str]) -> dict[str, str]:
    if not paper_ids:
        return {}
    placeholders = ", ".join("?" for _ in paper_ids)
    rows = db.query(f"SELECT id, title FROM papers WHERE id IN ({placeholders})", paper_ids)
    return {row["id"]: (row["title"] or "未命名论文") for row in rows}


def _build_context(rows: list[sqlite3.Row], titles: dict[str, str]) -> tuple[str, list[dict]]:
    """拼出带编号的上下文，以及每条片段的来源信息。"""
    blocks: list[str] = []
    items: list[dict] = []
    for index, row in enumerate(rows, start=1):
        paper_id = row["paper_id"]
        title = titles.get(paper_id, "未命名论文")
        page = int(row["page"]) if row["page"] else None
        location = f'原文第 {page} 页' if page else ('AI 生成概述（非原文，无页码）' if dict(row).get('source_kind') == 'generated_summary' else '论文摘要（非全文，无页码）')
        text = (row["text"] or "").strip()
        excerpt = " ".join(text.split())[:CONTEXT_CHUNK_LEN]
        blocks.append(f"[{index}] 《{title}》 {location}\n{excerpt}")
        items.append(
            {
                "index": index,
                "paper_id": paper_id,
                "title": title,
                "page": page,
                "snippet": " ".join(text.split())[:SNIPPET_LEN],
                "locator": excerpt if (dict(row).get("source_kind") or "original") == "original" else "",
                "source_kind": dict(row).get("source_kind") or "original",
                "has_pdf": bool((db.get_paper(paper_id) or {}).get("has_pdf")),
            }
        )
    return "\n\n".join(blocks), items


def _answer_prompt() -> str:
    skill_text = skills.task_prompt("qa", max_chars=4000)
    suffix = (
        "\n\n自动启用的论文阅读规范（只借鉴证据与引用要求，不改变上面的回答格式）：\n" + skill_text
        if skill_text
        else ""
    )
    return (
        "你是一名 AI 文献问答助手。论文结论严格依据本轮论文片段；库统计依据实际统计。"
        "背景解释可用明确标注的一般知识、附件或联网资料，不可冒充论文证据，不编造数据、作者或结论。\n"
        "要求：\n"
        "1. 用中文回答（除非用户用其他语言提问）。\n"
        "2. 引用某条片段的内容后标注 [编号]，例如 [1]；多条引用写成 [1][3]。\n"
        "3. 如果片段不足以回答，直接说明「现有片段不足以回答」，并指出还缺什么信息。\n"
        "4. 最后单独一行写「来源：论文标题（第 X 页）」，列出你实际用到的来源。"
        + WEB_IMAGE_HINT
        + suffix
    )


def _paper_answer_prompt() -> str:
    skill_text = skills.task_prompt("qa", max_chars=4000)
    suffix = (
        "\n\n自动启用的论文阅读规范（只借鉴证据与引用要求，不改变上面的回答格式）：\n" + skill_text
        if skill_text
        else ""
    )
    return (
        "你是一名 AI 论文精读助手。下面给的是**这一篇论文的材料节选**（类型与页码已标注，摘要或AI概述不能称为全文），"
        "本文结论以提供的原文为准；背景解释可以补充但必须明确标注。不要编造原文没有的内容。\n"
        "要求：\n"
        "1. 用中文回答；涉及图表、公式、实验设置时，尽量指出所在页码。\n"
        "2. 引用原文内容后标注 [编号]。\n"
        "3. 如果节选里确实没有相关信息，直说「论文这一部分没有提到」，不要用其他论文的知识补。\n"
        "4. 最后单独一行写「来源：第 X 页」列出实际用到的页码。"
        + suffix
    )


def _paper_fulltext_rows(paper_id: str, limit: int = FULLTEXT_LIMIT) -> list[dict]:
    """取单篇论文的全部 chunks（按页序），拼到字符上限为止。"""
    rows = db.query(
        "SELECT id, paper_id, page, text FROM chunks WHERE paper_id = ? ORDER BY page ASC, id ASC",
        (paper_id,),
    )
    result: list[dict] = []
    total = 0
    for row in rows:
        text = (row["text"] or "").strip()
        if not text:
            continue
        remain = limit - total
        if remain <= 0:
            break
        if len(text) > remain:
            text = text[:remain]
        result.append({"id": row['id'], "paper_id": paper_id, "page": int(row["page"] or 1), "text": text})
        total += len(text)
    return result


def _build_fulltext_context(rows: list[dict], title: str, header: str = "") -> tuple[str, list[dict]]:
    blocks: list[str] = []
    items: list[dict] = []
    if header:
        blocks.append(header)
    for index, row in enumerate(rows, start=1):
        page = int(row['page']) if row.get('page') else None
        location = f'第 {page} 页' if page else str(row.get('source_kind') or '摘要/生成概述（无原文页码）')
        text = " ".join((row.get("text") or "").split())
        blocks.append(f"[{index}] {location}\n{text}")
        items.append(
            {
                "index": index,
                "paper_id": row.get("paper_id"),
                "title": title,
                "page": page,
                "snippet": text[:SNIPPET_LEN],
                "locator": text[:CONTEXT_CHUNK_LEN] if (row.get("source_kind") or "original") == "original" else "",
                "source_kind": row.get("source_kind") or "original",
                "has_pdf": bool((db.get_paper(row.get("paper_id")) or {}).get("has_pdf")),
            }
        )
    return "\n\n".join(blocks), items


def _meta_result(result) -> dict:
    """兼容仍然返回纯文本的 llm.chat 实现（mock 或第三方封装）。"""
    if isinstance(result, dict):
        return result
    return {"content": str(result or ""), "reasoning": "", "model": "", "citations": [], "server_tool_calls": []}


WEB_IMAGE_HINT = (
    '\n你有联网搜索能力：回答前先判断这个问题是否需要联网检索（推荐搜索，能获得更新、更全的外部资料）。'
    '你所在的应用会自动把检索来源的配图附在回答下方——所以不要声称「无法显示/嵌入图片」，'
    '论文对话里还会自动附上原文对应页的图。需要图时直接说「下方配图」并描述图的内容；也不要编造图片链接或来源。'
)

WEB_TOOL_NOTICE = (
    '\n本轮已开启内置联网工具（deepseek_web_search 检索、deepseek_read_url 阅读链接）。'
    '涉及最新进展、外部资料、用户明确要求上网/搜索的问题时，必须先调用工具检索再回答，'
    '检索结果按「外部资料」标注，不得声称没有联网能力或没有可用检索结果。'
)
WEB_FORCE_NOTICE = (
    '\n\n【强制】上一轮没有调用联网工具。本轮必须先调用 deepseek_web_search 检索外部资料（至少一次搜索），'
    '再结合检索结果回答；如果检索确实失败，明确写出失败原因，不要说"没有联网能力"。'
)


def _answer_llm(system: str, user: str, deep: bool = False, history: list[dict] | None = None,
                server_tools: list[str] | None = None,
                documents: list[str] | None = None,
                require_web: bool = False) -> tuple[str, str, str, dict]:
    """返回 (回答, 深度思考过程, 实际模型, 模型附加信息)。"""
    if documents:
        system += '\n本轮已附上论文 PDF 原件，图表可直接读取；引用页码仍以资料区标注为准。'
    if server_tools:
        system += WEB_TOOL_NOTICE
    system += ai_context.RULES + '\n历史中的引用编号只属于旧轮次，本轮引用必须对应当前资料区。'
    tail = [*ai_context.history_messages(history or []), {'role': 'user', 'content': _user_content(user, documents)}]
    plan = ''
    if not deep:
        result = _meta_result(llm.chat(
            [{"role": "system", "content": system}, *tail],
            temperature=0.2,
            with_meta=True,
            server_tools=server_tools,
        ))
    elif str(config.get_settings().get("reasoning_model") or "").strip():
        result = _meta_result(llm.chat(
            [{"role": "system", "content": system}, *tail],
            temperature=0.2,
            deep=True,
            with_meta=True,
            server_tools=server_tools,
        ))
    else:
        plan = llm.chat(
            [
                {"role": "system", "content": system + "\n\n先输出给用户看的简短分析摘要（最多200字）：概括可用证据、主要不确定性与核对方向。不要输出私有推理、逐步思维链或最终答案。"},
                *tail,
            ],
            temperature=0.2,
            progress_channel='reasoning',
        )
        result = _meta_result(llm.chat(
            [
                {"role": "system", "content": system + "\n\n已给用户展示的分析摘要，作为回答参考：\n" + str(plan or "")[:5000]},
                *tail,
            ],
            temperature=0.2,
            with_meta=True,
            server_tools=server_tools,
        ))
    if server_tools and require_web and not (result.get("web_trace") or result.get("server_tool_calls")):
        result = _meta_result(llm.chat(
            [{"role": "system", "content": system + WEB_FORCE_NOTICE}, *tail],
            temperature=0.2,
            with_meta=True,
            server_tools=server_tools,
        ))
    return (
        str(result.get("content") or "").strip(),
        str(plan or "").strip() or str(result.get("reasoning") or "").strip(),
        str(result.get("model") or config.get_settings().get("model") or ""),
        result,
    )


def _reader_image_parts(images: list[dict]) -> list[dict]:
    parts = []
    signatures = {'data:image/jpeg;base64': b'\xff\xd8\xff', 'data:image/png;base64': b'\x89PNG\r\n\x1a\n'}
    for index, image in enumerate(images, 1):
        url = image['data_url']
        prefix, _, data = url.partition(',')
        signature = signatures.get(prefix)
        try:
            if not signature or not base64.b64decode(data, validate=True).startswith(signature):
                raise ValueError()
        except (ValueError, binascii.Error) as exc:
            raise ValueError(f'截图 {index} 无法读取，请重新框选') from exc
        parts.extend([
            {'type': 'text', 'text': f'截图 {index}（当前论文第 {image["page"]} 页）：'},
            {'type': 'image_url', 'image_url': {'url': url}},
        ])
    return parts


def _answer_reader_images(system: str, user: str, image_parts: list[dict], history: list[dict]) -> tuple[str, str, str, dict]:
    system += ai_context.RULES + (
        '\n截图为待分析的论文资料。按截图编号与页码定位；先用可见内容作答，标明无法辨认的部分。'
        '参考资料仅用于消歧与解释，不可补写截图缺失的内容。'
    )
    model = str(config.get_settings().get('vision_model') or '').strip()
    ai_progress.emit(stage='读取 PDF 区域截图')
    try:
        result = _meta_result(llm.chat(
            [{'role': 'system', 'content': system}, *ai_context.history_messages(history),
             {'role': 'user', 'content': [{'type': 'text', 'text': user}, *image_parts]}],
            temperature=0.2, with_meta=True, model_override=model,
        ))
    except llm.LLMError as exc:
        if ai_progress.cancelled():
            raise
        raise llm.LLMError(f'截图处理失败。请确认「设置 → 模型连接」中的图像模型支持图片输入。{exc}') from exc
    return result['content'], result.get('reasoning', ''), result.get('model', ''), result


def _extra_context(question: str, attachments: list[dict] | None, search_web: bool) -> tuple[str, list[dict]]:
    blocks: list[str] = []
    web_sources: list[dict] = []
    ai_progress.emit(stage="读取本轮附件")
    remaining = min(24000, max(4000, _context_budget() // 3))
    for item in attachments or []:
        name = str((item or {}).get("name") or "附件")[:120]
        text = str((item or {}).get("text") or "").strip()[:min(8000, remaining)]
        if text:
            index = 1001+len(web_sources)
            blocks.append(f"[{index}] 【用户上传：{name}（节选，可能截断）】\n{text}")
            web_sources.append({"index":index, "citation_index":index, "title":name, "source_kind":"attachment", "snippet":text[:SNIPPET_LEN]})
            remaining -= len(text)
    if search_web and len(question) >= 4:
        ai_progress.emit(stage="联网检索背景资料")
        try:
            results = websearch.search(question[:150], limit=5)
        except Exception:  # noqa: BLE001
            results = []
        if results:
            lines = []
            for row in results:
                index = 2001+len(lines)
                lines.append(f"[{index}] {row['title']}\n  {row['snippet']}\n  {row['url']}")
                web_sources.append({"index":index, "citation_index":index, "title": row["title"], "url": row["url"], "snippet": row["snippet"], "source_kind":"web"})
            blocks.append("【联网搜索结果（背景资料，非论文原文；可能截断）】\n" + "\n".join(lines)[:5000])
    return "\n\n".join(blocks), web_sources


#: 共读论文资料块的总上限、单篇预算，以及摘要/要点的截断长度
CO_READ_TOTAL_LIMIT = 12000
CO_READ_PAPER_LIMIT = 4000
CO_READ_ABSTRACT_LEN = 600
CO_READ_KEYPOINTS_LEN = 400
#: 最多带几篇共读论文（与 ChatIn.co_paper_ids 的 max_length 保持一致）
CO_READ_MAX_PAPERS = 4

#: 多篇共读时追加给模型的规则；单篇对话不注入，避免改变既有行为
_CO_READING_RULES = (
    '\n本轮同时打开多篇共读论文，可以把它们一起用于对比与解释；提问只涉及主论文时仍以主论文为准。\n'
    '引用不同论文时必须标注论文来源（用论文标题或共读编号，例如「（共读论文 2《XXX》）」，主论文写作「（本篇《XXX》）」），'
    '不能把别的论文的结论说成本篇结论。\n'
    '用户问「这两篇差异/共同点」时，按论文分点对比：每篇单独列点后再给结论，某篇缺少证据时逐篇说明。'
)


def _co_reading_context(primary_id: str, co_paper_ids: list[str] | None,
                        budget: int) -> tuple[str, list[dict]]:
    """把同时打开的共读论文压缩成本轮资料块，返回 (文本块, 每篇元信息)。

    去重、剔除主论文、最多 ``CO_READ_MAX_PAPERS`` 篇；论文不存在，或者
    摘要、要点、原文一样都没有时跳过。每篇约 4000 字符：摘要 ≤600、
    关键要点 ≤400，剩下的额度留给原文节选；整块不超过 ``min(12000, budget)``。
    """
    cap = max(0, min(int(budget or 0), CO_READ_TOTAL_LIMIT))
    remaining = cap
    ordered: list[str] = []
    for raw in co_paper_ids or []:
        pid = str(raw or "").strip()
        if not pid or pid == str(primary_id or "") or pid in ordered:
            continue
        ordered.append(pid)
        if len(ordered) >= CO_READ_MAX_PAPERS:
            break
    blocks: list[str] = []
    meta: list[dict] = []
    for pid in ordered:
        if remaining <= 0:
            break
        paper = db.get_paper(pid)
        if not paper:
            continue
        title = paper.get("title") or "未命名论文"
        abstract = " ".join(str(paper.get("abstract") or "").split())[:CO_READ_ABSTRACT_LEN]
        points = [str(point).strip() for point in (paper.get("key_points") or []) if str(point).strip()]
        key_points = "；".join(points)[:CO_READ_KEYPOINTS_LEN]
        if not key_points:
            key_points = " ".join(str(paper.get("summary") or "").split())[:CO_READ_KEYPOINTS_LEN]
        per_budget = min(CO_READ_PAPER_LIMIT, remaining)
        # 标题、元信息、格式符号也要占额度，先预留 300 字符再取原文
        excerpt_limit = max(0, per_budget - len(abstract) - len(key_points) - 300)
        rows = _paper_fulltext_rows(pid, limit=excerpt_limit) if excerpt_limit else []
        if not (abstract or key_points or rows):
            continue
        index = len(meta) + 1
        lines = [f"【共读论文 {index}：{title}】"]
        facts = []
        if paper.get("authors"):
            facts.append(f"作者：{paper['authors']}")
        if paper.get("year"):
            facts.append(f"年份：{paper['year']}")
        if paper.get("venue"):
            facts.append(f"期刊/会议：{paper['venue']}")
        if facts:
            lines.append("；".join(facts))
        if abstract:
            lines.append(f"摘要：{abstract}")
        if key_points:
            lines.append(f"关键要点：{key_points}")
        if rows:
            lines.append("原文节选：")
            for row in rows:
                text = " ".join(str(row.get("text") or "").split())
                if not text:
                    continue
                page = int(row["page"]) if row.get("page") else None
                lines.append(f"（第 {page} 页）{text}" if page else text)
        block = "\n".join(lines)[:per_budget].rstrip()
        if not block:
            continue
        blocks.append(block)
        remaining -= len(block) + 2
        meta.append({"index": index, "id": pid, "title": title})
    text = "\n\n".join(blocks)
    if len(text) > cap:
        text = text[:cap].rstrip()
    return text, meta


def _chat_paper(question: str, paper_id: str, deep: bool = False, extra: str = "",
                history: list[dict] | None = None, page: int | None = None, selection: str = '', extra_sources: list[dict] | None = None,
                retrieval_question: str | None = None, image_parts: list[dict] | None = None, image_action: str | None = None,
                server_tools: list[str] | None = None, require_web: bool = False,
                co_paper_ids: list[str] | None = None,
                parent_context: list[dict] | None = None,
                quoted_text: str = '', allow_web: bool = True) -> dict:
    ai_progress.emit(stage='读取论文原文与摘要')
    paper = db.get_paper(paper_id)
    if not paper:
        raise ValueError("论文不存在")
    title = paper.get("title") or "未命名论文"
    budget = _context_budget()
    rows = _paper_fulltext_rows(paper_id, limit=budget)
    # Reserve room for relevant material from the rest of a long paper and the reading position.
    tokens = _query_tokens(ai_context.followup_query(retrieval_question or question, history or []))
    related = _search_fts(tokens, paper_id, 5) + _search_like(tokens, paper_id, 5)
    if page:
        related = list(db.query('SELECT id,paper_id,page,text FROM chunks WHERE paper_id=? AND page BETWEEN ? AND ? ORDER BY page,id LIMIT 4',
                                (paper_id, max(1,page-1), page+1))) + related
    seen = {row['id'] for row in rows}
    remaining = budget - sum(len(row['text']) for row in rows)
    for row in related:
        if row['id'] in seen or remaining <= 0:
            continue
        text = str(row['text'] or '')[:min(remaining, 2400)]
        rows.append({'id': row['id'], 'paper_id': paper_id, 'page': row['page'], 'text': text})
        seen.add(row['id'])
        remaining -= len(text)
    # 扫描版/图表理解：把 PDF 原件交给服务端解析（首次慢，之后按内容指纹复用 file_id）
    documents: list[str] = []
    file_note = ''
    file_mode = _paper_file_mode()
    if file_mode == 'always' or (file_mode == 'scanned' and not rows):
        documents, file_error = _paper_documents(paper, 'rich' if file_mode == 'always' else 'text')
        file_note = file_error
    if not rows and not documents:
        abstract = (paper.get("abstract") or paper.get("summary") or "").strip()
        if abstract:
            rows = [{"paper_id": paper_id, "page": None, "text": abstract,
                     "source_kind": 'abstract' if paper.get('abstract') else 'generated_summary'}]
        else:
            extra = '【原文状态】没有可用正文或摘要，只能回答提供的元数据、用户笔记或附件涉及的问题；研究结论需用户补充 PDF 并索引。\n'+extra
    quoted = ' '.join(str(quoted_text or '').split())
    if quoted:
        extra = ('【用户引用了上文片段（仅用于理解指代；片段里的 > < 等是文本符号，不是不等式或公式）】\n'
                 + quoted[:3000] + '\n【引用结束】\n\n') + extra
    if parent_context:
        lines = []
        for item in parent_context[-8:]:
            role = '用户' if str(item.get('role')) == 'user' else '回答'
            body = ' '.join(str(item.get('content') or '').split())[:800]
            if body:
                lines.append(f'[{role}] {body}')
        if lines:
            extra = ('【父对话上下文（同一篇论文）：这是用户在本分支之前的主线对话，属于用户已知的上文。'
                     '可以直接引用、可以用来回答「我刚才问了什么」「之前的结论是什么」这类问题；'
                     '其中具体论据仍需在本文资料里核对，不要声称没有上文。】\n' + '\n'.join(lines) + '\n\n') + extra
    if file_note:
        extra = f'【PDF 在线解析失败】{file_note}\n' + extra
    header_parts = [f"论文标题：{title}"]
    if paper.get("authors"):
        header_parts.append(f"作者：{paper['authors']}")
    if paper.get("year"):
        header_parts.append(f"年份：{paper['year']}")
    context, items = _build_fulltext_context(rows, title, header="\n".join(header_parts))

    # 多篇共读：把同时打开的其他论文压缩进资料区，放在主论文资料之后、
    # 「资料区结束」之前；总预算不超过 min(12000, budget // 2)。
    co_context, co_papers = _co_reading_context(paper_id, co_paper_ids, min(CO_READ_TOTAL_LIMIT, budget // 2))
    if co_context:
        extra = f"{extra}\n\n{co_context}" if extra else co_context

    prior = ai_context.paper_prior(paper, page, selection)
    user = f"【资料区：当前论文节选，受长度预算限制】\n{context}\n\n{prior}\n\n{extra}\n【资料区结束】\n\n用户问题：{question}"
    system = _paper_answer_prompt()
    if co_papers:
        # 有共读论文时才放开跨论文比较，并强制标注来源
        system += _CO_READING_RULES
    else:
        system += '\n当前入口仅讨论这篇论文，跨论文比较请引导用户使用全库问答。'
    system += '用户明确要求上网或补充外部资料时，可用联网工具检索，结果作为「外部资料」明确标注，不可据此声称本文得出某结论。'
    if quoted_text:
        system += '\n用户在本轮引用了上文片段（见资料区引用块）：请结合它理解指代，不要把它里面的 > 当成大于号。'
    if parent_context:
        system += '\n本轮带有「父对话上下文」：回答可以引用其中的前情并说明来自上文；不要回答「看不到之前的对话」。'
    system += '\n用户要求翻译时，以下规范优先于上面的问答格式：\n' + skills.task_prompt('translation')
    if image_action == 'translate':
        system += '\n本轮是截图翻译。按截图顺序翻译可见内容，保持原文结构，只输出译文和必要的辨识标记。'
    elif image_action == 'explain':
        system += '\n本轮是截图解释。用中文讲清可见文字、公式或图表的含义及在本篇论文中的作用。'
    if image_parts:
        answer, reasoning, used_model, meta = _answer_reader_images(system, user, image_parts, history or [])
    else:
        answer, reasoning, used_model, meta = _answer_llm(
            system, user, deep=deep, history=history, server_tools=server_tools, documents=documents,
            require_web=require_web,
        )
    answer = (answer or "").strip()
    if not answer:
        raise llm.LLMError("大模型没有返回回答内容，请重试")
    citations = list((meta or {}).get("citations") or [])
    answer += _reference_block(citations)

    return _attach_paper_figures(_evidence_result(
        answer, reasoning, used_model, items + (extra_sources or []), citations,
        (meta or {}).get("web_trace"), co_papers, query=question, allow_web=allow_web),
        fallback_paper_id=paper_id)


def _attach_paper_figures(result: dict, fallback_paper_id: str = '') -> dict:
    """把回答引用（或候选证据）所在论文页面的原图附到结果里；失败不影响回答。"""
    result = result if isinstance(result, dict) else {}
    try:
        return _attach_paper_figures_inner(result, fallback_paper_id)
    except Exception:  # noqa: BLE001
        return result


def _attach_paper_figures_inner(result: dict, fallback_paper_id: str = '') -> dict:
    from . import paper_figures
    pages: dict[str, list[int]] = {}
    evidence = list(result.get('sources') or []) + list(result.get('retrieval_candidates') or [])
    for source in evidence:
        paper_id = str(source.get('paper_id') or '')
        try:
            page = int(source.get('page') or 0)
        except (TypeError, ValueError):
            page = 0
        if paper_id and page > 0:
            pages.setdefault(paper_id, []).append(page)
    figures: list[dict] = []
    for paper_id, page_list in list(pages.items())[:2]:
        try:
            figures.extend(paper_figures.extract(paper_id, sorted(set(page_list)), limit=2))
        except Exception:  # noqa: BLE001
            continue
    if not figures and fallback_paper_id:
        try:
            figures.extend(paper_figures.extract(fallback_paper_id, [], limit=2))
        except Exception:  # noqa: BLE001
            pass
    if figures:
        result['web_images'] = list(result.get('web_images') or []) + figures
    return result


def _evidence_result(answer: str, reasoning: str, model: str, items: list[dict],
                     external_sources: list[dict] | None = None,
                     web_trace: list[dict] | None = None,
                     co_papers: list[dict] | None = None, query: str = '',
                     allow_web: bool = True) -> dict:
    """A card represents exactly one numbered reference; candidates are never citations."""
    cited = {int(match.group(1)) for match in _CITATION_RE.finditer(answer)}
    sources, candidates = [], []
    for item in items:
        card = {**item, 'citation_index':item['index']}
        (sources if item['index'] in cited else candidates).append(card)
    web_rows = [row for row in sources if row.get('source_kind') == 'web']
    image_sources = list(web_rows)
    image_query = query
    for trace in web_trace or []:
        for candidate in (trace or {}).get('queries') or []:
            if re.search(r'[A-Za-z]{4,}', candidate) and len(candidate.split()) >= 2:
                image_query = candidate
                break
    for trace in web_trace or []:
        for item in (trace or {}).get('sources') or []:
            if item.get('url'):
                image_sources.append({'url': item.get('url'), 'title': item.get('title') or ''})
    images: list[dict] = []
    if allow_web and (image_sources or image_query):
        try:
            from . import web_images as web_images_service

            images = web_images_service.collect(image_sources, query=image_query) if image_sources else []
            if not images:
                images = web_images_service.collect([], query=image_query)
        except Exception:  # noqa: BLE001 - 配图失败不影响回答
            images = []
    return {'answer':answer, 'sources':sources, 'retrieval_candidates':candidates,
            'web_sources':web_rows,
            'web_images':images,
            'external_sources':list(external_sources or []),
            'web_trace':list(web_trace or []),
            'co_papers':list(co_papers or []),
            'reasoning':reasoning, 'model':model}


def chat(question: str, paper_id: str | None = None, deep: bool = False, attachments: list[dict] | None = None,
         search_web: bool = False, history: list[dict] | None = None, page: int | None = None, selection: str = '', paper_ids: list[str] | None = None,
         retrieval_question: str | None = None, images: list[dict] | None = None, image_action: str | None = None,
         co_paper_ids: list[str] | None = None, parent_context: list[dict] | None = None,
         quoted_text: str = '') -> dict:
    """基于文献库回答提问；给了 paper_id 时直接把该论文全文喂给模型。"""
    question = (question or "").strip()
    if not question:
        raise ValueError("问题不能为空")
    search_question=(retrieval_question or question).strip()
    image_parts = _reader_image_parts(images or [])
    builtin_search = _use_builtin_search(search_web, bool(image_action))
    server_tools = [llm.SERVER_TOOL_SEARCH, llm.SERVER_TOOL_READ_URL] if builtin_search else None
    require_web = bool(builtin_search and _wants_web_search(search_question, question))
    # 用服务端联网工具时不再本地抓取，避免重复搜索和两套来源混在一起
    extra, web_sources = _extra_context(
        search_question, attachments, search_web and not image_action and not builtin_search
    )
    web_allowed = bool(search_web or builtin_search)
    if paper_id:
        result = _chat_paper(question, paper_id, deep=deep, extra=extra, history=history, page=page, selection=selection,
                             extra_sources=web_sources, retrieval_question=search_question, image_parts=image_parts, image_action=image_action,
                             server_tools=server_tools, require_web=require_web, co_paper_ids=co_paper_ids,
                             parent_context=parent_context, quoted_text=quoted_text, allow_web=web_allowed)
        return result

    query = ai_context.followup_query(search_question, history or [])
    ai_progress.emit(stage='检索选定论文' if paper_ids else '检索文献库')
    if paper_ids:
        chosen = list(dict.fromkeys(paper_ids))[:12]
        groups = []
        for pid in chosen:
            if not db.get_paper(pid):
                raise ValueError('比较范围中有论文不存在，请重新选择')
            groups.append(_retrieve(query, pid)[:3])
        rows = [group[i] for i in range(3) for group in groups if len(group)>i][:max(TOP_K,len(chosen))]
        extra += '\n【明确比较范围】仅比较以下选中论文；缺少原文或证据时逐篇说明：'+ '、'.join(_paper_titles(chosen).values())
    else:
        rows = _retrieve(query, paper_id)
    if not paper_ids and history and re.search(r'刚才|上面|上述|它|这几篇|第二篇|继续|对比', question):
        pinned = []
        for pid in ai_context.cited_papers(history):
            pinned.extend(_retrieve(query, pid)[:2])
        seen = {row['id'] for row in pinned}
        rows = (pinned + [row for row in rows if row['id'] not in seen])[:TOP_K]
    paper_ids = list({row["paper_id"] for row in rows})
    titles = _paper_titles(paper_ids)
    context, items = _build_context(rows, titles)

    user = f"【资料区】\n{ai_context.library_prior()}\n\n论文片段：\n{context or '未找到相关原文证据'}\n\n{extra}\n【资料区结束】\n\n用户问题：{question}"
    answer, reasoning, used_model, meta = _answer_llm(
        _answer_prompt(), user, deep=deep, history=history, server_tools=server_tools, require_web=require_web
    )
    answer = (answer or "").strip()
    if not answer:
        raise llm.LLMError("大模型没有返回回答内容，请重试")
    citations = list((meta or {}).get("citations") or [])
    answer += _reference_block(citations)

    return _attach_paper_figures(_evidence_result(
        answer, reasoning, used_model, items + web_sources, citations,
        (meta or {}).get("web_trace"), query=question, allow_web=web_allowed))
