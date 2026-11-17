"""术语/缩写复习：抽取、SRS 排期、增删。"""
from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("PAPERWHALE_LLM_MOCK", "1")

from paperwhale_server import config, db
from paperwhale_server.services import terms


class TermsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="pw-terms-")
        root = Path(self.temp.name)
        self.paths = patch.multiple(
            config,
            DATA_DIR=root,
            SETTINGS_PATH=root / "settings.json",
            DB_PATH=root / "test.db",
            TMP_DIR=root / "tmp",
            DEFAULT_PDF_DIR=root / "pdfs",
            PROGRAM_DIR=root,
            _cache={**config.DEFAULT_SETTINGS},
        )
        self.paths.start()
        db.init_db()
        db.execute("INSERT INTO papers (id,title,created_at,updated_at) VALUES ('p','Paper',?,?)", (db.now(), db.now()))

    def tearDown(self):
        if terms._EXPORT_TIMER:
            terms._EXPORT_TIMER.cancel()
        self.paths.stop()
        self.temp.cleanup()

    def _client(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app
        return TestClient(app)

    def test_question_validation_and_retry(self):
        for i in range(3):
            terms.add_term(f"word{i}", meaning=f"释义{i}", sync=False)
        question = terms.quiz(1)[0]
        client = self._client()
        url = f"/api/terms/{question['id']}/answer"
        self.assertEqual(client.post(url, json={"question_id": question["question_id"], "chosen_option": "伪造"}).status_code, 400)
        body = {"question_id": question["question_id"], "chosen_option": question["answer"], "fuzzy": True}
        first = client.post(url, json=body)
        second = client.post(url, json=body)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(second.json()["item"]["reps"], 1)
        self.assertEqual(second.json()["item"]["fuzzy"], 1)

    def test_senses_and_alias_collisions(self):
        a = terms.add_term("NA", meaning="数值孔径", kind="abbr", sync=False)
        self.assertIsNotNone(terms.add_term("NA", meaning="不适用", kind="abbr", sync=False))
        terms.add_term("N.A.", meaning="数值孔径", kind="abbr", sync=False)
        terms.add_term("FFT", meaning="傅里叶变换", kind="abbr", sync=False)
        question = next(q for q in terms.quiz(10, mode="meaning2term") if q["id"] == a["id"])
        self.assertNotIn("N.A.", question["options"])
        terms.update_term(a["id"], aliases=["numerical aperture"])
        self.assertIn("numerical aperture", terms.get_term(a["id"])["aliases"])

    def test_queue_past_500_and_due_consistency(self):
        for i in range(501):
            terms.add_term(f"bad{i}", sync=False)
        for i in range(2):
            terms.add_term(f"good{i}", meaning=f"有效{i}", sync=False)
        self.assertEqual(len(terms.quiz(2)), 2)
        self.assertEqual(len(terms.due_terms(100)), 2)
        self.assertEqual(terms.stats()["due"], 2)

    def test_fuzzy_never_graduates_and_counters_survive_history(self):
        row = terms.add_term("test", meaning="测试", sync=False)
        with patch.object(terms, "schedule_dictionary_sync"):
            for _ in range(305):
                row = terms.answer_quiz(row["id"], True, fuzzy=True)
        self.assertLess(row["mastery_en2zh"], 100)
        self.assertEqual(row["asked"], 305)
        self.assertEqual(row["fuzzy"], 305)
        normal = terms.answer_quiz(row["id"], True)
        self.assertEqual(normal["mastery_en2zh"], 100)

    def test_concurrent_answers_preserved(self):
        from concurrent.futures import ThreadPoolExecutor
        row = terms.add_term("parallel", meaning="测试", sync=False)
        with patch.object(terms, "schedule_dictionary_sync"):
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _: terms.answer_quiz(row["id"], False), range(12)))
        self.assertEqual(terms.get_term(row["id"])["asked"], 12)

    def test_retention_and_direction_clocks(self):
        from datetime import timedelta
        for i in range(3):
            terms.add_term(f"retain{i}", meaning=f"保留{i}", sync=False)
        row = terms.list_terms()[0]
        first = terms.answer_quiz(row['id'], True)
        self.assertEqual(first['due_zh2en'], '')
        self.assertNotIn(row['id'], [q['id'] for q in terms.quiz(mode='term2meaning')])
        self.assertIn(row['id'], [q['id'] for q in terms.quiz(mode='meaning2term')])
        for direction in ('term2meaning', 'meaning2term'):
            for _ in range(3):
                result = terms.answer_quiz(row['id'], True, direction=direction)
        self.assertEqual(result['mastery'], 100)
        self.assertNotIn(row['id'], [q['id'] for q in terms.quiz()])
        future = terms._now() + timedelta(days=8)
        with patch.object(terms, '_now', return_value=future):
            self.assertIn(row['id'], [q['id'] for q in terms.quiz()])
            self.assertIn(row['id'], [r['id'] for r in terms.due_terms()])

    def test_legacy_migration_preserves_duplicates_and_history(self):
        import json
        a = terms.add_term('old', meaning='旧词义', sync=False)
        terms.answer_quiz(a['id'], True)
        db.execute('INSERT INTO terms(id,term,meaning,history_json,reps) VALUES (?,?,?,?,?)',
                   ('duplicate', 'old', '旧词义', '[]', 0))
        before = [dict(r) for r in db.query('SELECT id,history_json,mastery_en2zh FROM terms ORDER BY id')]
        db.init_db()
        db.init_db()
        self.assertEqual(before, [dict(r) for r in db.query('SELECT id,history_json,mastery_en2zh FROM terms ORDER BY id')])
        self.assertEqual(len(json.loads(terms.get_term(a['id'])['history_json'])), 1)

    def test_pagination_search_sources_and_patch_validation(self):
        for i in range(105):
            terms.add_term(f'page{i}', meaning=f'释义{i}', sync=False)
        client = self._client()
        page = client.get('/api/terms?offset=100&limit=100').json()
        self.assertEqual(len(page['items']), 5)
        self.assertEqual(page['total'], 105)
        row = page['items'][0]
        self.assertEqual(client.patch(f"/api/terms/{row['id']}", json={'term': ' '}).status_code, 400)
        self.assertEqual(client.get('/api/terms?q=page104').json()['total'], 1)
        terms._append_source(row['id'], 'second-paper', 9)
        self.assertEqual(len(terms.list_terms(paper_id='second-paper')), 1)

    def test_export_manifest_and_save_independent_of_export(self):
        import hashlib
        import json
        row = terms.add_term('export', meaning='导出', sync=False)
        with patch.object(terms, 'sync_dictionary', side_effect=OSError('disk unavailable')):
            result = terms.answer_quiz(row['id'], True)
            terms._EXPORT_TIMER.cancel()
            terms._EXPORT_TIMER.function()
            self.assertIn('disk unavailable', terms._EXPORT_ERROR)
        self.assertEqual(result['asked'], 1)
        terms.flush_dictionary_sync()
        folder = config.dictionary_dir()
        manifest = json.loads((folder / 'manifest.json').read_text(encoding='utf8'))
        for name, checksum in manifest['sha256'].items():
            self.assertEqual(hashlib.sha256((folder / name).read_bytes()).hexdigest(), checksum)

    def test_stale_and_expired_question_cannot_score(self):
        for i in range(3):
            terms.add_term(f'token{i}', meaning=f'验证{i}', sync=False)
        first = terms.quiz(1, mode='term2meaning')[0]
        second = terms.quiz(1, mode='term2meaning')[0]
        terms.answer_quiz(first['id'], question_id=first['question_id'], chosen_option=first['answer'])
        with self.assertRaises(ValueError):
            terms.answer_quiz(second['id'], question_id=second['question_id'], chosen_option=second['answer'])
        db.execute("UPDATE term_questions SET expires_at='2000-01-01' WHERE id=?", (second['question_id'],))
        with self.assertRaises(ValueError):
            terms.answer_quiz(second['id'], question_id=second['question_id'], chosen_option=second['answer'])
        self.assertEqual(terms.get_term(first['id'])['asked'], 1)

    def test_additive_migration_from_old_columns(self):
        import json
        row = terms.add_term('migration', meaning='迁移', sync=False)
        history = [{'correct': True}, {'correct': False, 'fuzzy': True}]
        db.execute('UPDATE terms SET reps=400, history_json=?,mastery=100,mastery_en2zh=100,mastery_zh2en=100 WHERE id=?', (json.dumps(history), row['id']))
        # Simulate the shipped schema, with a term-only unique index.
        for name in ('aliases_json','due_en2zh','due_zh2en','step_en2zh','step_zh2en','asked','correct_count','fuzzy_count','wrong_count'):
            db.execute(f'ALTER TABLE terms DROP COLUMN {name}')
        db.execute('CREATE UNIQUE INDEX idx_terms_unique_nocase ON terms(term COLLATE NOCASE)')
        db.init_db()
        migrated = terms.get_term(row['id'])
        self.assertEqual(migrated['asked'], 400)
        self.assertEqual(migrated['correct'], 1)
        self.assertEqual(migrated['wrong'], 1)
        self.assertEqual(migrated['unclassified'], 398)
        self.assertEqual(migrated['mastery'], 100)
        self.assertGreater(migrated['due_at'], terms._stamp(terms._now()))
        self.assertEqual(json.loads(migrated['history_json']), history)

    def test_failed_pair_export_keeps_previous_manifest_snapshot(self):
        import json
        import os
        row = terms.add_term('snapshot', meaning='快照', sync=False)
        terms.flush_dictionary_sync()
        folder = config.dictionary_dir()
        previous = (folder / 'manifest.json').read_bytes()
        manifest = json.loads(previous)
        contents = {name: (folder / name).read_bytes() for name in manifest['files'].values()}
        replace = os.replace
        def fail_csv(src, dst):
            if str(dst).endswith('.csv'):
                raise OSError('CSV unavailable')
            return replace(src, dst)
        with patch('os.replace', side_effect=fail_csv):
            with self.assertRaises(OSError):
                terms.sync_dictionary()
        self.assertEqual((folder / 'manifest.json').read_bytes(), previous)
        for name, content in contents.items():
            self.assertEqual((folder / name).read_bytes(), content)

    def test_add_dedupe_and_stats(self):
        created = terms.add_term("PCI", expansion="Polarization Contrast Imaging", meaning="偏振对比成像", paper_id="p")
        self.assertIsNotNone(created)
        self.assertIsNone(terms.add_term("PCI", paper_id="p"), "同一篇论文里重复术语不再添加")
        stats = terms.stats()
        self.assertEqual(stats["total"], 1)
        self.assertEqual(stats["due"], 0, "单个词条缺少有效干扰项")

    def test_review_scheduling(self):
        created = terms.add_term("OPD", expansion="Optical Path Difference", paper_id="p")
        first = terms.review_term(created["id"], "good")
        self.assertEqual(first["reps"], 1)
        self.assertEqual(first["mastery_en2zh"], 60.0, "第一次就答对说明本来认识")
        self.assertEqual(first["mastery"], 0.0, "只答了一个方向还不能算熟识")
        self.assertAlmostEqual(first["interval_days"], 25 / 1440, places=4)
        self.assertGreater(first["due_en2zh"], db.now()[:10])
        hard = terms.review_term(created["id"], "hard")
        self.assertAlmostEqual(hard["interval_days"], 25 / 1440, places=3)
        reset = terms.review_term(created["id"], "again")
        self.assertAlmostEqual(reset["interval_days"], 5 / 1440, places=4)
        self.assertEqual(reset["step"], 0)
        self.assertEqual(reset["lapses"], 1)
        import json
        history = json.loads(reset["history_json"])
        self.assertEqual(len(history), 3)
        self.assertEqual(history[0]["correct"], True)
        with self.assertRaises(ValueError):
            terms.review_term(created["id"], "nope")

    def test_ebbinghaus_steps_and_quiz_options(self):
        for index in range(7):
            terms.add_term(f"T{index}", expansion=f"Expansion number {index}",
                           meaning=f"释义{index}", paper_id="p")
        items = terms.quiz(limit=2)
        self.assertEqual(len(items), 2)
        for item in items:
            self.assertGreaterEqual(len(item["options"]), 2)
            self.assertLessEqual(len(item["options"]), 6)
            self.assertIn(item["answer"], item["options"])
            self.assertEqual(len(set(item["options"])), len(item["options"]))
            self.assertTrue(item["prompt"])
        # 连续答对：5 分钟 → 25 分钟 → 2 小时（艾宾浩斯阶梯）
        row = terms.add_term("SRS", meaning="间隔重复", paper_id="p")
        first = terms.answer_quiz(row["id"], True, direction="term2meaning")
        self.assertEqual(first["step"], 1)
        self.assertAlmostEqual(first["interval_days"], 25 / 1440, places=4)
        second = terms.answer_quiz(row["id"], True)
        self.assertEqual(second["step"], 2)
        self.assertAlmostEqual(second["interval_days"], 2 / 24, places=4)
        wrong = terms.answer_quiz(row["id"], False)
        self.assertEqual(wrong["step"], 0)
        self.assertAlmostEqual(wrong["interval_days"], 5 / 1440, places=4)
        self.assertEqual(wrong["lapses"], 1)

    def test_quiz_direction_meaning2term(self):
        for index in range(4):
            terms.add_term(f"W{index}", meaning=f"词义{index}", paper_id="p")
        items = terms.quiz(limit=1, mode="meaning2term")
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["direction"], "meaning2term")
        self.assertIn(items[0]["answer"], [f"W{i}" for i in range(4)])
        self.assertTrue(items[0]["prompt"].startswith("词义"))

    def test_extract_abbreviations_from_chunks(self):
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES (?,?,?)",
                   ("p", 3, "Polarization contrast imaging (PCI) and Optical path difference (OPD) are compared in Fig. 2."))
        added = terms.extract_abbreviations("p")
        self.assertEqual(added, 2)
        stored = {row["term"]: row for row in terms.list_terms(paper_id="p")}
        self.assertIn("PCI", stored)
        self.assertIn("OPD", stored)
        self.assertEqual(stored["PCI"]["expansion"], "Polarization contrast imaging")
        self.assertEqual(stored["PCI"]["page"], 3)
        self.assertEqual(terms.extract_abbreviations("p"), 0, "重复提取不应重复添加")

    def test_ai_extract_uses_model_json(self):
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES (?,?,?)", ("p", 1, "some text"))
        with patch.object(terms.llm, "chat_json", return_value={"items": [
            {"term": "SLM", "expansion": "spatial light modulator", "meaning": "空间光调制器", "page": 2},
            {"term": "GS", "expansion": "Gerchberg-Saxton", "meaning": "GS 算法", "page": 1},
        ]}):
            added = terms.ai_extract_terms("p")
        self.assertEqual(added, 2)
        stored = {row["term"]: row for row in terms.list_terms(paper_id="p")}
        self.assertEqual(stored["SLM"]["meaning"], "空间光调制器")
        self.assertEqual(stored["SLM"]["source"], "ai")


