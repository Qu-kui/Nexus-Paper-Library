/* 方法 / 参数页静态回归：不启动后端、不建 DOM，只钉住页面接线与契约字段名。
 *
 * 后端实现按 paperwhale_server/routers/methods.py 的契约来，字段/路径改错时这里先红。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');

const METHODS_IDS = [
  'btn-methods-refresh',
  'btn-methods-extract',
  'methods-search',
  'methods-state',
  'methods-status',
  'methods-list',
];

test('the methods page module exposes initMethods and listens for the page event', () => {
  const source = read('web/js/methods-page.js');
  assert.ok(source.includes('export function initMethods'), 'must export initMethods()');
  assert.ok(source.includes('pw:methods-shown'), 'must react to pw:methods-shown');
  assert.ok(source.includes('if (booted) return;'), 'initMethods must be idempotent');
  assert.ok(source.includes('document.querySelector(".page[data-page=\\"methods\\"]")')
    || source.includes("document.querySelector('.page[data-page=\"methods\"]')"),
  'must locate the methods page section');
  assert.ok(source.includes('classList.contains("active")'), 'polling must check whether the page is active');
});

test('the methods page calls exactly the documented API paths', () => {
  const source = read('web/js/methods-page.js');
  assert.ok(source.includes('/methods/status'), 'must poll GET /methods/status');
  assert.ok(source.includes('"/methods/extract"'), 'must POST /methods/extract');
  assert.ok(source.includes('/methods?state='), 'must load GET /methods with the state filter');
  assert.ok(source.includes('encodeURIComponent(stateFilter)'), 'the state filter must be encoded');
  assert.ok(source.includes('&q=${encodeURIComponent(keyword)}'), 'the search keyword must be encoded');
});

test('search is debounced and Enter triggers an immediate reload', () => {
  const source = read('web/js/methods-page.js');
  assert.ok(source.includes('SEARCH_DEBOUNCE_MS = 350'), 'debounce must be 350ms');
  assert.ok(source.includes('clearTimeout(searchTimer)'), 'each keystroke must reset the debounce timer');
  assert.match(source, /event\.key !== "Enter"/, 'Enter must trigger the search without waiting for the debounce');
});

test('polls with recursive setTimeout and stops when the page is left', () => {
  const source = read('web/js/methods-page.js');
  assert.equal(source.includes('setInterval'), false, 'polling must not use setInterval (duplicate timers on page switch)');
  assert.ok(source.includes('POLL_MS = 3000'), 'batch status must poll every 3s');
  assert.ok(source.includes('clearTimeout(pollTimer)'), 'the poll timer must be cleared explicitly');
  assert.match(source, /if \(!isActive\(\)\) \{\s*stopPolling\(\);\s*return;\s*\}/, 'poll must stop when the page is no longer active');
});

test('the extraction requests use the contract body fields', () => {
  const source = read('web/js/methods-page.js');
  assert.ok(source.includes('paper_ids'), 'single-paper extraction must send paper_ids');
  assert.ok(source.includes('only_new: true'), 'extract-all must send only_new');
});

test('index.html keeps the methods page skeleton ids', () => {
  const html = read('web/index.html');
  for (const id of METHODS_IDS) {
    assert.ok(html.includes(`id="${id}"`), `index.html must keep #${id} (the page skeleton)`);
  }
  assert.ok(html.includes('data-page="methods"'), 'the methods page section must exist');
  assert.ok(html.includes('/css/methods.css'), 'index.html must load the methods stylesheet');
});

test('methods.css styles the list, summary clamp and parameter chips', () => {
  const css = read('web/css/methods.css');
  assert.ok(css.includes('.methods-list'), 'must style .methods-list');
  assert.ok(css.includes('.method-card'), 'must style .method-card');
  assert.ok(css.includes('.method-summary'), 'must style the method summary');
  assert.ok(css.includes('-webkit-line-clamp: 3'), 'the summary must be clamped to three lines');
  assert.ok(css.includes('.method-chip'), 'must style the parameter chips');
  assert.ok(css.includes('.methods-list') && css.includes('data-page="methods"'),
    'selectors must be scoped to the methods page');
});
