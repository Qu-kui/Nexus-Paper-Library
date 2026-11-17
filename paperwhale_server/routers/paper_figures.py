"""论文原文配图（本地缓存）。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import paper_figures

router = APIRouter(prefix='/api/paper-figures', tags=['paper-figures'])
_NAME_RE = re.compile(r'^[a-f0-9]{16,64}\.jpg$')


@router.get('/{name}')
def serve(name: str) -> FileResponse:
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(404, '图片不存在')
    target = Path(paper_figures.cache_dir()) / name
    if not target.is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(target, headers={'Cache-Control': 'public, max-age=604800'})
