"""User-managed AI candidates, isolated topics and no automatic import."""
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from paperwhale_server import config,db
from paperwhale_server.main import app
from paperwhale_server.services import conversations


class DiscoveryAPITests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(prefix='pw-discovery-api-');root=Path(self.tmp.name)
        self.paths=patch.multiple(config,DATA_DIR=root,DEFAULT_PDF_DIR=root/'pdfs',TMP_DIR=root/'tmp',DB_PATH=root/'test.db',SETTINGS_PATH=root/'settings.json',_cache={**config.DEFAULT_SETTINGS,'api_key':'fake-test-key','scholar_auto_sync':False})
        self.paths.start();db.init_db();self.client=TestClient(app)
    def tearDown(self):self.client.close();self.paths.stop();self.tmp.cleanup()
    def session(self):
        result=self.client.post('/api/discover/sessions',json={});self.assertEqual(result.status_code,200,result.text);return result.json()['id']
    def result(self):return {'answer':'阶段一 [1]，阶段二 [2]','intent':'timeline','topic':'单像素成像','queries':['single pixel imaging'],'date_from':'','date_to':'','warnings':[], 'items':[
        {'title':'Real source A','authors':'A; B','year':'2024','doi':'10.1234/real-a','url':'https://doi.org/10.1234/real-a','citation_index':1,'reason':'基础方法','stage':'基础','sources':[{'name':'Crossref','url':'https://doi.org/10.1234/real-a'}]},
        {'title':'Real source B','year':'2026','arxiv_id':'2601.01234','url':'https://arxiv.org/abs/2601.01234','citation_index':2,'stage':'近期','reason':'新进展','sources':[{'name':'arXiv','url':'https://arxiv.org/abs/2601.01234'}]}]}
    def search(self,sid,result=None):
        from paperwhale_server.services import research_discovery
        with patch.object(research_discovery,'research',return_value=result or self.result()):
            response=self.client.post('/api/discover/ai',json={'session_id':sid,'question':'整理单像素成像发展脉络'})
        self.assertEqual(response.status_code,200,response.text);return response.json()
    def test_search_persists_candidates_and_topic_history_without_importing(self):
        sid=self.session();result=self.search(sid)
        self.assertEqual(db.query_one('SELECT COUNT(*) AS n FROM papers')['n'],0)
        self.assertEqual(len(result['items']),2)
        loaded=self.client.get('/api/discover/sessions/'+sid).json()
        self.assertEqual(len(loaded['conversation']['messages']),2)
        self.assertEqual(len(loaded['runs']),1)
        self.assertEqual([item['citation_index'] for item in loaded['items']],[1,2])
        other=self.session();otherdata=self.client.get('/api/discover/sessions/'+other).json()
        self.assertEqual(otherdata['items'],[]);self.assertEqual(otherdata['conversation']['messages'],[])
        self.assertEqual(self.client.get('/api/scholar/items').json()['items'],[])
    def test_ignored_choice_survives_new_run_and_can_be_restored(self):
        sid=self.session();item=self.search(sid)['items'][0]
        ignored=self.client.post('/api/discover/candidates/'+item['id']+'/state',json={'status':'ignored'})
        self.assertEqual(ignored.status_code,200,ignored.text)
        result=self.search(sid)
        self.assertEqual(len(result['items']),2)
        same=next(row for row in result['items'] if row['id']==item['id']);self.assertEqual(same['status'],'ignored')
        self.assertEqual(self.client.post('/api/discover/candidates/'+item['id']+'/state',json={'status':'pending'}).json()['item']['status'],'pending')
    def test_adding_requires_user_action_is_idempotent_and_queues_pdf_once(self):
        from paperwhale_server.services.pdf_fetch import manager
        sid=self.session();item=self.search(sid)['items'][0]
        with patch.object(manager,'start',return_value=1) as queued:
            first=self.client.post('/api/discover/candidates/'+item['id']+'/add');second=self.client.post('/api/discover/candidates/'+item['id']+'/add')
        self.assertEqual(first.status_code,200,first.text);self.assertEqual(second.status_code,200,second.text)
        self.assertEqual(first.json()['paper']['id'],second.json()['paper']['id']);self.assertEqual(db.query_one('SELECT COUNT(*) AS n FROM papers')['n'],1)
        self.assertEqual(queued.call_count,1)
        self.assertTrue(second.json()['item']['in_library'])
    def test_late_stopped_turn_never_stores_a_run_or_candidates(self):
        from paperwhale_server.services import research_discovery
        sid=self.session()
        def stopped(*args, **kwargs):conversations.cancel('discovery:'+sid);return self.result()
        with patch.object(research_discovery,'research',side_effect=stopped):
            result=self.client.post('/api/discover/ai',json={'session_id':sid,'question':'最新进展'})
        self.assertEqual(result.status_code,409,result.text)
        loaded=self.client.get('/api/discover/sessions/'+sid).json();self.assertEqual(loaded['items'],[]);self.assertEqual(loaded['runs'],[]);self.assertEqual(loaded['conversation']['messages'],[])
    def test_existing_library_record_is_identified_without_import_or_pdf_work(self):
        db.execute("INSERT INTO papers(id,title,doi) VALUES('existing','Real source A','10.1234/real-a')")
        sid=self.session();items=self.search(sid)['items'];self.assertTrue(items[0]['in_library']);self.assertEqual(items[0]['paper_id'],'existing')

    def test_same_title_conflicting_doi_is_a_distinct_work_on_import(self):
        from paperwhale_server.services.pdf_fetch import manager
        db.execute("INSERT INTO papers(id,title,doi) VALUES('existing','Editorial','10.1234/old')")
        sid=self.session();research=self.result();research['items']=[{**research['items'][0],'title':'Editorial','doi':'10.1234/new','url':'https://doi.org/10.1234/new'}]
        item=self.search(sid,research)['items'][0]
        self.assertFalse(item['in_library'])
        with patch.object(manager,'start',return_value=1) as queued:
            response=self.client.post('/api/discover/candidates/'+item['id']+'/add')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['paper']['doi'],'10.1234/new')
        self.assertEqual(response.json()['item']['status'],'added')
        self.assertEqual(db.query_one('SELECT COUNT(*) AS n FROM papers')['n'],2)
        self.assertNotEqual(queued.call_args.args[0],['existing'])

    def test_same_title_conflicting_arxiv_is_not_marked_as_already_in_library(self):
        db.execute("INSERT INTO papers(id,title,arxiv_id) VALUES('old','Research introduction','2501.01234')")
        sid=self.session();research=self.result();research['items']=[{**research['items'][1],'title':'Research introduction','arxiv_id':'2601.01234'}]
        item=self.search(sid,research)['items'][0]
        self.assertFalse(item['in_library']);self.assertEqual(item['status'],'pending')

    def test_partial_discovery_schema_backup_is_rejected_without_replacing_data(self):
        from paperwhale_server.services import recovery
        import sqlite3
        from contextlib import closing
        sid=self.session();backup=recovery.create_backup();data=(config.DATA_DIR/'backups'/backup['name']).read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            path=config.DATA_DIR/'partial.db';path.write_bytes(archive.read('database.sqlite3'))
            with closing(sqlite3.connect(path)) as conn,conn:conn.execute('DROP TABLE discovery_candidates')
            package=io.BytesIO()
            with zipfile.ZipFile(package,'w') as target:
                for name in archive.namelist():target.writestr(name,path.read_bytes() if name=='database.sqlite3' else archive.read(name))
        db.execute("UPDATE discovery_sessions SET title='CURRENT' WHERE id=?",(sid,))
        with self.assertRaises(conversations.ConversationError):recovery.restore_backup(package.getvalue())
        self.assertEqual(self.client.get('/api/discover/sessions/'+sid).json()['session']['title'],'CURRENT')
    def test_backup_restores_discovery_and_accepts_older_backup_without_new_tables(self):
        from paperwhale_server.services import recovery
        sid=self.session();self.search(sid)
        backup=recovery.create_backup();path=config.DATA_DIR/'backups'/backup['name'];data=path.read_bytes()
        db.execute('DELETE FROM discovery_sessions')
        self.assertTrue(recovery.restore_backup(data)['ok']);self.assertEqual(len(self.client.get('/api/discover/sessions/'+sid).json()['items']),2)
        # An older v1 backup had none of these tables; its original metadata must remain restorable.
        import sqlite3
        from contextlib import closing
        with zipfile.ZipFile(io.BytesIO(data)) as package:
            database=config.DATA_DIR/'old.db';database.write_bytes(package.read('database.sqlite3'))
            with closing(sqlite3.connect(database)) as conn,conn:
                for table in ('discovery_candidates','discovery_runs','discovery_sessions'):conn.execute('DROP TABLE '+table)
                conn.execute("DELETE FROM ai_messages WHERE conversation_id IN (SELECT id FROM ai_conversations WHERE scope LIKE 'discovery:%')")
                conn.execute("DELETE FROM ai_conversations WHERE scope LIKE 'discovery:%'")
            old=io.BytesIO()
            with zipfile.ZipFile(old,'w') as out:
                out.writestr('database.sqlite3',database.read_bytes());out.writestr('settings.json',package.read('settings.json'));out.writestr('manifest.json',package.read('manifest.json'))
        self.assertTrue(recovery.restore_backup(old.getvalue())['ok']);self.assertEqual(self.client.get('/api/discover/sessions').json()['items'],[])
    def test_stale_revision_cannot_trigger_another_network_search(self):
        from paperwhale_server.services import research_discovery
        sid=self.session();before=conversations.snapshot('discovery:'+sid);self.search(sid)
        with patch.object(research_discovery,'research') as research:
            result=self.client.post('/api/discover/ai',json={'session_id':sid,'question':'继续','conversation_id':before['id'],'revision':before['revision']})
        self.assertEqual(result.status_code,409);research.assert_not_called()

    def test_structured_llm_progress_keeps_actual_reasoning_and_hides_json(self):
        import httpx
        from paperwhale_server import llm
        events=[]
        stream='data: '+json.dumps({'choices':[{'delta':{'reasoning_content':'正在规划检索词','content':'{"queries":["optics"]}'}}]})+'\n\ndata: [DONE]\n\n'
        real_client=httpx.Client
        with patch.object(llm,'is_mock',return_value=False),patch.object(llm,'_resolve_config',return_value={'base_url':'https://mock/v1','api_key':'test','model':'m','temperature':.2}),patch.object(llm.httpx,'Client',side_effect=lambda **kw:real_client(transport=httpx.MockTransport(lambda request:httpx.Response(200,text=stream,headers={'content-type':'text/event-stream'})),**kw)):
            result=llm.chat([{'role':'user','content':'find optics'}],json_mode=True,with_meta=True,progress_channel='structured',on_delta=lambda **event:events.append(event))
        self.assertEqual(result['content'],'{"queries":["optics"]}')
        self.assertEqual(events,[{'reasoning_delta':'正在规划检索词'}])

    def test_topic_delete_restores_history_candidates_and_keeps_imported_papers(self):
        from paperwhale_server.services.pdf_fetch import manager
        sid=self.session();data=self.search(sid);old=data['conversation']
        with patch.object(manager,'start',return_value=1):
            added=self.client.post('/api/discover/candidates/'+data['items'][0]['id']+'/add').json()
        self.client.post('/api/discover/candidates/'+data['items'][1]['id']+'/state',json={'status':'ignored'})
        deleted=self.client.delete('/api/discover/sessions/'+sid)
        self.assertEqual(deleted.status_code,200,deleted.text)
        self.assertTrue(deleted.json()['recoverable'])
        self.assertEqual(self.client.get('/api/discover/sessions/'+sid).status_code,404)
        self.assertIsNone(db.query_one('SELECT id FROM ai_conversations WHERE scope=?',('discovery:'+sid,)))
        self.assertEqual(db.query('SELECT * FROM discovery_runs WHERE session_id=?',(sid,)),[])
        self.assertEqual(db.query('SELECT * FROM discovery_candidates WHERE session_id=?',(sid,)),[])
        self.assertIsNotNone(db.get_paper(added['paper']['id']))
        trash=self.client.get('/api/maintenance/trash').json()['items']
        self.assertEqual(trash[0]['kind'],'discovery_topic')
        restored=self.client.post('/api/maintenance/trash/'+deleted.json()['trash_id']+'/restore')
        self.assertEqual(restored.status_code,200,restored.text)
        current=self.client.get('/api/discover/sessions/'+sid).json()
        self.assertEqual(current['conversation']['messages'],old['messages'])
        self.assertNotEqual(current['conversation']['id'],old['id'])
        self.assertEqual(len(current['runs']),1)
        self.assertEqual({item['status'] for item in current['items']},{'added','ignored'})

    def test_deleted_then_restored_topic_rejects_late_generation(self):
        from paperwhale_server.services import research_discovery,ai_progress
        sid=self.session();before=self.search(sid)
        def deleted(*args, **kwargs):
            response=self.client.delete('/api/discover/sessions/'+sid)
            self.assertEqual(response.status_code,200,response.text)
            self.assertTrue(ai_progress.cancelled())
            restored=self.client.post('/api/maintenance/trash/'+response.json()['trash_id']+'/restore')
            self.assertEqual(restored.status_code,200,restored.text)
            return self.result()
        with patch.object(research_discovery,'research',side_effect=deleted):
            response=self.client.post('/api/discover/ai',json={'session_id':sid,'question':'late'})
        self.assertEqual(response.status_code,409,response.text)
        current=self.client.get('/api/discover/sessions/'+sid).json()
        self.assertEqual(len(current['runs']),1)
        self.assertEqual(current['conversation']['messages'],before['conversation']['messages'])
        self.search(sid)  # Cancellation from the deleted turn must not poison later turns.

    def test_topic_restore_collision_rolls_back_without_marking_restored(self):
        sid=self.session();self.search(sid)
        deleted=self.client.delete('/api/discover/sessions/'+sid)
        self.assertEqual(deleted.status_code,200,deleted.text)
        db.execute("INSERT INTO discovery_sessions(id,title,created_at,updated_at) VALUES(?,'current','','')",(sid,))
        response=self.client.post('/api/maintenance/trash/'+deleted.json()['trash_id']+'/restore')
        self.assertEqual(response.status_code,409,response.text)
        self.assertEqual(db.query_one('SELECT title FROM discovery_sessions WHERE id=?',(sid,))['title'],'current')
        self.assertEqual(len(self.client.get('/api/maintenance/trash').json()['items']),1)

    def test_candidate_chinese_title_persists_and_old_rows_translate_without_state_change(self):
        from paperwhale_server import llm
        sid=self.session();result=self.result();result['items'][0]['title_zh']='真实来源甲'
        data=self.search(sid,result);self.assertEqual(data['items'][0].get('title_zh'),'真实来源甲')
        cid=data['items'][1]['id'];self.client.post('/api/discover/candidates/'+cid+'/state',json={'status':'ignored'})
        translated=json.dumps({'items':[{'id':cid,'title_zh':'真实来源乙'}]},ensure_ascii=False)
        with patch.object(llm,'chat',return_value={'content':translated}) as chat:
            response=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['translated_count'],1)
        items=response.json()['items'];self.assertEqual(items[1]['title_zh'],'真实来源乙');self.assertEqual(items[1]['status'],'ignored')
        self.assertEqual(chat.call_args.kwargs['progress_channel'],'structured')
        self.assertEqual(db.query_one('SELECT COUNT(*) AS n FROM papers')['n'],0)

    def test_translation_invalid_result_remains_missing_and_retry_is_possible(self):
        from paperwhale_server import llm
        sid=self.session();data=self.search(sid);cid=data['items'][0]['id']
        with patch.object(llm,'chat',return_value={'content':json.dumps({'items':[{'id':cid,'title_zh':'Still English'}]})}):
            response=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['remaining_count'],2);self.assertTrue(response.json()['warnings'])
        self.assertFalse(response.json()['items'][0].get('title_zh'))
        with patch.object(llm,'chat',return_value={'content':json.dumps({'items':[{'id':cid,'title_zh':'有效译名'}]},ensure_ascii=False)}):
            retry=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(retry.status_code,200,retry.text);self.assertEqual(retry.json()['items'][0]['title_zh'],'有效译名')

    def test_translation_stale_source_title_cannot_overwrite_new_candidate_metadata(self):
        from paperwhale_server import llm
        sid=self.session();data=self.search(sid);cid=data['items'][0]['id']
        def changed(*args,**kwargs):
            row=db.query_one('SELECT payload FROM discovery_candidates WHERE id=?',(cid,));payload=json.loads(row['payload']);payload['title']='Changed source title'
            db.execute('UPDATE discovery_candidates SET payload=? WHERE id=?',(json.dumps(payload),cid))
            return {'content':json.dumps({'items':[{'id':cid,'title_zh':'旧题名译文'}]},ensure_ascii=False)}
        with patch.object(llm,'chat',side_effect=changed):response=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['items'][0]['title'],'Changed source title');self.assertFalse(response.json()['items'][0].get('title_zh'))

    def test_translation_deleted_then_restored_topic_does_not_apply_late_output(self):
        from paperwhale_server import llm
        sid=self.session();data=self.search(sid);cid=data['items'][0]['id']
        def deleted(*args,**kwargs):
            response=self.client.delete('/api/discover/sessions/'+sid)
            self.assertEqual(response.status_code,200,response.text)
            self.client.post('/api/maintenance/trash/'+response.json()['trash_id']+'/restore')
            return {'content':json.dumps({'items':[{'id':cid,'title_zh':'迟到译文'}]},ensure_ascii=False)}
        with patch.object(llm,'chat',side_effect=deleted):response=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(response.status_code,409,response.text)
        self.assertFalse(self.client.get('/api/discover/sessions/'+sid).json()['items'][0].get('title_zh'))

    def test_native_chinese_candidate_needs_no_llm_translation(self):
        from paperwhale_server import llm
        sid=self.session();result=self.result();result['items']=result['items'][:1];result['items'][0]['title']='单像素成像研究'
        data=self.search(sid,result);self.assertEqual(data['items'][0].get('title_zh'),'单像素成像研究')
        with patch.object(llm,'chat') as chat:response=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['remaining_count'],0);chat.assert_not_called()

    def test_translation_batches_are_bounded_and_llm_failure_is_retryable_without_secrets(self):
        from paperwhale_server import llm
        from paperwhale_server.services import discovery_translation
        sid=self.session();result=self.result();template=result['items'][0]
        result['items']=[{**template,'title':f'Paper {index}','doi':f'10.1234/paper-{index}','url':f'https://doi.org/10.1234/paper-{index}'} for index in range(23)]
        self.search(sid,result)
        sizes=[]
        def translate(messages,**kwargs):
            rows=json.loads(messages[-1]['content'])['items'];sizes.append(len(rows))
            return {'content':json.dumps({'items':[{'id':row['id'],'title_zh':'论文译名 '+row['title']} for row in rows]},ensure_ascii=False)}
        with patch.object(llm,'chat',side_effect=translate),patch.object(discovery_translation,'MAX_ITEMS',20):
            response=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(response.status_code,200,response.text);self.assertEqual(sizes,[10,10]);self.assertEqual(response.json()['remaining_count'],3)
        with patch.object(llm,'chat',side_effect=llm.LLMError('unavailable fake-test-key')):
            failure=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(failure.status_code,200,failure.text);self.assertEqual(failure.json()['remaining_count'],3)
        self.assertNotIn('fake-test-key',failure.text);self.assertIn('unavailable',failure.text)
        with patch.object(llm,'chat',side_effect=translate):retry=self.client.post('/api/discover/sessions/'+sid+'/translate')
        self.assertEqual(retry.json()['remaining_count'],0)

    def test_delete_serializes_with_import_and_archives_the_committed_candidate_state(self):
        from concurrent.futures import ThreadPoolExecutor
        from paperwhale_server.services import discovery_store,ingest,pdf_fetch,recovery
        sid=self.session();cid=self.search(sid)['items'][0]['id'];entered=threading.Event();release=threading.Event();delete_started=threading.Event()
        original=ingest.create_paper_from_metadata
        def import_wait(*args,**kwargs):
            entered.set();self.assertTrue(release.wait(5));return original(*args,**kwargs)
        def delete():delete_started.set();return discovery_store.delete_session(sid)
        with patch.object(ingest,'create_paper_from_metadata',side_effect=import_wait),patch.object(pdf_fetch.manager,'start',return_value=1),ThreadPoolExecutor(2) as workers:
            added=workers.submit(discovery_store.add_candidate,cid);self.assertTrue(entered.wait(5))
            deleted=workers.submit(delete);self.assertTrue(delete_started.wait(5));self.assertFalse(deleted.done())
            release.set();paper=added.result(timeout=5)['paper'];trash=deleted.result(timeout=5)
        self.assertIsNotNone(db.get_paper(paper['id']))
        payload=json.loads(db.query_one('SELECT payload FROM recovery_items WHERE id=?',(trash['trash_id'],))['payload'])
        candidate=next(row for row in payload['candidates'] if row['id']==cid)
        self.assertEqual(candidate['status'],'added');self.assertEqual(candidate['paper_id'],paper['id'])
        recovery.restore_paper(trash['trash_id'])
        self.assertEqual(discovery_store.get_candidate(cid)['status'],'added')

    def test_translated_title_drops_model_links_identifiers_and_citation_markers(self):
        from paperwhale_server.services.discovery_translation import clean_title_zh,native_title_zh
        self.assertEqual(clean_title_zh('中文题名 [42] https://fake.example/paper DOI:10.1234/fake arxiv:2601.01234'),'中文题名')
        self.assertEqual(native_title_zh('English paper with a Chinese term 中文'),'')
        self.assertEqual(native_title_zh('基于 CNN 的单像素成像方法'),'基于 CNN 的单像素成像方法')
        self.assertEqual(native_title_zh('光学'),'光学')

    def test_verified_citation_edges_are_persisted_and_malformed_edges_are_discarded(self):
        sid=self.session();result=self.result();edge={'source':'OpenAlex','citing':'https://openalex.org/W123','cited':'https://openalex.org/W456','evidence':'referenced_works'}
        result['items'][0]['citation_graph']=[edge,edge,{**edge,'citing':'https://openalex.org.evil/W123'},{**edge,'source':'model'},{**edge,'evidence':'invented'}]
        data=self.search(sid,result)
        self.assertEqual(data['items'][0].get('citation_graph'),[edge])
        result['items'][0].pop('citation_graph')
        self.assertEqual(self.search(sid,result)['items'][0].get('citation_graph'),[edge])

    def test_snapshot_racing_topic_delete_cannot_create_an_orphan_conversation(self):
        from paperwhale_server.services import discovery_store,recovery
        sid=self.session();validate=conversations.validate_scope;deleted={}
        def validated_then_deleted(scope):
            validate(scope)
            deleted.update(discovery_store.delete_session(sid))
        with patch.object(conversations,'validate_scope',side_effect=validated_then_deleted):
            with self.assertRaises(conversations.ConversationError):conversations.snapshot('discovery:'+sid)
        self.assertIsNone(db.query_one('SELECT id FROM ai_conversations WHERE scope=?',('discovery:'+sid,)))
        self.assertTrue(recovery.restore_paper(deleted['trash_id'])['ok'])


if __name__=='__main__':unittest.main()
