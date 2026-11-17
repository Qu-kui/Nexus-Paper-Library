/* 分类筛选器的回归：面板一次只显示第一级，点进去才展开下一级。
 *
 * 用户报障（2.7.13）：下拉里直接把「人工智能 / 光神经网络」这类完整路径平铺一长串，
 * 几百个分类根本没法翻。现在改成层级选择器。
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {spawnSync} from 'node:child_process';
import {fileURLToPath, pathToFileURL} from 'node:url';
import {dirname, join} from 'node:path';

const root = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel) => readFileSync(join(root, rel), 'utf8');
const modulePath = pathToFileURL(join(root, 'web/js/category-picker.js')).href;
const {buildCategoryTree, levelView, renderPanelHtml, pickRowOutcome} = await import(modulePath);

const PATHS = [
  {label: '人工智能', count: 3},
  {label: '人工智能 / 光神经网络', count: 1},
  {label: '人工智能 / 图像分割', count: 2},
  {label: '偏振测量与偏振成像 / 偏振器件', count: 4},
];

test('根层只列第一级，不把完整路径平铺出来', () => {
  const view = levelView(buildCategoryTree(PATHS), []);
  const names = view.items.map((item) => item.name);
  assert.deepEqual(names, ['人工智能', '偏振测量与偏振成像']);
  assert.ok(!names.includes('光神经网络'), '第二级不能出现在第一级列表里');
  assert.ok(!names.some((name) => name.includes(' / ')), '列表项必须只有本级名字');
  assert.deepEqual(view.trail, []);
});

test('父级篇数按整棵子树合计', () => {
  const view = levelView(buildCategoryTree(PATHS), []);
  const ai = view.items.find((item) => item.name === '人工智能');
  assert.equal(ai.total, 6, '3（本级）+ 1 + 2（子级）');
  assert.equal(ai.hasChildren, true);
  const optics = view.items.find((item) => item.name === '偏振测量与偏振成像');
  assert.equal(optics.total, 4);
  assert.equal(optics.hasChildren, true);
});

test('点进某一级后才出现它的下一级', () => {
  const tree = buildCategoryTree(PATHS);
  const level2 = levelView(tree, ['人工智能']);
  assert.deepEqual(level2.items.map((item) => item.name), ['图像分割', '光神经网络'],
    '按篇数从多到少排；只有这一级的子目录');
  assert.deepEqual(level2.trail, ['人工智能']);
  for (const item of level2.items) {
    assert.deepEqual(item.path.slice(0, 1), ['人工智能']);
    assert.equal(item.hasChildren, false, '这两个子级下面没有更深的目录');
  }
});

test('三级目录能继续往下钻', () => {
  const tree = buildCategoryTree([...PATHS, {label: '人工智能 / 光神经网络 / 深层网络', count: 5}]);
  const level2 = levelView(tree, ['人工智能']);
  const net = level2.items.find((item) => item.name === '光神经网络');
  assert.equal(net.hasChildren, true);
  assert.equal(net.total, 6);
  const level3 = levelView(tree, ['人工智能', '光神经网络']);
  assert.deepEqual(level3.items.map((item) => item.name), ['深层网络']);
});

test('点击结果：有子级就展开，没有子级就选中并收起', () => {
  const parent = pickRowOutcome('人工智能', true);
  assert.deepEqual(parent, {selected: '人工智能', trail: ['人工智能'], close: false});
  const leaf = pickRowOutcome('人工智能 / 光神经网络', false);
  assert.deepEqual(leaf, {selected: '人工智能 / 光神经网络', trail: ['人工智能'], close: true});
  const all = pickRowOutcome('', false);
  assert.deepEqual(all, {selected: '', trail: [], close: true});
});

test('面板 HTML：根层没有返回键，子层有面包屑和返回键', () => {
  const tree = buildCategoryTree(PATHS);
  const rootHtml = renderPanelHtml(levelView(tree, []), '');
  assert.ok(rootHtml.includes('全部分类'));
  assert.ok(!rootHtml.includes('data-picker-back'), '第一级不该有返回按钮');
  assert.ok(rootHtml.includes('data-picker-has-children="1"'), '有子级的行要带箭头标记');
  assert.ok(rootHtml.includes('人工智能'));

  const innerHtml = renderPanelHtml(levelView(tree, ['人工智能']), '人工智能');
  assert.ok(innerHtml.includes('data-picker-back'), '进入子级后要能返回上级');
  assert.ok(innerHtml.includes('category-picker-current'), '要显示当前路径面包屑');
  assert.ok(innerHtml.includes('光神经网络'));
  assert.ok(!innerHtml.includes('偏振器件'), '别的分支不能混进来');
});

test('分类名里的尖括号会被转义', () => {
  const html = renderPanelHtml(levelView(buildCategoryTree([{label: '<b>注入</b> / 子级', count: 1}]), []), '');
  assert.ok(!html.includes('<b>注入</b>'));
  assert.ok(html.includes('&lt;b&gt;'));
});

test('页面接线：隐藏 input + 按钮 + 面板，library 不再拼 <option>', () => {
  const html = read('web/index.html');
  assert.ok(html.includes('id="filter-category"'), '保留 #filter-category，原有筛选逻辑靠它');
  assert.match(html, /type="hidden"[^>]*id="filter-category"/, '分类值要存在隐藏 input 里，不再是 select');
  assert.ok(html.includes('id="filter-category-button"'));
  assert.ok(html.includes('id="filter-category-panel"'));

  const library = read('web/js/library.js');
  assert.ok(library.includes('setCategoryPaths'), 'library.js 要把分类路径喂给选择器');
  assert.ok(library.includes('initCategoryPicker'), 'library.js 要初始化选择器');
  assert.ok(library.includes('syncCategoryPicker'), '清空筛选时要同步按钮文字');
  assert.ok(!/options\.push\(`<option value="\$\{escapeHtml\(label\)\}"/.test(library),
    '不能再把完整分类路径拼成一长串 option');

  const picker = read('web/js/category-picker.js');
  assert.ok(picker.includes('dispatchEvent'), '选中后要派发 change，触发原有刷新逻辑');
  assert.ok(picker.includes('aria-expanded'), '展开状态要反映到 aria-expanded');

  for (const rel of ['web/js/category-picker.js', 'web/js/library.js']) {
    const checked = spawnSync(process.execPath, ['--check', join(root, rel)], {encoding: 'utf8'});
    assert.equal(checked.status, 0, `${rel} 语法错误：${checked.stderr || ''}`);
  }
});
