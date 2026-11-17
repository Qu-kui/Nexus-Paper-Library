"""Source-grounded note migration, repeated imports and old backup compatibility."""
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from paperwhale_server import config, db
from paperwhale_server.services import importers

RDF = '''<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
 xmlns:z="http://www.zotero.org/namespaces/export#" xmlns:dc="http://purl.org/dc/elements/1.1/"
 xmlns:dcterms="http://purl.org/dc/terms/" xmlns:bib="http://purl.org/net/biblio#"
 xmlns:link="http://purl.org/rss/1.0/modules/link/">
 <bib:Article rdf:about="#paper"><z:itemType>journalArticle</z:itemType><dc:title>Migration optics</dc:title>
 <dcterms:isReferencedBy rdf:resource="#note1"/><link:link rdf:resource="#pdf"/></bib:Article>
 <bib:Memo rdf:about="#note1"><rdf:value><![CDATA[<h2>方法总结</h2><p>保持 <b>中文</b> 和换行</p><ul><li>第一点</li><li>第二点</li></ul><script>bad()</script>]]></rdf:value></bib:Memo>
 <z:Attachment rdf:about="#pdf"><z:itemType>attachment</z:itemType><z:path rdf:resource="files/paper.pdf"/><dc:description><![CDATA[<p>附件备注</p>]]></dc:description></z:Attachment>
 <bib:Memo rdf:about="#orphan"><z:itemType>note</z:itemType><rdf:value>不能挂给其他论文</rdf:value></bib:Memo>
 <bib:Article rdf:about="#other"><z:itemType>journalArticle</z:itemType><dc:title>Another article</dc:title></bib:Article>
 </rdf:RDF>'''


class ImportedNotesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='npl-note-import-')
        self.root = Path(self.temp.name)
        self.settings = patch.multiple(config, DATA_DIR=self.root/'data', DEFAULT_PDF_DIR=self.root/'data/pdfs',
            TMP_DIR=self.root/'data/tmp', DB_PATH=self.root/'data/test.db', SETTINGS_PATH=self.root/'data/settings.json',
            _cache={**config.DEFAULT_SETTINGS,'scholar_auto_sync':False,'clipboard_watch':False,'clipboard_title_guess':False,'pdf_browser_session_enabled':False})
        self.settings.start(); db.init_db()
        self.addCleanup(self.temp.cleanup); self.addCleanup(self.settings.stop)

    def test_endnote_preserves_both_note_fields_and_paragraphs(self):
        path=self.root/'library.xml'
        path.write_text('<xml><records><record><titles><title>Optics notes</title></titles>'
            '<notes><style>第一段</style><p>第二段</p></notes><research-notes><![CDATA[<p>研究 <b>想法</b></p>]]></research-notes>'
            '<abstract>摘要不应成为笔记</abstract></record></records></xml>',encoding='utf-8')
        result=importers.import_endnote_path(path)
        self.assertEqual(result['notes_imported'],2)
        from paperwhale_server.services import imported_notes
        notes=imported_notes.read(result['imported'][0]['id'])['imported']
        self.assertIn('第一段',notes[0]['content']);self.assertIn('\n',notes[0]['content'])
        self.assertIn('研究 **想法**',notes[1]['content'])
        self.assertNotIn('摘要',json.dumps(notes,ensure_ascii=False))

    def test_rdf_child_memo_without_itemtype_and_attachment_note(self):
        path=self.root/'library.rdf';path.write_text(RDF,encoding='utf-8')
        parsed=importers.parse_zotero_rdf(path)
        self.assertEqual(len(parsed),2)
        self.assertEqual(len(parsed[0]['notes']),2)
        text='\n'.join(n['content'] for n in parsed[0]['notes'])
        self.assertIn('## 方法总结',text);self.assertIn('- 第一点',text);self.assertIn('附件备注',text)
        self.assertNotIn('bad()',text);self.assertNotIn('不能挂给',text)
        self.assertEqual(parsed[1].get('notes',[]),[])
        self.assertIn('files/paper.pdf',parsed[0]['pdf_hints'])

    def test_repeat_migration_enriches_existing_paper_without_pdf_or_overwrite(self):
        pid='existing';db.execute('INSERT INTO papers(id,title,notes) VALUES(?,?,?)',(pid,'Migration optics','自己的笔记'))
        path=self.root/'library.rdf';path.write_text(RDF,encoding='utf-8')
        first=importers.import_zotero_file(path)
        second=importers.import_zotero_file(path)
        self.assertEqual(len(first['imported']),0,'existing-only enrichment never creates ghost papers')
        self.assertEqual(first['notes_imported'],2);self.assertEqual(first['notes_updated_papers'],1)
        self.assertEqual(second['notes_imported'],0);self.assertEqual(second['notes_duplicates'],2)
        self.assertEqual(db.get_paper(pid)['notes'],'自己的笔记')
        from paperwhale_server.services import imported_notes
        self.assertEqual(len(imported_notes.read(pid)['imported']),2)
        self.assertEqual(db.query_one('SELECT count(*) AS n FROM papers')['n'],1)

    def test_new_zotero_paper_imports_linked_pdf_and_notes(self):
        from paperwhale_server.services import imported_notes
        (self.root/'files').mkdir()
        (self.root/'files/paper.pdf').write_bytes((Path(__file__).parent/'fixtures/attention.pdf').read_bytes())
        path=self.root/'library.rdf';path.write_text(RDF,encoding='utf-8')
        result=importers.import_zotero_file(path)
        self.assertEqual(len(result['imported']),1)
        paper=result['imported'][0]
        self.assertTrue(paper['has_pdf'])
        self.assertEqual(len(imported_notes.read(paper['id'])['imported']),2)

    def test_csv_ris_bib_notes_are_not_abstract_or_extra(self):
        csv_path=self.root/'notes.csv';csv_path.write_text('Title,Notes,Extra,Abstract Note\nCSV paper,"<p>CSV 笔记</p>",publisher field,摘要\n',encoding='utf-8')
        ris_path=self.root/'notes.ris';ris_path.write_text('TY  - JOUR\nTI  - RIS paper\nN1  - 第一条备注\nN1  - 第二条备注\nAB  - 摘要\nER  -\n',encoding='utf-8')
        bib_path=self.root/'notes.bib';bib_path.write_text('@article{key,title={Bib paper},note={Bib note},annote={Annotation text},abstract={Abstract only}}',encoding='utf-8')
        for path in [csv_path,ris_path,bib_path]:
            item=importers._parse_any(path)[0]
            self.assertTrue(item['notes'],path.name)
            text='\n'.join(n['content'] for n in item['notes'])
            self.assertNotIn('摘要',text);self.assertNotIn('publisher field',text);self.assertNotIn('Abstract only',text)

    def test_notes_api_and_personal_append_remain_separate(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app
        from paperwhale_server.services import imported_notes
        db.execute('INSERT INTO papers(id,title,notes) VALUES(?,?,?)',('p','Title','原笔记'))
        imported_notes.merge('p',[{'label':'Notes','content':'迁移笔记'}],source='endnote')
        client=TestClient(app)
        data=client.get('/api/papers/p/notes').json()
        self.assertEqual(data['personal'],'原笔记');self.assertEqual(data['imported'][0]['source'],'endnote')
        self.assertEqual(client.get('/api/papers/missing/notes').status_code,404)
        client.post('/api/papers/p/notes/append',json={'text':'新增想法'})
        self.assertIn('新增想法',client.get('/api/papers/p/notes').json()['personal'])
        self.assertEqual(len(client.get('/api/papers/p/notes').json()['imported']),1)

    def test_backup_and_trash_preserve_imported_notes_and_old_schema(self):
        from paperwhale_server.services import imported_notes,recovery
        db.execute('INSERT INTO papers(id,title) VALUES(?,?)',('p','Title'))
        imported_notes.merge('p',[{'label':'Notes','content':'带走的笔记'}],source='endnote')
        trash=recovery.archive_paper('p');recovery.restore_paper(trash['trash_id'])
        self.assertEqual(imported_notes.read('p')['imported'][0]['content'],'带走的笔记')
        with closing(db.connect()) as source, closing(sqlite3.connect(':memory:')) as target:
            target.row_factory=sqlite3.Row;target.executescript(db.SCHEMA);db._migrate(target)
            recovery._copy_rows(source,target)
            self.assertIn('带走的笔记',target.execute('SELECT imported_notes FROM papers').fetchone()[0])
            source.execute('ALTER TABLE papers DROP COLUMN imported_notes')
            target.execute('DELETE FROM papers');target.execute('DELETE FROM recovery_items')
            recovery._copy_rows(source,target)
            self.assertEqual(target.execute('SELECT imported_notes FROM papers').fetchone()[0],'[]')

    def test_parallel_enrichment_never_loses_notes(self):
        from concurrent.futures import ThreadPoolExecutor
        from paperwhale_server.services import imported_notes
        db.execute('INSERT INTO papers(id,title,notes) VALUES(?,?,?)',('p','Title','本地内容'))
        def run(index):
            return imported_notes.merge('p',[{'content':f'并发笔记 {index % 8}'}],source='zotero')
        with ThreadPoolExecutor(max_workers=4) as pool:
            counts=list(pool.map(run,range(24)))
        self.assertEqual(sum(c[0] for c in counts),8)
        self.assertEqual(len(imported_notes.read('p')['imported']),8)
        self.assertEqual(db.get_paper('p')['notes'],'本地内容')

    def test_real_backup_restore_roundtrip_and_legacy_backup(self):
        from paperwhale_server.services import imported_notes,recovery
        db.execute('INSERT INTO papers(id,title,notes) VALUES(?,?,?)',('p','Title','我的笔记'))
        imported_notes.merge('p',[{'content':'备份笔记'}],source='zotero')
        saved=recovery.create_backup()
        data=(config.DATA_DIR/'backups'/saved['name']).read_bytes()
        imported_notes.merge('p',[{'content':'备份后的笔记'}],source='zotero')
        recovery.restore_backup(data)
        self.assertEqual(len(imported_notes.read('p')['imported']),1)
        self.assertEqual(imported_notes.read('p')['personal'],'我的笔记')
        with closing(db.connect()) as conn, conn:
            conn.execute('ALTER TABLE papers DROP COLUMN imported_notes')
        legacy=recovery.create_backup()
        legacy_data=(config.DATA_DIR/'backups'/legacy['name']).read_bytes()
        db.init_db()
        recovery.restore_backup(legacy_data)
        self.assertEqual(imported_notes.read('p'),{'personal':'我的笔记','imported':[]})

    def test_changed_source_note_preserves_old_revision_and_does_not_leak_into_list(self):
        from paperwhale_server.services import imported_notes
        db.execute('INSERT INTO papers(id,title) VALUES(?,?)',('p','Title'))
        imported_notes.merge('p',[{'content':'原内容'}],source='endnote')
        imported_notes.merge('p',[{'content':'修改后的内容'}],source='endnote')
        self.assertEqual(len(imported_notes.read('p')['imported']),2)
        self.assertNotIn('imported_notes',db.get_paper('p'))
        with self.assertRaises(ValueError):
            imported_notes.merge('p',[{'content':'x'*1_000_001}],source='endnote')
        self.assertEqual(len(imported_notes.read('p')['imported']),2)

if __name__=='__main__':unittest.main()
