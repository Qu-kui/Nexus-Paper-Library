/* 无边框窗口缩放手柄的回归：宽度必须等于「起点宽度 + 鼠标累计位移」。
 *
 * 真机事故（2.7.12）：左右拖边缘时窗口越拖越宽、完全不跟鼠标。
 * 根因：每帧把 start/base 更新成上一帧的结果，但位移又是「相对拖动起点」的累计值，
 * 于是同一段位移被反复叠加（100 → 100+200 → …），窗口飞速变宽。
 * 这里用 vm 跑真实的 titlebar.js，直接派发 pointerdown/pointermove，钉住数值。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {fileURLToPath} from 'node:url';
import {dirname, join} from 'node:path';
import vm from 'node:vm';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const SOURCE = readFileSync(join(root, 'web/js/titlebar.js'), 'utf8');

function makeNode() {
  return {
    className: '',
    textContent: '',
    type: '',
    style: {},
    children: [],
    listeners: {},
    classList: {add() {}, remove() {}, contains: () => false, toggle() {}},
    appendChild(child) {
      this.children.push(child);
    },
    addEventListener(type, fn) {
      (this.listeners[type] ||= []).push(fn);
    },
    insertAdjacentHTML() {},
    setPointerCapture() {},
    releasePointerCapture() {},
    querySelector: () => null,
  };
}

function boot({width = 1400, height = 900, x = 200, y = 120} = {}) {
  const handles = [];
  const listeners = new Map();
  const calls = [];
  const body = makeNode();
  body.appendChild = (child) => handles.push(child);

  const document = {
    body,
    addEventListener() {},
    createElement: () => makeNode(),
    getElementById: () => null,
    querySelector: () => null,
    querySelectorAll: () => [],
  };
  const window = {
    pywebview: {
      api: {
        window_geometry: async () => ({x, y, width, height}),
        window_resize: (w, h) => calls.push({kind: 'resize', w, h}),
        window_move: (nx, ny) => calls.push({kind: 'move', x: nx, y: ny}),
      },
    },
    addEventListener(type, fn) {
      if (!listeners.has(type)) listeners.set(type, []);
      listeners.get(type).push(fn);
    },
    removeEventListener(type, fn) {
      const arr = listeners.get(type) || [];
      const index = arr.indexOf(fn);
      if (index >= 0) arr.splice(index, 1);
    },
    requestAnimationFrame(fn) {
      // 同步执行，但返回 0：真实 rAF 是异步的，返回非 0 会让「本帧已在跑」的标志卡住
      fn();
      return 0;
    },
    cancelAnimationFrame() {},
    alert() {},
  };
  const fetch = async () => ({json: async () => ({})});
  // titlebar.js 启动时会读 location.hash（支持 /#help 直达使用说明），沙箱里补一个浏览器的 location
  const location = {hash: ''};
  const context = vm.createContext({window, document, location, fetch, console, Math, Number, Promise, JSON, String});
  vm.runInContext(SOURCE, context);

  const handle = (edge) => handles.find((node) => String(node.className).includes(`tb-resize-${edge}`));
  const fire = (target, type, event) => {
    for (const fn of target.listeners[type] || []) fn(event);
  };
  const pointerEvent = (screenX, screenY, target) => ({
    button: 0,
    screenX,
    screenY,
    pointerId: 7,
    pointerType: 'mouse',
    currentTarget: target,
    preventDefault() {},
    stopPropagation() {},
  });
  const settle = () => new Promise((resolve) => setTimeout(resolve, 0));

  async function start(edge, screenX = 1000, screenY = 500) {
    const target = handle(edge);
    assert.ok(target, `没有 ${edge} 手柄`);
    fire(target, 'pointerdown', pointerEvent(screenX, screenY, target));
    await settle();
    return {target, screenX, screenY};
  }
  const moveTo = (screenX, screenY) => {
    for (const fn of listeners.get('pointermove') || []) fn({screenX, screenY});
  };
  const release = () => {
    for (const fn of listeners.get('pointerup') || []) fn({});
  };

  return {handles, calls, start, moveTo, release, listeners};
}

test('east edge follows the mouse exactly once (no compounding)', async () => {
  const env = boot({width: 1400});
  await env.start('e');
  env.moveTo(1100, 500); // +100
  assert.deepEqual(env.calls, [{kind: 'resize', w: 1500, h: 900}]);
  env.calls.length = 0;
  env.moveTo(1200, 500); // 累计 +200 → 1600，绝不是 1500+200 或 1700
  assert.deepEqual(env.calls, [{kind: 'resize', w: 1600, h: 900}], '同一段位移不能被叠加两次');
  env.calls.length = 0;
  env.moveTo(1200, 500); // 没动 → 不应再发请求
  assert.deepEqual(env.calls, []);
});

test('south edge follows the mouse exactly once', async () => {
  const env = boot({height: 900});
  await env.start('s', 800, 500);
  env.moveTo(800, 560); // +60
  assert.deepEqual(env.calls, [{kind: 'resize', w: 1400, h: 960}]);
  env.calls.length = 0;
  env.moveTo(800, 620); // 累计 +120
  assert.deepEqual(env.calls, [{kind: 'resize', w: 1400, h: 1020}]);
});

test('west edge moves then resizes so the right edge stays put', async () => {
  const env = boot({x: 200, width: 1400});
  await env.start('w');
  env.moveTo(940, 500); // dx = -60 → 左边界左移 60，宽度 +60
  assert.deepEqual(env.calls, [
    {kind: 'move', x: 140, y: 120},
    {kind: 'resize', w: 1460, h: 900},
  ]);
});

test('corner handle applies both axes from the same origin', async () => {
  const env = boot({x: 200, y: 120, width: 1400, height: 900});
  await env.start('se');
  env.moveTo(1080, 540); // +80 / +40
  assert.deepEqual(env.calls, [{kind: 'resize', w: 1480, h: 940}]);
});

test('east edge stops at the minimum width instead of going negative', async () => {
  const env = boot({width: 1400});
  await env.start('e');
  env.moveTo(0, 500); // dx = -1000 → 会被下限挡住
  assert.deepEqual(env.calls, [{kind: 'resize', w: 1040, h: 900}]);
});

test('pointerup stops the drag', async () => {
  const env = boot({width: 1400});
  await env.start('e');
  env.release();
  env.calls.length = 0;
  env.moveTo(1600, 500);
  assert.deepEqual(env.calls, [], '松手后不能再改窗口尺寸');
});

test('resize logic never feeds its own output back as the origin', () => {
  const block = SOURCE.split('async function beginResize')[1].split('function setupResizeHandles')[0];
  assert.ok(!/\b(base|start)\.(w|h|x|y)\s*=/.test(block),
    '起点几何在拖动期间必须只读：一旦把上一帧结果写回起点，位移会被累计叠加');
  assert.match(block, /start\.w \+ dx/, 'east 边必须用「起点宽度 + 累计位移」计算');
  assert.match(block, /setPointerCapture/, '拖出窗口后仍然要收到 pointermove（指针捕获）');
  assert.match(block, /pointercancel/, '指针被系统取消时要能收尾，否则监听器泄漏');
});
