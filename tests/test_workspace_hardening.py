"""Workspace contracts: pagination, states, recovery, batches; isolated local data."""
from __future__ import annotations

import io
import json
import zipfile
import tempfile
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from paperwhale_server import config,db
from paperwhale_server.main import app
from paperwhale_server.services import conversations,pdf_fetch


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='pw-workspace-')
        root=Path(self.temp.name)
        self.paths=patch.multiple(config,DATA_DIR=root,DEFAULT_PDF_DIR=root/'pdfs',TMP_DIR=root/'tmp',
            DB_PATH=root/'test.db',SETTINGS_PATH=root/'settings.json',_cache={**config.DEFAULT_SETTINGS,'scholar_auto_sync':False,'pdf_browser_session_enabled':False})
        self.paths.start()
        db.init_db()
        self.client=TestClient(app)
        self.add('a','Zebra','2020')
        self.add('b','Alpha','2025')

    def add(self,pid,title,year='2024',path=''):
        db.execute('INSERT INTO papers(id,title,year,file_path,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?)',
                   (pid,title,year,path,'indexed',db.now(),db.now()))

    def tearDown(self):
        self.client.close()
        self.paths.stop()
        self.temp.cleanup()

    def test_full_library_sorting_and_pagination(self):
        for n in range(205): self.add(f'p{n}',f'Paper {n:03}')
        res=self.client.get('/api/papers',params={'sort':'title','limit':200}).json()
        self.assertEqual(res['items'][0]['title'],'Alpha')
        self.assertEqual(res['total'],207)
        self.assertEqual(res['limit'],200)
        second=self.client.get('/api/papers',params={'sort':'title','limit':200,'offset':200}).json()
        self.assertEqual(len(second['items']),7)
        self.assertEqual(second['items'][-1]['title'],'Zebra')

    def test_missing_file_state_and_filter_agree(self):
        db.update_paper('a',{'file_path':str(config.DATA_DIR/'lost.pdf')})
        a=self.client.get('/api/papers/a').json()
        self.assertFalse(a['has_pdf'])
        self.assertEqual(a['pdf_state'],'missing')
        missing=self.client.get('/api/papers?missing_pdf=1').json()
        self.assertEqual(missing['total'],2)
        self.assertEqual(self.client.get('/api/stats').json()['missing_pdf'],2)

    def test_clear_can_be_undone_with_materials(self):
        conversations.turn('paper:a','QUESTION',lambda h,m:{'answer':'ANSWER'})
        conversations.set_materials('paper:a',[{'name':'a.txt','text':'material'}])
        clear=self.client.delete('/api/ai/conversations/paper:a')
        self.assertTrue(clear.json()['undo_available'])
        restored=self.client.post('/api/ai/conversations/paper:a/undo')
        self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(restored.json()['messages'][0]['content'],'QUESTION')
        self.assertEqual(restored.json()['attachments'][0]['text'],'material')

    def test_undo_cannot_overwrite_a_new_conversation(self):
        conversations.turn('library','old',lambda h,m:{'answer':'old answer'})
        conversations.clear('library')
        conversations.turn('library','new',lambda h,m:{'answer':'new answer'})
        res=self.client.post('/api/ai/conversations/library/undo')
        self.assertEqual(res.status_code,409)
        self.assertEqual(conversations.snapshot('library')['messages'][0]['content'],'new')

    def test_paper_delete_moves_to_trash_and_restores_children(self):
        pdf=config.DATA_DIR/'original.pdf'
        pdf.write_bytes(b'original bytes')
        db.update_paper('a',{'file_path':str(pdf)})
        db.execute('INSERT INTO chunks(paper_id,page,text) VALUES(?,?,?)',('a',1,'ORIGINAL_TEXT'))
        conversations.turn('paper:a','saved question',lambda h,m:{'answer':'saved answer'})
        res=self.client.delete('/api/papers/a')
        self.assertEqual(res.status_code,200,res.text)
        self.assertEqual(self.client.get('/api/papers/a').status_code,404)
        items=self.client.get('/api/maintenance/trash').json()['items']
        item=next(item for item in items if item['kind']=='paper')
        restored=self.client.post('/api/maintenance/trash/'+item['id']+'/restore')
        self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(self.client.get('/api/papers/a').status_code,200)
        self.assertEqual(db.query_one("SELECT text FROM chunks WHERE paper_id='a'")['text'],'ORIGINAL_TEXT')
        self.assertTrue(pdf.is_file())
        self.assertEqual(len(conversations.snapshot('paper:a')['messages']),2)

    def test_pdf_current_batch_does_not_include_history(self):
        manager=pdf_fetch.PdfFetchManager()
        with patch.object(manager,'_ensure_workers'):
            manager.start(['a'])
            first=manager.status()['batch_id']
            db.execute("UPDATE pdf_fetch_jobs SET state='succeeded' WHERE paper_id='a'")
            from paperwhale_server.services import batches
            batches.finish(first,'a','succeeded')
            manager.start(['b'])
        state=manager.status()
        self.assertNotEqual(state['batch_id'],first)
        self.assertEqual(state['total'],1)
        self.assertEqual(state['done'],0)
        self.assertEqual(state['succeeded'],0)

    def test_metadata_backup_excludes_config_keys_and_restores(self):
        config.update_settings({'api_key':'SECRET_LLM','openalex_api_key':'SECRET_OA','profile':'SAVED_PROFILE'})
        res=self.client.post('/api/maintenance/backup')
        self.assertEqual(res.status_code,200,res.text)
        data=self.client.get(res.json()['download_url']).content
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            saved=archive.read('settings.json')
            self.assertNotIn(b'SECRET_LLM',saved)
            self.assertNotIn(b'SECRET_OA',saved)
            self.assertNotIn('api_key',json.loads(saved))
        db.update_paper('a',{'title':'changed'})
        restored=self.client.post('/api/maintenance/restore',files={'file':('backup.zip',data,'application/zip')})
        self.assertEqual(restored.status_code,200,restored.text)
        self.assertEqual(db.get_paper('a')['title'],'Zebra')
        self.assertEqual(config.get_settings()['api_key'],'SECRET_LLM')

    def test_connection_status_is_based_on_actual_test_and_invalidates_changes(self):
        from paperwhale_server import llm
        self.assertEqual(self.client.get('/api/settings/connection-status').json()['state'],'untested')
        with patch.object(llm,'chat',return_value='OK'):
            res=self.client.post('/api/settings/test-connection')
        self.assertEqual(res.json()['state'],'success')
        config.update_settings({'model':'other-model'})
        self.assertEqual(self.client.get('/api/settings/connection-status').json()['state'],'untested')
        with patch.object(llm,'chat',side_effect=llm.LLMError('network failure')):
            failed=self.client.post('/api/settings/test-connection').json()
        self.assertEqual(failed['state'],'failed')

    def test_invalid_backup_does_not_replace_existing_data(self):
        result=self.client.post('/api/maintenance/restore',files={'file':('invalid.zip',b'invalid','application/zip')})
        self.assertEqual(result.status_code,400)
        self.assertEqual(db.get_paper('a')['title'],'Zebra')

    def test_structurally_invalid_database_never_replaces_current_data(self):
        path=config.DATA_DIR/'malformed.db'
        with closing(sqlite3.connect(path)) as conn,conn:
            for table in ('papers','chunks','annotations','ai_conversations','ai_messages','recovery_items'):
                conn.execute(f'CREATE TABLE {table}(wrong TEXT)')
            for table in ('pdf_fetch_jobs','task_batch_items'):
                conn.execute(f'CREATE TABLE {table}(state TEXT)')
        package=io.BytesIO()
        with zipfile.ZipFile(package,'w') as output:
            output.write(path,'database.sqlite3')
            output.writestr('settings.json','{}')
            output.writestr('manifest.json','{"format":"paperwhale-metadata-v1"}')
        result=self.client.post('/api/maintenance/restore',files={'file':('bad.zip',package.getvalue(),'application/zip')})
        self.assertEqual(result.status_code,400)
        self.assertEqual(db.get_paper('a')['title'],'Zebra')

    def test_restoring_selected_old_clear_does_not_restore_a_different_clear(self):
        conversations.turn('library','first',lambda h,m:{'answer':'first answer'})
        conversations.clear('library')
        first=db.query_one("SELECT id FROM recovery_items WHERE kind='conversation'")['id']
        conversations.turn('library','second',lambda h,m:{'answer':'second answer'})
        conversations.clear('library')
        result=self.client.post('/api/maintenance/trash/'+first+'/restore')
        self.assertEqual(result.status_code,409)
        self.assertEqual(conversations.snapshot('library')['messages'],[])

    def test_stale_stop_cannot_invalidate_a_newer_turn(self):
        conversations.turn('library','first',lambda h,m:{'answer':'first answer'})
        old=conversations.snapshot('library')['revision']
        conversations.turn('library','second',lambda h,m:{'answer':'second answer'})
        current=conversations.snapshot('library')
        result=self.client.post('/api/ai/conversations/library/cancel',json={'revision':old})
        self.assertEqual(result.status_code,409)
        self.assertEqual(conversations.snapshot('library')['revision'],current['revision'])

    def test_note_appends_are_atomic(self):
        db.update_paper('a',{'notes':'ORIGINAL'})
        def append(n):
            result=self.client.post('/api/papers/a/notes/append',json={'text':f'NOTE-{n}'})
            self.assertEqual(result.status_code,200,result.text)
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(append,range(8)))
        notes=db.get_paper('a')['notes']
        self.assertTrue(notes.startswith('ORIGINAL'))
        for n in range(8): self.assertEqual(notes.count(f'NOTE-{n}'),1)

    def test_explicit_deleted_analysis_targets_never_enqueue_entire_library(self):
        from paperwhale_server import llm
        from paperwhale_server.services.analysis_queue import queue_manager,AnalysisQueue
        with patch.object(llm,'is_configured',return_value=True),patch.object(queue_manager,'start') as start:
            result=self.client.post('/api/ai/analyze-batch',json={'paper_ids':['deleted-id'],'only_new':False})
        self.assertEqual(result.status_code,404)
        start.assert_not_called()
        self.assertEqual(AnalysisQueue().start([],only_new=False),0)

    def test_stopped_but_inflight_generation_blocks_backup_restore(self):
        from paperwhale_server.services import recovery
        def generate(history,materials):
            backup=recovery.create_backup()
            data=(config.DATA_DIR/'backups'/backup['name']).read_bytes()
            conversations.cancel('library')
            with self.assertRaises(conversations.ConversationError): recovery.restore_backup(data)
            return {'answer':'late answer'}
        with self.assertRaises(conversations.ConversationError): conversations.turn('library','stop me',generate)
        self.assertEqual(conversations.snapshot('library')['messages'],[])

    def test_stop_before_generation_arrives_invalidates_that_request(self):
        original=conversations.snapshot('library')
        result=self.client.post('/api/ai/conversations/library/cancel',json={'revision':original['revision']+1})
        self.assertEqual(result.status_code,200)
        generate=unittest.mock.Mock(return_value={'answer':'late answer'})
        with self.assertRaises(conversations.ConversationError):
            conversations.turn('library','late question',generate,revision=original['revision'])
        generate.assert_not_called()

    def test_restore_transaction_rolls_back_if_import_fails_after_deleting_rows(self):
        from paperwhale_server.services import recovery
        saved=recovery.create_backup()
        data=(config.DATA_DIR/'backups'/saved['name']).read_bytes()
        db.update_paper('a',{'title':'CURRENT'})
        copy_rows=recovery._copy_rows
        calls=0
        def fail_second(source,target):
            nonlocal calls
            calls+=1
            if calls==2: raise sqlite3.DatabaseError('simulated write failure')
            return copy_rows(source,target)
        with patch.object(recovery,'_copy_rows',side_effect=fail_second):
            with self.assertRaises(conversations.ConversationError): recovery.restore_backup(data)
        self.assertEqual(db.get_paper('a')['title'],'CURRENT')

    def test_deleted_paper_during_pdf_download_finishes_its_batch(self):
        manager=pdf_fetch.PdfFetchManager()
        db.update_paper('a',{'arxiv_id':'1234.56789'})
        def downloaded(*args,**kwargs):
            path=config.DATA_DIR/'download.pdf'
            path.write_bytes(b'pdf')
            self.client.delete('/api/papers/a')
            return path
        with patch.object(pdf_fetch,'_try_candidates',side_effect=downloaded):
            result=manager.fetch_one('a')
        self.assertEqual(result['code'],'cancelled')
        self.assertEqual(manager.status()['active'],0)
        self.assertEqual(manager.status()['done'],1)


if __name__=='__main__': unittest.main()
