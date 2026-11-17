import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const read = path => readFileSync(new URL('../' + path, import.meta.url), 'utf8');

test('Connector popup and settings accept both legacy and NPL health identities', async () => {
  const popup = read('extension/popup.js');
  const options = read('extension/options.js');
  const probes = [
    popup.slice(popup.indexOf('async function probeBase('), popup.indexOf('\nasync function findBase(')) + '\nprobeBase',
    options.slice(options.indexOf('async function testPort('), options.indexOf('\n$("save")')) + '\ntestPort',
  ];
  for (const source of probes) {
    for (const app of ['PaperWhale', 'PaperWhale Desktop', 'NPL', 'Nexus Paper Library', 'UnrelatedApp']) {
      const probe = vm.runInNewContext(source, {
        AbortController, AbortSignal, setTimeout, clearTimeout,
        fetch: async () => ({ok: true, json: async () => ({app})}),
      });
      assert.equal(await probe('http://127.0.0.1:8765'), app !== 'UnrelatedApp', app);
    }
  }
});

test('Connector visible entry points use the approved NPL brand', () => {
  const manifest = JSON.parse(read('extension/manifest.json'));
  assert.equal(manifest.name, 'NPL Connector');
  assert.equal(manifest.version, '2.10.1');
  assert.equal(manifest.action.default_title, '保存到 NPL');
  for (const path of ['extension/popup.html', 'extension/options.html']) {
    const source = read(path);
    assert.doesNotMatch(source, /PaperWhale|鲸|🐳|🐋/u);
    assert.match(source, /NPL/);
  }
  assert.match(read('extension/popup.html'), /src="icons\/icon32.png"/);
});
