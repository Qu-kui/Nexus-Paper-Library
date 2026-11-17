/* PaperWhale 2.7.43 UI 第三批：图标 SVG 化、状态圆点、阅读器顶栏分组、可访问性。
 *
 * 全部是静态回归：只读 web/ 源码文本，不启动浏览器。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');

const INDEX = 'web/index.html';
const STYLE = 'web/css/style.css';
const LIBRARY = 'web/js/library.js';
const APP = 'web/js/app.js';
const TITLEBAR = 'web/js/titlebar.js';
const READER_JS = 'web/js/reader.js';
const READER_NAV = 'web/js/reader-navigation.js';
const WP_JS = 'web/js/word-picker.js';
const WP_HTML = 'web/word-picker.html';
const WP_CSS = 'web/css/word-picker.css';

const SPRITE_IDS = [
  'i-library',
  'i-flask',
  'i-cards',
  'i-chat',
  'i-compass',
  'i-import',
  'i-settings',
  'i-grid',
  'i-rows',
  'i-copy',
  'i-spark',
  'i-search',
  'i-close',
];

test('index.html ships the 13-symbol SVG sprite before the app shell', () => {
  const html = read(INDEX);
  assert.ok(html.includes('<svg class="svg-sprite"'), 'the sprite must be an <svg class="svg-sprite">');
  const ids = [...html.matchAll(/<symbol id="([^"]+)"/g)].map((match) => match[1]);
  for (const id of SPRITE_IDS) {
    assert.ok(ids.includes(id), `the sprite must define #${id}`);
  }
  assert.equal(new Set(ids).size, ids.length, 'symbol ids must be unique');
  assert.ok(html.indexOf('<svg class="svg-sprite"') < html.indexOf('<aside'),
    'the sprite must be defined at the start of <body>, before the sidebar');
});

test('every sidebar nav item uses its SVG icon instead of a text glyph', () => {
  const html = read(INDEX);
  const nav = html.slice(html.indexOf('<nav'), html.indexOf('</nav>'));
  assert.equal((nav.match(/class="nav-item/g) || []).length, 7, 'the sidebar keeps seven literature workspace entries');
  assert.ok(!nav.includes('data-page="employment"'), 'employment has been withdrawn');
  const mapping = [
    ['library', 'i-library'],
    ['methods', 'i-flask'],
    ['cards', 'i-cards'],
    ['chat', 'i-chat'],
    ['discover', 'i-compass'],
    ['import', 'i-import'],
    ['settings', 'i-settings'],
  ];
  for (const [page, icon] of mapping) {
    const at = nav.indexOf(`data-page="${page}"`);
    assert.ok(at >= 0, `nav item ${page} must exist`);
    assert.ok(
      nav.slice(at, at + 240).includes(`<span class="nav-ico"><svg class="ico"><use href="#${icon}"/></svg></span>`),
      `nav item ${page} must render #${icon} through .nav-ico > svg.ico`,
    );
  }
  for (const glyph of ['▤', '⚗', '◈', '✦', '⌕', '↧', '⚙']) {
    assert.equal(html.includes(glyph), false, `the old text glyph ${glyph} must be gone from index.html`);
  }
});

test('search boxes, the drop zone and the view toggle all use sprite icons', () => {
  const html = read(INDEX);
  const searchIco = '<span class="search-ico"><svg class="ico"><use href="#i-search"/></svg></span>';
  assert.equal(html.split(searchIco).length - 1, 2, 'both search boxes must use the #i-search sprite');
  assert.match(html, /<div class="dz-mark"><svg class="ico" style="width:26px;height:26px"><use href="#i-import"\/><\/svg><\/div>/,
    'the drop zone must use the #i-import sprite at 26px');
  assert.match(html, /data-view="cards"[^>]*aria-label="卡片视图"><svg class="ico"><use href="#i-grid"\/>/,
    'the card view toggle must use #i-grid and carry an aria-label');
  assert.match(html, /data-view="compact"[^>]*aria-label="紧凑列表"><svg class="ico"><use href="#i-rows"\/>/,
    'the compact view toggle must use #i-rows and carry an aria-label');

  const css = read(STYLE);
  const sprite = /\.svg-sprite\s*\{([^}]*)\}/.exec(css);
  assert.ok(sprite, '.svg-sprite must be styled');
  assert.match(sprite[1], /width:\s*0/, 'the sprite host must be collapsed');
  const ico = /\.ico\s*\{([^}]*)\}/.exec(css);
  assert.ok(ico, '.ico must be styled');
  assert.match(ico[1], /width:\s*16px/, 'icons must be 16px wide');
  assert.match(ico[1], /stroke-width:\s*1\.6/, 'icons must use a 1.6 stroke');
  assert.equal(/font-size/.test(ico[1]), false, 'icon sizing must not depend on font-size');
  const small = /\.icon-btn \.ico\s*\{([^}]*)\}/.exec(css);
  assert.ok(small, '.icon-btn .ico must be styled');
  assert.match(small[1], /width:\s*14px/, 'row action icons must be 14px');
});

test('the library status column renders semantic dots instead of a badge', () => {
  const js = read(LIBRARY);
  const start = js.indexOf('function rowStatusHtml(');
  assert.ok(start >= 0, 'rowStatusHtml() must exist');
  const body = js.slice(start, js.indexOf('\n}', start));
  for (const cls of ['st-ok', 'st-idle', 'st-bad', 'st-run', 'st-warn']) {
    assert.ok(body.includes(cls), `the status dots must cover ${cls}`);
  }
  assert.match(body, /<span class="pr-status" aria-label="\$\{escapeHtml\(summary\)\}"/,
    'the dot group must expose the summary as aria-label (the native title moved to the custom tooltip)');
  assert.ok((body.match(/<i class="st-dot /g) || []).length >= 3,
    'each row must render at least the PDF / index / analysis dots');
  const rowStart = js.indexOf('function rowHtml(');
  const rowHtml = js.slice(rowStart, js.indexOf('\n}', rowStart));
  assert.ok(rowHtml.includes('${rowStatusHtml(paper)}'), 'rowHtml must delegate to rowStatusHtml');
  assert.equal(js.includes('state-badge'), false, 'the old .state-badge markup must be gone');
  assert.match(js, /<span class="plh-label" title="[^"]*完成[^"]*">状态<\/span>/,
    'the 状态 column header must carry the legend title');
  // 2.7.44：圆点必须和列头「状态」左对齐（实测起点 x 一致），不能再用 flex-end
  const css = read(STYLE);
  assert.match(css, /\.paper-row-item \.pr-status\s*\{[^}]*justify-content:\s*flex-start/,
    'the status dots must be left-aligned with the 状态 header (flex-start, not flex-end)');
});

test('the status column is pinned to a fixed 80px width', () => {
  const js = read(LIBRARY);
  assert.match(js, /const COMPACT_DEFAULT_COLS = \[240, 160, 140, 56, 80\];/,
    'the fifth (状态) column must default to 80px');
  assert.match(js, /const COMPACT_FIXED_COLS = \{ 4: 80 \};/,
    'the 状态 column must be declared as a fixed column');
  assert.match(js, /function normalizeCompactCols\(widths\) \{[\s\S]{0,200}COMPACT_FIXED_COLS\[index\] \?\? width/,
    'normalizeCompactCols must force the pinned width over any stored value');
  assert.match(js, /return normalizeCompactCols\(raw\.map\(/,
    'readCompactCols must normalise widths coming from old localStorage data');
  assert.match(js, /normalizeCompactCols\(saved \|\| COMPACT_DEFAULT_COLS\)/,
    'applyCompactColumns must normalise the saved/default widths too');
  const resizers = [...js.matchAll(/<span class="col-resizer" data-col="(\d)"/g)].map((match) => match[1]);
  assert.deepEqual(resizers, ['0', '1', '2', '3'],
    'only columns 0-3 may keep a resize handle; 状态 is fixed');
  assert.equal(js.includes('data-col="4"'), false, 'the 状态 column must not expose a resizer');
  assert.ok(js.includes('const index = Number(handle.dataset.col);'),
    'the drag handler must resolve the column index');
  assert.ok(js.includes('if (index in COMPACT_FIXED_COLS) return;'),
    'the drag handler must bail out early for fixed columns');
});

test('library detail actions use sprite icons and the new empty-summary copy', () => {
  const js = read(LIBRARY);
  const closeIcon = 'class="detail-close" id="detail-close" title="关闭侧栏" aria-label="关闭侧栏"><svg class="ico"><use href="#i-close"/>';
  assert.equal(js.split(closeIcon).length - 1, 2,
    'both detail templates must render the #i-close sprite with aria-label="关闭侧栏"');
  const mapping = [
    ['copy-title', 'i-copy'],
    ['summarize', 'i-spark'],
    ['delete', 'i-close'],
  ];
  for (const [action, icon] of mapping) {
    const at = js.indexOf(`data-action="${action}"`);
    assert.ok(at >= 0, `row action ${action} must exist`);
    assert.ok(js.slice(at, at + 320).includes(`href="#${icon}"`),
      `row action ${action} must use #${icon}`);
  }
  assert.ok(js.includes('点右侧的「AI 精读」让 AI 先读一遍'),
    'the empty-summary hint must point at the AI 精读 button');
  assert.equal(js.includes('点 ✦ 让鲸先读一遍'), false, 'the old ✦ hint must be gone');
});

test('the reader toolbar is split into four groups and injected in place', () => {
  const html = read(INDEX);
  for (const id of ['rt-nav', 'rt-search', 'rt-pages', 'rt-actions']) {
    assert.ok(html.includes(`class="rt-group" id="${id}"`),
      `the reader toolbar must keep #${id} as its own .rt-group`);
  }
  const css = read(STYLE);
  assert.match(css, /\.rt-group\s*\{[^}]*display:\s*flex/, 'the toolbar groups must be flex rows');
  assert.match(css, /\.rt-group \+ \.rt-group\s*\{[^}]*border-left:\s*1px solid var\(--shell-line-strong\)/,
    'adjacent groups must be separated by the shell hairline token');

  const nav = read(READER_NAV);
  assert.ok(nav.includes("document.querySelector('#reader #rt-nav')"),
    'back/forward must be injected into #rt-nav');
  assert.ok(nav.includes("document.querySelector('#reader .reader-tools')"),
    'the old .reader-tools target must stay as a fallback');
  assert.ok(nav.includes('tools?.insertBefore(node,tools.firstChild)'),
    'back/forward must be prepended inside the nav group');

  const reader = read(READER_JS);
  assert.ok(reader.includes("reader?.querySelector('#rt-actions')"),
    'the page tools must target #rt-actions');
  assert.ok((reader.match(/toolActions\?\.insertBefore\([\s\S]{0,60}reader-close/g) || []).length >= 2,
    'both the pager toggle and the zoom control must sit before the close button');
});

test('the bulk-analyze CTA is demoted to a secondary button', () => {
  const html = read(INDEX);
  assert.match(html, /<button class="btn ghost" id="btn-analyze-all"/,
    '#btn-analyze-all must be "btn ghost" (the primary action is adding papers)');
});

test('app.js keeps aria-current="page" in sync with the active nav item', () => {
  const app = read(APP);
  assert.ok(app.includes('btn.setAttribute("aria-current", "page")'),
    'the active nav item must announce aria-current="page"');
  assert.ok(app.includes('btn.removeAttribute("aria-current")'),
    'the previous nav item must drop aria-current');
});

test('the help overlay is modal and traps focus with restore', () => {
  const js = read(TITLEBAR);
  assert.ok(js.includes('overlay.setAttribute("aria-modal", "true")'),
    'the guide overlay must declare aria-modal="true"');
  assert.ok(js.includes('function guideKeydown(event)'),
    'a keydown handler must implement the focus trap');
  assert.ok(js.includes('if (event.key !== "Tab") return;'), 'the trap must only intercept Tab');
  assert.ok(
    js.includes('if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last.focus(); }'),
    'Shift+Tab on the first focusable must wrap to the last',
  );
  assert.ok(
    js.includes('else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first.focus(); }'),
    'Tab on the last focusable must wrap to the first',
  );
  assert.ok(js.includes('guideLastFocus = document.activeElement'),
    'opening the guide must remember the previously focused element');
  assert.ok(js.includes('guideLastFocus.focus()'), 'closing the guide must restore the previous focus');
});

test('the word picker fixes category scoping and becomes keyboard accessible', () => {
  const js = read(WP_JS);
  assert.ok(js.includes('scope: r.path'),
    'category nodes must carry scope: r.path, otherwise nodeEl reads undefined and filtering never happens');
  assert.ok(js.includes("el.setAttribute('role', 'treeitem')"), 'tree rows must be treeitems');
  assert.ok(js.includes("el.setAttribute('aria-selected'"), 'treeitems must announce the selection state');
  assert.ok(js.includes("el.setAttribute('aria-expanded'"), 'parent nodes must announce expansion');
  assert.ok(js.includes('el.tabIndex = 0'), 'treeitems must be tabbable');
  for (const key of ['ArrowRight', 'ArrowLeft', 'ArrowDown', 'ArrowUp']) {
    assert.ok(js.includes(`ev.key === '${key}'`), `the tree must handle ${key}`);
  }
  const html = read(WP_HTML);
  assert.ok(html.includes('id="tree"') && html.includes('role="tree"'),
    'the tree container must declare role="tree"');
  const css = read(WP_CSS);
  const focus = /\.wp-node:focus-visible\s*\{([^}]*)\}/.exec(css);
  assert.ok(focus, '.wp-node:focus-visible must be styled');
  assert.match(focus[1], /outline:\s*2px solid var\(--accent-2\)/,
    'the focused tree row must show a visible ring');
});

test('reduced motion and the shared type token are respected', () => {
  const css = read(STYLE);
  const at = css.indexOf('@media (prefers-reduced-motion: reduce)');
  assert.ok(at >= 0, 'a prefers-reduced-motion block must exist');
  const block = css.slice(at, at + 400);
  assert.match(block, /animation-duration:\s*\.01ms\s*!important/, 'animations must collapse under reduced motion');
  assert.match(block, /transition-duration:\s*\.01ms\s*!important/, 'transitions must collapse under reduced motion');
  const reader = read(READER_JS);
  assert.equal(reader.includes('font-size:13px'), false,
    'the reader body must not use a 13px literal');
  assert.ok(reader.includes('font-size:var(--fs-13)'),
    'the reader body must use the shared --fs-13 step');
});

test('the status dot tooltip is a custom layer above the cursor', () => {
  const js = read(LIBRARY);
  const start = js.indexOf('function rowStatusHtml(');
  const status = js.slice(start, js.indexOf('\n}', start));
  assert.ok(status.includes('data-tip='), 'each dot must carry its tip text in data-tip');
  assert.equal(/title=/.test(status), false,
    'neither the dots nor the .pr-status wrapper may use the native title');
  assert.ok(js.includes('let dotTipEl = null;'), 'one reusable tooltip element must be kept module-wide');
  for (const fn of ['function ensureDotTip()', 'function showDotTip(dot)', 'function hideDotTip()']) {
    assert.ok(js.includes(fn), `${fn} must exist`);
  }
  assert.ok(js.includes('document.addEventListener("mouseover"') && js.includes('document.addEventListener("mouseout"'),
    'the tooltip must be delegated from document');
  assert.ok(js.includes('".st-dot[data-tip]"'), 'only dots carrying a data-tip show the tooltip');
  assert.ok(js.includes('window.addEventListener("scroll", hideDotTip, true)'),
    'scroll must hide the tooltip (capture phase)');
  assert.ok(js.includes('window.addEventListener("resize", hideDotTip)'), 'resize must hide the tooltip');
  // 定位：圆点右上方；越界回退
  assert.ok(js.includes('let left = rect.right + margin;'), 'the tip must sit to the right of the dot');
  assert.ok(js.includes('let top = rect.top - tip.offsetHeight - margin;'), 'the tip must sit above the dot');
  assert.ok(js.includes('if (left + tip.offsetWidth > window.innerWidth - margin)'),
    'the tip must fall back when it would overflow the right edge');
  assert.ok(js.includes('left = window.innerWidth - tip.offsetWidth - margin;'),
    'the right-edge fallback must clamp the tip inside the window');
  assert.ok(js.includes('if (top < margin) top = rect.bottom + margin;'),
    'the tip must drop below the dot when it would overflow the top');
  assert.ok(js.includes('tip.classList.add("show")') && js.includes('dotTipEl?.classList.remove("show")'),
    'visibility must be toggled through the .show class');

  const css = read(STYLE);
  const tip = /\.pw-dot-tip\s*\{([^}]*)\}/.exec(css);
  assert.ok(tip, '.pw-dot-tip must be styled');
  assert.match(tip[1], /position:\s*fixed/, 'the tooltip layer must be fixed positioned');
  assert.match(tip[1], /pointer-events:\s*none/, 'the tooltip must never cover the cursor');
  assert.match(tip[1], /z-index:\s*9600/, 'the tooltip must sit above the list');
  assert.match(tip[1], /background:\s*var\(--navy\)/, 'the tooltip must use the navy surface');
  assert.match(tip[1], /font-size:\s*var\(--fs-12\)/, 'the tooltip must use the shared 12px step');
  assert.match(css, /\.pw-dot-tip\.show\s*\{[^}]*opacity:\s*1/, '.pw-dot-tip.show must reveal the tooltip');
});

test('the detail toggle is a rail inside .library-split, after #paper-detail', () => {
  const html = read(INDEX);
  const buttonAt = html.indexOf('id="btn-detail-toggle"');
  assert.ok(buttonAt >= 0, 'the detail rail must keep the #btn-detail-toggle id');
  assert.equal(html.includes('class="btn tiny ghost" id="btn-detail-toggle"'), false,
    'the old toolbar button must be gone');
  const splitAt = html.indexOf('<div class="library-split">');
  const detailAt = html.indexOf('<aside class="paper-detail" id="paper-detail">');
  assert.ok(splitAt >= 0 && detailAt >= 0, 'the library split and detail panel must exist');
  assert.ok(splitAt < detailAt && detailAt < buttonAt,
    'the rail must come after #paper-detail inside .library-split');
  assert.ok(buttonAt < html.indexOf('</section>', splitAt), 'the rail must stay inside the library section');
  const button = html.slice(buttonAt - 120, buttonAt + 420);
  assert.ok(button.includes('class="detail-rail"'), 'the toggle must be a .detail-rail button');
  assert.ok(button.includes('aria-controls="paper-detail"'), 'the rail must control #paper-detail');
  assert.ok(button.includes('aria-expanded="false"'), 'the rail must start collapsed');
  assert.ok(button.includes('title="展开详情"'), 'the rail must start with the 展开详情 tooltip');
  assert.ok(button.includes('<span class="detail-rail-text">展开</span>'), 'the rail must start with 展开 text');
  const toolbarAt = html.indexOf('<div class="toolbar">');
  const optionsAt = html.indexOf('<div class="library-options">', toolbarAt);
  assert.ok(!(toolbarAt >= 0 && toolbarAt < buttonAt && buttonAt < optionsAt),
    'the rail must not live inside the library toolbar anymore');
});

test('the detail panel is a fixed drawer revealed by .detail-open', () => {
  const css = read(STYLE);
  const rail = /\.detail-rail\s*\{([^}]*)\}/.exec(css);
  assert.ok(rail, '.detail-rail must be styled');
  assert.match(rail[1], /position:\s*fixed/, 'the rail must be fixed to the window edge');
  assert.match(rail[1], /width:\s*26px/, 'the rail must be 26px wide');
  assert.match(rail[1], /height:\s*132px/, 'the rail must be 132px tall');
  assert.match(rail[1], /right:\s*10px/, 'the rail must hug the right window edge');
  assert.match(rail[1], /border-radius:\s*var\(--radius-pill\)/,
    'the rail pill must use the shared pill radius token (999px clamps to 13px on a 26px width)');
  const railText = /\.detail-rail-text\s*\{([^}]*)\}/.exec(css);
  assert.ok(railText, '.detail-rail-text must be styled');
  assert.match(railText[1], /writing-mode:\s*vertical-rl/, 'the rail label must be vertical');

  const panel = /#paper-detail\s*\{([^}]*)\}/.exec(css);
  assert.ok(panel, '#paper-detail must be styled');
  assert.match(panel[1], /position:\s*fixed/, 'the detail panel must be a fixed drawer');
  assert.match(panel[1], /top:\s*0;\s*right:\s*0;\s*bottom:\s*0/,
    'the drawer must span the full window height');
  assert.match(panel[1], /transform:\s*translateX\(102%\)/, 'the drawer must start off-screen');
  assert.match(panel[1], /visibility:\s*hidden/, 'the drawer must start hidden');
  assert.match(css, /\.library-split\.detail-open #paper-detail\s*\{[^}]*transform:\s*none;\s*visibility:\s*visible/,
    '.detail-open must slide the drawer in');
  assert.match(css, /body\.desktop-shell #paper-detail\s*\{[^}]*top:\s*36px/,
    'the drawer must start below the custom desktop title bar');
  assert.match(css, /body\.desktop-shell \.detail-rail\s*\{[^}]*top:\s*calc\(50% \+ 18px\)/,
    'the rail must shift down by half the title bar');

  assert.equal(/#paper-detail\s*\{[^}]*position:\s*sticky/.test(css), false,
    'the old sticky in-flow panel must be gone');
  assert.equal(/\.library-split\.detail-open \.library-main[^{]*\{[^}]*overflow-x/.test(css), false,
    'opening the drawer must not force a horizontal scrollbar');
});

test('setDetailOpen only toggles classes and rail state (no window resize)', () => {
  const js = read(LIBRARY);
  const start = js.indexOf('function setDetailOpen(');
  assert.ok(start >= 0, 'setDetailOpen() must exist');
  const body = js.slice(start, js.indexOf('\n}', start));
  assert.equal(body.includes('resize_window'), false, 'the drawer must not resize the native window');
  assert.equal(body.includes('detailBaseWidth'), false, 'the old width bookkeeping must be gone');
  assert.ok(body.includes('classList.toggle("detail-open"'), 'opening/closing must only toggle .detail-open');
  assert.ok(body.includes('rail.setAttribute("aria-expanded"'), 'the rail must announce aria-expanded');
  assert.ok(body.includes('willOpen ? "收起详情" : "展开详情"'),
    'the rail title must flip between 展开详情 and 收起详情');
  assert.ok(body.includes('willOpen ? "收起" : "展开"'),
    'the rail label must flip between 展开 and 收起');
  assert.ok(body.includes('cleanupDetailPdf()'), 'closing must release the PDF preview');
});

test('the search input keeps its 34px gutter for the magnifier icon', () => {
  const css = read(STYLE);
  const base = /\.search-wrap input\s*\{([^}]*)\}/.exec(css);
  assert.ok(base, '.search-wrap input must keep its own rule');
  assert.match(base[1], /padding-left:\s*34px/, '.search-wrap input must reserve 34px for the icon');
  assert.ok(
    css.includes('/* 文件末尾的共享控件规则会把 padding 重置成 9px 11px，这里用更高特异性把左侧 34px 留给放大镜 */'),
    'the override must document that the shared control rule resets the padding',
  );
  const override = /\.page \.toolbar \.search-wrap input\[type="search"\]\s*\{([^}]*)\}/.exec(css);
  assert.ok(override,
    'the higher-specificity override must exist: the shared input rule (0,3,1) beats .search-wrap input (0,1,1)');
  assert.match(override[1], /padding-left:\s*34px/, 'the override must keep the 34px icon gutter');
  assert.ok(css.indexOf('.search-wrap input {') < css.indexOf('.page .toolbar .search-wrap input[type="search"]'),
    'the override must come after the base rule it fixes');
});

