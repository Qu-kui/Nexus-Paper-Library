"""Recovery and local metadata backups; no cloud upload."""
from pathlib import Path

from fastapi import APIRouter,File,UploadFile,HTTPException
from fastapi.responses import FileResponse

from .. import config
from ..services import recovery

router=APIRouter(prefix='/api/maintenance',tags=['maintenance'])


@router.get('/trash')
def trash():
    return {'items':recovery.list_items()}


@router.post('/trash/{item_id}/restore')
def restore(item_id: str):
    return recovery.restore_paper(item_id)


@router.post('/backup')
def backup():
    return recovery.create_backup()


@router.get('/backups/{name}')
def download(name: str):
    if Path(name).name!=name or not name.startswith('paperwhale-metadata-') or not name.endswith('.zip'):
        raise HTTPException(404,'备份不存在')
    path=config.DATA_DIR/'backups'/name
    if not path.is_file():
        raise HTTPException(404,'备份不存在')
    return FileResponse(path,media_type='application/zip',filename=name)


@router.post('/restore')
async def restore_backup(file:UploadFile=File(...)):
    data=await file.read(100*1024*1024+1)
    return recovery.restore_backup(data)
