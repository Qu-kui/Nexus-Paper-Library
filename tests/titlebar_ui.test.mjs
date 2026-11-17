/* 自定义桌面程序框（2.7.7）静态回归：不依赖 DOM，只校验源码里必须存在的接线点。
 *
 * 目的：这类“窗口壳”改动一旦被回退、漏提交或换错实现，构建照样能过，
 * 但用户会看到系统灰边 / 缩放手柄没用 / 浏览器模式下多出一条空条。
 * 2.7.7 的教训：给 frameless 窗口加 WS_THICKFRAME 会让 WinForms 画非客户区（顶部灰边），
 * 而且点击命中不生效 —— 所以这里反向钉住“不许再补 GWL_STYLE”，改用 DWM 上色 + 前端手柄。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync, readdirSync, statSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');

test('the desktop window is frameless and never patches GWL_STYLE back', () => {
  const desktop = read('desktop.py');
  assert.match(desktop, /frameless\s*=\s*True/, 'desktop.py must create a frameless window');
  // 2.7.15：easy_drag 会让 pywebview 在 window 上挂全局 mousedown，正文里划词选字时整窗被拖走。
  // 现在只靠标题栏的 .pywebview-drag-region 拖窗口（pywebview 的 body 级监听与 easy_drag 无关）。
  assert.match(desktop, /easy_drag\s*=\s*False/,
    'easy_drag must stay off, otherwise dragging to select text moves the whole window');
  assert.doesNotMatch(desktop, /easy_drag\s*=\s*True/);
  // 2.7.6 的 WS_THICKFRAME 补丁是用户看到顶部灰边的根因，且缩放并未生效。
  assert.doesNotMatch(desktop, /SetWindowLongW/,
    'must not patch the window style: WinForms paints the non-client area grey on a frameless window');
  assert.doesNotMatch(desktop, /\|\s*ws_thickframe/i,
    'must not OR WS_THICKFRAME into the window style anymore');
});

test('dragging still works: the titlebar keeps pywebview drag regions', () => {
  const html = read('web/index.html');
  assert.ok(html.includes('pywebview-drag-region'), 'the drag region marker is required by pywebview');
  const dragRegions = (html.match(/pywebview-drag-region/g) || []).length;
  assert.ok(dragRegions >= 2, `titlebar needs the brand area plus the drag strip, found ${dragRegions}`);
  assert.ok(html.includes('id="tb-drag"'), 'the drag strip must exist so the window can still be moved');
});

test('the minimize button minimizes to the taskbar instead of hiding to the tray', () => {
  const desktop = read('desktop.py');
  // 用户在标题栏点「–」时，期望的是最小化到任务栏；以前 events.minimized 里 hide() 会直接收进托盘。
  assert.doesNotMatch(desktop, /events\.minimized\s*\+=/,
    'minimize must not be hijacked into the tray');
  const block = desktop.split('def window_minimize(')[1].split('def ')[0];
  assert.match(block, /window\.minimize\(\)/, 'window_minimize() must actually minimize the window');
  assert.doesNotMatch(block, /window\.hide\(\)/, 'window_minimize() must not hide the window to the tray');
  // 关闭（×）仍然收进托盘，这是用户要的常驻行为，不能一起删掉
  assert.match(desktop, /events\.closing\s*\+=/, 'the close button must still be wired');
  assert.ok(desktop.includes('已收进托盘'), 'closing to the tray should still tell the user how to quit');
});

test('desktop.py paints the DWM border/caption to match the titlebar', () => {
  const desktop = read('desktop.py');
  assert.ok(desktop.includes('DwmSetWindowAttribute'), 'DWM attributes are what remove the system grey edge');
  for (const attribute of ['DWMWA_USE_IMMERSIVE_DARK_MODE', 'DWMWA_BORDER_COLOR', 'DWMWA_CAPTION_COLOR', 'DWMWA_WINDOW_CORNER_PREFERENCE']) {
    assert.ok(desktop.includes(attribute), `desktop.py must set ${attribute}`);
  }
  assert.ok(desktop.includes('0x002B1A0A'),
    'the DWM color must be #0a1a2b as COLORREF (0x00BBGGRR), matching the titlebar');
});

test('desktop.py exposes the geometry API used by the resize handles', () => {
  const desktop = read('desktop.py');
  for (const name of ['window_geometry', 'window_move', 'window_resize']) {
    assert.ok(desktop.includes(`def ${name}(`), `desktop.py must define ${name}()`);
    assert.ok(desktop.includes(name + ','), `${name} must be registered in the js_api list`);
  }
  for (const name of ['window_minimize', 'window_toggle_maximize', 'window_close', 'window_quit', 'window_toggle_fullscreen', 'open_guide']) {
    assert.ok(desktop.includes(`def ${name}(`), `desktop.py must define ${name}()`);
    assert.ok(desktop.includes(name + ','), `${name} must be registered in the js_api list`);
  }
});

test('index.html ships the custom titlebar and loads titlebar.js', () => {
  const html = read('web/index.html');
  assert.ok(html.includes('id="app-titlebar"'), 'the titlebar element must exist');
  assert.ok(html.includes('tb-menus'), 'the menu host must exist');
  assert.ok(html.includes('pywebview-drag-region'), 'the drag region marker is required by pywebview');
  assert.ok(html.includes('/js/titlebar.js'), 'titlebar.js must be loaded');
  assert.ok(html.includes('/css/style.css'), 'the titlebar styles live in style.css');
});

test('full-screen overlays start below the 36px titlebar in desktop mode', () => {
  const css = read('web/css/style.css');
  // .reader/.modal 是 fixed inset:0 的全屏层；桌面模式下标题栏占住 0-36px（z-index 9000），
  // 不把这两个层下移，阅读器顶部的工具栏/标签行就会被标题栏盖掉一半。
  assert.match(css, /body\.desktop-shell\s+\.reader\s*\{[^}]*top\s*:\s*36px/,
    'the reader overlay must start below the titlebar');
  assert.match(css, /body\.desktop-shell\s+\.modal\s*\{[^}]*top\s*:\s*36px/,
    'modal overlays must also respect the titlebar height');
  assert.match(css, /\.reader\s*\{[^}]*inset\s*:\s*0/,
    'browser mode still needs the full-viewport reader');
});

test('the selection toolbar never hides under the desktop titlebar', () => {
  const reader = read('web/js/reader.js');
  assert.match(reader, /desktop-shell[^\n]*\?\s*44\s*:\s*8/,
    'the reader selection toolbar must be pushed below the 36px titlebar in desktop mode');
});

test('style.css defines the shell, the reduced height and the eight resize handles', () => {
  const css = read('web/css/style.css');
  assert.ok(css.includes('.app-titlebar'), 'style.css must style .app-titlebar');
  assert.ok(css.includes('body.desktop-shell'), 'style.css must scope desktop-only layout');
  assert.match(css, /calc\(100vh\s*-\s*36px\)/,
    'the sidebar/main height must subtract the titlebar height in desktop shell mode');
  for (const edge of ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']) {
    assert.ok(css.includes(`.tb-resize-${edge}`), `style.css must position .tb-resize-${edge}`);
  }
  for (const cursor of ['ns-resize', 'ew-resize', 'nesw-resize', 'nwse-resize']) {
    assert.ok(css.includes(`cursor: ${cursor}`), `style.css must define cursor: ${cursor} for the handles`);
  }
  assert.match(css, /body\.desktop-shell\s+\.tb-resize\s*\{[^}]*display\s*:\s*block/,
    'handles must only appear in desktop shell mode');
});

test('titlebar.js wires eight resize handles through rAF-throttled pywebview calls', () => {
  const source = read('web/js/titlebar.js');
  assert.ok(source.includes('pywebviewready'), 'titlebar.js must wait for pywebviewready');
  assert.ok(source.includes('window.pywebview'), 'titlebar.js must feature-detect the pywebview bridge');
  for (const label of ['文件', '编辑', '查看', '工具', '帮助']) {
    assert.ok(source.includes(label), `the ${label} menu must be wired`);
  }
  assert.ok(source.includes('tb-resize'), 'titlebar.js must create the resize handles');
  for (const edge of ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw']) {
    assert.ok(source.includes(`tb-resize-${edge}`), `titlebar.js must create the ${edge} handle`);
  }
  for (const api of ['window_geometry', 'window_resize', 'window_move']) {
    assert.ok(source.includes(api), `titlebar.js must call ${api}()`);
  }
  assert.ok(source.includes('requestAnimationFrame'), 'pointermove must be throttled with requestAnimationFrame');
  const checked = spawnSync(process.execPath, ['--check', join(root, 'web/js/titlebar.js')], {encoding: 'utf8'});
  assert.equal(checked.status, 0, `node --check failed: ${checked.stderr || checked.error || ''}`);
});

test('verify_release counts every web asset from source, including titlebar.js', () => {
  const tool = read('tools/verify_release.py');
  assert.match(tool, /\(ROOT\s*\/\s*'web'\)\.rglob\('\*'\)/,
    'portable verification must glob web/ instead of a hardcoded asset list');
  assert.ok(tool.includes('is_file()'), 'the glob must skip directories');
  assert.ok(tool.includes('count+=1'), 'the resource count must come from the walk, not a constant');
  assert.doesNotMatch(tool, /expected\s+\d+\s+web\s+resources/i,
    'the asset count must not be hardcoded in the tool');
  const files = [];
  const walk = (rel) => {
    for (const entry of readdirSync(join(root, 'web', rel))) {
      const relPath = rel ? `${rel}/${entry}` : entry;
      if (statSync(join(root, 'web', relPath)).isFile()) files.push(relPath.replaceAll('\\', '/'));
      else walk(relPath);
    }
  };
  walk('');
  assert.ok(files.includes('js/titlebar.js'), 'titlebar.js must be part of the packaged web assets');
  // 前端资源数会随并行开发变化，这里只确认「自动计数覆盖全部文件」，具体数字由 verify_release 自己打印。
  assert.ok(files.length > 0, 'web/ must not be empty');
});
