/* PaperWhale 2.7.42 设计令牌收口：所有颜色/圆角/字号只允许在 tokens.css 定义。
 *
 * 这批重构把散落在 style.css / word-picker.css / 各模块 CSS / reader.js 内联样式里的
 * 字面量收进 tokens.css。这里做「令牌合规」静态扫描，防止以后又写死回去。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');

const TOKENS = 'web/css/tokens.css';
const STYLE = 'web/css/style.css';
const HELP = 'web/css/help.css';
const READER_JS = 'web/js/reader.js';
const INDEX = 'web/index.html';
const WP_HTML = 'web/word-picker.html';

// 图标 / 装饰 emoji 的图形尺寸，不属于字号刻度（见 tokens.css 顶部约定）
const ICON_SIZES = new Set([22, 30, 40, 44]);
const ICON_SELECTORS = [
  '.brand-mark',
  '.empty-mark',
  '.dz-mark',
  '.hint-mark',
  '.detail-placeholder-mark',
  '.wp-logo',
];

const cssFiles = () => readdirSync(join(root, 'web/css'))
  .filter((name) => name.endsWith('.css') && name !== 'tokens.css');

test('tokens.css is the single source of the shared design tokens', () => {
  const tokens = read(TOKENS);
  const required = [
    ['--ink', '#10203a'],
    ['--ink-2', '#44566f'],
    ['--ink-3', '#5f7186'],
    ['--accent', '#0e7c86'],
    ['--accent-2', '#14a3a3'],
    ['--surface-2', '#f8fafc'],
    ['--accent-line', '#cfe9e6'],
    ['--line-strong', '#cbd5e1'],
    ['--radius-control', '8px'],
    ['--radius-card', '14px'],
    ['--row-alt', '#e5f2ec'],
    ['--row-selected', '#d7ece6'],
  ];
  for (const [name, value] of required) {
    assert.ok(tokens.includes(`${name}: ${value}`), `tokens.css must define ${name}: ${value}`);
  }
  for (const size of [11, 12, 13, 14, 16, 18, 24]) {
    assert.ok(tokens.includes(`--fs-${size}: ${size}px`), `tokens.css must define --fs-${size}: ${size}px`);
  }
});

test('every page loads tokens.css before its own stylesheet', () => {
  const index = read(INDEX);
  const indexTokens = index.indexOf('/css/tokens.css');
  const indexStyle = index.indexOf('/css/style.css');
  assert.ok(indexTokens >= 0, 'index.html must link /css/tokens.css');
  assert.ok(indexStyle >= 0, 'index.html must link /css/style.css');
  assert.ok(indexTokens < indexStyle, 'tokens.css must load before style.css');

  const picker = read(WP_HTML);
  const pickerTokens = picker.indexOf('/css/tokens.css');
  const pickerStyle = picker.indexOf('/css/word-picker.css');
  assert.ok(pickerTokens >= 0, 'word-picker.html must link /css/tokens.css');
  assert.ok(pickerStyle >= 0, 'word-picker.html must link /css/word-picker.css');
  assert.ok(pickerTokens < pickerStyle, 'tokens.css must load before word-picker.css');
});

test('style.css no longer declares palette tokens', () => {
  const css = read(STYLE);
  // 只允许 @media (prefers-reduced-motion) 里那个 :root 覆写时长令牌；顶层 :root 仍然禁止
  assert.equal(/(^|\n):root\s*\{/.test(css), false,
    'style.css must not declare a top-level :root block');
  for (const block of [...css.matchAll(/:root\s*\{([^}]*)\}/g)].map((match) => match[1])) {
    for (const definition of ['--ink:', '--ink-2:', '--ink-3:', '--accent:', '--accent-2:', '--surface:', '--line:']) {
      assert.equal(block.includes(definition), false,
        `a style.css :root block must not define ${definition}`);
    }
  }
  for (const definition of ['--ink:', '--ink-2:', '--ink-3:', '--accent:', '--accent-2:', '--surface:', '--line:']) {
    assert.equal(css.includes(definition), false, `style.css must not define ${definition}`);
  }
  assert.equal(css.includes('--gold'), false, 'the unused --gold token must stay deleted');
});

test('no CSS file outside tokens.css hard-codes radius or font sizes', () => {
  const problems = [];
  for (const file of cssFiles()) {
    const text = read(`web/css/${file}`);
    for (const match of text.matchAll(/border-radius:\s*\d+px/g)) {
      problems.push(`${file}: ${match[0]}`);
    }
    for (const match of text.matchAll(/font-size:\s*(\d+(?:\.\d+)?)px/g)) {
      const size = Number(match[1]);
      const before = text.slice(Math.max(0, match.index - 300), match.index);
      const isIcon = ICON_SIZES.has(size) && ICON_SELECTORS.some((selector) => before.includes(selector));
      if (!isIcon) problems.push(`${file}: font-size ${size}px`);
    }
  }
  assert.deepEqual(problems, [], `hard-coded values outside tokens.css: ${problems.join('; ')}`);
});

test('no CSS file invents intermediate font weights', () => {
  const problems = [];
  for (const file of cssFiles()) {
    if (/font-weight:\s*(620|650)\b/.test(read(`web/css/${file}`))) problems.push(file);
  }
  assert.deepEqual(problems, [], `only the standard weight steps are allowed: ${problems.join(', ')}`);
});

test('help.css never falls back to a token value inline', () => {
  const help = read(HELP);
  const tokens = read(TOKENS);
  assert.equal(/var\(--ink-3\s*,/.test(help), false, 'help.css must not hard-fallback --ink-3');
  assert.equal(/var\(--accent\s*,/.test(help), false, 'help.css must not hard-fallback --accent');
  const fallbacks = [...help.matchAll(/var\(--([a-z0-9-]+)\s*,/g)].map((match) => match[1]);
  for (const name of fallbacks) {
    assert.ok(tokens.includes(`--${name}:`), `help.css uses --${name} but tokens.css does not define it`);
  }
});

test('reader.js inline styles use tokens instead of literal colours and sizes', () => {
  const reader = read(READER_JS);
  for (const needle of ['#e2e8f0', '#44566f', '13.5px']) {
    assert.equal(reader.includes(needle), false, `reader.js inline CSS must not hard-code ${needle}`);
  }
  assert.ok(reader.includes('background:var(--surface);border:1px solid var(--line);'),
    'reader.js inline CSS must use the shared surface/line tokens');
  assert.ok(reader.includes('font-size:var(--fs-14)'),
    'reader.js inline CSS must use the shared --fs-14 step');
});

test('dead component styles stay deleted', () => {
  const style = read(STYLE);
  assert.equal(style.includes('.btn.small'), false, '.btn.small is dead and must stay deleted');
  assert.equal(style.includes('.scholar-snippet-en'), false, '.scholar-snippet-en is dead and must stay deleted');
});
