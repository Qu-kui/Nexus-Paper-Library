"""Exercise the synchronous launch gate without a browser, server, or user data."""
from pathlib import Path
import re
import shutil
import subprocess
import unittest


class StartupGateTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which('node'), 'Node.js is required for inline JavaScript verification')
    def test_launch_gate_and_fail_open_cleanup(self):
        index = (Path(__file__).resolve().parents[1] / 'web' / 'index.html').read_text(encoding='utf-8')
        gate = re.search(r'<script>(.*?)</script>', index, re.S).group(1)
        self.assertLess(index.index(gate), index.index('rel="stylesheet"'))
        harness = r"""
const assert = require('node:assert/strict');
const vm = require('node:vm');
const source = JSON.parse(process.argv[1]);
const token = '0123456789abcdef0123456789abcdef';
function run(options = {}) {
  const dataset = {};
  let timer, delay, replaced, removed = false;
  const storage = options.storage || new Map();
  const window = {
    location: {href: options.url || `http://localhost/?view=library&startup=${token}#papers`},
    history: {state: {saved: true}, replaceState(state, unused, url) {
      assert.deepEqual(state, {saved: true});
      if (options.historyError) throw Error('history unavailable');
      replaced = url;
    }},
    sessionStorage: {
      getItem(key) { if (options.storageError) throw Error('blocked'); return storage.get(key) || null; },
      setItem(key, value) { if (options.writeError) throw Error('quota'); storage.set(key, value); }
    },
    matchMedia() { if (options.mediaError) throw Error('unsupported'); return {matches: !!options.reduced}; },
    setTimeout(fn, ms) { timer = fn; delay = ms; return 42; }
  };
  const document = {documentElement: {dataset}, getElementById(id) {
    assert.equal(id, 'npl-startup'); return {remove() { removed = true; }};
  }};
  vm.runInNewContext(source, {window, document, URL});
  return {window, dataset, storage, replaced, delay, expire() { timer(); }, removed() { return removed; }};
}
const first = run();
assert.equal(first.dataset.nplStartup, 'pending');
assert.equal(first.window.__NPL_STARTUP__.requested, true);
assert.equal(first.window.__NPL_STARTUP__.token, token);
assert.equal(first.window.__NPL_STARTUP__.failsafe, 42);
assert.equal(first.replaced, '/?view=library#papers');
assert.equal(first.storage.get('npl.startup.played.' + token), '1');
assert.equal(first.delay, 6000);
assert.equal(run({storage: first.storage}).dataset.nplStartup, undefined);
for (const options of [
  {url: 'http://localhost/'}, {url: 'http://localhost/?startup=invalid'},
  {reduced: true}, {storageError: true}, {writeError: true},
  {historyError: true}, {mediaError: true}
]) assert.equal(run(options).dataset.nplStartup, undefined);
const owned = {inert: true};
const untouched = {inert: true};
first.window.__NPL_STARTUP__.inertElements.push(owned);
first.expire();
assert.equal(first.dataset.nplStartup, undefined);
assert.equal(first.removed(), true);
assert.equal(owned.inert, false);
assert.equal(untouched.inert, true);
assert.equal(first.window.__NPL_STARTUP__.requested, false);
const runtime = run();
let finished = false;
runtime.window.__NPL_STARTUP__.finish = () => { finished = true; };
runtime.expire();
assert.equal(finished, true);
const brokenRuntime = run();
brokenRuntime.window.__NPL_STARTUP__.finish = () => { throw Error('module failure'); };
brokenRuntime.expire();
assert.equal(brokenRuntime.dataset.nplStartup, undefined);
console.log('startup gate assertions passed');
"""
        import json
        result = subprocess.run(
            ['node', '-e', harness, json.dumps(gate)], capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    unittest.main()
