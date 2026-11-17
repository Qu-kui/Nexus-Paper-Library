"""本地缓存的检索配图。"""
from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from ..services import web_images

router = APIRouter(prefix='/api/web-images', tags=['web-images'])
_NAME_RE = re.compile(r'^[a-f0-9]{16,64}\.(?:png|jpg|jpeg|webp|gif)$')


@router.get('/{name}')
def serve(name: str) -> FileResponse:
    if not _NAME_RE.fullmatch(name):
        raise HTTPException(404, '图片不存在')
    target = Path(web_images.cache_dir()) / name
    if not target.is_file():
        raise HTTPException(404, '图片不存在')
    return FileResponse(target, headers={'Cache-Control': 'public, max-age=604800'})
