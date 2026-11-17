import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from paperwhale_server import config,db
from paperwhale_server.services import metadata,ingest

DIRTY='72 物理学报 DOI 10.7498/aps.72.20230934 2023 72(22): 224202'

class VenueTests(unittest.TestCase):
    def test_actual_polluted_citation_is_split_into_fields(self):
        clean=metadata.clean_meta({'venue':DIRTY})
        self.assertEqual(clean['venue'],'物理学报')
        self.assertEqual(clean['doi'],'10.7498/aps.72.20230934')
        self.assertEqual((clean['volume'],clean['issue'],clean['pages']),('72','22','224202'))
    def test_valid_numeric_journal_names_are_preserved(self):
        for title in ['2D Materials','3 Biotech','4OR','npj 2D Materials and Applications','IEEE Transactions on Pattern Analysis and Machine Intelligence','CVPR 2023','Acta Phys. Sin.']:
            self.assertEqual(metadata.clean_meta({'venue':title})['venue'],title)
    def test_embedded_citation_never_overwrites_explicit_fields(self):
        clean=metadata.clean_meta({'venue':DIRTY,'doi':'10.1000/explicit','volume':'73','issue':'1','pages':'123'})
        self.assertEqual((clean['doi'],clean['volume'],clean['issue'],clean['pages']),('10.1000/explicit','73','1','123'))
    def test_english_citation_and_chinese_typesetting(self):
        self.assertEqual(metadata.clean_meta({'venue':'物 理 学 报 Vol. 72, No. 22 (2023)'})['venue'],'物理学报')
        result=metadata.clean_meta({'venue':'Applied Optics 58(12): 3179–3184 DOI: 10.1364/AO.58.003179'})
        self.assertEqual(result['venue'],'Applied Optics');self.assertEqual(result['pages'],'3179–3184')
    def test_database_read_write_and_upgrade_cover_existing_records(self):
        with tempfile.TemporaryDirectory(prefix='npl-venue-') as folder:
            root=Path(folder)
            with patch.multiple(config,DATA_DIR=root,DEFAULT_PDF_DIR=root/'pdf',TMP_DIR=root/'tmp',DB_PATH=root/'test.db',SETTINGS_PATH=root/'settings.json',_cache={**config.DEFAULT_SETTINGS,'scholar_auto_sync':False}):
                db.init_db()
                db.execute('INSERT INTO papers(id,title,venue,volume) VALUES(?,?,?,?)',('old','测试论文',DIRTY,'72'))
                self.assertEqual(db.get_paper('old')['venue'],'物理学报','old rows are presented cleanly before migration')
                db.init_db()
                self.assertEqual(db.query_one("SELECT venue FROM papers WHERE id='old'")['venue'],'物理学报')
                db.update_paper('old',{'venue':DIRTY})
                self.assertEqual(db.query_one("SELECT venue FROM papers WHERE id='old'")['venue'],'物理学报')
                db.update_paper('old',{'doi':'10.1000/explicit','volume':'73'})
                db.update_paper('old',{'venue':DIRTY})
                self.assertEqual(db.get_paper('old')['doi'],'10.1000/explicit')
                self.assertEqual(db.get_paper('old')['volume'],'73')
                pid=ingest.create_paper_from_metadata({'title':'new','venue':DIRTY})
                self.assertEqual(db.get_paper(pid)['venue'],'物理学报')

if __name__=='__main__':unittest.main()
