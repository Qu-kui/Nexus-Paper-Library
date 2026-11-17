import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
const require = createRequire(import.meta.url);
globalThis.window = {katex:require('../web/vendor/katex/katex.min.js')};
const importSource = source => import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
const {md} = await importSource(await readFile(new URL('../web/js/api.js',import.meta.url),'utf8'));
const visualCode = (await readFile(new URL('../web/js/answer-visuals.js',import.meta.url),'utf8'))
  .replace("import {toast} from './api.js';", 'const toast = () => {};');
const {chartOption} = await importSource(visualCode);

test('Markdown tables preserve equations, alignment, inline code and escaped pipes', () => {
  const html = md('| 原文 | 含义 | 页码 |\n|:---|:---:|---:|\n| $s_x=\\frac{a}{b}$ | **偏振** | 4 [8] |\n| `a|b` | 左\\|右 | 5 |\n\n下一段');
  assert.match(html, /<table>/);assert.equal((html.match(/<th /g)||[]).length, 3);
  assert.equal((html.match(/<td /g)||[]).length, 6);
  assert.match(html, /class="katex"/);assert.match(html, /md-align-right/);
  assert.match(html, /<code>a\|b<\/code>/);assert.match(html, /左\|右/);
  assert.match(html, /<p>下一段<\/p>/);
  assert.doesNotMatch(md('a | b\nordinary | prose'), /<table>/);
});

test('only completed supported visual code blocks become figure descriptions', () => {
  assert.match(md('```mermaid\nflowchart LR\nA-->B\n```'), /data-visual-kind="mermaid"/);
  assert.match(md('```chart\n{"type":"bar"}\n```'), /data-visual-kind="chart"/);
  assert.doesNotMatch(md('```mermaid\nflowchart LR\nA-->'), /data-visual-kind/);
  assert.doesNotMatch(md('```python\nprint("hello")\n```'), /data-visual-kind/);
  assert.match(md('```mermaid\n<script>alert(1)</script>\n```'), /&lt;script&gt;/);
});

test('chart descriptions create bounded, source-labelled numeric charts', () => {
  const spec = {type:'bar',title:'测试',source:'第 3 页，表 1',labels:['A','B'],series:[{name:'指标',data:[1,2]}]};
  const option = chartOption(spec);
  assert.equal(option.series[0].type, 'bar');assert.deepEqual(option.series[0].data,[1,2]);
  assert.match(option.graphic[0].style.text,/表 1/);
  assert.equal(option.animation,false);
  assert.throws(()=>chartOption({...spec, source:''}));
  assert.throws(()=>chartOption({...spec, series:[{data:[1]}]}));
  assert.throws(()=>chartOption({...spec, series:[{data:['1','2']}]}));
  assert.throws(()=>chartOption({...spec, series:[{data:[NaN,2]}]}));
  assert.throws(()=>chartOption({...spec, type:'pie',series:[{data:[-1,2]}]}));
  assert.equal(chartOption({type:'scatter',source:'用户数据',series:[{data:[[1,2],[2,3]]}]}).xAxis.type,'value');
});
