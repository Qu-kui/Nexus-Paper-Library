import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const source = fs.readFileSync(new URL('../web/js/newsfeed-page.js', import.meta.url), 'utf8')
  .replace(/^import .*;\r?\n/m, '').replace('export function initNewsfeed', 'function initNewsfeed');
function setup(nodes = {}, api = {}) {
  const context = vm.createContext({ URL, Date, setTimeout, clearTimeout, api, toast() {},
    escapeHtml: (s) => String(s).replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`),
    document: { getElementById: (id) => nodes[id] || null }, window: {} });
  vm.runInContext(source, context);
  return (code) => vm.runInContext(code, context);
}
test('unsafe protocols and embedded credentials never become article links', () => {
  const run = setup();
  for (const value of ['javascript:alert(1)', 'data:text/html,hello', 'https://user:pass@example.com', 'http://']) {
    assert.equal(run(`safeUrl(${JSON.stringify(value)})`), '');
  }
  assert.equal(run('safeUrl("https://example.com/a")'), 'https://example.com/a');
});
test('renders escaped text, pending scores, and a distinct gradient per score', () => {
  const list = {};
  const run = setup({ 'news-list': list });
  run('state.snapshot = {items:[{title:"<img onerror=evil()>",url:"javascript:evil()",score:45},{title:"GPT 发布",score:46},{title:"待处理"}]}; renderItems()');
  assert.ok(list.innerHTML.includes('&#60;img'));
  assert.ok(!list.innerHTML.includes('href="javascript:'));
  assert.ok(list.innerHTML.includes('--score-hue:129.5'));
  assert.ok(list.innerHTML.includes('--score-hue:127.60000000000001') || list.innerHTML.includes('--score-hue:127.6'));
  assert.ok(list.innerHTML.includes('待评分'));
});
test('source panel shows quality labels and merges shared topic sources', () => {
  const list = {};
  const sub = {};
  const run = setup({ 'news-sources-list': list, 'news-sources-sub': sub });
  run(`state.snapshot = {topics:[{id:"cn-politics",label:"国内时政新闻"}],sources:[
    {id:"media-xinhua",name:"新华网",kind:"主页",type:"media",topic_ids:["cn-politics","t-politics"],quality:"official",quality_label:"官方",article_count:3},
    {id:"site-caixin",name:"财新网",kind:"检索结果",type:"search-result",topic_ids:["t-politics"],quality:"media",quality_label:"媒体",article_count:1}
  ]}; renderSources();`);
  assert.ok(list.innerHTML.includes('官方'));
  assert.ok(list.innerHTML.includes('媒体'));
  assert.ok(sub.textContent.includes('2 个站点'));
});
test('date-only publication renders as a date and keeps an exact-time tooltip', () => {
  const list = {};
  const run = setup({ 'news-list': list });
  run('state.snapshot={items:[{title:"今日要闻",published_at:"2026-11-16"},{title:"无时间"}]}; renderItems()');
  assert.ok(list.innerHTML.includes('2026-11-16'));
  assert.ok(!list.innerHTML.includes('小时前'));
  assert.ok(list.innerHTML.includes('title="发布时间：2026-11-16"'));
  assert.ok(list.innerHTML.includes('发布时间未知'));
});
test('topic filters include shared articles and retain all cached rows', () => {
  const run = setup();
  run('state.snapshot={items:[{topic_id:"a",topic_ids:["a","b"]},{topic_id:"b"}]}; state.filter="topic:b"');
  assert.equal(run('filteredItems().length'), 2);
});
test('sort modes order by score or by publication time', () => {
  const run = setup();
  run(`state.snapshot={items:[
    {title:"高分红",score:90,published_at:"2026-11-10T10:00:00Z"},
    {title:"低分新",score:10,published_at:"2026-11-16T10:00:00Z"},
    {title:"无分最新",published_at:"2026-11-16T12:00:00Z"}
  ]}`);
  assert.equal(run('state.sort="score"; JSON.stringify(filteredItems().map((item)=>item.title))'), JSON.stringify(['高分红', '低分新', '无分最新']));
  assert.equal(run('state.sort="time"; JSON.stringify(filteredItems().map((item)=>item.title))'), JSON.stringify(['无分最新', '低分新', '高分红']));
});
test('topic chips stay filters while management panel owns remove and restore', () => {
  const chips = {};
  const list = {};
  const sub = {};
  const run = setup({ 'news-topics': chips, 'news-topics-list': list, 'news-topics-sub': sub });
  run(`state.snapshot = {
    topics: [{id:"cn-politics", label:"国内时政新闻", builtin:true, article_count:12, source_count:2}],
    items: []
  }; renderTopics(); renderTopicsPanel();`);
  assert.ok(!chips.innerHTML.includes('data-news-delete'));
  assert.ok(list.innerHTML.includes('data-news-delete'));
  assert.ok(list.innerHTML.includes('data-news-restore'));
  assert.ok(list.innerHTML.includes('默认方向已移除'));
});
test('overlapping loads share a single request', async () => {
  let requests = 0;
  let finish;
  const run = setup({}, { get: () => { requests++; return new Promise((resolve) => { finish = resolve; }); } });
  const a = run('load()');
  const b = run('load()');
  assert.equal(a, b);
  finish({ items: [], refreshed_at: '2026-11-16', topics: [], sources: [] });
  await a;
  assert.equal(requests, 1);
});
