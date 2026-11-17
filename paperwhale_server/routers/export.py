"""导出文件：把前端生成的图/表存到磁盘（WebView2 里 a[download] 不可靠）。"""
from __future__ import annotations

import base64
import binascii
import os
import re
import subprocess
import sys
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from .. import config

router = APIRouter(prefix='/api/export', tags=['export'])

_EXPORT_DIR_NAME = 'PaperWhale导出'


def exports_dir() -> Path:
    downloads = Path.home() / 'Downloads'
    base = downloads if downloads.is_dir() else config.DATA_DIR
    folder = base / _EXPORT_DIR_NAME
    folder.mkdir(parents=True, exist_ok=True)
    return folder


class SaveIn(BaseModel):
    filename: str = Field(min_length=1, max_length=160)
    data_base64: str = Field(min_length=1, max_length=32_000_000)


@router.post('/save')
def save_file(body: SaveIn) -> dict:
    name = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', body.filename).strip().strip('.') or 'export'
    suffix = Path(name).suffix.lower()
    if suffix not in {'.svg', '.png', '.json', '.csv', '.md', '.txt'}:
        name += '.png'
    try:
        payload = base64.b64decode(body.data_base64, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(400, '导出数据不合法') from exc
    if not payload:
        raise HTTPException(400, '导出内容为空')
    folder = exports_dir()
    target = folder / name
    counter = 1
    while target.exists():
        target = folder / f'{Path(name).stem}-{counter}{Path(name).suffix}'
        counter += 1
    target.write_bytes(payload)
    return {'ok': True, 'path': str(target), 'folder': str(folder), 'name': target.name}


@router.post('/open')
def open_folder() -> dict:
    folder = exports_dir()
    try:
        if sys.platform.startswith('win'):
            os.startfile(folder)  # noqa: S606 - 用户主动点开文件夹
        elif sys.platform == 'darwin':
            subprocess.Popen(['open', str(folder)])
        else:
            subprocess.Popen(['xdg-open', str(folder)])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(500, f'打开文件夹失败：{exc}') from exc
    return {'ok': True, 'folder': str(folder)}
