/* 引文格式设置卡片静态回归：不启动后端、不建 DOM，只钉住接线与契约。
 *
 * 页面按 /api/cite/styles 的 JSON 契约写死；接口 Bot 改字段/路径时这里先红。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');
const SOURCE = 'web/js/citation-styles.js';

const CS_IDS = [
  'cs-state',
  'cs-style-list',
  'cs-description',
  'cs-name',
  'cs-generate',
  'cs-csl-file',
  'cs-import',
  'cs-warnings',
  'cs-preview',
];

test('the citation styles module exports initCitationStyles and is idempotent', () => {
  const source = read(SOURCE);
  assert.ok(source.includes('export function initCitationStyles'), 'must export initCitationStyles()');
  assert.ok(source.includes('if (booted) return;'), 'must guard against double mounting');
});

test('the card is appended to .settings-grid after settings.js rebuilt it', () => {
  const source = read(SOURCE);
  assert.ok(source.includes('document.querySelector(".settings-grid")'), 'must locate the settings grid');
  assert.ok(source.includes('document.createElement("section")'), 'must create its own section');
  assert.ok(source.includes('className = "card settings-group"'), 'must reuse the settings card classes');
  assert.ok(source.includes('grid.append(section)'), 'must append to the end of .settings-grid');
  assert.ok(!source.includes("getElementById(\"settings\")"),
    'must not inject into index.html markup (settings.js owns that DOM)');
});

test('the card calls exactly the documented JSON endpoints', () => {
  const source = read(SOURCE);
  assert.ok(source.includes('api.get("/cite/styles")'), 'must load GET /api/cite/styles');
  assert.ok(source.includes('/cite/styles/${encodeURIComponent(id)}'), 'must load/delete a single style by id');
  assert.ok(source.includes('"/cite/styles/ai"'), 'must POST /api/cite/styles/ai');
  assert.ok(source.includes('"/cite/styles/import"'), 'must POST /api/cite/styles/import');
  assert.ok(source.includes('/preview'), 'must POST /api/cite/styles/{id}/preview');
  assert.ok(source.includes('api.del('), 'must DELETE /api/cite/styles/{id}');
  assert.ok(!source.includes('/cite/styles.txt'), 'must use the JSON styles API, not the .txt one');
  for (const key of ['description', 'csl', 'name']) {
    assert.ok(source.includes(key), `request body must carry ${key}`);
  }
});

test('AI generation and CSL import read user input safely', () => {
  const source = read(SOURCE);
  assert.ok(source.includes('getElementById("cs-description")') || source.includes('$("cs-description")'),
    'AI description must come from #cs-description');
  assert.ok(source.includes('file.text()'), 'the .csl file must be read with file.text()');
  assert.ok(source.includes('accept=".csl,.xml,text/xml,application/xml"'), 'the file input must accept .csl/.xml');
  assert.ok(source.includes('生成中…'), 'the generate button must show a loading label');
  assert.ok(source.includes('导入中…'), 'the import button must show a loading label');
  assert.ok(source.includes('删除中…'), 'the delete button must show a loading label');
  assert.ok(source.includes('button.disabled = true'), 'loading states must disable the button');
  assert.ok(source.includes('warnings'), 'import warnings must be surfaced');
  assert.ok(source.includes('classList.toggle("hidden"'), '#cs-warnings must be hidden when there are no warnings');
});

test('deleting a style requires confirmation and only custom styles can be deleted', () => {
  const source = read(SOURCE);
  assert.ok(source.includes('confirm('), 'delete must ask for confirmation');
  assert.ok(source.includes('item.builtin === false'), 'only builtin === false (custom) styles may be deleted');
  assert.ok(source.includes('stopPropagation'), 'the delete click must not also select the row');
});

test('list rows keep every cs- prefixed id and show the required badges', () => {
  const source = read(SOURCE);
  for (const id of CS_IDS) {
    assert.ok(source.includes(id), `#${id} must exist in the card`);
  }
  for (const badge of ['内置', '数字', '自定义']) {
    assert.ok(source.includes(badge), `the row must show the「${badge}」badge`);
  }
  assert.ok(source.includes('引文格式'), 'the card title must be 引文格式');
  assert.ok(source.includes('内置 6 种'), 'the description must explain the six builtin styles');
  assert.ok(source.includes('et al.') && source.includes('斜体'), 'the AI textarea placeholder must show an example');
});

test('preview and row text are written with textContent, never innerHTML from data', () => {
  const source = read(SOURCE);
  assert.ok(source.includes('正文引文：'), 'the preview must label the inline citation');
  assert.ok(source.includes('参考文献：'), 'the preview must label the reference entry');
  assert.ok(source.includes('textContent'), 'dynamic text must use textContent');
  assert.ok(!/innerHTML\s*=\s*[^`"']*data\b/.test(source), 'must not inject API data through innerHTML');
  assert.ok(!/innerHTML\s*=\s*`[^`]*\$\{/.test(source), 'must not interpolate API data into innerHTML');
  assert.ok(source.includes('replaceChildren()'), 'lists/preview must be rebuilt with replaceChildren');
});
