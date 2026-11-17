"""Source-backed discovery: isolated model/HTTP fixtures, never live searches."""
from __future__ import annotations

import json
import threading
import unittest
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch

import httpx

from paperwhale_server import llm
from paperwhale_server.services import ai_progress, discovery_sources as sources
from paperwhale_server.services import research_discovery as discovery


def record(title='Real optics paper', doi='10.1234/real', year='2026', **extra):
    return dict(title=title, authors='A. Author', year=year, published=year+'-10-01',
                venue='Journal', doi=doi, arxiv_id='', url='https://doi.org/'+doi,
                abstract='An abstract about optics.', sources=[{'name':'Crossref', 'url':'https://doi.org/'+doi}], **extra)


class SourceTests(unittest.TestCase):
    def setUp(self):
        provider = patch.object(sources, 'search_europepmc', return_value=[])
        provider.start()
        self.addCleanup(provider.stop)

    def response(self, data):
        return httpx.Response(200, json=data, request=httpx.Request('GET', 'https://api.crossref.org/works'))

    def test_crossref_dates_identifiers_and_jats_are_normalized(self):
        payload = {'message':{'items':[{'title':['<b>Real &amp; useful</b>'],
            'DOI':'https://doi.org/10.1234/ABC', 'URL':'javascript:bad',
            'author':[{'given':'A', 'family':'Author'}], 'container-title':['Journal'],
            'published':{'date-parts':[[2026, 10, 1]]}, 'abstract':'<jats:p>Real abstract</jats:p>'}]}}
        with patch.object(sources.httpx, 'get', return_value=self.response(payload)) as get:
            rows = sources.search_crossref('optics', '2026-01-01', '2026-10-28', latest=True)
        self.assertEqual(rows[0]['doi'], '10.1234/abc')
        self.assertEqual(rows[0]['published'], '2026-10-01')
        self.assertEqual(rows[0]['title'], 'Real & useful')
        self.assertEqual(rows[0]['abstract'], 'Real abstract')
        self.assertEqual(rows[0]['url'], 'https://doi.org/10.1234/abc')
        self.assertIn('from-pub-date:2026-01-01', get.call_args.kwargs['params']['filter'])
        self.assertEqual(get.call_args.kwargs['params']['sort'], 'published')
        self.assertLessEqual(get.call_args.kwargs['timeout'], 15)

    def test_openalex_abstract_and_source_url(self):
        payload = {'results':[{'id':'https://openalex.org/W123', 'display_name':'Real work',
            'publication_date':'2025-02-03', 'publication_year':2025,
            'abstract_inverted_index':{'Real':[0], 'abstract':[1]},
            'authorships':[{'author':{'display_name':'B Author'}}],
            'primary_location':{'landing_page_url':'https://example.org/paper', 'source':{'display_name':'Venue'}}}]}
        with patch.object(sources.httpx, 'get', return_value=self.response(payload)) as get:
            rows = sources.search_openalex('optics', '2025-01-01', '2026-10-28')
        self.assertEqual(rows[0]['abstract'], 'Real abstract')
        self.assertEqual(rows[0]['url'], 'https://example.org/paper')
        self.assertEqual(rows[0]['sources'][0]['url'], 'https://openalex.org/W123')
        self.assertIn('from_publication_date:2025-01-01', get.call_args.kwargs['params']['filter'])
        self.assertNotIn('api_key', get.call_args.kwargs['params'])

    def test_arxiv_atom_and_submitted_date_query(self):
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2610.01234v2</id><title>Real\n optics</title><published>2026-10-01T00:00:00Z</published><summary>Real abstract</summary><author><name>A Author</name></author></entry></feed>'''
        response = httpx.Response(200, text=xml, request=httpx.Request('GET', sources.ARXIV_API))
        with patch.object(sources.httpx, 'get', return_value=response) as get, patch.object(sources, '_wait_arxiv_slot'):
            rows = sources.search_arxiv('optics', '2026-01-01', '2026-10-28', latest=True)
        self.assertEqual(rows[0]['arxiv_id'], '2610.01234')
        self.assertEqual(rows[0]['title'], 'Real optics')
        self.assertIn('submittedDate:[202601010000 TO 202610282359]', get.call_args.kwargs['params']['search_query'])

    def test_dedup_merges_sources_by_doi_arxiv_and_unicode_title(self):
        one = record()
        two = {**record('Alternate title'), 'doi':'https://doi.org/10.1234/REAL', 'sources':[{'name':'OpenAlex', 'url':'https://openalex.org/W1'}]}
        three = {**record('Real optics paper', doi=''), 'arxiv_id':'2610.01234v2', 'sources':one['sources']}
        four = {**record('Fourth title', doi=''), 'arxiv_id':'2610.01234', 'sources':one['sources']}
        rows = sources.deduplicate([one,two,three,four])
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]['sources']), 2)
        self.assertEqual(rows[0]['arxiv_id'], '2610.01234')

    def test_collect_keeps_partial_results_and_filters_known_old_dates(self):
        with patch.object(sources, 'search_crossref', return_value=[record(), record('Old', '10.1234/old', '2020')]), \
             patch.object(sources, 'search_openalex', side_effect=httpx.ConnectError('offline')), \
             patch.object(sources, 'search_arxiv', return_value=[]):
            rows, warnings = sources.collect(['optics'], '2026-01-01', '2026-10-28', latest=True)
        self.assertEqual([r['title'] for r in rows], ['Real optics paper'])
        self.assertTrue(any('OpenAlex' in warning for warning in warnings))

    def test_collect_bounds_queries_arxiv_calls_and_results(self):
        many = [record('Paper '+str(i), '10.1234/'+str(i)) for i in range(50)]
        with patch.object(sources, 'search_crossref', return_value=many) as crossref, \
             patch.object(sources, 'search_openalex', return_value=[]), \
             patch.object(sources, 'search_arxiv', return_value=[]) as arxiv:
            rows, _ = sources.collect(['one','two','three','four'], '', '')
        self.assertLessEqual(len(rows), 30)
        self.assertEqual(crossref.call_count, 3)
        self.assertEqual(arxiv.call_count, 1)

    def test_search_preserves_provider_relevance_instead_of_oldest_first(self):
        newer = record('Exact relevant match', '10.1234/new', '2026')
        older = record('Loosely related old paper', '10.1234/old', '1950')
        with patch.object(sources, 'search_crossref', return_value=[newer,older]), \
             patch.object(sources, 'search_openalex', return_value=[]), \
             patch.object(sources, 'search_arxiv', return_value=[]):
            rows, _ = sources.collect(['optics'])
        self.assertEqual([r['title'] for r in rows], [newer['title'],older['title']])

    def test_search_round_robins_source_and_query_lists_before_cap(self):
        def provider(name):
            return lambda query,*args,**kwargs: [record(f'{name}-{query}-{i}',f'10.1234/{name}-{query}-{i}','2020') for i in range(8)]
        arxiv = record('Unique arXiv match','10.1234/arxiv','2026')
        with patch.object(sources, 'search_crossref', side_effect=provider('C')), \
             patch.object(sources, 'search_openalex', side_effect=provider('O')), \
             patch.object(sources, 'search_arxiv', return_value=[arxiv]):
            rows, _ = sources.collect(['one','two','three'])
        self.assertEqual(len(rows),30)
        self.assertEqual([r['title'] for r in rows[:7]],
                         ['C-one-0','O-one-0','C-two-0','O-two-0','C-three-0','O-three-0','Unique arXiv match'])

    def test_timeline_retains_full_date_span_and_chronological_representatives(self):
        early = [record(f'Early {year}',f'10.1234/{year}',str(year)) for year in range(1900,1935)]
        later = [record(f'Later {year}',f'10.1234/{year}',str(year)) for year in range(1990,2025)]
        newest = record('Newest stage','10.1234/newest','2026')
        with patch.object(sources, 'search_crossref', return_value=early), \
             patch.object(sources, 'search_openalex', return_value=later), \
             patch.object(sources, 'search_arxiv', return_value=[newest]):
            rows, _ = sources.collect(['optics'], timeline=True)
        years = [r['year'] for r in rows]
        self.assertEqual(len(rows),30)
        self.assertEqual(years, sorted(years))
        self.assertEqual((years[0],years[-1]),('1900','2026'))
        self.assertTrue(any('1920' <= year <= '1934' for year in years))
        self.assertTrue(any('2000' <= year <= '2024' for year in years))

    def test_explicit_dates_exclude_unknown_dates_but_warn_on_year_only(self):
        unknown = {**record('Unknown date','10.1234/unknown'), 'published':'','year':''}
        year_only = {**record('Year only','10.1234/year'), 'published':'','year':'2026'}
        with patch.object(sources, 'search_crossref', return_value=[unknown,year_only]), \
             patch.object(sources, 'search_openalex', return_value=[]), \
             patch.object(sources, 'search_arxiv', return_value=[]):
            rows, warnings = sources.collect(['optics'],'2026-07-01','2026-10-28',latest=True)
        self.assertEqual([r['title'] for r in rows], ['Year only'])
        self.assertTrue(any('排除' in w for w in warnings))
        self.assertTrue(any('年份' in w for w in warnings))

    def test_conflicting_dois_do_not_merge_through_generic_title_or_identifierless_bridge(self):
        one = record('Editorial','10.1234/one')
        two = record('Editorial','10.1234/two')
        bridge = record('Editorial','')
        rows = sources.deduplicate([one,two,bridge])
        self.assertEqual(len(rows),2)
        self.assertEqual({r['doi'] for r in rows},{'10.1234/one','10.1234/two'})
        one['arxiv_id'] = two['arxiv_id'] = '2610.01234'
        self.assertEqual(len(sources.deduplicate([one,two])),1)

    def test_arxiv_live_requests_have_three_second_spacing(self):
        with patch.object(sources.time, 'monotonic', side_effect=[21, 23]), \
             patch.object(sources.time, 'sleep') as sleep, patch.object(sources, '_arxiv_last_call', 20):
            sources._wait_arxiv_slot()
        sleep.assert_called_once_with(2)

    def test_collector_cancellation_is_checked_in_callers_progress_context(self):
        token = ai_progress.begin('discovery:sources', 'conversation', 1)
        started = threading.Event()
        def source(*args, **kwargs):
            started.set()
            ai_progress.cancel('discovery:sources')
            return [record()]
        try:
            with patch.object(sources, 'search_crossref', side_effect=source), \
                 patch.object(sources, 'search_openalex', return_value=[]), \
                 patch.object(sources, 'search_arxiv', return_value=[]):
                with self.assertRaisesRegex(llm.LLMError, '停止'):
                    sources.collect(['optics'])
            self.assertTrue(started.is_set())
        finally:
            ai_progress.finish(token)

    def test_all_http_sources_fail_with_warnings_without_inventing_records(self):
        with patch.object(sources, 'search_crossref', side_effect=httpx.ConnectError('offline')), \
             patch.object(sources, 'search_openalex', side_effect=httpx.ConnectError('offline')), \
             patch.object(sources, 'search_arxiv', side_effect=ValueError('invalid XML')), \
             patch.object(sources, 'search_europepmc', side_effect=httpx.ConnectError('offline')):
            rows, warnings = sources.collect(['optics'])
        self.assertEqual(rows, [])
        self.assertEqual(len(warnings), 4)
        self.assertTrue(all('查询失败' in warning for warning in warnings))

    def test_unsafe_urls_and_absent_titles_are_rejected(self):
        self.assertEqual(sources.safe_url('javascript:alert(1)'), '')
        self.assertEqual(sources.safe_url('https://user:secret@example.org'), '')
        self.assertEqual(sources.safe_url('https://:secret@example.org'), '')
        self.assertEqual(sources.safe_url('https://example.org/\nattack'), '')
        self.assertIsNone(sources._normalise({'title':'Real title','url':'javascript:bad'}, 'Crossref'))
        self.assertIsNone(sources._normalise({'url':'https://example.org'}, 'Crossref'))


class ResearchTests(unittest.TestCase):
    def test_named_paper_followup_uses_publication_and_preserves_verified_citations(self):
        seed = record('Brownian spin-locking effect', '10.1038/s41563-025-02413-5', '2025')
        seed['published'] = '2025-11-18'
        direct = record('A verified citation', '10.1234/citation', relation='direct')
        self.selection['items'] = [{'index':2, 'reason':'同方向'}]
        with patch.object(sources, 'resolve_seed', return_value=([seed], [])) as lookup:
            result, collect = self.run_research('Brownian spin-locking effect这篇论文之后的一些相关研究',
                                                [direct, record()])
        lookup.assert_called_once_with(seed['title'])
        self.assertEqual((result['intent'], result['date_from']), ('followup', '2025-11-18'))
        self.assertEqual(result['tracking']['seed']['doi'], seed['doi'])
        self.assertEqual(collect.call_args.kwargs['seed'], seed)
        self.assertEqual([row['title'] for row in result['items']], [direct['title'], 'Real optics paper'])

    def test_ambiguous_title_requires_selection_before_search(self):
        candidates = [record('Shared title', '10.1234/one'), record('Shared title', '10.1234/two')]
        with patch.object(sources, 'resolve_seed', return_value=(candidates, [])):
            result, collect = self.run_research('Shared title这篇论文之后的研究')
        collect.assert_not_called()
        self.assertEqual(result['tracking']['choices'], candidates)

    def test_attachment_reading_and_seed_resolution_use_bounded_excerpts(self):
        files = [{'name':'paper.txt', 'text':'Brownian spin-locking effect\n'+'x'*30000}]
        self.plan = {'answer':'依据 paper.txt 的节选。'}
        with patch.object(discovery.llm, 'chat', side_effect=self.reply), patch.object(sources, 'collect') as collect:
            result = discovery.research('概括这份附件', [], '', materials=files, options={'mode':'file'})
        collect.assert_not_called()
        self.assertEqual(result['intent'], 'file')
        data = json.loads(self.messages[0][0][-1]['content'])
        self.assertLessEqual(len(data['file_excerpts'][0]['text']), 4500)
        self.messages.clear()
        self.plan = {'intent':'followup', 'seed':'Brownian spin-locking effect', 'queries':['light scattering']}
        with patch.object(discovery.llm, 'chat', side_effect=self.reply), \
             patch.object(sources, 'resolve_seed', return_value=([], [])) as lookup:
            discovery.research('附件这篇论文之后的研究', [], '', materials=files)
        lookup.assert_called_once_with(self.plan['seed'])

    def test_unidentified_new_attachment_does_not_reuse_old_original(self):
        files = [{'name':'new.pdf','text':'Excerpt without an identifiable title'}]
        with patch.object(discovery.llm, 'chat', side_effect=self.reply), \
             patch.object(sources, 'resolve_seed') as lookup:
            result = discovery.research('追踪附件里的这篇论文之后的研究', [], '', materials=files,
                                        previous_seed='10.1234/previous')
        lookup.assert_not_called()
        self.assertIn('未能确认原文', result['answer'])

    def test_chinese_explicit_year_is_not_replaced_by_latest_default(self):
        from datetime import date
        self.assertEqual(discovery._bounds({'date_from':'2024-01-01'},'整理2024年以后的最新进展',date(2026,10,28),'latest'),('2024-01-01','2026-10-28'))

    def setUp(self):
        self.plan = {'intent':'latest', 'topic':'偏振成像', 'queries':['polarization imaging','偏振成像']}
        self.selection = {'items':[{'index':1,'reason':'标题与偏振成像相关','stage':'近期探索'}],
                          'answer':'FABRICATED DOI https://fake.invalid [99]'}
        self.messages = []

    def reply(self, messages, **kwargs):
        self.messages.append((messages, kwargs))
        value = self.plan if len(self.messages) == 1 else self.selection
        return {'content':json.dumps(value, ensure_ascii=False), 'reasoning':'Actual analysis', 'model':'fixture'}

    def run_research(self, question='最近偏振成像有什么进展？', rows=None, warnings=None, history=None):
        with patch.object(discovery.llm, 'chat', side_effect=self.reply), \
             patch.object(discovery.sources, 'collect', return_value=(rows if rows is not None else [record()], warnings or [])) as collect, \
             patch.object(discovery, '_today', return_value=date(2026,10,28)):
            result = discovery.research(question, history or [], '研究偏振成像')
        return result, collect

    def test_chinese_plan_uses_topic_history_profile_and_explicit_current_bounds(self):
        result, collect = self.run_research(history=[{'role':'user','content':'上一轮偏振问题'}])
        self.assertEqual(result['intent'], 'latest')
        self.assertEqual(result['date_from'], '2025-10-28')
        self.assertEqual(result['date_to'], '2026-10-28')
        self.assertEqual(result['queries'], self.plan['queries'])
        self.assertIn('上一轮偏振问题', str(self.messages[0][0]))
        self.assertIn('研究偏振成像', str(self.messages[0][0]))
        self.assertTrue(all(kwargs['json_mode'] and kwargs['deep'] and kwargs['with_meta'] and kwargs['progress_channel']=='structured' for _, kwargs in self.messages))
        self.assertIn('2025-10-28', result['answer'])
        self.assertEqual(collect.call_args.args[0], self.plan['queries'])

    def test_recent_three_months_and_bounded_queries(self):
        self.plan['queries'] = ['one','two','three','four','one']
        result, _ = self.run_research('最近三个月偏振成像进展')
        self.assertEqual(len(result['queries']), 3)
        self.assertEqual(result['date_from'], '2026-07-28')

    def test_model_can_only_select_source_indices_never_replace_metadata(self):
        self.selection['items'] = [{'index':2, 'title':'FAKE', 'url':'https://fake.invalid','doi':'10.9999/fake', 'reason':'相关', 'stage':'第二阶段'}, {'index':99}]
        result, _ = self.run_research(rows=[record(), record('Second real paper','10.1234/second')])
        self.assertEqual([r['title'] for r in result['items']], ['Second real paper'])
        self.assertEqual(result['items'][0]['citation_index'], 1)
        self.assertEqual(result['items'][0]['doi'], '10.1234/second')
        self.assertNotIn('fake.invalid', result['answer'])
        self.assertNotIn('[99]', result['answer'])
        self.assertIn('[1]', result['answer'])

    def test_timeline_stage_summary_cites_only_returned_items(self):
        self.plan['intent'] = 'timeline'
        self.selection['items'] = [{'index':2,'stage':'后续探索','reason':'摘要显示关注成像'}, {'index':1,'stage':'早期方法','reason':'标题相关'}]
        result, collect = self.run_research('梳理偏振成像的发展脉络', [record('Earlier','10.1234/one','2020'),record('Later','10.1234/two')])
        self.assertTrue(collect.call_args.kwargs['timeline'])
        self.assertEqual(result['intent'], 'timeline')
        self.assertIn('早期方法', result['answer'])
        self.assertIn('后续探索', result['answer'])
        self.assertIn('[1]', result['answer'])
        self.assertIn('[2]', result['answer'])
        self.assertIn('元数据', result['answer'])

    def test_current_search_date_uses_shanghai_even_when_utc_is_previous_day(self):
        instant = datetime(2026,10,28,18,0,tzinfo=timezone.utc)
        with patch.object(discovery,'datetime') as clock:
            clock.now.side_effect = lambda zone: instant.astimezone(zone)
            self.assertEqual(discovery._today(),date(2026,10,29))

    def test_empty_or_all_failed_does_not_call_synthesis_or_fabricate_success(self):
        result, _ = self.run_research(rows=[], warnings=['Crossref 查询失败'])
        self.assertEqual(result['items'], [])
        self.assertEqual(len(self.messages), 1)
        self.assertIn('未检索到', result['answer'])
        self.assertEqual(result['warnings'], ['Crossref 查询失败'])

    def test_empty_selection_preserves_no_relevance_conclusion(self):
        self.selection['items'] = []
        result, _ = self.run_research()
        self.assertEqual(result['items'], [])
        self.assertIn('没有选出', result['answer'])

    def test_cancel_before_work_or_after_synthesis_never_returns_candidates(self):
        with patch.object(discovery.ai_progress, 'cancelled', return_value=True), patch.object(discovery.llm, 'chat') as chat:
            with self.assertRaisesRegex(llm.LLMError, '停止'):
                discovery.research('search', [], '')
            chat.assert_not_called()
        token = ai_progress.begin('discovery:test', 'conversation', 1)
        try:
            def cancelled_reply(messages, **kwargs):
                result = self.reply(messages, **kwargs)
                if len(self.messages) == 2:
                    ai_progress.cancel('discovery:test')
                return result
            with patch.object(discovery.llm, 'chat', side_effect=cancelled_reply), patch.object(discovery.sources, 'collect', return_value=([record()], [])):
                with self.assertRaisesRegex(llm.LLMError, '停止'):
                    discovery.research('search', [], '')
        finally:
            ai_progress.finish(token)

    def test_model_annotations_cannot_inject_links_or_citation_indices(self):
        self.selection['items'] = [{'index':1,'reason':'See https://fake.invalid [99] <script>bad</script>', 'stage':'[77] https://fake.invalid'}]
        result, _ = self.run_research()
        self.assertNotIn('fake.invalid', result['answer'])
        self.assertNotIn('[99]', result['answer'])
        self.assertNotIn('[77]', result['answer'])

    def test_specific_search_keeps_unbounded_dates_and_real_exact_metadata(self):
        self.plan = {'intent':'search','topic':'specific paper','queries':['10.1234/real']}
        result, collect = self.run_research('找这篇 DOI 10.1234/real')
        self.assertEqual((result['date_from'], result['date_to']), ('',''))
        self.assertFalse(collect.call_args.kwargs['latest'])
        self.assertEqual(result['items'][0]['authors'], 'A. Author')
        self.assertEqual(result['items'][0]['url'], 'https://doi.org/10.1234/real')

    def test_synthesis_failure_preserves_fetched_records_and_truthful_warning(self):
        def reply(messages, **kwargs):
            if self.messages:
                raise llm.LLMError('fixture unavailable')
            return self.reply(messages, **kwargs)
        with patch.object(discovery.llm, 'chat', side_effect=reply), \
             patch.object(discovery.sources, 'collect', return_value=([record()], [])):
            result = discovery.research('最近偏振成像进展', [], '')
        self.assertEqual(result['items'][0]['title'], 'Real optics paper')
        self.assertIn('筛选', ' '.join(result['warnings']))
        self.assertIn('未读取全文', result['answer'])

    def test_http_to_model_pipeline_deduplicates_real_provider_records(self):
        self.plan['queries'] = ['polarization imaging']
        crossref = {'message':{'items':[{'title':['Real optics paper'], 'DOI':'10.1234/real',
            'published':{'date-parts':[[2026,10,1]]}, 'author':[{'given':'A.','family':'Author'}]}]}}
        openalex = {'results':[{'display_name':'Real optics paper','doi':'https://doi.org/10.1234/REAL',
            'id':'https://openalex.org/W1','publication_year':2026,'publication_date':'2026-10-01'}]}
        def get(url, **kwargs):
            request = httpx.Request('GET', url)
            if url == sources.CROSSREF_API:
                return httpx.Response(200,json=crossref,request=request)
            if url == sources.OPENALEX_API:
                return httpx.Response(200,json=openalex,request=request)
            if url == sources.EUROPEPMC_API:
                return httpx.Response(200,json={'resultList':{'result':[]}},request=request)
            return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"/>',request=request)
        with patch.object(discovery.llm, 'chat', side_effect=self.reply), \
             patch.object(sources.httpx, 'get', side_effect=get) as http, \
             patch.object(sources, '_wait_arxiv_slot'), \
             patch.object(discovery, '_today', return_value=date(2026,10,28)):
            result = discovery.research('最新偏振成像论文', [], '')
        self.assertEqual(http.call_count, 4)
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(len(result['items'][0]['sources']), 2)
        self.assertEqual(result['items'][0]['doi'], '10.1234/real')

    def test_long_history_profile_and_record_briefs_have_explicit_context_budgets(self):
        history = []
        for index in range(6):
            history.extend([{'role':'user','content':f'TOPIC_{index} '+('问'*20000)},
                            {'role':'assistant','content':('Details '*20000)+f'\n[1] ASSISTANT_TITLE_{index}\n'}])
        history[-2]['content'] = 'LATEST_USER_TOPIC '+('问'*20000)
        rows = [{**record(f'Real title {index}',f'10.1234/{index}'),
                 'abstract':'Full scientific abstract '+('abstract detail '*500),
                 'authors':'Author '*500, 'venue':'Venue '*200} for index in range(30)]
        self.selection['items'] = [{'index':1,'reason':'Title match'}, {'index':30,'reason':'Another title match'}]
        with patch.object(discovery.llm,'chat',side_effect=self.reply), \
             patch.object(discovery.sources,'collect',return_value=(rows,[])):
            result = discovery.research('Find these papers',history,'PROFILE '+('画像'*6000))
        planning = json.loads(self.messages[0][0][1]['content'])
        ranking = json.loads(self.messages[1][0][1]['content'])
        for payload in (planning,ranking):
            self.assertLessEqual(len(json.dumps(payload['history'],ensure_ascii=False)),8000)
            self.assertLessEqual(len(payload['profile']),4000)
            self.assertTrue(payload['history_is_excerpt'])
            self.assertIn('LATEST_USER_TOPIC',str(payload['history']))
            self.assertIn('ASSISTANT_TITLE_5',str(payload['history']))
        self.assertLessEqual(len(json.dumps(ranking['records'],ensure_ascii=False)),24000)
        self.assertEqual([row['index'] for row in ranking['records']],list(range(1,31)))
        self.assertEqual([row['title'] for row in ranking['records']],[row['title'] for row in rows])
        self.assertTrue(ranking['records_are_excerpts'])
        self.assertEqual(result['items'][0]['abstract'],rows[0]['abstract'])
        self.assertEqual(result['items'][1]['abstract'],rows[29]['abstract'])
        self.assertGreater(len(rows[0]['abstract']),6000)

    def test_large_last_history_exchange_still_keeps_latest_question_and_topic(self):
        history = [{'role':'user','content':'LATEST_POLARIZATION_TOPIC '+('q'*100000)},
                   {'role':'assistant','content':('a'*100000)+'\n[1] Latest known paper title'}]
        self.run_research(history=history)
        payload = json.loads(self.messages[0][0][1]['content'])
        self.assertLessEqual(len(json.dumps(payload['history'],ensure_ascii=False)),8000)
        self.assertIn('LATEST_POLARIZATION_TOPIC',str(payload['history']))
        self.assertIn('Latest known paper title',str(payload['history']))

    def test_record_brief_budget_handles_long_escaped_titles_without_changing_output(self):
        rows = [{**record(f'Paper {index} '+('\\"'*500),f'10.1234/{index}'),
                 'abstract':'Abstract '*1000,'authors':'Author '*500} for index in range(30)]
        result, _ = self.run_research(rows=rows)
        payload = json.loads(self.messages[1][0][1]['content'])
        self.assertLessEqual(len(json.dumps(payload['records'],ensure_ascii=False)),24000)
        self.assertEqual(len(payload['records']),30)
        self.assertTrue(all(row['title'].startswith(f'Paper {index} ') for index,row in enumerate(payload['records'])))
        self.assertEqual(result['items'][0]['title'],rows[0]['title'])


if __name__ == '__main__':
    unittest.main()
