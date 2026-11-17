import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';

const moduleFrom = path => import('data:text/javascript;base64,' + readFileSync(new URL(path, import.meta.url)).toString('base64'));
const { createResourceCache } = await moduleFrom('../web/js/resource-cache.js');
const { THEME_PRESETS } = await moduleFrom('../web/js/theme.js');

test('pagination metadata cache shares requests and reloads on data changes', async () => {
  let requests = 0;
  const cache = createResourceCache(async () => ({ revision: ++requests }));
  const [first, shared] = await Promise.all([cache.read(), cache.read()]);
  assert.equal(first, shared);
  assert.equal((await cache.read()).revision, 1);
  cache.invalidate();
  assert.equal((await cache.read()).revision, 2);
});

test('a failed obsolete request cannot discard a newer cache generation', async () => {
  let rejectFirst;
  let requests = 0;
  const cache = createResourceCache(() => ++requests === 1
    ? new Promise((_, reject) => { rejectFirst = reject; }) : Promise.resolve('fresh'));
  const old = cache.read();
  const failure = assert.rejects(old, /old request/);
  await Promise.resolve();
  cache.invalidate();
  assert.equal(await cache.read(), 'fresh');
  rejectFirst(new Error('old request'));
  await failure;
  assert.equal(await cache.read(), 'fresh');
  assert.equal(requests, 2);
});

test('failed metadata requests retry instead of caching a failure forever', async () => {
  let attempts = 0;
  const cache = createResourceCache(async () => {
    if (++attempts === 1) throw new Error('offline');
    return 'restored';
  });
  await assert.rejects(cache.read(), /offline/);
  assert.equal(await cache.read(), 'restored');
});

const luminance = hex => [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255)
  .map(v => v <= .04045 ? v / 12.92 : ((v + .055) / 1.055) ** 2.4)
  .reduce((sum, v, i) => sum + v * [.2126, .7152, .0722][i], 0);
const contrast = (a, b) => (Math.max(luminance(a), luminance(b)) + .05) / (Math.min(luminance(a), luminance(b)) + .05);
test('all preset themes keep small secondary text readable on page and card backgrounds', () => {
  for (const theme of THEME_PRESETS) {
    for (const background of ['bg', 'surface']) {
      assert.ok(contrast(theme.content.ink3, theme.content[background]) >= 4.5, `${theme.name}: ${background}`);
    }
  }
});
