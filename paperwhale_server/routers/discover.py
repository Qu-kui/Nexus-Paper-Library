"""在线发现 API：arXiv 检索与下载入库。"""
from __future__ import annotations
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel,Field

from .. import db
from ..models import DownloadIn
from ..services import discover as discover_service
from ..services import metadata as metadata_service
from ..services.metadata import clean_meta

router = APIRouter(prefix="/api", tags=["discover"])


class DiscoverySessionIn(BaseModel):
    title:str=Field(default='',max_length=120)


class DiscoverySearchIn(BaseModel):
    session_id:str=Field(max_length=100)
    question:str=Field(min_length=1,max_length=8000)
    conversation_id:str|None=None
    revision:int|None=None
    mode: Literal['auto', 'file', 'followup'] = 'auto'
    seed_query: str = Field(default='', max_length=500)
    date_basis: Literal['publication', 'preprint'] = 'publication'
    date_from: str = Field(default='', max_length=10)
    broaden: bool = False
    citations_only: bool = False


class CandidateStateIn(BaseModel):
    status:str


@router.get('/discover/sessions')
def discovery_sessions():
    from ..services import discovery_store
    return {'items':discovery_store.list_sessions()}


@router.post('/discover/sessions')
def new_discovery_session(body:DiscoverySessionIn):
    from ..services import discovery_store
    return discovery_store.create_session(body.title)


@router.get('/discover/sessions/{session_id}')
def discovery_session(session_id:str):
    from ..services import discovery_store
    return discovery_store.detail(session_id)


from ..services.activity import tracked


@router.delete('/discover/sessions/{session_id}')
def delete_discovery_session(session_id:str):
    from ..services import discovery_store
    return discovery_store.delete_session(session_id)


@router.post('/discover/sessions/{session_id}/translate')
def translate_discovery_session(session_id:str):
    from ..services import discovery_translation
    return discovery_translation.translate_session(session_id)


@router.post('/discover/ai')
@tracked
def ai_discovery(body:DiscoverySearchIn):
    from .. import config,llm
    from ..services import discovery_store,research_discovery,conversations
    discovery_store.get_session(body.session_id)
    if not(llm.is_configured() or llm.is_mock()):
        raise HTTPException(400,'请先在设置中配置大模型，再使用 AI 找论文')
    try:
        options = body.model_dump(include={'mode','seed_query','date_basis','date_from','broaden','citations_only'})
        result=conversations.turn('discovery:'+body.session_id,body.question,
            lambda history,materials:research_discovery.research(
                body.question, history, str(config.get_settings().get('profile') or '')[:6000],
                materials=materials, options=options, previous_seed=discovery_store.last_seed(body.session_id)),
            conversation_id=body.conversation_id,revision=body.revision)
        return discovery_store.save_run(body.session_id,body.question,result,result['conversation'])
    except (llm.LLMError,ValueError) as exc:
        message=str(exc)[:600]
        for key in ('api_key','openalex_api_key','scholar_password'):
            secret=str(config.get_settings().get(key) or '')
            if secret:message=message.replace(secret,'***')
        raise HTTPException(400,message) from exc


@router.post('/discover/candidates/{candidate_id}/add')
def add_discovery_candidate(candidate_id:str):
    from ..services import discovery_store
    return discovery_store.add_candidate(candidate_id)


@router.post('/discover/candidates/{candidate_id}/state')
def discovery_candidate_state(candidate_id:str,body:CandidateStateIn):
    from ..services import discovery_store
    return discovery_store.change_state(candidate_id,body.status)


@router.get("/discover/arxiv")
def arxiv_search(q: str = "", limit: int = 10) -> dict:
    keyword = (q or "").strip()
    if not keyword:
        return {"items": []}
    try:
        items = discover_service.arxiv_search(keyword, limit)
    except discover_service.DiscoverError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"arXiv 检索失败：{exc}") from exc
    return {"items": items}


@router.post("/discover/download")
def download(body: DownloadIn) -> dict:
    arxiv_id = (body.arxiv_id or "").strip()
    if not arxiv_id:
        raise HTTPException(400, "请提供 arXiv ID")
    try:
        pdf_path, meta = discover_service.download_arxiv(arxiv_id)
    except metadata_service.MetadataError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except discover_service.DiscoverError as exc:
        raise HTTPException(exc.status, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"下载 arXiv 论文失败：{exc}") from exc

    payload = clean_meta(meta)
    try:
        from ..services.ingest import create_paper_from_metadata

        paper_id = create_paper_from_metadata(payload, pdf_path=pdf_path, source="arxiv")
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f"PDF 已下载但保存失败：{exc}") from exc
    finally:
        try:
            pdf_path.unlink(missing_ok=True)
        except OSError:
            pass
    try:
        paper = db.get_paper(paper_id)
    except Exception:  # noqa: BLE001
        paper = None
    return {"paper": paper or {"id": paper_id, **payload}}
