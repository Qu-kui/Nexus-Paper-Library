import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createRequire} from 'node:module';
import test from 'node:test';

const require = createRequire(import.meta.url);
globalThis.window = {katex: require('../web/vendor/katex/katex.min.js')};
const source = await readFile(new URL('../web/js/api.js', import.meta.url), 'utf8');
const {md} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));

test('typesets inline and multiline equations before Markdown changes them', () => {
  for (const expression of [
    '$t_{exp} \\ll \\tau_c$',
    '\\(\\Delta s_x \\approx 2\\langle s_x\\rangle\\)',
    '$$\n\\begin{aligned}a&=b+c\\\\d&=\\frac{1}{2}\\end{aligned}\n$$',
    '\\[\\sqrt{x^2+y^2}\\]',
    '```math\nx^2\n```',
  ]) {
    const html = md('**公式**\n' + expression + '\n原文 [12]');
    assert.match(html, /class="katex"/);
    assert.match(html, /<math/);
    assert.doesNotMatch(html, /katex-error/);
    assert.match(html, /原文 \[12\]/);
  }
});

test('preserves code, currency, incomplete streams and escaped delimiters', () => {
  for (const plain of [
    '`$x^2$`', '```python\nprint("$x$")\n```',
    '$5 and $10', '\\$5 and \\$10', '$$\\frac{a}{', '$x_{',
  ]) assert.doesNotMatch(md(plain), /class="katex"/);
  assert.match(md('before $x_{1}$ after'), /class="katex"/);
});

test('untrusted LaTeX, links and HTML cannot create active content', () => {
  const html = md('$\\href{javascript:alert(1)}{click}$\n<img src=x onerror=alert(1)>');
  assert.doesNotMatch(html, /href="javascript:|<img/);
  assert.match(html, /&lt;img/);
  const link = md('[paper](https://example.com/$x$)');
  assert.match(link, /href="https:\/\/example.com\/\$x\$"/);
  assert.doesNotMatch(link, /class="katex"/);
  assert.doesNotThrow(() => md('$\\unknowncommand{a}$'));
});
