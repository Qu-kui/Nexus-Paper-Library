import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import test from 'node:test';

let outline;
try {
  outline = await import('data:text/javascript;base64,' + Buffer.from(await readFile(new URL('../web/js/reader-outline.js', import.meta.url))).toString('base64'));
} catch { outline = {}; }

const item = (str, y, size = 10, x = 40, extra = {}) => ({
  str, transform: [size, 0, 0, size, x, y], width: str.length * size * .5,
  height: size, fontName: 'Body', hasEOL: true, ...extra,
});
const body = (y = 650) => item('These observations describe the samples collected during the controlled experiment.', y);

test('exports a deterministic, bounded heading recognizer', () => {
  assert.equal(typeof outline.extractHeadings, 'function');
  assert.equal(outline.MAX_OUTLINE_SCAN_PAGES, 120);
});

test('recognizes English numbered sections and their depth in reading order', () => {
  assert.deepEqual(outline.extractHeadings([{page: 3, items: [
    body(600), item('2.1 Experimental Setup', 680, 12), item('2 Methods', 720, 14),
    item('2.1.1 Data Collection', 640, 11),
  ]}]), [
    {title: '2 Methods', page: 3, level: 1},
    {title: '2.1 Experimental Setup', page: 3, level: 2},
    {title: '2.1.1 Data Collection', page: 3, level: 3},
  ]);
});

test('joins PDF fragments by coordinates while keeping separate columns apart', () => {
  assert.deepEqual(outline.extractHeadings([{page: 1, items: [
    item('Introduction', 720, 14, 53), item('1', 720.7, 14, 40, {hasEOL: false, width: 7}),
    item('Results', 680, 14, 330), item('3', 680, 14, 315, {hasEOL: false, width: 7}),
    item('Some unrelated column text', 680, 10, 40, {width: 125}), body(600),
  ]}]), [
    {title: '1 Introduction', page: 1, level: 1},
    {title: '3 Results', page: 1, level: 1},
  ]);
});

test('recognizes Chinese numbered and common headings without spaces inside Chinese fragments', () => {
  assert.deepEqual(outline.extractHeadings([{page: 2, items: [
    item('第一章 绪论', 740, 14), item('（二）研究方法', 700, 12),
    item('三、实验结果', 660, 14), item('结论', 620, 14), body(560),
  ]}]), [
    {title: '第一章 绪论', page: 2, level: 1},
    {title: '（二）研究方法', page: 2, level: 2},
    {title: '三、实验结果', page: 2, level: 1},
    {title: '结论', page: 2, level: 1},
  ]);
});

test('joins adjacent Chinese fragments and recognizes compact decimal section numbers', () => {
  assert.deepEqual(outline.extractHeadings([{page: 8, items: [
    item('一、', 740, 14, 40, {width: 28, hasEOL: false}),
    item('引言', 740, 14, 68, {width: 28}),
    item('2.1研究方法', 690, 12), body(600),
    item('第二章研究节奏', 650, 14),
  ]}]), [
    {title: '一、引言', page: 8, level: 1},
    {title: '2.1研究方法', page: 8, level: 2},
    {title: '第二章研究节奏', page: 8, level: 1},
  ]);
});

test('rejects a numbered sentence even when a PDF breaks it into fragments', () => {
  assert.deepEqual(outline.extractHeadings([{page: 8, items: [
    item('1', 700, 12, 40, {width: 6, hasEOL: false}),
    item('The treatment was applied to samples.', 700, 12, 51), body(600),
  ]}]), []);
});

test('uses font prominence for short title-like headings', () => {
  assert.deepEqual(outline.extractHeadings([{page: 4, items: [
    item('Robustness Checks', 710, 15), body(660), body(640),
    item('A plain sentence about collected measurements', 600, 10),
  ]}]), [{title: 'Robustness Checks', page: 4, level: 1}]);
});

test('recognizes common section names without requiring larger fonts', () => {
  assert.deepEqual(outline.extractHeadings([{page: 5, items: [
    item('ABSTRACT', 730), item('Materials and Methods', 690), item('参考文献', 640), body(600),
  ]}]), [
    {title: 'ABSTRACT', page: 5, level: 1},
    {title: 'Materials and Methods', page: 5, level: 1},
    {title: '参考文献', page: 5, level: 1},
  ]);
});

test('excludes repeated running headers and variable page footers', () => {
  const pages = [1, 2, 3].map(page => ({page, items: [
    item('Journal of Interesting Results', 790, 15), item('Methods', 710, 12),
    body(600), item(`Page ${page}`, 30, 12),
  ]}));
  assert.deepEqual(outline.extractHeadings(pages), [
    {title: 'Methods', page: 1, level: 1},
    {title: 'Methods', page: 2, level: 1},
    {title: 'Methods', page: 3, level: 1},
  ]);
});

test('rejects ordinary prose, numbered prose, captions, references, table cells, and TOC entries', () => {
  const strings = [
    '1 The treatment was applied to each sample.', '2 We measured changes over time',
    '1.5 mg of protein was added', '3 0.15 0.20 0.30',
    'Table 2 Results', 'Figure 1 Experimental Setup', 'See Table 2 for Results',
    '[12] Smith, J. Methods in Science. 2020.', 'Introduction ........ 3',
    'This is an ordinary sentence.', 'The Measurement Was Repeated.',
    '本研究发现实验结果具有一致性。',
  ];
  assert.deepEqual(outline.extractHeadings([{page: 1, items: strings.map((str, i) => item(str, 750 - i * 25, 16))}]), []);
});

test('accepts empty/scanned pages and skips malformed text items', () => {
  assert.deepEqual(outline.extractHeadings([{page: 1, items: []}, {page: 2, items: [
    {}, {str: 'Methods'}, item('', 700), item('Methods', NaN),
  ]}]), []);
  assert.deepEqual(outline.extractHeadings(null), []);
});

test('bounds page processing and leaves source text untouched', () => {
  const pages = Array.from({length: 121}, (_, i) => ({page: i + 1, items: i === 120 ? [item('Introduction', 700)] : []}));
  const source = JSON.stringify(pages);
  assert.deepEqual(outline.extractHeadings(pages), []);
  assert.equal(JSON.stringify(pages), source);
});
