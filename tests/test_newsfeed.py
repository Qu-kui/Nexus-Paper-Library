"""资讯流的纯逻辑与本地状态测试（不访问网络）。"""
from __future__ import annotations

import json
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch
from types import SimpleNamespace

from paperwhale_server.services import newsfeed, newsfeed_ai


class NewsfeedTests(unittest.TestCase):
    def test_topic_tokens_chinese_and_english(self):
        self.assertIn("具身智能", newsfeed.topic_tokens("具身智能"))
        tokens = newsfeed.topic_tokens("quantum computing")
        self.assertIn("quantum", tokens)
        self.assertIn("computing", tokens)
        self.assertEqual(newsfeed.topic_tokens("  "), [])

    def test_relevance_requires_enough_keywords(self):
        self.assertTrue(newsfeed.is_relevant("具身智能机器人进入工厂", "具身智能"))
        self.assertTrue(newsfeed.is_relevant("Quantum computing reaches new milestone", "quantum computing"))
        self.assertFalse(newsfeed.is_relevant("Quantum stocks jump today", "quantum computing"))
        self.assertFalse(newsfeed.is_relevant("今天天气不错", "具身智能"))

    def test_feed_links_and_parse_titles(self):
        page = '<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
        self.assertEqual(newsfeed._feed_links(page, "https://example.com/"), ["https://example.com/feed.xml"])
        payload = b"""<?xml version="1.0"?><rss><channel>
          <item><title>Hello world</title><link>https://example.com/a</link>
          <description>body</description></item></channel></rss>"""
        rows = newsfeed._parse_feed_titles(payload)
        self.assertTrue(rows)
        self.assertEqual(rows[0]["title"], "Hello world")
        self.assertEqual(rows[0]["url"], "https://example.com/a")

    def test_parse_atom_entry_metadata(self):
        payload = b"""<?xml version="1.0" encoding="utf-8"?>
        <feed xmlns="http://www.w3.org/2005/Atom">
          <entry>
            <title>Gemini update</title>
            <link rel="self" href="https://example.com/self" />
            <link rel="alternate" href="https://example.com/post" />
            <summary>Model release notes</summary>
            <published>2026-11-16T08:00:00Z</published>
          </entry>
        </feed>"""
        rows = newsfeed._parse_feed_titles(payload)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["url"], "https://example.com/post")
        self.assertEqual(rows[0]["summary"], "Model release notes")
        self.assertEqual(rows[0]["published_at"], "2026-11-16T08:00:00+00:00")

    def test_site_quality_rejects_aggregators_and_rewards_official(self):
        self.assertEqual(newsfeed._site_quality("360kuai.com")[0], "blocked")
        self.assertEqual(newsfeed._site_quality("www.gov.cn")[0], "official")
        self.assertEqual(newsfeed._site_quality("www.caixin.com")[0], "media")
        state = newsfeed._empty_state()
        self.assertIsNone(newsfeed._register_site(
            state, "360检索", "https://news.so.com/ns?q=test", "https://news.so.com/", "search", "t1", "测试"
        ))
        self.assertIsNone(newsfeed._register_site(
            state, "360快资讯", "https://360kuai.com/a", "https://360kuai.com/", "search-result", "t1", "测试"
        ))
        sid, host = newsfeed._publisher_for(state, "https://360kuai.com/a", "t1", "测试")
        self.assertEqual((sid, host), ("", "360kuai.com"))
        self.assertEqual(state["sources"], [])

    def test_related_topic_shares_quality_sources(self):
        state = newsfeed._empty_state()
        newsfeed._ensure_default_topics(state)
        topic = {
            "id": "t-politics", "label": "时政新闻", "builtin": False,
            "status": "ready", "channels": [], "articles": [],
        }
        state["topics"].append(topic)
        state["items"] = [
            {"url": "https://www.news.cn/a", "title": "A", "source": "media-xinhua", "topic_id": "cn-politics"},
            {"url": "https://www.people.com.cn/b", "title": "B", "source": "media-people", "topic_id": "cn-politics"},
        ]
        count = newsfeed._share_related_sources(state, topic)
        self.assertEqual(count, 6)
        shared = [site for site in state["sources"] if "t-politics" in site.get("topic_ids", [])]
        self.assertEqual(len(shared), 6)
        self.assertTrue(all(site.get("shared") for site in shared))
        self.assertTrue(all("t-politics" in item["topic_ids"] for item in state["items"]))

    def test_curated_sites_fill_when_ai_has_no_candidates(self):
        state = newsfeed._empty_state()
        topic = {"id": "t-politics", "label": "时政新闻"}
        response = Mock(status_code=200, text="<html></html>", url="https://www.gov.cn/")
        client = Mock()
        client.__enter__ = Mock(return_value=client)
        client.__exit__ = Mock(return_value=False)
        client.get = Mock(return_value=response)
        with patch.object(newsfeed, "_client", return_value=client), \
             patch.object(newsfeed, "_fetch_dynamic_site", return_value=[]):
            rows = newsfeed._discover_ai_sites(state, topic, allow_llm=False)
        self.assertEqual(rows, [])
        self.assertEqual(
            [site["name"] for site in state["sources"]],
            ["中国政府网", "财新网", "南方周末"],
        )
        self.assertEqual([site["quality"] for site in state["sources"]], ["official", "media", "media"])

    def test_cleanup_drops_search_entry_and_aggregator(self):
        state = newsfeed._empty_state()
        state["sources"] = [
            {"id": "search-1", "name": "360 新闻检索", "homepage": "https://news.so.com/", "type": "search", "topic_ids": ["t1"]},
            {"id": "low-1", "name": "360kuai.com", "homepage": "https://360kuai.com/", "type": "search-result", "topic_ids": ["t1"]},
            {"id": "ok-1", "name": "中国政府网", "homepage": "https://www.gov.cn/", "type": "search-result", "topic_ids": ["t1"]},
        ]
        state["items"] = [{"url": "https://360kuai.com/a", "source": "low-1", "topic_id": "t1"}]
        newsfeed._cleanup_sources(state)
        self.assertEqual([site["id"] for site in state["sources"]], ["ok-1"])
        self.assertEqual(state["items"][0]["source"], "topic-t1")
        self.assertEqual(state["items"][0]["source_name"], "360kuai.com")

    def test_news_timestamps_and_url_dates(self):
        self.assertEqual(newsfeed._iso_timestamp(1794753839), "2026-11-15T14:43:59+00:00")
        self.assertEqual(newsfeed._iso_timestamp(1794753839000, millis=True), "2026-11-15T14:43:59+00:00")
        self.assertEqual(newsfeed._iso_timestamp("bad"), "")
        self.assertEqual(newsfeed._iso_cn_datetime("2026-11-15 22:43:59"), "2026-11-15T14:43:59+00:00")
        self.assertEqual(
            newsfeed._date_from_url("https://www.news.cn/20261116/politics/c.html"),
            "2026-11-16",
        )
        self.assertEqual(
            newsfeed._date_from_url("http://js.people.com.cn/n2/2026/1116/c358232-41751565.html"),
            "2026-11-16",
        )
        self.assertEqual(
            newsfeed._cctv_published("https://tv.cctv.com/2026/11/15/VIDE123.shtml", "完整版《新闻联播》 20261115 19:00"),
            "2026-11-15T11:00:00+00:00",
        )
        self.assertEqual(
            newsfeed._extract_article_time('<script>{"pubtime":"2026-11-16 11:55:20"}</script>'),
            "2026-11-16T03:55:20+00:00",
        )
        self.assertEqual(
            newsfeed._extract_article_time('<textarea class="article-time">1794801191345</textarea>'),
            "2026-11-16T03:53:11+00:00",
        )
        self.assertEqual(
            newsfeed._extract_article_time('<meta name="publishdate" content="2026-11-16"><span class="time">11:37:26</span>'),
            "2026-11-16T03:37:26+00:00",
        )
        self.assertEqual(
            newsfeed._extract_article_time('<b id="newstime">2026年11月16日06:49</b>'),
            "2026-11-15T22:49:00+00:00",
        )

    def test_enrich_items_translates_and_scores(self):
        items = [
            {"title": "Gemini 4 is here", "summary": "Model release", "source_name": "DeepMind", "topic_label": "大模型发布"},
            {"title": "Robot learns to walk", "summary": "A new robot", "source_name": "Example", "topic_label": "具身智能"},
        ]
        reply = {"items": [
            {"id": 0, "title_zh": "Gemini 4 发布", "summary_zh": "模型发布。", "score": 88, "reason": "重要发布"},
            {"id": 1, "title_zh": "机器人学会行走", "summary_zh": "一台新机器人。", "score": 72, "reason": "技术进展"},
        ]}
        with patch.object(newsfeed_ai.llm, "is_configured", return_value=True), \
             patch.object(newsfeed_ai.llm, "chat_json", return_value=reply):
            count, error = newsfeed_ai.enrich_items(items, limit=10, batch_size=10)
        self.assertEqual(count, 2)
        self.assertEqual(error, "")
        self.assertEqual(items[0]["title_zh"], "Gemini 4 发布")
        self.assertEqual(items[0]["score"], 88)
        self.assertTrue(items[0]["scored_at"])
        self.assertEqual(items[1]["title_zh"], "机器人学会行走")

    def test_enrich_prompt_uses_each_items_own_topic(self):
        batch = [
            {"title": "Qwen 4 Max", "topic_label": "大模型发布"},
            {"title": "时政快讯", "topic_label": "国内时政新闻"},
        ]
        captured = []

        def fake_chat_json(system, user):
            captured.append(json.loads(user))
            return {"items": [
                {"id": 0, "title_zh": "Qwen 4 Max", "summary_zh": "模型发布。", "score": 90, "reason": "模型发布"},
                {"id": 1, "title_zh": "时政快讯", "summary_zh": "时政新闻。", "score": 80, "reason": "时政新闻"},
            ]}

        with patch.object(newsfeed_ai.llm, "chat_json", side_effect=fake_chat_json):
            newsfeed_ai._enrich_batch(batch, "大模型发布")
        self.assertNotIn("topic", captured[0])
        self.assertEqual([row["topic"] for row in captured[0]["items"]], ["大模型发布", "国内时政新闻"])

    def test_old_scores_are_recomputed_after_prompt_version_change(self):
        item = {"title": "old", "title_zh": "旧", "summary_zh": "旧", "score": 80}
        self.assertFalse(newsfeed_ai._complete(item))
        item["score_version"] = newsfeed_ai.SCORE_VERSION
        self.assertTrue(newsfeed_ai._complete(item))

    def test_topic_lifecycle_without_network(self):
        with tempfile.TemporaryDirectory(prefix="pw-news-") as temp:
            path = Path(temp) / "newsfeed.json"
            found = ([{
                "title": "具身智能进入工厂",
                "url": "https://example.com/a",
                "summary": "机器人",
                "published_at": "",
                "domain": "example.com",
            }], "so360", [])
            with patch.object(newsfeed, "DATA_PATH", path), \
                 patch.object(newsfeed, "_search_topic", return_value=found), \
                 patch.object(newsfeed, "_discover_ai_sites", return_value=[]), \
                 patch.object(newsfeed, "_enrich_state", return_value=0), \
                 patch.object(newsfeed, "discover_channels", return_value=[{
                     "id": "feed-1", "title": "example.com", "url": "https://example.com/feed.xml", "type": "rss",
                 }]):
                topic = newsfeed.start_topic("具身智能")
                self.assertEqual(topic["id"], "t-4d07aa13")

                deadline = time.time() + 5
                snapshot = {}
                while time.time() < deadline:
                    snapshot = newsfeed.public_snapshot()
                    row = next((t for t in snapshot["topics"] if t["id"] == topic["id"]), None)
                    if row and row["status"] == "ready":
                        break
                    time.sleep(0.05)

                row = next(t for t in snapshot["topics"] if t["id"] == topic["id"])
                self.assertEqual(row["status"], "ready")
                self.assertEqual(row["article_count"], 1)
                self.assertEqual(row["channels"][0]["type"], "rss")
                self.assertFalse(any(c["type"] == "search" for c in row["channels"]))
                self.assertEqual([t["id"] for t in snapshot["topics"][:2]], ["model-release", "cn-politics"])
                self.assertTrue(any(source["name"] == "example.com" for source in snapshot["sources"]))
                items = snapshot["items"]
                self.assertEqual(len(items), 1)
                self.assertEqual(items[0]["topic_id"], topic["id"])
                self.assertEqual(items[0]["title"], "具身智能进入工厂")
                self.assertEqual(items[0]["source_name"], "example.com")

                self.assertTrue(newsfeed.remove_topic(topic["id"]))
                snapshot = newsfeed.public_snapshot()
                self.assertEqual(snapshot["items"], [])
                self.assertEqual([t["id"] for t in snapshot["topics"]], ["model-release", "cn-politics"])
                self.assertTrue(newsfeed.remove_topic("model-release"))
                snapshot = newsfeed.public_snapshot()
                self.assertEqual([t["id"] for t in snapshot["topics"]], ["cn-politics"])
                restored = newsfeed.start_topic("大模型发布")
                self.assertEqual(restored["id"], "model-release")
                self.assertTrue(restored.get("restored"))
                self.assertEqual([t["id"] for t in newsfeed.public_snapshot()["topics"]], ["model-release", "cn-politics"])

