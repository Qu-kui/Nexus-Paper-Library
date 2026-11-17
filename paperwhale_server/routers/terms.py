"""术语复习 API：列表/统计、手动添加、抽取、复习、删除。"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import os
import subprocess
import sys

from ..services import terms as terms_service

router = APIRouter(prefix='/api/terms', tags=['terms'])


class TermIn(BaseModel):
    term: str = Field(min_length=1, max_length=120)
    expansion: str = Field(default='', max_length=400)
    meaning: str = Field(default='', max_length=400)
    paper_id: str = Field(default='', max_length=80)
    page: int = 0
    kind: str = ''
    phonetic: str = ''
    pos: str = ''
    aliases: list[str] = Field(default_factory=list, max_length=50)


class TermUpdateIn(BaseModel):
    term: str | None = Field(default=None, max_length=120)
    expansion: str | None = Field(default=None, max_length=400)
    meaning: str | None = Field(default=None, max_length=400)
    kind: str | None = None
    phonetic: str | None = Field(default=None, max_length=120)
    pos: str | None = Field(default=None, max_length=40)
    aliases: list[str] | None = Field(default=None, max_length=50)


class ReviewIn(BaseModel):
    grade: str = 'good'


class AnswerIn(BaseModel):
    correct: bool = False
    unknown: bool = False
    fuzzy: bool = False
    direction: str = ''
    question_id: str = Field(default='', max_length=80)
    chosen_option: str | None = Field(default=None, max_length=600)


class ExtractIn(BaseModel):
    paper_id: str = Field(default='', max_length=80)
    mode: str = 'regex'
    all: bool = False


@router.get('')
def list_terms(paper_id: str = '', q: str = '', limit: int = 500, offset: int = 0) -> dict:
    return {'items': terms_service.list_terms(paper_id=paper_id, q=q, limit=limit, offset=offset),
            'total': terms_service.count_terms(paper_id, q), 'stats': terms_service.stats()}


@router.get('/due')
def due(limit: int = 20) -> dict:
    return {'items': terms_service.due_terms(limit), 'stats': terms_service.stats()}


@router.post('')
def add_term(body: TermIn) -> dict:
    try:
        created = terms_service.add_term(body.term, body.expansion, body.meaning,
                                         body.paper_id, body.page, source='manual', kind=body.kind,
                                         phonetic=body.phonetic, pos=body.pos, aliases=body.aliases)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if created is None:
        raise HTTPException(409, '这个术语已经在列表里了')
    return {'item': created, 'stats': terms_service.stats()}


@router.patch('/{term_id}')
def update_term(term_id: str, body: TermUpdateIn) -> dict:
    try:
        updated = terms_service.update_term(term_id, **body.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not updated:
        raise HTTPException(404, '术语不存在')
    return {'item': updated}


@router.delete('/{term_id}')
def delete_term(term_id: str) -> dict:
    if not terms_service.delete_term(term_id):
        raise HTTPException(404, '术语不存在')
    return {'ok': True, 'stats': terms_service.stats()}


@router.post('/{term_id}/review')
def review_term(term_id: str, body: ReviewIn) -> dict:
    try:
        updated = terms_service.review_term(term_id, body.grade)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not updated:
        raise HTTPException(404, '术语不存在')
    return {'item': updated, 'stats': terms_service.stats()}


@router.get('/quiz')
def quiz(limit: int = 20, paper_id: str = '', mode: str = 'mixed') -> dict:
    items = terms_service.quiz(limit=limit, paper_id=paper_id, mode=mode)
    return {'items': items, 'stats': terms_service.stats()}


@router.post('/{term_id}/retire')
def retire(term_id: str) -> dict:
    updated = terms_service.retire_term(term_id)
    if not updated:
        raise HTTPException(404, '术语不存在')
    return {'item': updated, 'stats': terms_service.stats()}


@router.get('/dictionary')
def dictionary() -> dict:
    info = terms_service.sync_dictionary()
    return {**info, 'stats': terms_service.stats()}


@router.post('/dictionary/open')
def open_dictionary() -> dict:
    folder = terms_service.sync_dictionary()['path']
    try:
        if sys.platform.startswith('win'):
            os.startfile(folder)  # noqa: S606 - 用户主动点开文件夹
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', folder])
        else:
            subprocess.Popen(['xdg-open', folder])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f'打开文件夹失败：{exc}') from exc
    return {'path': folder}


@router.post('/{term_id}/answer')
def answer(term_id: str, body: AnswerIn) -> dict:
    try:
        updated = terms_service.answer_quiz(term_id, bool(body.correct), unknown=bool(body.unknown),
                                           fuzzy=bool(body.fuzzy), direction=body.direction,
                                           question_id=body.question_id, chosen_option=body.chosen_option)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not updated:
        raise HTTPException(404, '术语不存在')
    return {'item': updated, 'stats': terms_service.stats()}


@router.get('/extract/status')
def extract_status() -> dict:
    return {'job': terms_service.ai_job_status(), 'stats': terms_service.stats()}


@router.post('/extract/cancel')
def extract_cancel() -> dict:
    return {'job': terms_service.cancel_ai_job(), 'stats': terms_service.stats()}


@router.post('/extract')
def extract(body: ExtractIn) -> dict:
    if not body.all and not body.paper_id:
        raise HTTPException(400, '请选择一篇论文，或勾选全部英文文献')
    if body.all and body.mode == 'ai':
        try:
            job = terms_service.start_ai_extract_all()
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
        return {'started': True, 'job': job, 'stats': terms_service.stats()}
    try:
        if body.all:
            added = terms_service.extract_abbreviations_all()
        elif body.mode == 'ai':
            added = terms_service.ai_extract_terms(body.paper_id)
        else:
            added = terms_service.extract_abbreviations(body.paper_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        from .. import llm
        if isinstance(exc, llm.LLMError):
            raise HTTPException(400, str(exc)) from exc
        raise HTTPException(500, f'抽取失败：{exc}') from exc
    return {'added': added, 'stats': terms_service.stats()}
