/* EndNote 迁移的界面接线：导入页卡片 + 桌面文件选择 + 前端请求参数。 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');

test('import page ships the EndNote migration card', () => {
  const html = read('web/index.html');
  for (const id of ['endnote-path', 'btn-pick-endnote', 'btn-import-endnote', 'endnote-result', 'endnote-include-missing']) {
    assert.ok(html.includes(`id="${id}"`), `import page is missing #${id}`);
  }
  assert.match(html, /EndNote/);
  assert.match(html, /XML/, 'the card must tell the user which EndNote export format to use');
});

test('the desktop app exposes a file picker for the migration file', () => {
  const desktop = read('desktop.py');
  assert.match(desktop, /def pick_file\(\)/, 'desktop.py must expose pick_file()');
  assert.match(desktop, /pick_file,/, 'pick_file must be registered in the js_api list');
  assert.match(desktop, /OPEN_DIALOG/, 'the picker must be a file dialog, not a folder dialog');
});

test('import.js posts to /import/endnote with the migration options', () => {
  const source = read('web/js/import.js');
  assert.match(source, /function pickEndnoteFile\(/);
  assert.match(source, /function importEndnote\(/);
  assert.match(source, /api\.post\("\/import\/endnote"/);
  assert.match(source, /include_without_pdf/, 'the "keep entries without PDF" switch must reach the backend');
  assert.match(source, /pywebview\.api\.pick_file/, 'desktop mode should open a native file picker');
  const checked = spawnSync(process.execPath, ['--check', join(root, 'web/js/import.js')], {encoding: 'utf8'});
  assert.equal(checked.status, 0, `node --check failed: ${checked.stderr || ''}`);
});
