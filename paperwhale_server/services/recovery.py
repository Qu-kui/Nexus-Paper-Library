"""Local recoverable deletions and credential-free metadata backups."""
from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from pathlib import Path

from .. import config,db
from .conversations import ConversationError
from .activity import exclusive_restore


def archive(conn,kind,label,payload):
    item_id=db.new_id()
    conn.execute('INSERT INTO recovery_items(id,kind,label,payload,created_at) VALUES(?,?,?,?,?)',
                 (item_id,kind,label,json.dumps(payload,ensure_ascii=False),db.now()))
    return item_id


def list_items():
    return [dict(row) for row in db.query("SELECT id,kind,label,created_at FROM recovery_items WHERE restored_at='' ORDER BY created_at DESC,rowid DESC LIMIT 300")]


def archive_paper(paper_id):
    with closing(db.connect()) as conn,conn:
        row=conn.execute('SELECT * FROM papers WHERE id=?',(paper_id,)).fetchone()
        if not row:
            raise ConversationError('论文不存在',404)
        payload={'paper':dict(row),'children':{},'conversations':[]}
        for table in ('chunks','annotations','chat_messages','pdf_fetch_jobs'):
            payload['children'][table]=[dict(child) for child in conn.execute(f'SELECT * FROM {table} WHERE paper_id=?',(paper_id,))]
        for conversation in conn.execute('SELECT * FROM ai_conversations WHERE scope=?',('paper:'+paper_id,)):
            item=dict(conversation)
            item['messages']=[dict(msg) for msg in conn.execute('SELECT * FROM ai_messages WHERE conversation_id=? ORDER BY id',(item['id'],))]
            payload['conversations'].append(item)
        item_id=archive(conn,'paper',row['title'] or '未命名论文',payload)
        for table in ('chunks','annotations','chat_messages'):
            conn.execute(f'DELETE FROM {table} WHERE paper_id=?',(paper_id,))
        conn.execute('DELETE FROM ai_conversations WHERE scope=?',('paper:'+paper_id,))
        conn.execute('DELETE FROM papers WHERE id=?',(paper_id,))
    # The original PDF stays on disk until explicitly managed by the user.
    return {'ok':True,'trash_id':item_id,'recoverable':True}


def _insert(conn,table,row,drop_id=False):
    row={k:v for k,v in row.items() if not(drop_id and k=='id')}
    allowed={item[1] for item in conn.execute(f'PRAGMA table_info("{table}")')}
    if not row or not set(row).issubset(allowed):
        raise ConversationError('恢复记录包含不兼容的字段，未恢复',400)
    columns=','.join('"'+key+'"' for key in row)
    conn.execute(f'INSERT INTO {table}({columns}) VALUES({",".join("?" for _ in row)})',tuple(row.values()))


def restore_discovery_topic(item_id):
    from . import discovery_store
    with discovery_store._actions,closing(db.connect()) as conn,conn:
        conn.execute('BEGIN IMMEDIATE')
        item=conn.execute("SELECT * FROM recovery_items WHERE id=? AND kind='discovery_topic' AND restored_at=''",(item_id,)).fetchone()
        if not item:raise ConversationError('回收记录不存在',404)
        payload=json.loads(item['payload']);session=payload['session'];scope='discovery:'+session['id']
        for table,rows in (('discovery_sessions',[session]),('discovery_runs',payload['runs']),('discovery_candidates',payload['candidates'])):
            if any(conn.execute(f'SELECT id FROM {table} WHERE id=?',(row['id'],)).fetchone() for row in rows):
                raise ConversationError('找论文话题或候选记录已经存在，未覆盖现有数据')
        if conn.execute('SELECT id FROM ai_conversations WHERE scope=?',(scope,)).fetchone():
            raise ConversationError('这个话题的对话已经存在，未覆盖现有数据')
        _insert(conn,'discovery_sessions',session)
        for row in payload['runs']:_insert(conn,'discovery_runs',row)
        for row in payload['candidates']:_insert(conn,'discovery_candidates',row)
        conversation=payload.get('conversation')
        if conversation:
            conversation={**conversation,'id':db.new_id(),'revision':conversation['revision']+1}
            _insert(conn,'ai_conversations',conversation)
            for row in payload['messages']:
                _insert(conn,'ai_messages',{**row,'conversation_id':conversation['id']},drop_id=True)
        conn.execute('UPDATE recovery_items SET restored_at=? WHERE id=?',(db.now(),item_id))
        return {'ok':True,'session':session,'kind':'discovery_topic'}


