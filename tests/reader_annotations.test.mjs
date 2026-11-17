import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';
import { createRequire } from 'node:module';
import { join } from 'node:path';

let mod = {};
try {
  const source = await readFile(new URL('../web/js/reader-annotations.js', import.meta.url));
  mod = await import(`data:text/javascript;base64,${source.toString('base64')}`);
} catch (error) {
  if (error.code !== 'ENOENT') throw error;
}

const chapters = [
  { title: 'Introduction', page: 2, level: 1 },
  { title: 'Methods', page: 5, level: 1 },
  { title: 'Details', page: 5, level: 2 },
];
const items = Object.freeze([
  Object.freeze({ id: 9, page: 5, color: '#FFDD00', quote: 'METHOD sample', note: 'Check result' }),
  Object.freeze({ id: 3, page: 2, color: '#ffdd00', quote: 'Start here', note: '' }),
  Object.freeze({ id: 2, page: 2, color: '#14a3a3', quote: '选中文本', note: '  ' }),
  Object.freeze({ id: 1, page: 1, color: '#14a3a3', quote: 'Cover', note: 'Remember' }),
]);

test('filter returns original objects sorted by page then numeric id without mutation', () => {
  assert.equal(typeof mod.filterAnnotations, 'function');
  const result = mod.filterAnnotations(items, {}, chapters);
  assert.deepEqual(result.map(item => item.id), [1, 2, 3, 9]);
  assert.equal(result[0], items[3]);
  assert.deepEqual(items.map(item => item.id), [9, 3, 2, 1]);
});

test('combines color, trimmed note presence and literal case-insensitive quote/note search', () => {
  assert.deepEqual(mod.filterAnnotations(items, { color: '#ffdd00', note: 'with', query: ' CHECK ' }, chapters), [items[0]]);
  assert.deepEqual(mod.filterAnnotations(items, { note: 'without' }, chapters).map(item => item.id), [2, 3]);
  assert.deepEqual(mod.filterAnnotations(items, { query: '选中' }, chapters), [items[2]]);
  assert.deepEqual(mod.filterAnnotations(items, { query: '.*' }, chapters), []);
  assert.deepEqual(mod.filterAnnotations(items, { color: '', note: 'all', query: '', chapter: '' }, chapters).map(item => item.id), [1, 2, 3, 9]);
});

test('chapter index selects the latest heading at or before annotation page', () => {
  assert.deepEqual(mod.filterAnnotations(items, { chapter: '0' }, chapters).map(item => item.id), [2, 3]);
  assert.deepEqual(mod.filterAnnotations(items, { chapter: '1' }, chapters), []);
  assert.deepEqual(mod.filterAnnotations(items, { chapter: '2' }, chapters), [items[0]]);
  assert.deepEqual(mod.filterAnnotations(items, { chapter: '99' }, chapters), []);
  assert.deepEqual(mod.filterAnnotations(items, { chapter: '0' }, []), []);
});

