/* PaperWhale 2.7.53 + 2.7.54：动效系统、主题预设与自定义强调色。
 *
 * ① 动效：tokens.css 的时长/曲线令牌、style.css 的 4 个入场 keyframes、
 *    prefers-reduced-motion 的令牌覆写与 spinner 例外。
 * ② 主题：theme.js 的 8 套 THEME_PRESETS（外壳 + 内容）与 applyAppearance，
 *    设置页主题网格、首屏内联缓存脚本、tokens 的 --shell-* 令牌。
 * ③ 强调色：ui_accent 的运行时变量与 #RRGGBB 校验。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');
const escapeRe = (text) => text.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

const TOKENS = 'web/css/tokens.css';
const STYLE = 'web/css/style.css';
const WP_CSS = 'web/css/word-picker.css';
const THEME = 'web/js/theme.js';
const SETTINGS = 'web/js/settings.js';
const APP = 'web/js/app.js';
const INDEX = 'web/index.html';

test('tokens.css defines the motion durations and easings', () => {
  const tokens = read(TOKENS);
  const durations = [
    ['--dur-instant', '60ms'],
    ['--dur-fast', '120ms'],
    ['--dur-normal', '180ms'],
    ['--dur-moderate', '260ms'],
    ['--dur-slow', '340ms'],
  ];
  for (const [name, value] of durations) {
    assert.ok(tokens.includes(`${name}: ${value};`), `tokens.css must define ${name}: ${value}`);
  }
  for (const ease of ['--ease-standard', '--ease-decelerate', '--ease-accelerate', '--ease-spring']) {
    assert.match(tokens, new RegExp(`${ease}:\\s*cubic-bezier\\(`), `tokens.css must define ${ease}`);
  }
});

test('style.css animates the four entrance surfaces with named keyframes', () => {
  const css = read(STYLE);
  for (const name of ['pwPageIn', 'pwOverlayIn', 'pwModalIn', 'pwPanelIn']) {
    assert.match(css, new RegExp(`@keyframes ${name}\\s*\\{`), `${name} keyframes must exist`);
  }
  const references = [
    ['.page.active', 'pwPageIn', 'var(--dur-normal)'],
    ['.modal:not(.hidden)', 'pwOverlayIn', 'var(--dur-fast)'],
    ['.modal:not(.hidden) .modal-card', 'pwModalIn', 'var(--dur-moderate)'],
    ['.task-center:not(.hidden)', 'pwPanelIn', 'var(--dur-moderate)'],
  ];
  for (const [selector, animation, duration] of references) {
    const rule = new RegExp(`${escapeRe(selector)}\\s*\\{[^}]*animation:\\s*${animation}\\s+${escapeRe(duration)}`);
    assert.match(css, rule, `${selector} must use ${animation} with ${duration}`);
  }
});

test('reduced motion neutralises the durations but keeps the spinner spinning', () => {
  const css = read(STYLE);
  const at = css.indexOf('@media (prefers-reduced-motion: reduce)');
  assert.ok(at >= 0, 'the reduced-motion media block must exist');
  const block = css.slice(at);
  for (const token of ['--dur-instant', '--dur-fast', '--dur-normal', '--dur-moderate', '--dur-slow']) {
    assert.ok(block.includes(`${token}: 1ms`), `${token} must be overridden to 1ms under reduced motion`);
  }
  assert.match(block, /\.spinner\s*\{[^}]*animation-duration:\s*\.7s\s*!important/,
    'the loading spinner must keep spinning under reduced motion');
  assert.match(block, /\.spinner\s*\{[^}]*animation-iteration-count:\s*infinite\s*!important/,
    'the loading spinner must keep looping under reduced motion');
});

test('theme.js ships eight theme presets with shell and content palettes', () => {
  const theme = read(THEME);
  assert.ok(theme.includes('export const DEFAULT_THEME = "midnight";'),
    'the default theme must be midnight');
  assert.ok(theme.includes('export const DEFAULT_ACCENT = "#0e7c86";'),
    'the default accent must stay #0e7c86');
  assert.ok(theme.includes('export const THEME_PRESETS = ['), 'THEME_PRESETS must be exported');
  const section = theme.slice(theme.indexOf('THEME_PRESETS'), theme.indexOf('\n];', theme.indexOf('THEME_PRESETS')));
  const keys = [...section.matchAll(/key: "([a-z0-9-]+)"/g)].map((match) => match[1]);
  assert.deepEqual(keys, ['midnight', 'graphite', 'violet', 'paper', 'cream', 'celadon', 'sakura', 'daylight'],
    'exactly eight presets must exist, in the documented order');
  for (const name of ['深海', '石墨', '紫夜', '纸白', '米白', '青瓷', '樱粉', '日光']) {
    assert.ok(section.includes(`name: "${name}"`), `the preset ${name} must keep its display name`);
  }
  assert.equal((section.match(/dark: true/g) || []).length, 3, 'three presets must use a dark shell');
  assert.equal((section.match(/dark: false/g) || []).length, 5, 'five presets must use a light shell');
  assert.equal((section.match(/shell: \{/g) || []).length, 8, 'every preset must define a shell palette');
  assert.equal((section.match(/content: \{/g) || []).length, 8, 'every preset must define a content palette');
  for (const accent of ['#0e7c86', '#2563eb', '#7c3aed', '#b45309', '#db2777']) {
    assert.ok(section.includes(`accent: "${accent}"`), `the preset accents must include ${accent}`);
  }
  for (const fn of ['export function getTheme(', 'export function applyAppearance(']) {
    assert.ok(theme.includes(fn), `${fn} must be exported`);
  }
});

test('applyAppearance writes the shell + accent variables and caches them', () => {
  const theme = read(THEME);
  assert.ok(theme.includes('const STORAGE_KEY = "pw.appearance.vars";'),
    'the first-paint cache key must be pw.appearance.vars');
  assert.ok(theme.includes('const LEGACY_KEY = "pw.accent.vars";'),
    'the old accent cache key must be read as a fallback');
  for (const cssVar of [
    '--shell-bg', '--shell-bg-mid', '--shell-bg-2', '--shell-bg-3',
    '--shell-text', '--shell-text-strong', '--shell-muted', '--shell-faint',
    '--shell-nav', '--shell-nav-strong', '--shell-pill-text', '--shell-chrome-text',
    '--shell-line', '--shell-line-strong', '--shell-hover', '--shell-hover-strong',
    '--shell-hover-active', '--shell-menu-bg', '--shell-danger', '--shell-active-text',
  ]) {
    assert.ok(theme.includes(`"${cssVar}"`), `theme.js must map ${cssVar}`);
  }
  assert.ok(theme.includes('root.style.setProperty(name, color)'),
    'applyAppearance must write the variables on <html>');
  assert.ok(theme.includes('localStorage.setItem(STORAGE_KEY, JSON.stringify(vars))'),
    'applyAppearance must cache the variables for the inline first-paint script');
  assert.match(theme, /export function applyAccent\([\s\S]{0,400}applyAppearance\(/,
    'applyAccent must stay as a compatibility wrapper around applyAppearance');
});

test('settings, index.html and app.js all drive ui_theme + ui_accent', () => {
  const settings = read(SETTINGS);
  assert.ok(settings.includes('appearance:["ui_theme","ui_accent"]'),
    'groupFields must map appearance -> ui_theme + ui_accent');
  assert.ok(settings.includes('ui_theme:"set-ui-theme"'), 'fieldIds must map ui_theme -> #set-ui-theme');
  assert.ok(settings.includes('ui_accent:"set-ui-accent"'), 'fieldIds must map ui_accent -> #set-ui-accent');
  assert.ok(settings.includes('ui_theme: themeValue(),'), 'readForm must submit ui_theme');
  assert.ok(settings.includes('ui_accent: accentValue(),'), 'readForm must submit ui_accent');
  assert.ok(settings.includes('setValue("set-ui-theme", settings.ui_theme || DEFAULT_THEME);'),
    'fillForm must restore ui_theme into the hidden input');
  for (const fn of ['function themeValue()', 'function previewAppearance()', 'function syncAppearancePickers()', 'function buildThemePresets()']) {
    assert.ok(settings.includes(fn), `settings.js must define ${fn}`);
  }
  assert.ok(settings.includes('buildThemePresets();'), 'the theme grid must be built on page setup');
  assert.ok(settings.includes('applyAppearance({ theme: saved.ui_theme || DEFAULT_THEME, accent: saved.ui_accent || DEFAULT_ACCENT });'),
    'saving the appearance group must apply the new look immediately');

  const html = read(INDEX);
  assert.match(html, /<input id="set-ui-theme" type="hidden" value="midnight" \/>/,
    'the theme key must live in a hidden input');
  assert.ok(html.includes('id="theme-presets"'), 'the theme preset grid container must exist');
  assert.ok(html.includes('id="accent-swatches"'), 'the accent swatch grid must stay');
  const inlineAt = html.indexOf('localStorage.getItem("pw.appearance.vars")');
  assert.ok(inlineAt > html.indexOf('href="/css/tokens.css"'),
    'the appearance cache script must come after the token stylesheet');
  assert.ok(inlineAt < html.indexOf('</head>'), 'the appearance cache script must run in <head>');
  assert.ok(html.includes('localStorage.getItem("pw.accent.vars")'),
    'the inline script must fall back to the old pw.accent.vars cache');

  const app = read(APP);
  assert.ok(app.includes('import { applyAppearance, DEFAULT_THEME } from "./theme.js";'),
    'app.js must import applyAppearance and DEFAULT_THEME');
  assert.ok(app.includes('applyAppearance({ theme: settings.ui_theme || DEFAULT_THEME, accent: settings.ui_accent });'),
    'app.js must apply the saved appearance on startup');
  assert.ok(app.includes('applyAppearance({ theme: state.settings.ui_theme || DEFAULT_THEME, accent: state.settings.ui_accent });'),
    'app.js must re-apply the appearance after settings change');
  assert.ok(app.includes('on("settings-changed"'), 'app.js must listen for settings-changed');
});

test('tokens.css owns the shell palette and the shell surfaces consume it', () => {
  const tokens = read(TOKENS);
  for (const name of [
    '--shell-bg', '--shell-bg-mid', '--shell-bg-2', '--shell-bg-3',
    '--shell-text', '--shell-text-strong', '--shell-muted', '--shell-faint',
    '--shell-nav', '--shell-nav-strong', '--shell-pill-text', '--shell-chrome-text',
    '--shell-line', '--shell-line-strong', '--shell-hover', '--shell-hover-strong',
    '--shell-hover-active', '--shell-menu-bg', '--shell-danger', '--shell-active-text',
  ]) {
    assert.match(tokens, new RegExp(`${name}:\\s*[^;]+;`), `tokens.css must define ${name}`);
  }
  const css = read(STYLE);
  assert.equal(css.includes('#0b2136'), false, 'the old sidebar navy literal must be gone from style.css');
  assert.match(css, /\.sidebar\s*\{[^}]*background:\s*linear-gradient\(168deg,\s*var\(--shell-bg\)/,
    'the sidebar must paint its gradient from the shell tokens');
  assert.match(css, /\.app-titlebar\s*\{[^}]*background:\s*linear-gradient\(180deg,\s*var\(--shell-bg-3\)/,
    'the custom title bar must paint from the shell tokens');
  assert.match(css, /\.reader-head\s*\{[^}]*background:\s*var\(--shell-bg\)/,
    'the reader top bar must use the shell background token');
  assert.match(css, /\.task-center-toggle\s*\{[^}]*background:\s*var\(--shell-hover\)/,
    'the task-center toggle must use the shell hover token');
  assert.match(css, /\.rt-group \+ \.rt-group\s*\{[^}]*border-left:\s*1px solid var\(--shell-line-strong\)/,
    'the reader group separators must use the shell line token');
  assert.match(css, /\.theme-presets\s*\{/, 'the theme preset grid must be styled');
  for (const cls of ['.theme-preset {', '.theme-preset-chip {', '.theme-preset-dot {']) {
    assert.ok(css.includes(cls), `style.css must style ${cls}`);
  }

  const wordPicker = read(WP_CSS);
  assert.equal(wordPicker.includes('var(--navy)'), false, 'word-picker.css must not use the old --navy token');
  assert.match(wordPicker, /\.wp-head\s*\{[^}]*background:\s*linear-gradient\(160deg,\s*var\(--shell-bg\)/,
    'the word-picker top bar must paint from the shell tokens');
});

test('the backend stores ui_theme/ui_accent and rejects invalid values', () => {
  const config = read('paperwhale_server/config.py');
  assert.match(config, /"ui_theme":\s*"midnight"/, 'DEFAULT_SETTINGS must include ui_theme=midnight');
  assert.match(config, /"ui_accent":\s*"#0e7c86"/, 'DEFAULT_SETTINGS must include ui_accent');
  const models = read('paperwhale_server/models.py');
  assert.match(models, /class SettingsUpdate[\s\S]{0,600}ui_theme:\s*str \| None = None/,
    'SettingsUpdate must accept ui_theme');
  assert.match(models, /class SettingsUpdate[\s\S]{0,600}ui_accent:\s*str \| None = None/,
    'SettingsUpdate must accept ui_accent');
  const settingsPy = read('paperwhale_server/routers/settings.py');
  assert.match(settingsPy, /if 'ui_theme' in patch and not re\.fullmatch\(r'\[a-z\]\[a-z0-9-\]\{0,31\}',\s*str\(patch\['ui_theme'\] or ''\)\)/,
    'settings.py must validate ui_theme as [a-z][a-z0-9-]{0,31}');
  assert.ok(settingsPy.includes("raise HTTPException(400,'主题预设标识不合法')"),
    'settings.py must reject invalid themes with a 400');
  assert.match(settingsPy, /if 'ui_accent' in patch and not re\.fullmatch\(r'#\[0-9a-fA-F\]\{6\}',\s*str\(patch\['ui_accent'\] or ''\)\)/,
    'settings.py must keep the ui_accent #RRGGBB validation');
});

test('accent tints use color-mix while keeping the rgba fallback for older engines', () => {
  const css = read(STYLE);
  assert.ok((css.match(/color-mix\(in srgb, var\(--accent-2\)/g) || []).length >= 6,
    'accent tints/shades must be derived from --accent-2 via color-mix');
  assert.ok((css.match(/rgba\(20, 163, 163/g) || []).length >= 6,
    'the rgba accent fallbacks must stay for older engines');
  assert.match(
    css,
    /\.nav-item\.active\s*\{[^}]*background:\s*rgba\(20, 163, 163, \.16\);\s*background:\s*color-mix\(in srgb, var\(--accent-2\) 16%, transparent\)/,
    'the nav highlight must declare rgba first, then the color-mix override',
  );
});