class NewsfeedRegressionTests(unittest.TestCase):
    def test_nonfinite_scores_are_rejected(self):
        for value in ['NaN', 'Infinity', float('-inf'), True]:
            self.assertIsNone(newsfeed_ai._clamp_score(value))

    def test_incomplete_enrichment_remains_retryable(self):
        items = [{'title': 'GPT update', 'summary': 'New release'}]
        with patch.object(newsfeed_ai.llm, 'is_configured', return_value=True), patch.object(newsfeed_ai.llm, 'chat_json', return_value={'items': [{'id': 0, 'score': 80}]}):
            count, error = newsfeed_ai.enrich_items(items)
        self.assertFalse(items[0].get('scored_at'))
        self.assertTrue(error)
        self.assertEqual(count, 0)

    def test_merge_retains_new_rows_at_capacity(self):
        state = {'items': [{'url': f'https://example.com/{i}', 'title': 'old'} for i in range(newsfeed.MAX_ITEMS)]}
        newsfeed._merge_items(state, [{'url': 'https://example.com/new', 'title': 'new'}])
        self.assertTrue(any(row['title'] == 'new' for row in state['items']))

    def test_old_news_is_pruned_but_recent_and_unknown_are_kept(self):
        now = datetime.now(timezone.utc)
        state = {'items': [
            {'url': 'https://example.com/old', 'published_at': (now - timedelta(days=40)).isoformat()},
            {'url': 'https://example.com/new', 'published_at': (now - timedelta(days=2)).isoformat()},
            {'url': 'https://example.com/unknown'},
        ]}
        removed = newsfeed._prune_items(state)
        self.assertEqual(removed, 1)
        self.assertEqual([row['url'] for row in state['items']], [
            'https://example.com/new', 'https://example.com/unknown',
        ])
        self.assertEqual(state['retention_days'], newsfeed.MAX_ITEM_AGE_DAYS)

    def test_merge_skips_articles_outside_retention_window(self):
        now = datetime.now(timezone.utc)
        state = {'items': []}
        newsfeed._merge_items(state, [
            {'url': 'https://example.com/old', 'title': 'old',
             'published_at': (now - timedelta(days=45)).isoformat()},
            {'url': 'https://example.com/new', 'title': 'new',
             'published_at': (now - timedelta(days=1)).isoformat()},
        ])
        self.assertEqual([row['url'] for row in state['items']], ['https://example.com/new'])

    def test_snapshot_does_not_hide_low_ranked_sources(self):
        state = newsfeed._empty_state()
        state['items'] = [{'url': f'https://example.com/{i}', 'title': str(i), 'source': 'hn', 'score': i % 100} for i in range(400)]
        newsfeed._ensure_default_topics(state)
        with patch.object(newsfeed, '_load', return_value=state):
            snapshot = newsfeed.public_snapshot()
        self.assertEqual(len(snapshot['items']), snapshot['total'])

    def test_enrichment_does_not_resurrect_deleted_topic(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'):
            state = newsfeed._load()
            state['topics'].append({'id': 'custom', 'label': 'test'})
            state['items'] = [{'url': 'https://example.com/a', 'title': 'test', 'topic_id': 'custom'}]
            newsfeed._save(state)
            def enrich(items, on_batch, **kwargs):
                newsfeed.remove_topic('custom')
                items[0]['score'] = 80
                on_batch()
                return 1, ''
            with patch.object(newsfeed_ai, 'enrich_items', side_effect=enrich):
                newsfeed._enrich_state(state)
            self.assertIsNone(newsfeed._find_topic(newsfeed._load(), 'custom'))
            self.assertEqual(newsfeed._load()['items'], [])

    def test_refresh_is_reserved_before_thread_starts(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'), patch.object(newsfeed, 'threading', SimpleNamespace(Thread=Mock())), patch.object(newsfeed, '_REFRESHING', False):
            self.assertTrue(newsfeed.start_refresh())
            self.assertFalse(newsfeed.start_refresh())

    def test_source_registration_deduplicates_homepage_and_feed(self):
        state = newsfeed._empty_state()
        first = newsfeed._register_site(state, 'example', 'https://example.com/', 'https://example.com/', 'search-result', 'a', 'A')
        second = newsfeed._register_site(state, 'example', 'https://www.example.com/feed', 'https://www.example.com/', 'rss', 'b', 'B')
        self.assertEqual(first['id'], second['id'])
        self.assertEqual(len(state['sources']), 1)
        self.assertEqual(set(second['topic_ids']), {'a', 'b'})

    def test_missing_key_does_not_mutate_items(self):
        items = [{'title': 'GPT update'}]
        with patch.object(newsfeed_ai.llm, 'is_configured', return_value=False), patch.object(newsfeed_ai.llm, 'chat_json') as chat:
            count, error = newsfeed_ai.enrich_items(items)
        self.assertEqual(count, 0)
        self.assertTrue(error)
        self.assertEqual(items, [{'title': 'GPT update'}])
        chat.assert_not_called()

    def test_discovery_does_not_resurrect_deleted_topic(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'):
            state = newsfeed._load()
            state['topics'].append({'id': 'custom', 'label': 'robot'})
            newsfeed._save(state)
            def search(label):
                newsfeed.remove_topic('custom')
                return [{'title': 'robot update', 'url': 'https://example.com/a'}], 'baidu', []
            with patch.object(newsfeed, '_search_topic', side_effect=search), patch.object(newsfeed, 'discover_channels', return_value=[]), patch.object(newsfeed, '_discover_ai_sites', return_value=[]):
                newsfeed._discover_topic_rows(state, 'custom')
            self.assertIsNone(newsfeed._find_topic(newsfeed._load(), 'custom'))
            self.assertEqual(newsfeed._load()['items'], [])

    def test_ai_recommendations_run_when_search_is_empty(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'):
            state = newsfeed._load()
            state['topics'].append({'id': 'custom', 'label': 'robot'})
            newsfeed._save(state)
            with patch.object(newsfeed, '_search_topic', return_value=([], '', [])), patch.object(newsfeed, 'discover_channels', return_value=[]), patch.object(newsfeed, '_discover_ai_sites', return_value=[]) as suggest:
                newsfeed._discover_topic_rows(state, 'custom')
            suggest.assert_called_once()

    def test_shared_article_survives_removing_one_topic(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'):
            state = newsfeed._load()
            state['topics'] += [{'id': 'a', 'label': 'A'}, {'id': 'b', 'label': 'B'}]
            for tid in ['a', 'b']:
                newsfeed._merge_items(state, [{'title': 'shared', 'url': 'https://example.com/shared', 'topic_id': tid}])
            newsfeed._save(state)
            newsfeed.remove_topic('b')
            items = newsfeed._load()['items']
            self.assertEqual(len(items), 1)
            self.assertEqual(items[0]['topic_id'], 'a')

    def test_private_redirect_is_blocked_before_fetch(self):
        import httpx
        visited = []
        def handler(request):
            visited.append(str(request.url))
            return httpx.Response(302, headers={'location': 'http://127.0.0.1/secret'})
        with patch.object(newsfeed, '_safe_public_url', side_effect=lambda url: '127.0.0.1' not in url):
            with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True, event_hooks={'request': [newsfeed._check_request_url]}) as client:
                with self.assertRaises(ValueError):
                    client.get('https://example.com/')
        self.assertEqual(visited, ['https://example.com/'])

    def test_malformed_cache_and_scores_recover(self):
        import json
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'):
            newsfeed.DATA_PATH.write_text(json.dumps({'topics': [None], 'sources': [None], 'items': [None, {'title': 'test', 'score': float('inf')}]}), encoding='utf-8')
            snapshot = newsfeed.public_snapshot()
            self.assertEqual(snapshot['items'][0]['score'], None)
            self.assertEqual(len(snapshot['topics']), 2)

    def test_json_fence_and_trailing_comma_recovery(self):
        result = newsfeed_ai.llm._loads_json_object('```json\n{"items": [{"id": 0, "score": 70}],}\n```')
        self.assertEqual(result['items'][0]['score'], 70)

    def test_legacy_site_migration_is_idempotent(self):
        state = newsfeed._empty_state()
        state['topics'] = [{'id': 'custom', 'label': 'robot', 'channels': [{'title': 'Example', 'url': 'https://example.com/feed', 'type': 'rss'}]}]
        newsfeed._register_site(state, 'Example', 'https://example.com/', 'https://example.com/', 'search-result', 'custom', 'robot')
        newsfeed._ensure_topic_sites(state)
        newsfeed._ensure_topic_sites(state)
        self.assertEqual(len(state['sources']), 1)
        self.assertEqual(state['sources'][0]['type'], 'rss')

    def test_gdelt_cache_rows_are_not_shared_with_workers(self):
        cached = [{'title': 'robot', 'url': 'https://example.com/a'}]
        with patch.object(newsfeed, '_GDELT_CACHE', {'robot': (time.time(), cached)}):
            rows, _ = newsfeed.search_gdelt('robot')
            rows[0]['topic_id'] = 'mutated'
        self.assertNotIn('topic_id', cached[0])

    def test_recreated_topic_does_not_reuse_running_job_id(self):
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'), patch.object(newsfeed, 'threading', SimpleNamespace(Thread=Mock())), patch.object(newsfeed, '_TOPIC_JOBS', set()):
            first = newsfeed.start_topic('robot')
            newsfeed.remove_topic(first['id'])
            second = newsfeed.start_topic('robot')
            self.assertNotEqual(first['id'], second['id'])

    def test_source_counts_merge_builtin_publisher_aliases(self):
        state = newsfeed._empty_state()
        newsfeed._ensure_default_topics(state)
        state['sources'] = [{'id': 'alias', 'name': 'OpenAI', 'homepage': 'https://openai.com/', 'topic_ids': ['custom']}]
        state['items'] = [{'title': 'A', 'source': 'alias'}, {'title': 'B', 'source': 'rss-openai'}]
        with patch.object(newsfeed, '_load', return_value=state):
            snapshot = newsfeed.public_snapshot()
        matches = [site for site in snapshot['sources'] if newsfeed._domain(site['homepage']) == 'openai.com']
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]['article_count'], 2)
        self.assertTrue(all(item['source'] == 'rss-openai' for item in snapshot['items']))

    def test_api_refresh_deduplication_and_builtin_protection(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from paperwhale_server.routers.newsfeed import router
        app = FastAPI()
        app.include_router(router)
        with tempfile.TemporaryDirectory() as temp, patch.object(newsfeed, 'DATA_PATH', Path(temp) / 'newsfeed.json'), patch.object(newsfeed, 'threading', SimpleNamespace(Thread=Mock())), patch.object(newsfeed, '_REFRESHING', False):
            with TestClient(app) as client:
                self.assertTrue(client.post('/api/newsfeed/refresh').json()['accepted'])
                self.assertFalse(client.post('/api/newsfeed/refresh').json()['accepted'])
                self.assertTrue(client.get('/api/newsfeed').json()['refreshing'])
                self.assertEqual(client.delete('/api/newsfeed/topics/model-release').status_code, 200)
                self.assertEqual(client.delete('/api/newsfeed/topics/model-release').status_code, 404)
                self.assertEqual(client.post('/api/newsfeed/topics', json={'label': '   '}).status_code, 400)
                self.assertEqual(client.post('/api/newsfeed/topics/missing/refresh').status_code, 404)