test('the detail panel lists facts row by row and drops the acquisition report', () => {
  const js = read(LIBRARY);
  for (const dead of ['detail-fetch-report', 'pdf-fetch/report', 'browser_attempts', 'detailStatus(']) {
    assert.equal(js.includes(dead), false, `${dead} must be removed from library.js`);
  }
  assert.ok(js.includes('<div class="detail-meta"><div class="detail-facts">'),
    'the meta block must wrap a .detail-facts grid (the .detail-meta class itself must stay)');
  for (const label of ['作者', '年份', '期刊', '卷期页', '分类', '状态']) {
    assert.ok(js.includes(`<dt>${label}</dt>`), `the facts grid must have a dt for ${label}`);
  }
  assert.ok(js.includes('<dt>批注</dt>'), 'the annotation count must still be listed as an optional fact');
  assert.ok(js.includes('class="facts-authors"'), 'authors must use the two-line clamp class');
  assert.ok((js.match(/class="facts-one-line"/g) || []).length >= 3,
    'venue / volume-pages / category must use the single-line ellipsis class');
  // 2.7.52：状态行只显示 PDF 状态（索引/精读已在列表圆点里，不再重复一行文字）
  const statusAt = js.indexOf('<dt>状态</dt>');
  assert.ok(statusAt >= 0, 'the facts grid must have a 状态 row');
  const statusRow = js.slice(statusAt, statusAt + 120);
  assert.ok(statusRow.includes('statusInfo(paper).label'),
    'the 状态 row must show only the PDF label');
  assert.equal(statusRow.includes('statusInfo(paper).detail'), false,
    'the 状态 row must not use statusInfo(paper).detail anymore');
  assert.equal(statusRow.includes('索引：') || statusRow.includes('精读：'), false,
    'the 状态 row must not repeat the index / analysis text (those live in the dots)');
});