def restore_paper(item_id):
    with closing(db.connect()) as conn,conn:
        item=conn.execute("SELECT * FROM recovery_items WHERE id=? AND restored_at=''",(item_id,)).fetchone()
        if not item:
            raise ConversationError('回收记录不存在',404)
        if item['kind']=='discovery_topic':
            conn.rollback()
            return restore_discovery_topic(item_id)
        if item['kind']=='conversation':
            from . import conversations

            payload=json.loads(item['payload'])
            # Release this transaction before the version-checked conversation restore.
            conn.rollback()
            return {'ok':True,'conversation':conversations.undo(payload['scope'],item_id)}
        if item['kind']!='paper':
            raise ConversationError('不支持的恢复记录',400)
        payload=json.loads(item['payload'])
        paper=payload['paper']
        if conn.execute('SELECT id FROM papers WHERE id=?',(paper['id'],)).fetchone():
            raise ConversationError('同 ID 的论文已经存在，未覆盖现有数据')
        _insert(conn,'papers',paper)
        for table in ('chunks','annotations','chat_messages'):
            for row in payload['children'].get(table,[]):
                _insert(conn,table,row,drop_id=table in {'chunks','chat_messages'})
        for conversation in payload['conversations']:
            messages=conversation.pop('messages',[])
            _insert(conn,'ai_conversations',conversation)
            for message in messages:
                _insert(conn,'ai_messages',message,drop_id=True)
        # Recovered jobs are historical; a restore must not silently start downloads.
        for job in payload['children'].get('pdf_fetch_jobs',[]):
            if job['state'] in {'queued','running'}:
                job['state']='cancelled'
            _insert(conn,'pdf_fetch_jobs',job)
        conn.execute('UPDATE recovery_items SET restored_at=? WHERE id=?',(db.now(),item_id))
    return {'ok':True,'paper':db.get_paper(paper['id'])}


def create_backup():
    destination=config.DATA_DIR/'backups'
    destination.mkdir(parents=True,exist_ok=True)
    name='paperwhale-metadata-'+db.new_id()+'.zip'
    settings=config.get_settings()
    safe={key:settings.get(key) for key in ('profile','profile_name','category_guide','language','summary_language','pdf_dir')}
    with tempfile.TemporaryDirectory(prefix='pw-backup-') as folder:
        snapshot=Path(folder)/'database.sqlite3'
        with closing(db.connect()) as source,closing(sqlite3.connect(snapshot)) as target:
            source.backup(target)
            target.execute("DELETE FROM settings WHERE key NOT IN ('profile','profile_name','category_guide','language','summary_language','pdf_dir','employment_preferences')")
            target.commit()
        with zipfile.ZipFile(destination/name,'w',compression=zipfile.ZIP_DEFLATED) as archive_file:
            archive_file.write(snapshot,'database.sqlite3')
            archive_file.writestr('settings.json',json.dumps(safe,ensure_ascii=False))
            archive_file.writestr('manifest.json',json.dumps({'format':'paperwhale-metadata-v1','created_at':db.now(),'includes_pdfs':False,'includes_config_credentials':False}))
    return {'name':name,'download_url':'/api/maintenance/backups/'+name,'includes_pdfs':False}


_DATA_TABLES = ('papers','chunks','annotations','settings','scholar_items','chat_messages',
                'ai_conversations','ai_messages','pdf_fetch_jobs','task_batches','task_batch_items','recovery_items',
                'discovery_sessions','discovery_runs','discovery_candidates','employment_items','employment_checks')


