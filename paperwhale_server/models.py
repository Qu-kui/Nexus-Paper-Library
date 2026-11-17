"""请求/响应模型。"""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class SettingsUpdate(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    reasoning_model: str | None = None
    vision_model: str | None = None
    temperature: float | None = None
    language: str | None = None
    ui_accent: str | None = None
    ui_theme: str | None = None
    summary_language: str | None = None
    profile: str | None = None
    profile_name: str | None = None
    pdf_dir: str | None = None
    category_guide: str | None = None
    openalex_api_key: str | None = None
    openalex_content_enabled: bool | None = None
    unpaywall_email: str | None = None
    pdf_browser: str | None = None
    pdf_browser_profile: str | None = None
    pdf_browser_session_enabled: bool | None = None
    clipboard_watch: bool | None = None
    clipboard_title_guess: bool | None = None
    thinking_mode: str | None = None
    app_api_enabled: bool | None = None
    web_search_mode: str | None = None
    paper_file_mode: str | None = None
    answer_context_chars: int | None = None


class PdfMigrateIn(BaseModel):
    old_dir: str = ""
    dry_run: bool = False


class ConversationIn(BaseModel):
    conversation_id: str | None = None
    revision: int | None = None


class CategoryChatIn(ConversationIn):
    messages: list[dict] = Field(default_factory=list)


class CategoryApplyIn(BaseModel):
    guide: str = ""
    # unclassified：只整理还没有分类的论文（默认，原行为）；all：对全部论文重新分类
    scope: str = "unclassified"
    # 重新分类时是否把 PDF 一起移动到新的分类目录（默认移动，与原行为一致）
    move_files: bool = True


class PaperPatch(BaseModel):
    title: str | None = None
    authors: str | None = None
    year: str | None = None
    venue: str | None = None
    doi: str | None = None
    volume: str | None = None
    issue: str | None = None
    pages: str | None = None
    abstract: str | None = None
    tags: list[str] | None = None
    category: str | None = None
    notes: str | None = None


class AnnotationIn(BaseModel):
    page: int = 1
    color: str = "#ffd666"
    rects: list[dict[str, float]] = Field(default_factory=list)
    quote: str = ""
    note: str = ""
    kind: str = "highlight"


class ReaderImage(BaseModel):
    page: int = Field(ge=1)
    data_url: str = Field(max_length=2_000_000)


class ChatIn(ConversationIn):
    question: str = Field(max_length=16000)
    display_question: str = Field(default='',max_length=16000)
    retrieval_question: str = Field(default='',max_length=16000)
    paper_id: str | None = None
    deep_thinking: bool = False
    attachments: list[dict[str, str]] | None = None
    search_web: bool = False
    page: int | None = Field(default=None, ge=1)
    selection: str = Field(default='', max_length=4000)
    paper_ids: list[str] = Field(default_factory=list,max_length=20)
    # 阅读器里同时打开的论文（共读上下文），最多 4 篇
    co_paper_ids: list[str] = Field(default_factory=list, max_length=4)
    images: list[ReaderImage] = Field(default_factory=list, max_length=6)
    image_action: Literal['translate', 'explain'] | None = None
    branch: str | None = Field(default=None, max_length=32)
    quoted_text: str = Field(default='', max_length=4000)
    reading_session: str = Field(default='', max_length=40)


class BranchCreateIn(BaseModel):
    paper_id: str
    from_branch: str = Field(default='', max_length=32)
    upto_index: int = -1


class ReadingBranchIn(BaseModel):
    scope: str = Field(min_length=8, max_length=80)
    from_branch: str = Field(default='', max_length=32)
    upto_index: int = -1


class ReadingBranchDeleteIn(BaseModel):
    scope: str = Field(min_length=8, max_length=80)
    branch: str = Field(min_length=1, max_length=32)


class ReadingSessionIn(BaseModel):
    scope: str = Field(min_length=8, max_length=80)


class BranchDeleteIn(BaseModel):
    paper_id: str
    branch: str = Field(min_length=1, max_length=32)


class TranslateIn(ConversationIn):
    text: str
    target_lang: str | None = None
    paper_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    # 阅读器里当前会话的实际 scope（reading:... 或 paper:...），
    # 避免前端带来的 conversation_id 与固定 paper scope 对不上。
    scope: str | None = Field(default=None, max_length=160)


class ExplainIn(ConversationIn):
    text: str
    paper_id: str | None = None
    page: int | None = Field(default=None, ge=1)
    scope: str | None = Field(default=None, max_length=160)


class ProfileIn(BaseModel):
    profile: str = ""


class ProfileChatIn(ConversationIn):
    messages: list[dict] = Field(default_factory=list)
    attachments: list[dict[str, str]] | None = None
    search_web: bool = False
    deep_thinking: bool = False


class PathIn(BaseModel):
    path: str


class ReferenceExpandIn(BaseModel):
    """参考文献外扩：以库里一篇论文为种子，拉它引用/被引的文献。"""

    paper_id: str
    direction: str = "references"  # references：它引用的；citations：引用它的
    limit: int = 40


class ReferenceImportIn(BaseModel):
    items: list[dict] = Field(default_factory=list)
    fetch_pdf: bool = True


class EndnoteIn(BaseModel):
    """EndNote 迁移：path 可以是 .xml 导出文件、.enl 库文件或库所在文件夹。"""
    path: str
    include_without_pdf: bool = True


class PdfFolderIn(BaseModel):
    path: str
    recursive: bool = True
    category_path: list[str] = Field(default_factory=list)


class IdentifierIn(BaseModel):
    identifier: str


class DownloadIn(BaseModel):
    arxiv_id: str


class MetadataIn(BaseModel):
    title: str
    authors: str = ""
    year: str = ""
    venue: str = ""
    doi: str = ""
    arxiv_id: str = ""
    url: str = ""
    abstract: str = ""
    tags: list[str] = Field(default_factory=list)
    category: str = ""


class ApiMessage(BaseModel):
    ok: bool = True
    message: str = ""
    data: Any = None


class AnnotationPatch(BaseModel):
    note: str | None = None
    color: str | None = None
    kind: str | None = None
