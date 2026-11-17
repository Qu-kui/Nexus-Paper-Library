from fastapi import APIRouter,HTTPException,Query
from .. import db
from ..services import batches

router=APIRouter(prefix='/api/tasks',tags=['tasks'])


@router.get('/batches')
def recent(batch_id: list[str] = Query(default=[])):
    return {'items':batches.recent(batch_id)}


@router.post('/batches/{batch_id}/retry')
def retry(batch_id:str):
    batch=db.query_one('SELECT kind FROM task_batches WHERE id=?',(batch_id,))
    if not batch:
        raise HTTPException(404,'批次不存在')
    ids=[row['paper_id'] for row in db.query("SELECT paper_id FROM task_batch_items WHERE batch_id=? AND state='failed'",(batch_id,)) if db.get_paper(row['paper_id'])]
    if batch['kind']=='pdf':
        from ..services.pdf_fetch import manager
        queued=manager.start(ids) if ids else 0
    elif batch['kind']=='category':
        from ..services.category_assistant import queue_manager as manager
        queued=manager.start(ids) if ids else 0
    elif batch['kind']=='methods':
        from ..services.methods import queue_manager as manager
        queued=manager.start(ids) if ids else 0
        queued=manager.start(ids) if ids else 0
    else:
        from ..services.analysis_queue import queue_manager as manager
        queued=manager.start(ids,only_new=False) if ids else 0
    return {'queued':queued,'status':manager.status()}