def _copy_rows(source, target):
    """Import only data into our own schema; never execute uploaded triggers or views."""
    added={'discovery_sessions','discovery_runs','discovery_candidates'}
    present={row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if present&added and not added.issubset(present):
        raise ValueError('incomplete discovery schema')
    for table in _DATA_TABLES:
        if table in {'employment_items','employment_checks'} and table not in present:continue
        if table in added and not present&added:continue
        definition=source.execute('SELECT type,sql FROM sqlite_master WHERE name=?',(table,)).fetchone()
        if not definition or definition[0]!='table' or 'VIRTUAL TABLE' in definition[1].upper():
            raise ValueError('incompatible table')
        columns=[row[1] for row in target.execute(f'PRAGMA table_info("{table}")')]
        incoming=[row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
        optional = {'papers': {'imported_notes'}, 'discovery_runs': {'tracking_json'}}.get(table, set())
        if set(incoming)-set(columns) or (set(columns)-set(incoming))-optional:
            raise ValueError('incompatible columns')
        columns=[column for column in columns if column in incoming]
        names=','.join('"'+column+'"' for column in columns)
        target.executemany(f'INSERT INTO "{table}"({names}) VALUES({",".join("?" for _ in columns)})',
                           source.execute(f'SELECT {names} FROM "{table}"'))


@exclusive_restore
def restore_backup(data: bytes):
    if len(data)>100*1024*1024:
        raise ConversationError('备份超过 100MB',400)
    from . import ai_progress,conversations
    from .analysis_queue import queue_manager
    from .category_assistant import queue_manager as category_queue

    if db.query_one("SELECT paper_id FROM pdf_fetch_jobs WHERE state IN ('queued','running') LIMIT 1") or queue_manager.has_inflight() or category_queue.has_inflight() or ai_progress.has_active() or conversations.has_inflight():
        raise ConversationError('请先等待当前任务结束，再恢复备份')
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            infos={item.filename:item for item in package.infolist()}
            if set(infos)!={'database.sqlite3','settings.json','manifest.json'} or sum(item.file_size for item in infos.values())>150*1024*1024:
                raise ValueError('unsupported archive')
            manifest=json.loads(package.read('manifest.json'))
            if not isinstance(manifest,dict) or manifest.get('format')!='paperwhale-metadata-v1':
                raise ValueError('unsupported format')
            settings=json.loads(package.read('settings.json'))
            if not isinstance(settings,dict) or any(not isinstance(value,(str,type(None))) for value in settings.values()):
                raise ValueError('invalid settings')
            database=package.read('database.sqlite3')
        with tempfile.TemporaryDirectory(prefix='pw-restore-') as folder:
            path=Path(folder)/'validated.db'
            path.write_bytes(database)
            with closing(sqlite3.connect(path)) as source,closing(sqlite3.connect(Path(folder)/'clean.db')) as clean:
                source.execute('PRAGMA trusted_schema=OFF')
                if source.execute('PRAGMA integrity_check').fetchone()[0]!='ok':
                    raise ValueError('corrupt database')
                clean.execute('PRAGMA foreign_keys=ON')
                clean.executescript(db.SCHEMA)
                db._migrate(clean)
                _copy_rows(source,clean)
                if clean.execute('PRAGMA foreign_key_check').fetchone():
                    raise ValueError('incompatible references')
                clean.execute("DELETE FROM settings WHERE key NOT IN ('profile','profile_name','category_guide','language','summary_language','pdf_dir','employment_preferences')")
                clean.execute("UPDATE pdf_fetch_jobs SET state='interrupted' WHERE state IN ('queued','running')")
                clean.execute("UPDATE task_batch_items SET state='interrupted' WHERE state IN ('queued','running')")
                clean.commit()
                # Preserve a recoverable pre-restore snapshot before replacing metadata.
                previous=create_backup()
                allowed={'profile','profile_name','category_guide','language','summary_language','pdf_dir'}
                old_settings={key:config.get_settings().get(key) for key in allowed}
                with closing(db.connect()) as target,target:
                    target.execute('BEGIN IMMEDIATE')
                    # A single transaction keeps the original database intact on any import failure.
                    for table in reversed(_DATA_TABLES):
                        target.execute(f'DELETE FROM "{table}"')
                    _copy_rows(clean,target)
                    try:
                        config.update_settings({key:value for key,value in settings.items() if key in allowed})
                        target.commit()
                    except Exception:
                        target.rollback()
                        config.update_settings(old_settings)
                        raise
        return {'ok':True,'previous_backup':previous['name'],'reload_required':True}
    except (ValueError,KeyError,TypeError,zipfile.BadZipFile,sqlite3.DatabaseError) as exc:
        raise ConversationError('备份格式无效或数据库校验失败，未恢复该文件',400) from exc