test('Markdown includes metadata and sorted annotation source, note, page and chapter', () => {
  assert.equal(typeof mod.annotationsMarkdown, 'function');
  const result = mod.annotationsMarkdown({ title: '论文 Title', authors: 'Alice; 李四', doi: '10.1234/example', year: 2026 }, items, chapters);
  assert.match(result, /^# 论文 Title — 批注\n/);
  assert.match(result, /作者：Alice; 李四/);
  assert.match(result, /年份：2026/);
  assert.match(result, /DOI：10\.1234\/example/);
  assert.match(result, /第 5 页 · Details/);
  assert.match(result, /> METHOD sample/);
  assert.match(result, /备注：Check result/);
  assert.ok(result.indexOf('> Cover') < result.indexOf('> Start here'));
  assert.match(result, /共 4 条批注/);
});

test('Markdown escapes untrusted HTML and syntax, preserves multiline quotes and handles empty data', () => {
  const result = mod.annotationsMarkdown({ title: '<img src=x> [title](javascript:x)\n# heading', authors: '*Author*' }, [{
    id: 1, page: 3, quote: '<script>bad</script>\n[click](javascript:x)', note: '# heading\n- list\n1. list\n```html', html: '<b>unused</b>',
  }], [{ title: '<b>Section</b>', page: 1, level: 1 }]);
  assert.ok(!result.includes('<script>') && !result.includes('<img') && !result.includes('<b>'));
  assert.ok(!result.includes('[click](javascript:x)'));
  assert.ok(!result.includes('unused'));
  assert.match(result, /> &lt;script&gt;bad&lt;\/script&gt;\n> \\\[click\\\]\\\(javascript:x\\\)/);
  assert.match(result, /备注：\\# heading\n\\- list\n1\\\. list\n\\`\\`\\`html/);
  assert.match(mod.annotationsMarkdown({}, [], []), /^# 未命名论文 — 批注\n/);
  assert.match(mod.annotationsMarkdown({}, [], []), /共 0 条批注/);
});

test('export filename is safe on Windows and keeps Chinese title', () => {
  assert.equal(typeof mod.annotationExportFilename, 'function');
  assert.equal(mod.annotationExportFilename({ title: '论文: A/B?*' }), '论文_ A_B__-批注.md');
  assert.equal(mod.annotationExportFilename({ title: '  ' }), '未命名论文-批注.md');
  assert.ok(!/[<>:"/\\|?*\x00-\x1f]/.test(mod.annotationExportFilename({ title: 'CON<>:\\x\n' })));
});

test('annotation controls filter, preserve controls on refresh, export the shown set and reset', async () => {
  const helperSource = await readFile(new URL('../web/js/reader-annotations.js', import.meta.url), 'utf8');
  const helperUrl = `data:text/javascript;base64,${Buffer.from(helperSource).toString('base64')}`;
  const panelSource = (await readFile(new URL('../web/js/reader-annotation-panel.js', import.meta.url), 'utf8'))
    .replace('./reader-annotations.js', helperUrl);
  const panelUrl = `data:text/javascript;base64,${Buffer.from(panelSource).toString('base64')}`;
  const dependencies = process.env.PAPERWHALE_NODE_MODULES || 'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
  const require = createRequire(join(dependencies, 'package.json'));
  const { chromium } = require('playwright');
  const browser = await chromium.launch({ executablePath: process.env.PAPERWHALE_EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless: true });
  try {
    const page = await browser.newPage({ acceptDownloads: true });
    await page.setContent('<div><div id="reader-annotations"></div></div>');
    await page.evaluate(async ({ panelUrl, items, chapters }) => {
      window.paper = { title: '测试论文', doi: '10.1/test' };
      window.annotations = items;
      window.chapters = chapters;
      window.changes = 0;
      const { installAnnotationTools } = await import(panelUrl);
      window.panel = installAnnotationTools({ getPaper: () => window.paper, getAnnotations: () => window.annotations, getChapters: () => window.chapters, onChange: () => window.changes++ });
    }, { panelUrl, items, chapters });
    assert.equal(await page.locator('.filter-count').textContent(), '显示 4 / 4 条批注');
    await page.getByLabel('批注颜色').selectOption('#ffdd00');
    await page.getByLabel('备注筛选').selectOption('with');
    await page.getByLabel('搜索批注').fill('check');
    await page.getByLabel('批注章节').selectOption('2');
    assert.deepEqual(await page.evaluate(() => window.panel.items().map(item => item.id)), [9]);
    assert.equal(await page.locator('.filter-count').textContent(), '显示 1 / 4 条批注');
    await page.evaluate(() => window.panel.refresh());
    assert.equal(await page.getByLabel('搜索批注').inputValue(), 'check');
    assert.equal(await page.getByLabel('批注章节').inputValue(), '2');
    const downloadPromise = page.waitForEvent('download');
    await page.getByRole('button', { name: '导出筛选批注' }).click();
    const download = await downloadPromise;
    assert.equal(download.suggestedFilename(), '测试论文-批注.md');
    const downloaded = await readFile(await download.path(), 'utf8');
    assert.match(downloaded, /METHOD sample/);
    assert.ok(!downloaded.includes('Start here'));
    await page.evaluate(() => { window.annotations = window.annotations.filter(item => ![9, 3].includes(item.id)); window.panel.refresh(); });
    assert.equal(await page.getByLabel('批注颜色').inputValue(), '#ffdd00');
    await page.evaluate(items => { window.annotations = items; window.panel.refresh(); }, items);
    await page.getByLabel('搜索批注').fill('not present');
    assert.equal(await page.getByRole('button', { name: '导出筛选批注' }).isDisabled(), true);
    await page.getByRole('button', { name: '重置筛选' }).click();
    assert.equal(await page.getByLabel('搜索批注').inputValue(), '');
    assert.equal(await page.locator('.filter-count').textContent(), '显示 4 / 4 条批注');
    await page.evaluate(() => { window.paper = { title: '另一论文' }; window.annotations = []; window.chapters = []; window.panel.reset(); });
    assert.equal(await page.locator('.filter-count').textContent(), '显示 0 / 0 条批注');
    assert.equal(await page.getByRole('button', { name: '导出筛选批注' }).isDisabled(), true);
    assert.ok(await page.evaluate(() => window.changes >= 5));
  } finally {
    await browser.close();
  }
});
