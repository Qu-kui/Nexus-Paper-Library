"""Public metadata and citation fixtures; no live network or user data."""
from contextlib import ExitStack
import unittest
from unittest.mock import patch

import httpx

from paperwhale_server import llm
from paperwhale_server.services import ai_progress, discovery_sources as sources


def response(data, url=sources.OPENALEX_API):
    return httpx.Response(200, json=data, request=httpx.Request('GET', url))


def work(identifier='W1', title='Seed optics', year=2020, references=None, doi='10.1234/seed'):
    return {'id':'https://openalex.org/'+identifier, 'display_name':title,
            'publication_year':year, 'publication_date':f'{year}-02-03',
            'doi':'https://doi.org/'+doi if doi else '', 'referenced_works':references or [],
            'abstract_inverted_index':{'Observed':[0], 'metadata':[1]}}


class ExtendedSourceTests(unittest.TestCase):
    def empty_other_sources(self):
        stack = ExitStack()
        for name in ('search_crossref', 'search_arxiv', 'search_europepmc'):
            stack.enter_context(patch.object(sources, name, return_value=[]))
        self.addCleanup(stack.close)
        return stack

    def test_europepmc_core_has_real_identifiers_abstract_and_precise_date(self):
        payload = {'resultList':{'result':[
            {'id':'12345', 'source':'MED', 'title':'<i>Optical</i> &amp; imaging',
             'doi':'https://doi.org/10.1234/ABC', 'authorString':'A Author, B Author',
             'pubYear':'2025', 'firstPublicationDate':'2025-04-07',
             'abstractText':'<jats:p>Actual &amp; abstract</jats:p>',
             'journalInfo':{'journal':{'title':'A Journal'}}},
            {'id':'PMC123', 'source':'PMC', 'title':'Full text source', 'pubYear':'2024'},
            {'id':'', 'source':'MED', 'title':'DOI fallback', 'doi':'10.1234/fallback'},
            {'id':'https://evil.test', 'source':'MED', 'title':'No valid identifier'},
            {'id':'1', 'source':'MED'}, None]}}
        with patch.object(sources.httpx, 'get', return_value=response(payload, sources.EUROPEPMC_API)) as get:
            rows = sources.search_europepmc('optical imaging', '2024-01-01', '2026-10-28', latest=True)
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0]['title'], 'Optical & imaging')
        self.assertEqual(rows[0]['abstract'], 'Actual & abstract')
        self.assertEqual(rows[0]['doi'], '10.1234/abc')
        self.assertEqual(rows[0]['published'], '2025-04-07')
        self.assertEqual(rows[0]['venue'], 'A Journal')
        self.assertEqual(rows[0]['url'], 'https://europepmc.org/article/MED/12345')
        self.assertEqual(rows[0]['sources'], [{'name':'Europe PMC','url':rows[0]['url']}])
        self.assertEqual(rows[2]['url'], 'https://doi.org/10.1234/fallback')
        args = get.call_args.kwargs
        self.assertEqual(args['params']['resultType'], 'core')
        self.assertEqual(args['params']['pageSize'], 12)
        self.assertIn('FIRST_PDATE:[2024-01-01 TO 2026-10-28]', args['params']['query'])
        self.assertTrue(args['params']['query'].endswith('sort_date:y'))
        self.assertNotIn('api_key', args['params'])
        self.assertLessEqual(args['timeout'], 12)

    def test_europepmc_relevance_and_malformed_payloads(self):
        with patch.object(sources.httpx, 'get', return_value=response({'resultList':{'result':[]}})) as get:
            self.assertEqual(sources.search_europepmc('optics'), [])
            self.assertNotIn('sort_date:y', get.call_args.kwargs['params']['query'])
            self.assertEqual(sources.search_europepmc('!!!'), [])
            self.assertEqual(get.call_count, 1)
        with patch.object(sources.httpx, 'get', return_value=response({'resultList':{'result':{}}})):
            with self.assertRaises(ValueError):
                sources.search_europepmc('optics')

    def test_collector_adds_one_pmc_job_preserves_order_dedup_and_dates(self):
        seed = sources._openalex_rows({'results':[work()]})[0]
        other = {**seed, 'doi':'10.1234/other', 'published':'2026-01-01', 'year':'2026'}
        duplicate = {**seed, 'sources':[{'name':'Europe PMC','url':'https://europepmc.org/article/MED/123'}]}
        old = {**seed,'title':'Old','doi':'10.1234/old','year':'1900','published':'1900-01-01'}
        with patch.object(sources, 'search_crossref', return_value=[seed]), \
             patch.object(sources, 'search_openalex', return_value=[]), \
             patch.object(sources, 'search_arxiv', return_value=[]), \
             patch.object(sources, 'search_europepmc', return_value=[duplicate, other, old]) as pmc:
            rows, _ = sources.collect(['one','two','three','four'], '2020-01-01','2026-10-28')
        self.assertEqual(pmc.call_count, 1)
        self.assertEqual(pmc.call_args.args[0], 'one')
        self.assertEqual([row['doi'] for row in rows], ['10.1234/seed','10.1234/other'])
        self.assertEqual(len(rows[0]['sources']), 2)
        self.assertTrue(all('_openalex_id' not in row for row in rows))

    def graph_fixture(self, *, fail_backward=False, extra_forward=None):
        calls = []
        seed = work(references=['https://openalex.org/W2','W3','https://evil.test/W4','W4|cites:W9'])
        def get(url, **kwargs):
            params = kwargs['params']
            calls.append((params, kwargs['timeout']))
            if params.get('search'):
                return response({'results':[seed]})
            expression = params.get('filter','')
            if expression.startswith('openalex:'):
                if fail_backward:
                    raise httpx.ConnectError('sensitive endpoint text should not appear')
                return response({'results':[work('W2','Earlier',1990,doi='10.1234/earlier'),
                                             work('W999','Unrelated',1980,doi='10.1234/unrelated')]})
            if expression.startswith('cites:'):
                return response({'results':extra_forward or [work('W4','Later',2026,doi='10.1234/later')]})
            raise AssertionError(params)
        return get, calls

    def test_timeline_fetches_actual_edges_only_two_calls_with_provenance(self):
        self.empty_other_sources()
        get, calls = self.graph_fixture()
        with patch.object(sources.httpx,'get',side_effect=get):
            rows, warnings = sources.collect(['optics'], timeline=True)
        self.assertEqual(warnings, [])
        self.assertEqual([row['title'] for row in rows], ['Earlier','Seed optics','Later'])
        self.assertEqual(len(calls), 3)
        filters = [params.get('filter','') for params, _ in calls]
        self.assertIn('openalex:W2|W3', filters)
        self.assertIn('cites:W1', filters)
        self.assertEqual(rows[0]['citation_graph'], [{'source':'OpenAlex', 'citing':'https://openalex.org/W1',
                         'cited':'https://openalex.org/W2','evidence':'referenced_works'}])
        self.assertEqual(rows[-1]['citation_graph'][0]['evidence'], 'cites_filter')
        self.assertEqual(rows[-1]['citation_graph'][0]['cited'], 'https://openalex.org/W1')
        self.assertTrue(all(row['sources'][0]['name']=='OpenAlex' for row in rows))
        self.assertTrue(all('_referenced_works' not in row for row in rows))
        self.assertNotIn('Unrelated', str(rows))

    def test_all_adapters_share_eight_search_and_two_graph_request_cap(self):
        get_graph, calls = self.graph_fixture()
        def get(url, **kwargs):
            if url == sources.OPENALEX_API:
                return get_graph(url, **kwargs)
            if url == sources.CROSSREF_API:
                return response({'message':{'items':[]}}, url)
            if url == sources.EUROPEPMC_API:
                return response({'resultList':{'result':[]}}, url)
            if url == sources.ARXIV_API:
                return httpx.Response(200,text='<feed xmlns="http://www.w3.org/2005/Atom"/>',
                                      request=httpx.Request('GET',url))
            raise AssertionError(url)
        with patch.object(sources.httpx,'get',side_effect=get) as http, patch.object(sources,'_wait_arxiv_slot'):
            rows, warnings = sources.collect(['one','two','three','four','one'],timeline=True)
        self.assertEqual(http.call_count,10)
        self.assertEqual(len(calls),5)  # Three OpenAlex keyword calls and two graph calls.
        self.assertEqual(len(rows),3)
        self.assertEqual(warnings,[])

    def test_reference_batch_is_limited_to_twelve_validated_returned_ids(self):
        seed = sources._openalex_rows({'results':[work(references=['W'+str(i) for i in range(2,102)])]})
        jobs = sources._citation_jobs(seed,'','',123)
        self.assertEqual(len(jobs),2)
        self.assertEqual(jobs[0][2][0],'openalex:'+'|'.join('W'+str(i) for i in range(2,14)))
        self.assertEqual(jobs[1][2][0],'cites:W1')

    def test_normal_search_and_invalid_seed_do_not_expand_graph(self):
        self.empty_other_sources()
        for timeline, identifier in [(False,'W1'), (True,'W1?filter=cites:W2'), (True,'../W1')]:
            with self.subTest(timeline=timeline, identifier=identifier):
                payload = {'results':[work(identifier, references=['W2'])]}
                with patch.object(sources.httpx,'get',return_value=response(payload)) as get:
                    sources.collect(['optics'], timeline=timeline)
                self.assertEqual(get.call_count, 1)

    def test_graph_failure_preserves_seed_and_other_direction_without_private_text(self):
        self.empty_other_sources()
        get, calls = self.graph_fixture(fail_backward=True)
        with patch.object(sources.httpx,'get',side_effect=get):
            rows, warnings = sources.collect(['optics'], timeline=True)
        self.assertEqual([row['title'] for row in rows], ['Seed optics','Later'])
        self.assertEqual(len(calls),3)
        self.assertTrue(any('后向参考文献' in warning for warning in warnings))
        self.assertNotIn('sensitive', str(warnings))

    def test_graph_dates_conflicting_doi_dedup_and_thirty_record_cap(self):
        self.empty_other_sources()
        matching_title = work('W5','Seed optics',2026,doi='10.1234/distinct')
        get, calls = self.graph_fixture(extra_forward=[matching_title])
        with patch.object(sources.httpx,'get',side_effect=get):
            rows, _ = sources.collect(['optics'],'2020-01-01','2026-10-28',timeline=True)
        self.assertEqual({row['doi'] for row in rows}, {'10.1234/seed','10.1234/distinct'})
        self.assertTrue(all('from_publication_date:2020-01-01' in params['filter'] for params,_ in calls[1:]))
        many = sources._openalex_rows({'results':[work('W'+str(i),f'Paper {i}',2000+i,doi=f'10.1234/{i}') for i in range(12)]})
        with patch.object(sources,'search_openalex',return_value=many), \
             patch.object(sources,'search_crossref',return_value=[{**row,'doi':row['doi']+'x','title':row['title']+'x'} for row in many]), \
             patch.object(sources,'_search_citations',return_value=[{**row,'doi':row['doi']+'y','title':row['title']+'y'} for row in many]):
            rows, _ = sources.collect(['optics'],timeline=True)
        self.assertEqual(len(rows), 30)

    def test_graph_metadata_merges_with_existing_doi_without_mutating_source_rows(self):
        seed = sources._openalex_rows({'results':[work()]})[0]
        edge = {'source':'OpenAlex','citing':'https://openalex.org/W2',
                'cited':'https://openalex.org/W1','evidence':'cites_filter'}
        duplicate = {**seed,'citation_graph':[edge],
                     'sources':[{'name':'Crossref','url':'https://doi.org/10.1234/seed'}]}
        rows = sources.deduplicate([seed,duplicate])
        self.assertEqual(rows[0]['citation_graph'],[edge])
        self.assertEqual(len(rows[0]['sources']),2)
        self.assertNotIn('citation_graph',seed)

    def test_citation_timeout_uses_remaining_budget_and_never_calls_after_deadline(self):
        args = ('cites:W1','W1','cites_filter',[],'','','deadline')
        with patch.object(sources.time,'monotonic',return_value=100), \
             patch.object(sources.httpx,'get',return_value=response({'results':[]})) as get:
            self.assertEqual(sources._search_citations(*args[:-1],103.5),[])
            self.assertEqual(get.call_args.kwargs['timeout'],3.5)
            get.reset_mock()
            with self.assertRaises(httpx.TimeoutException):
                sources._search_citations(*args[:-1],99)
            get.assert_not_called()

    def test_exhausted_global_budget_starts_no_graph_requests(self):
        self.empty_other_sources()
        seed = sources._openalex_rows({'results':[work(references=['W2'])]})
        with patch.object(sources,'search_openalex',return_value=seed), \
             patch.object(sources,'SEARCH_BUDGET',0), patch.object(sources,'_search_citations') as graph:
            _, warnings = sources.collect(['optics'],timeline=True)
        graph.assert_not_called()
        self.assertTrue(any('时间已到' in warning for warning in warnings))

    def test_cancellation_during_graph_is_checked_in_callers_progress_context(self):
        self.empty_other_sources()
        token = ai_progress.begin('discovery:graph-test','conversation',1)
        seed = sources._openalex_rows({'results':[work(references=['W2'])]})
        worker_contexts = []
        def graph(*args):
            worker_contexts.append(ai_progress.cancelled())
            ai_progress.cancel('discovery:graph-test')
            return []
        try:
            with patch.object(sources,'search_openalex',return_value=seed), \
                 patch.object(sources,'_search_citations',side_effect=graph):
                with self.assertRaisesRegex(llm.LLMError,'停止'):
                    sources.collect(['optics'],timeline=True)
            self.assertTrue(worker_contexts)
            self.assertTrue(all(value is False for value in worker_contexts))
        finally:
            ai_progress.finish(token)

    def test_malformed_openalex_work_never_becomes_a_graph_seed(self):
        rows = sources._openalex_rows({'results':[None, {'authorships':['bad']},
            work('W1',references=['W2','javascript:bad','W3,cites:W1',None])]})
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['_referenced_works'],['W2'])


if __name__ == '__main__':
    unittest.main()