test('the PDF preview sits above the summary and scrolls inside its own box', () => {
  const js = read(LIBRARY);
  const previewAt = js.indexOf('<h4>PDF 预览</h4>');
  const summaryAt = js.indexOf('<h4>摘要</h4>');
  assert.ok(previewAt >= 0 && summaryAt >= 0, 'both detail sections must exist');
  assert.ok(previewAt < summaryAt, 'PDF 预览 must render before 摘要');
  const css = read(STYLE);
  const thumbs = /\.detail-thumbs\s*\{([^}]*)\}/.exec(css);
  assert.ok(thumbs, '.detail-thumbs must be styled');
  assert.match(thumbs[1], /height:\s*min\(56vh,\s*430px\)/,
    'the preview box must be capped at min(56vh, 430px)');
  assert.match(thumbs[1], /overflow-y:\s*auto/, 'extra pages must scroll inside the box');
  assert.match(thumbs[1], /padding:\s*8px/, 'the preview box must keep 8px padding');
  assert.match(thumbs[1], /border:\s*1px solid var\(--line\)/, 'the preview box must keep its outline');
  const thumb = /\.detail-thumb\s*\{([^}]*)\}/.exec(css);
  assert.ok(thumb, '.detail-thumb must be styled');
  assert.match(thumb[1], /background:\s*var\(--surface\)/,
    'each page thumbnail must sit on the surface colour');
  assert.ok(js.includes('{ root: box, rootMargin: "120px" }'),
    'the lazy-load observer must use the scrolling box itself as its root');
});
