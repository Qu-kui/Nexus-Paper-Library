"""Reader materials keep model input and readable user-history labels separate."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from fastapi.testclient import TestClient
from paperwhale_server import config,db
from paperwhale_server.main import app
from paperwhale_server.services import rag


class ReaderMaterialsTests(unittest.TestCase):
    def test_prepared_material_display_survives_history_reload(self):
        with tempfile.TemporaryDirectory(prefix='pw-materials-') as folder:
            root=Path(folder)
            with patch.multiple(config,DATA_DIR=root,DEFAULT_PDF_DIR=root/'pdfs',TMP_DIR=root/'tmp',DB_PATH=root/'test.db',SETTINGS_PATH=root/'settings.json',_cache={**config.DEFAULT_SETTINGS,'scholar_auto_sync':False}):
                db.init_db();db.execute("INSERT INTO papers(id,title) VALUES('a','PAPER A')")
                client=TestClient(app)
                with patch.object(rag,'chat',return_value={'answer':'ANSWER'}) as generate:
                    # Do not enter lifespan: no scheduler or background workers in this unit test.
                    result=client.post('/api/ai/chat',json={'paper_id':'a','page':3,'question':'RAW QUOTED MATERIAL + QUESTION',
                        'display_question':'本轮材料：本页，第 3 页\n解释本页','retrieval_question':'ORIGINAL QUERY'})
                    self.assertEqual(result.status_code,200,result.text)
                    saved=client.get('/api/ai/conversations/paper:a').json()['messages'][0]
                    self.assertEqual(saved['content'],'RAW QUOTED MATERIAL + QUESTION')
                    self.assertEqual(saved.get('display_question'),'本轮材料：本页，第 3 页\n解释本页')
                    self.assertEqual(generate.call_args.kwargs['page'],3)
                    self.assertEqual(generate.call_args.kwargs.get('retrieval_question'),'ORIGINAL QUERY')
                client.close()

    def test_retrieval_uses_user_question_while_model_receives_quoted_material(self):
        from paperwhale_server.services import websearch
        with tempfile.TemporaryDirectory(prefix='pw-material-query-') as folder:
            with patch.multiple(config,DB_PATH=Path(folder)/'test.db',_cache={**config.DEFAULT_SETTINGS}):
                db.init_db();db.execute("INSERT INTO papers(id,title,abstract) VALUES('a','Paper','source abstract')")
                wire='SOURCE BOILERPLATE '+('REFERENCE '*1000)+'Actual research question'
                with patch.object(websearch,'search',return_value=[]) as search,patch.object(rag,'_answer_llm',return_value=('ANSWER','','model',{})) as answer,patch.object(rag,'_query_tokens',wraps=rag._query_tokens) as tokens:
                    rag.chat(wire,'a',search_web=True,retrieval_question='Actual research question',history=[{'role':'user','content':'OLD SOURCE BOILERPLATE','retrieval_question':'previous research question'}])
                self.assertEqual(search.call_args.args[0],'Actual research question')
                self.assertEqual(tokens.call_args.args[0],'Actual research question previous research question')
                self.assertIn(wire,answer.call_args.args[1])


if __name__=='__main__':unittest.main()
