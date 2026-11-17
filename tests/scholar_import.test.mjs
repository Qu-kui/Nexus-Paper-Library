/* Google Scholar 搜索结果页批量入库：解析 + 勾选 + 逐条提交 + 排队补 PDF。
 *
 * 场景：用户在 Scholar 搜到一批论文，希望像 Zotero/Mendeley 那样一次挑多条存进库。
 * 这里把「读结果页」和「批量提交」都钉住，避免以后改弹窗把这些功能带崩。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';
import vm from 'node:vm';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const source = readFileSync(join(root, 'extension/popup.js'), 'utf8');

const extractStart = source.indexOf('function extractScholarResults(');
const extractEnd = source.indexOf('const scholarState', extractStart);

function fakeHeading(text, href) {
  return {textContent: text, querySelector: (sel) => (sel === 'a' ? {href} : null)};
}

test('extractScholarResults reads a Scholar result page into structured items', async () => {
  const rows = [
    {
      querySelector(sel) {
        if (sel === 'h3.gs_rt') return fakeHeading('[PDF] An Optical Frontend for a Convolutional Neural Network', 'https://example.org/a');
        if (sel === '.gs_a') return {textContent: 'Shane Colburn, Yi Chu - Applied Optics, 2019 - osapublishing.org'};
        if (sel === '.gs_or_ggsm a, a.gs_or_ggsm, .gs_ggsm a') return {href: 'https://example.org/a.pdf'};
        return null;
      },
    },
    {
      querySelector(sel) {
        if (sel === 'h3.gs_rt') return fakeHeading('Optical computing with metasurfaces', 'https://example.org/b');
        if (sel === '.gs_a') return {textContent: 'A Author - Nature Photonics, 2021 - nature.com'};
        return null;
      },
    },
  ];
  const context = vm.createContext({
    document: {
      querySelectorAll: () => rows,
    },
    Array,
    String,
    Number,
    Math,
    Set,
  });
  vm.runInContext(source.slice(extractStart, extractEnd) + '\nglobalThis.extractScholarResults=extractScholarResults;', context);
  const items = context.extractScholarResults(20);
  assert.equal(items.length, 2);
  assert.equal(items[0].title, 'An Optical Frontend for a Convolutional Neural Network');
  assert.equal(items[0].authors, 'Shane Colburn, Yi Chu');
  assert.equal(items[0].venue, 'Applied Optics');
  assert.equal(items[0].year, '2019');
  assert.equal(items[0].pdf_url, 'https://example.org/a.pdf');
  assert.equal(items[1].pdf_url, '', 'no PDF link means metadata-only');
  assert.equal(items[1].year, '2021');
});

function scholarContext(results) {
  const nodes = new Map();
  const listeners = new Map();
  const documentListeners = [];
  const posts = [];
  const fetches = [];
  const node = (id) => {
    if (!nodes.has(id)) {
      const classes = new Set();
      nodes.set(id, {
        value: '',
        checked: false,
        textContent: '',
        innerHTML: '',
        disabled: false,
        dataset: {},
        classList: {
          add: (...names) => names.forEach((name) => classes.add(name)),
          remove: (...names) => names.forEach((name) => classes.delete(name)),
          toggle: (name, force) => {
            const want = force === undefined ? !classes.has(name) : Boolean(force);
            if (want) classes.add(name); else classes.delete(name);
            return want;
          },
          contains: (name) => classes.has(name),
        },
        addEventListener(type, fn) {
          const list = listeners.get(id) || [];
          list.push({type, fn});
          listeners.set(id, list);
        },
        focus() {},
      });
    }
    return nodes.get(id);
  };
  const context = vm.createContext({
    AbortController,
    setTimeout,
    clearTimeout,
    Uint8Array,
    TextDecoder,
    btoa,
    URL,
    Date,
    Array,
    String,
    Number,
    Math,
    Set,
    Object,
    JSON,
    document: {
      getElementById: node,
      addEventListener: (type, fn) => documentListeners.push({type, fn}),
      querySelectorAll: () => [],
    },
    window: {},
    console,
    chrome: {
      runtime: {lastError: null, sendMessage: async () => {}, openOptionsPage() {}},
      storage: {local: {get: async () => ({}), set: async () => {}}},
      tabs: {
        query: async () => [{id: 7, url: 'https://scholar.google.com/scholar?q=optical+computing'}],
        get: async () => ({url: 'https://scholar.google.com/scholar?q=optical+computing'}),
        create: async () => ({id: 70}),
        remove: async () => {},
      },
      scripting: {
        executeScript: async (options) => {
          if (options.func && options.func.name === 'extractScholarResults') return [{result: results}];
          return [{result: {}}];
        },
      },
    },
    fetch: async (url, options) => {
      fetches.push({url, options});
      if (String(url).includes('/api/health')) {
        return {ok: true, json: async () => ({app: 'PaperWhale · 论文鲸', version: '2.7.21'})};
      }
      if (String(url).includes('/api/import/web')) {
        const body = JSON.parse(options.body);
        posts.push(body);
        const isDuplicate = /Optical computing/.test(body.title);
        return {
          ok: true,
          json: async () => ({duplicate: isDuplicate, paper: {id: 'p' + posts.length, title: body.title}, pdf: isDuplicate ? 'attached' : 'missing'}),
        };
      }
      if (String(url).includes('/api/pdf-fetch/start')) {
        return {ok: true, json: async () => ({queued: 2, status: {}})};
      }
      return {ok: false, json: async () => ({})};
    },
  });
  vm.runInContext(source, context);
  return {context, nodes, posts, fetches};
}

const RESULTS = [
  {
    title: 'An Optical Frontend for a Convolutional Neural Network',
    url: 'https://example.org/a',
    authors: 'Shane Colburn, Yi Chu',
    venue: 'Applied Optics',
    year: '2019',
    pdf_url: 'https://example.org/a.pdf',
  },
  {
    title: 'Optical computing with metasurfaces',
    url: 'https://example.org/b',
    authors: 'A Author',
    venue: 'Nature Photonics',
    year: '2021',
    pdf_url: '',
  },
];

test('opening the popup on a Scholar page shows every result preselected', async () => {
  const {context, nodes} = scholarContext(RESULTS);
  await context.init();
  const panel = nodes.get('scholar-panel');
  assert.ok(panel, 'the scholar panel must exist');
  assert.equal(panel.classList.contains('hidden'), false, 'the scholar panel must be shown');
  assert.equal(nodes.get('scholar-count').textContent, '2 条 · 已选 2');
  assert.match(nodes.get('scholar-list').innerHTML, /An Optical Frontend/);
  assert.match(nodes.get('scholar-list').innerHTML, /Optical computing with metasurfaces/);
  assert.equal(nodes.get('form').classList.contains('hidden'), true, 'single-paper form stays hidden on a Scholar page');
});

test('batch import posts one payload per selected item and queues PDF fetching', async () => {
  const {context, nodes, posts, fetches} = scholarContext(RESULTS);
  await context.init();
  await context.importScholar();
  assert.equal(posts.length, 2, 'one /api/import/web call per selected result');
  assert.equal(posts[0].title, 'An Optical Frontend for a Convolutional Neural Network');
  assert.equal(posts[0].authors, 'Shane Colburn, Yi Chu');
  assert.equal(posts[0].venue, 'Applied Optics');
  assert.equal(posts[0].year, '2019');
  assert.equal(posts[0].download_pdf, false, 'batch import must not block on synchronous PDF downloads');
  const queueCall = fetches.find((item) => String(item.url).includes('/api/pdf-fetch/start'));
  assert.ok(queueCall, 'after importing, the new papers must be queued for PDF fetching');
  const queued = JSON.parse(queueCall.options.body);
  assert.deepEqual(queued.paper_ids, ['p1', 'p2']);
  assert.match(nodes.get('scholar-status').textContent, /新增 1 篇/);
  assert.match(nodes.get('scholar-status').textContent, /已存在 1 篇/);
  assert.match(nodes.get('scholar-status').textContent, /后台补 PDF/);
});

test('select-none blocks the batch import with a hint', async () => {
  const {context, nodes, posts} = scholarContext(RESULTS);
  await context.init();
  vm.runInContext('scholarState.selected = new Set();', context);
  await context.importScholar();
  assert.equal(posts.length, 0);
  assert.match(nodes.get('scholar-status').textContent, /先勾选/);
});
