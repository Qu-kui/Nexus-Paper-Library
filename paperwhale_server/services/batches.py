"""Batch-owned task counts, independent of lifetime job history."""
from contextlib import closing

from .. import db


def create(kind,paper_ids):
    ids=list(dict.fromkeys(paper_ids))
    if not ids:
        return None
    batch_id=db.new_id()
    with closing(db.connect()) as conn,conn:
        conn.execute('INSERT INTO task_batches(id,kind,created_at) VALUES(?,?,?)',(batch_id,kind,db.now()))
        conn.executemany('INSERT INTO task_batch_items(batch_id,paper_id) VALUES(?,?)',[(batch_id,pid) for pid in ids])
    return batch_id


def finish(batch_id,paper_id,state,error=''):
    if batch_id:
        db.execute('UPDATE task_batch_items SET state=?,error=? WHERE batch_id=? AND paper_id=?',(state,str(error)[:500],batch_id,paper_id))


def current(kind):
    row=db.query_one('SELECT id FROM task_batches WHERE kind=? ORDER BY rowid DESC LIMIT 1',(kind,))
    return row['id'] if row else None


def status(batch_id):
    zero={'batch_id':batch_id,'id':batch_id,'total':0,'pending':0,'active':0,'done':0,'succeeded':0,'failed':0,'errors':[]}
    if not batch_id:
        return zero
    batch=db.query_one('SELECT * FROM task_batches WHERE id=?',(batch_id,))
    if not batch:
        return zero
    counts={r['state']:r['n'] for r in db.query('SELECT state,COUNT(*) AS n FROM task_batch_items WHERE batch_id=? GROUP BY state',(batch_id,))}
    errors=[dict(row) for row in db.query("SELECT t.paper_id,t.error,p.title FROM task_batch_items t LEFT JOIN papers p ON p.id=t.paper_id WHERE batch_id=? AND state='failed' ORDER BY t.rowid LIMIT 100",(batch_id,))]
    pending=counts.get('queued',0)
    active=counts.get('running',0)
    total=sum(counts.values())
    return {**zero,'kind':batch['kind'],'created_at':batch['created_at'],'total':total,'pending':pending,'active':active,
            'done':total-pending-active,'succeeded':counts.get('succeeded',0),'failed':counts.get('failed',0),
            'skipped':counts.get('skipped',0),'errors':errors,'state':'running' if pending+active else 'completed'}


def recent(batch_ids=()):
    # Include older work on first load, and its final result on subsequent polls.
    # A tracked batch may finish after it has fallen outside the recent window.
    ids=list(dict.fromkeys(batch_ids))
    tracked=(' OR id IN ('+','.join('?' for _ in ids)+')') if ids else ''
    rows=db.query('''SELECT id FROM task_batches WHERE
        id IN (SELECT id FROM task_batches ORDER BY rowid DESC LIMIT 30)
        OR id IN (SELECT batch_id FROM task_batch_items WHERE state IN ('queued','running'))'''
        +tracked+' ORDER BY rowid DESC',tuple(ids))
    return [status(row['id']) for row in rows]


def interrupt_unfinished(kind):
    db.execute("UPDATE task_batch_items SET state='interrupted' WHERE state IN ('queued','running') AND batch_id IN (SELECT id FROM task_batches WHERE kind=?)",(kind,))