class TermsApiTests(TermsTests):
    def _client(self):
        from fastapi.testclient import TestClient
        from paperwhale_server.main import app
        return TestClient(app)


    def test_extract_all_only_english_and_global_dedupe(self):
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('en1','Optics paper','a.pdf',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('en2','Another optics paper','b.pdf',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('zh','中文论文标题','c.pdf',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES ('en1',2,'use Polarization contrast imaging (PCI) here')")
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES ('en2',5,'the Polarization contrast imaging (PCI) again, plus Numerical aperture (NA)')")
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES ('zh',1,'偏振对比成像 (PCI) 的中文论文')")
        added = terms.extract_abbreviations_all()
        self.assertEqual(added, 2)
        stored = {row['term']: row for row in terms.list_terms()}
        self.assertEqual(set(stored), {'PCI', 'NA'})
        self.assertEqual(stored['PCI']['paper_id'], 'en1', '跨论文去重后保留第一次出现')
        self.assertNotIn('zh', [row['paper_id'] for row in terms.list_terms()])

    def test_extract_all_api(self):
        client = self._client()
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('en1','Optics paper','a.pdf',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES ('en1',2,'use Optical path difference (OPD) here')")
        ok = client.post("/api/terms/extract", json={"all": True, "mode": "regex"})
        self.assertEqual(ok.status_code, 200, ok.text)
        self.assertEqual(ok.json()["added"], 1)
        bad = client.post("/api/terms/extract", json={"mode": "regex"})
        self.assertEqual(bad.status_code, 400)



    def test_abbreviation_quiz_shows_pair_and_meaning_choices(self):
        for index in range(6):
            terms.add_term(f"A{index}", expansion=f"Expansion {index}", meaning=f"缩写释义{index}", paper_id="p")
        items = terms.quiz(limit=6, mode="term2meaning")
        item = next(row for row in items if row["kind"] == "abbr")
        self.assertIn("（", item["prompt"], "缩写题面要同时给出缩写和全称")
        self.assertTrue(item["answer"].startswith("缩写释义"))
        reverse = terms.quiz(limit=1, mode="meaning2term")
        if reverse:
            self.assertIn("（", reverse[0]["answer"], "反向题的选项是缩写+全称")

    def test_history_and_dictionary_counts(self):
        row = terms.add_term("SPI", expansion="single-pixel imaging", meaning="单像素成像", paper_id="p")
        terms.answer_quiz(row["id"], True)
        terms.answer_quiz(row["id"], True)
        stored = terms.get_term(row["id"])
        import json
        history = json.loads(stored["history_json"])
        self.assertEqual(len(history), 2)
        self.assertEqual([entry["correct"] for entry in history], [True, True])
        terms.flush_dictionary_sync()
        payload = json.loads((config.dictionary_dir() / "words.json").read_text(encoding="utf-8"))
        entry = next(item for item in payload["entries"] if item["term"] == "SPI")
        self.assertEqual(entry["asked"], 2)
        self.assertEqual(entry["correct"], 2)
        self.assertEqual(entry["kind"], "abbr")

    def test_first_answer_correct_counts_as_known(self):
        for index in range(6):
            terms.add_term(f"F{index}", meaning=f"填充{index}", paper_id="p")
        row = terms.add_term("NA", expansion="numerical aperture", meaning="数值孔径", paper_id="p")
        first = terms.answer_quiz(row["id"], True, direction="term2meaning")
        self.assertEqual(first["mastery_en2zh"], 60.0, "首答即对更像本来就认识")
        self.assertEqual(first["step"], 1, "首答不跳过短间隔练习")
        self.assertEqual(first["mastery"], 0.0)
        second = terms.answer_quiz(row["id"], True, direction="term2meaning")
        self.assertEqual(second["mastery_en2zh"], 85.0)
        third = terms.answer_quiz(row["id"], True, direction="term2meaning")
        self.assertEqual(third["mastery_en2zh"], 100.0)
        self.assertEqual(third["mastery"], 0.0)
        db.execute("UPDATE terms SET due_at='' WHERE id=?", (row["id"],))
        self.assertIn("NA", [item["term"] for item in terms.quiz(limit=20)], "另一个方向没满，还要继续出题")
        for _ in range(3):
            row = terms.answer_quiz(row["id"], True, direction="meaning2term")
        self.assertEqual(row["mastery"], 100.0)
        self.assertNotIn("NA", [item["term"] for item in terms.quiz(limit=20)])

    def test_extract_all_skips_one_paper_specific_abbreviations(self):
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('en1','Optics one','a.pdf',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('en2','Optics two','b.pdf',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES ('en1',2,'Our XYZ-Setup (XYZ) and Single Pixel Imaging (SPI)')")
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES ('en2',2,'Single Pixel Imaging (SPI) setup')")
        added = terms.extract_abbreviations_all()
        labels = {row["term"] for row in terms.list_terms()}
        self.assertIn("SPI", labels)
        self.assertNotIn("XYZ", labels, "只在一篇里出现的自造缩写不收录")
        self.assertEqual(added, 1)


    def test_abbreviation_without_meaning_uses_pair_fallback(self):
        for index in range(6):
            terms.add_term(f"B{index}", expansion=f"Full name {index}", paper_id="p")
        items = terms.quiz(limit=6, mode="meaning2term")
        self.assertTrue(items)
        item = items[0]
        self.assertIn("Full name", item["prompt"], "没有中文释义时用全称出题")
        self.assertNotIn("（", item["answer"], "全称题选项仅含缩写，不能泄露答案")

    def test_fill_missing_meanings(self):
        terms.add_term("SPI", expansion="single-pixel imaging", paper_id="p")
        with patch.object(terms.llm, "chat_json", return_value={"items": [{"i": 0, "meaning": "单像素成像"}]}):
            updated = terms.fill_missing_meanings()
        self.assertEqual(updated, 1)
        stored = terms.list_terms()[0]
        self.assertEqual(stored["meaning"], "单像素成像")

    def test_dictionary_file_is_written_and_append_only(self):
        import json
        terms.add_term("PCI", expansion="polarization contrast imaging", meaning="偏振对比成像", paper_id="p", page=3)
        terms.flush_dictionary_sync()
        folder = config.dictionary_dir()
        self.assertEqual(folder.parent, config.PROGRAM_DIR, "单词本应位于程序根目录")
        payload = json.loads((folder / "words.json").read_text(encoding="utf-8"))
        self.assertEqual(payload["count"], 1)
        self.assertEqual(payload["entries"][0]["term"], "PCI")
        self.assertTrue((folder / "words.csv").exists())
        self.assertTrue((folder / "README.txt").exists())
        # 重复提取不增条目、只补来源
        terms.extract_abbreviations("p")
        self.assertEqual(terms.stats()["total"], 1)

    def test_global_dedupe_across_papers(self):
        db.execute("INSERT INTO papers (id,title,created_at,updated_at) VALUES ('q','Q',?,?)", (db.now(), db.now()))
        terms.add_term("PCI", expansion="polarization contrast imaging", paper_id="p", page=1)
        terms.add_term("PCI", expansion="polarization contrast imaging", paper_id="q", page=9)
        rows = terms.list_terms()
        self.assertEqual(len(rows), 1)
        import json
        sources = json.loads(rows[0]["sources_json"])
        self.assertEqual({item["paper_id"] for item in sources}, {"p", "q"})

    def test_mastery_graduates_and_leaves_quiz(self):
        for index in range(12):
            terms.add_term(f"T{index}", expansion=f"Expansion number {index}", meaning=f"释义{index}", paper_id="p")
        row = next(item for item in terms.list_terms() if item["term"] == "T0")
        for _ in range(3):
            row = terms.answer_quiz(row["id"], True, direction="term2meaning")
        for _ in range(3):
            row = terms.answer_quiz(row["id"], True, direction="meaning2term")
        self.assertEqual(row["mastery"], 100)
        self.assertTrue(terms.stats()["mastered"] >= 1)
        prompts = [item["term"] for item in terms.quiz(limit=30)]
        self.assertNotIn("T0", prompts, "记忆度满的词不再出现")
        import json
        terms.flush_dictionary_sync()
        payload = json.loads((config.dictionary_dir() / "words.json").read_text(encoding="utf-8"))
        entry = next(item for item in payload["entries"] if item["term"] == "T0")
        self.assertTrue(entry["mastered"])

    def test_retire_keeps_word_but_stops_quiz(self):
        for index in range(12):
            terms.add_term(f"W{index}", meaning=f"词义{index}", paper_id="p")
        row = next(item for item in terms.list_terms() if item["term"] == "W0")
        retired = terms.retire_term(row["id"])
        self.assertEqual(retired["retired"], 1)
        self.assertNotIn("W0", [item["term"] for item in terms.quiz(limit=30)])
        payload = terms.list_terms()
        self.assertIn("W0", [item["term"] for item in payload], "退出的词仍留在词典里")


    def test_ai_extract_all_runs_in_background_over_all_papers(self):
        import time
        for pid, title in (("en1", "Optics one"), ("en2", "Optics two")):
            db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES (?,?,'x.pdf',?,?)",
                       (pid, title, db.now(), db.now()))
            db.execute("INSERT INTO chunks (paper_id,page,text) VALUES (?,1,?)", (pid, f"text of {pid}"))

        with patch.object(terms.llm, "chat_json", side_effect=[
            {"items": [{"term": "SLM", "kind": "abbreviation", "expansion": "spatial light modulator",
                        "meaning": "空间光调制器", "page": 1}]},
            {"items": [{"term": "retardation", "kind": "word", "meaning": "延迟", "pos": "n.",
                        "phonetic": "ˌriːtɑːˈdeɪʃn", "page": 1}]},
            {"items": []},
        ]):
            job = terms.start_ai_extract_all()
            self.assertTrue(job["running"])
            for _ in range(50):
                status = terms.ai_job_status()
                if not status["running"]:
                    break
                time.sleep(0.05)
        status = terms.ai_job_status()
        self.assertFalse(status["running"])
        self.assertEqual(status["total"], 2)
        self.assertEqual(status["done"], 2)
        self.assertEqual(status["added"], 2)
        stored = {row["term"]: row for row in terms.list_terms()}
        self.assertEqual(stored["SLM"]["kind"], "abbr")
        self.assertEqual(stored["retardation"]["pos"], "n.")
        self.assertEqual(stored["retardation"]["phonetic"], "ˌriːtɑːˈdeɪʃn")

    def test_ai_extract_all_api_starts_job(self):
        client = self._client()
        db.execute("INSERT INTO papers (id,title,file_path,created_at,updated_at) VALUES ('en1','Optics one','a.pdf',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO chunks (paper_id,page,text) VALUES ('en1',1,'text')")
        with patch.object(terms.llm, "chat_json", return_value={"items": []}):
            started = client.post("/api/terms/extract", json={"all": True, "mode": "ai"})
            self.assertEqual(started.status_code, 200, started.text)
            self.assertTrue(started.json()["started"])
            import time
            for _ in range(60):
                status = client.get("/api/terms/extract/status").json()["job"]
                if not status["running"]:
                    break
                time.sleep(0.05)
        self.assertFalse(status["running"])



    def test_stats_buckets_sum_to_total(self):
        unlearned = terms.add_term("U1", meaning="未学", paper_id="p")
        unknown = terms.add_term("K1", meaning="不认识", paper_id="p")
        unfamiliar = terms.add_term("F1", meaning="不熟", paper_id="p")
        familiar = terms.add_term("R1", meaning="认识", paper_id="p")
        mastered = terms.add_term("M1", meaning="熟识", paper_id="p")
        terms.answer_quiz(unknown["id"], False, unknown=True)
        terms.answer_quiz(unfamiliar["id"], False)
        terms.answer_quiz(familiar["id"], True, direction="term2meaning")
        terms.answer_quiz(familiar["id"], True, direction="meaning2term")
        for _ in range(3):
            terms.answer_quiz(mastered["id"], True, direction="term2meaning")
            terms.answer_quiz(mastered["id"], True, direction="meaning2term")
        stats = terms.stats()
        self.assertEqual(stats["total"], 5)
        self.assertEqual(stats["unlearned"], 1)
        self.assertEqual(stats["unknown"], 1)
        self.assertEqual(stats["unfamiliar"], 1)
        self.assertEqual(stats["familiar"], 1)
        self.assertEqual(stats["mastered"], 1)
        self.assertEqual(stats["bucket_sum"], stats["total"])

    def test_distractors_come_from_same_paper(self):
        db.execute("INSERT INTO papers (id,title,created_at,updated_at) VALUES ('a','A',?,?)", (db.now(), db.now()))
        db.execute("INSERT INTO papers (id,title,created_at,updated_at) VALUES ('b','B',?,?)", (db.now(), db.now()))
        meanings_a = ["干涉", "衍射", "色散", "偏振", "折射", "吸收"]
        for index, meaning in enumerate(meanings_a):
            terms.add_term(f"A{index}", meaning=meaning, paper_id="a")
        for index, meaning in enumerate(["显微术", "染色", "切片"]):
            terms.add_term(f"B{index}", meaning=meaning, paper_id="b")
        items = terms.quiz(limit=1, mode="term2meaning")
        item = items[0]
        self.assertIn(item["term"], {f"A{i}" for i in range(6)})
        self.assertNotIn(item["answer"], ["显微术", "染色", "切片"])
        self.assertTrue(set(item["options"]).issubset(set(meanings_a)))


    def test_quiz_skips_unanswerable_cards(self):
        for index in range(30):
            terms.add_term(f"N{index}", paper_id="p")  # 无释义，不能出题
        for index in range(6):
            terms.add_term(f"Y{index}", meaning=f"有释义{index}", paper_id="p")
        items = terms.quiz(limit=3)
        self.assertEqual(len(items), 3, "不能被前面的无释义词卡死")
        self.assertTrue(all(item["answer"] for item in items))


    def test_fuzzy_correct_gains_less_and_recorded(self):
        normal = terms.add_term("NM", meaning="正常", paper_id="p")
        fuzzy = terms.add_term("FZ", meaning="模糊", paper_id="p")
        normal_row = terms.answer_quiz(normal["id"], True, direction="term2meaning")
        fuzzy_row = terms.answer_quiz(fuzzy["id"], True, fuzzy=True, direction="term2meaning")
        self.assertEqual(normal_row["mastery_en2zh"], 60.0)
        self.assertEqual(fuzzy_row["mastery_en2zh"], 18.0, "模糊答对只给 30% 的分")
        import json
        entry = json.loads(fuzzy_row["history_json"])[-1]
        self.assertTrue(entry["fuzzy"])
        self.assertGreaterEqual(fuzzy_row["step"], 1)
        terms.flush_dictionary_sync()
        payload = json.loads((config.dictionary_dir() / "words.json").read_text(encoding="utf-8"))
        saved = next(item for item in payload["entries"] if item["term"] == "FZ")
        self.assertEqual(saved["fuzzy"], 1)
        self.assertEqual(saved["mastery_en2zh"], 18.0)
        self.assertEqual(saved["mastery_zh2en"], 0.0)

    def test_quiz_prefers_incomplete_direction(self):
        for index in range(6):
            terms.add_term(f"Q{index}", meaning=f"方向{index}", paper_id="p")
        row = next(item for item in terms.list_terms() if item["term"] == "Q0")
        for _ in range(3):
            row = terms.answer_quiz(row["id"], True, direction="term2meaning")
        self.assertEqual(row["mastery_en2zh"], 100.0)
        self.assertEqual(row["mastery_zh2en"], 0.0)
        db.execute("UPDATE terms SET due_at='' WHERE id=?", (row["id"],))
        items = terms.quiz(limit=8, mode="mixed")
        target = next(item for item in items if item["term"] == "Q0")
        self.assertEqual(target["direction"], "meaning2term", "还没练过的方向优先")

    def test_unknown_answer_records_and_resets(self):
        row = terms.add_term("OPD", expansion="optical path difference", meaning="光程差", paper_id="p")
        terms.answer_quiz(row["id"], True)
        result = terms.answer_quiz(row["id"], False, unknown=True)
        self.assertLess(result["mastery"], 70.0)
        self.assertEqual(result["step"], 0)
        self.assertEqual(result["lapses"], 1)
        import json
        history = json.loads(result["history_json"])
        self.assertTrue(history[-1]["unknown"])
        self.assertFalse(history[-1]["correct"])

    def test_unknown_api(self):
        client = self._client()
        created = client.post("/api/terms", json={"term": "PSF", "meaning": "点扩散函数"}).json()
        term_id = created["item"]["id"]
        r = client.post(f"/api/terms/{term_id}/answer", json={"correct": False, "unknown": True})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["item"]["lapses"], 1)

    def test_api_roundtrip(self):
        client = self._client()
        created = client.post("/api/terms", json={"term": "PCE", "expansion": "power conversion efficiency", "paper_id": "p"})
        self.assertEqual(created.status_code, 200, created.text)
        term_id = created.json()["item"]["id"]
        listed = client.get("/api/terms").json()
        self.assertEqual(len(listed["items"]), 1)
        self.assertEqual(listed["stats"]["due"], 0, "无干扰项时不能出题")
        quiz = client.get("/api/terms/quiz", params={"limit": 5})
        self.assertEqual(quiz.status_code, 200)
        answered = client.post(f"/api/terms/{term_id}/answer", json={"correct": True})
        self.assertEqual(answered.status_code, 200)
        self.assertEqual(answered.json()["item"]["step"], 1)
        deleted = client.delete(f"/api/terms/{term_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(client.get("/api/terms").json()["items"], [])
        self.assertEqual(client.post("/api/terms", json={"term": " "}).status_code, 400)


if __name__ == "__main__":
    unittest.main()
