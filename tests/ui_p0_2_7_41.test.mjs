/* PaperWhale 2.7.41 UI P0 静态回归：只读 web/ 源码，不启动浏览器。
 *
 * 这一批修的是审查清单里的 P0：
 *   - 对比度：--ink-3 加深；输入框不再用 outline:none 抹掉焦点环
 *   - 版面：.page 的 30px 38px 48px 页边距保留（96px 实验已回退）
 *   - 任务中心：按钮回到侧栏 .sidebar-foot，面板改为侧栏旁浮层
 *   - 引注页：主/副色与主程序对齐，选中行用左侧色条而不是 !important
 *   - 参考文献卡：改用主题变量，不再有硬编码浅蓝
 *   - 版本：docs/版本更新.md 顶部是 2.7.41
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');

const STYLE = 'web/css/style.css';
const FEATURES = 'web/css/features.css';
const PICKER_CSS = 'web/css/word-picker.css';
const TOKENS_CSS = 'web/css/tokens.css';
const INDEX = 'web/index.html';

test('tokens.css owns the muted ink token used by the shell', () => {
  // 2.7.42 起 --ink-3 只在 tokens.css 定义（style.css 不再有 :root 配色）
  const tokens = read(TOKENS_CSS);
  assert.ok(tokens.includes('--ink-3: #5f7186'),
    '--ink-3 must be #5f7186 so muted text keeps AA contrast on the light shell');
  const style = read(STYLE);
  assert.equal(/--ink-3:\s*#/.test(style), false, 'style.css must not re-declare --ink-3');
});

test('inputs keep a focus ring and never set outline:none', () => {
  const css = read(STYLE);
  const inputRule = /\ninput\[type="text"\][^{]*\{[^}]*\}/.exec(css);
  assert.ok(inputRule, 'the base input rule must stay in style.css');
  assert.equal(/outline\s*:/.test(inputRule[0]), false,
    'the base input rule must not set outline: none (keyboard users need the focus ring)');
  const ring = /:where\(button,a,input,textarea,select,\[tabindex\]\):focus-visible\s*\{([^}]*)\}/.exec(css);
  assert.ok(ring, 'a global :focus-visible ring must exist');
  assert.match(ring[1], /outline:\s*3px/, 'the focus ring must be 3px thick');
  assert.match(ring[1], /outline-offset:\s*3px/, 'the focus ring must be offset from the control');
});

test('the page gutter stays 30px 38px 48px and .pc-meta is defined once', () => {
  const css = read(STYLE);
  assert.match(css, /\n\.page\s*\{[^}]*padding:\s*30px 38px 48px;/,
    'the base .page gutter must stay 30px 38px 48px (the 96px experiment was reverted)');
  assert.equal(/padding:\s*96px/.test(css), false, 'the 96px gutter experiment must not come back');
  const definitions = css.match(/\.pc-meta\s*\{/g) || [];
  assert.equal(definitions.length, 1, '.pc-meta must be defined exactly once');
  const block = /\.pc-meta\s*\{([^}]*)\}/.exec(css);
  assert.ok(block, '.pc-meta rule must exist');
  assert.match(block[1], /font-size:\s*var\(--fs-13\)/, '.pc-meta must use the shared --fs-13 step');
  assert.match(block[1], /color:\s*var\(--ink-3\)/, '.pc-meta must use the muted ink token');
});

test('the task center toggle flows inside the sidebar and its panel is anchored', () => {
  const css = read(STYLE);
  const toggle = /\.task-center-toggle\s*\{([^}]*)\}/.exec(css);
  assert.ok(toggle, '.task-center-toggle must stay styled in style.css');
  assert.equal(/position:\s*fixed/.test(toggle[1]), false,
    'the toggle must flow inside .sidebar-foot, not be a fixed body button');
  const panel = /\.task-center\s*\{([^}]*)\}/.exec(css);
  assert.ok(panel, '.task-center panel rule must exist');
  assert.match(panel[1], /left:\s*244px/, 'the panel must be anchored next to the sidebar');
  assert.match(panel[1], /bottom:\s*20px/, 'the panel must sit above the bottom edge');
});

test('index.html keeps a single #btn-task-center inside .sidebar-foot', () => {
  const html = read(INDEX);
  const count = (html.match(/id="btn-task-center"/g) || []).length;
  assert.equal(count, 1, 'exactly one #btn-task-center may exist');
  const buttonAt = html.indexOf('id="btn-task-center"');
  const footAt = html.indexOf('class="sidebar-foot"');
  const pillAt = html.indexOf('id="llm-pill"');
  const asideEnd = html.indexOf('</aside>');
  const mainAt = html.indexOf('<main');
  assert.ok(footAt >= 0, '.sidebar-foot must exist');
  assert.ok(buttonAt > footAt && buttonAt < asideEnd,
    'the button must live inside .sidebar-foot, not at the end of <body>');
  assert.ok(buttonAt < pillAt, 'by DOM order the button must come before #llm-pill');
  assert.ok(buttonAt < mainAt, 'the button must not be a trailing body element after <main>');
});

test('word-picker aliases the shared row tokens instead of owning a palette', () => {
  // 2.7.42 起引注页不再自建配色：颜色/圆角只在 tokens.css，这里只留行色别名。
  const css = read(PICKER_CSS);
  const root = /:root\s*\{([^}]*)\}/.exec(css);
  assert.ok(root, 'word-picker.css must keep a :root for its row aliases');
  assert.match(root[1], /--zebra:\s*var\(--row-alt\)/, '--zebra must alias the shared --row-alt token');
  assert.match(root[1], /--select:\s*var\(--row-selected\)/, '--select must alias the shared --row-selected token');
  assert.equal(/--(?:ink|ink-2|ink-3|accent|accent-2|radius):/.test(css), false,
    'the picker must not re-declare palette tokens (tokens.css is the only source)');
  const selected = /\.wp-row\.selected[^{]*\{([^}]*)\}/.exec(css);
  assert.ok(selected, 'the selected-row rule must exist');
  assert.equal(selected[1].includes('!important'), false,
    'no !important: the rule order must be enough');
  assert.match(selected[1], /box-shadow:\s*inset 3px 0 0 var\(--accent-2\)/,
    'the selected row must carry a 3px left accent bar');
});

test('reference cards use theme tokens instead of hard-coded blues', () => {
  const css = read(FEATURES);
  const item = /\.reference-item\s*\{([^}]*)\}/.exec(css);
  assert.ok(item, '.reference-item must stay styled in features.css');
  assert.match(item[1], /border:\s*1px solid var\(--line\)/, 'the card border must use --line');
  assert.match(item[1], /background:\s*var\(--surface\)/, 'the card background must use --surface');
  const flag = /\.reference-item \.rp-flag\s*\{([^}]*)\}/.exec(css);
  assert.ok(flag, '.rp-flag must be styled');
  assert.match(flag[1], /color:\s*var\(--accent\)/, '.rp-flag text must use --accent');
  assert.equal(css.includes('#8fd6ff'), false, 'the old hard-coded light blue must be gone');
  assert.equal(/rgba\(255,\s*255,\s*255/.test(css), false,
    'no white rgba leftovers in the reference cards');
});

test('the changelog top heading is the current NPL version', () => {
  const changelog = read('docs/版本更新.md');
  const heading = /^#\s*NPL\s+(\d+\.\d+\.\d+)\s*$/m.exec(changelog);
  assert.ok(heading, 'the changelog must start with a "# NPL x.y.z" heading');
  const version = /APP_VERSION = "([^"]+)"/.exec(read('paperwhale_server/main.py'))[1];
  assert.equal(heading[1], version, 'the newest changelog section must match the application');
});
