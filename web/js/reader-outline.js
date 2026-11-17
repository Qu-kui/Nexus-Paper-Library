/* Conservative, local fallback for PDFs without an embedded outline. */
export const MAX_OUTLINE_SCAN_PAGES = 120;
const MAX_ITEMS_PER_PAGE = 20000;
const MAX_HEADINGS = 400;
const COMMON_ENGLISH = /^(?:abstract|introduction|background|related work|literature review|methods?|methodology|materials and methods|experimental (?:setup|results)|experiments?|results(?: and discussion)?|discussion|conclusions?(?: and future work)?|limitations|future work|references|bibliography|acknowledg(?:e)?ments?|appendix(?: [A-Z])?|supplementary (?:materials?|information))$/i;
const COMMON_CHINESE = /^(?:摘要|引言|绪论|前言|背景|研究背景|相关工作|文献综述|研究方法|方法|材料与方法|实验(?:设置|方法|结果)?|结果(?:与讨论)?|讨论|结论(?:与展望)?|总结(?:与展望)?|局限性|参考文献|致谢|附录)$/;
const CHINESE_NUMBER = '[一二三四五六七八九十百零〇两\\d]+';

function normalized(value) {
  return value.replace(/\s+/gu, ' ').trim();
}

function textLines(items) {
  const fragments = [];
  for (const item of (Array.isArray(items) ? items : []).slice(0, MAX_ITEMS_PER_PAGE)) {
    if (typeof item?.str !== 'string' || !Array.isArray(item.transform)) continue;
    const [a, b, c, d, x, y] = item.transform;
    if (![a, b, c, d, x, y].every(Number.isFinite)) continue;
    const size = Math.hypot(c, d) || Math.hypot(a, b) || Number(item.height);
    const text = normalized(item.str);
    if (!text || !(size > 0) || Math.abs(b) > Math.abs(a) * .25) continue;
    fragments.push({text, x, y, size, width: Math.max(0, Number(item.width) || 0)});
  }
  fragments.sort((a, b) => b.y - a.y || a.x - b.x);
  const rows = [];
  for (const fragment of fragments) {
    const row = rows.at(-1);
    if (row && Math.abs(row.y - fragment.y) <= Math.max(2, Math.min(row.size, fragment.size) * .25)) {
      row.parts.push(fragment);
    } else rows.push({y: fragment.y, size: fragment.size, parts: [fragment]});
  }
  const lines = [];
  for (const row of rows) {
    row.parts.sort((a, b) => a.x - b.x);
    let line;
    for (const part of row.parts) {
      const previous = line?.last;
      const gap = previous ? part.x - previous.x - previous.width : 0;
      // Large horizontal gaps are usually columns, rather than one heading.
      if (!line || gap > Math.max(36, part.size * 3)) {
        line = {text: part.text, y: row.y, size: part.size, last: part};
        lines.push(line);
      } else {
        const cjkBoundary = /[\p{Script=Han}（(]$/u.test(line.text) && /^[\p{Script=Han}）)]/u.test(part.text);
        const separator = cjkBoundary || gap < part.size * .12 ? '' : ' ';
        line.text += separator + part.text;
        line.size = Math.max(line.size, part.size);
        line.last = part;
      }
    }
  }
  return lines;
}

function bodySize(lines) {
  const sorted = lines.map(line => ({size: line.size, weight: Math.min(150, line.text.length)})).sort((a, b) => a.size - b.size);
  const midpoint = sorted.reduce((sum, line) => sum + line.weight, 0) / 2;
  let weight = 0;
  for (const line of sorted) {
    weight += line.weight;
    if (weight >= midpoint) return line.size;
  }
  return 10;
}

function safeTitle(text) {
  if (!text || text.length > 100 || /[.!?。！？;；:,，：]$/u.test(text)) return false;
  if (/(?:\.{2,}|…|·{3,})\s*\d+$/u.test(text)) return false;
  if (/^(?:table|fig(?:ure)?\.?|see\s|图\s*\d|表\s*\d|\[\d+\])/i.test(text)) return false;
  if (/\b(?:https?:|doi:|was|were|is|are|has|have|we|our|these|this)\b/i.test(text)) return false;
  if (/^(?:本研究|我们|本文发现|结果表明)/u.test(text)) return false;
  return text.split(/\s+/u).length <= 13 && !/\d\s+\d/u.test(text);
}

function titleLike(text) {
  if (/^[\p{Script=Han}\s]{2,18}$/u.test(text)) return true;
  const words = text.split(/\s+/u);
  return words.length <= 10 && words.every((word, index) =>
    (index > 0 && /^(?:a|an|and|at|by|for|in|of|on|or|the|to|with)$/i.test(word)) || /^[A-Z][A-Za-z'’\-]*$/u.test(word));
}

function headingLevel(line, typicalSize) {
  const text = normalized(line.text);
  if (!safeTitle(text)) return null;
  if (COMMON_ENGLISH.test(text) || COMMON_CHINESE.test(text)) return 1;
  let match = text.match(new RegExp(`^第${CHINESE_NUMBER}([章节篇部])\\s*(.+)$`, 'u'));
  if (match && safeTitle(match[2])) return match[1] === '节' ? 2 : 1;
  match = text.match(new RegExp(`^[（(]${CHINESE_NUMBER}[）)]\\s*(.+)$`, 'u'));
  if (match && safeTitle(match[1])) return 2;
  match = text.match(new RegExp(`^${CHINESE_NUMBER}[、．]\\s*(.+)$`, 'u'));
  if (match && safeTitle(match[1])) return 1;
  match = text.match(/^(?:(?:chapter|section)\s+)?(\d{1,2}(?:\.\d{1,2}){0,3})[.)]?(?:\s+|(?=\p{Script=Han}))(.+)$/iu);
  if (match && safeTitle(match[2]) && (/^[A-Z\p{Script=Han}]/u.test(match[2]))) {
    return Math.min(3, match[1].split('.').length);
  }
  match = text.match(/^[IVX]{1,6}[.)]\s+(.+)$/u);
  if (match && safeTitle(match[1]) && titleLike(match[1])) return 1;
  if (line.size >= typicalSize * 1.22 && titleLike(text)) return 1;
  return null;
}

function edgeKey(line) {
  return normalized(line.text).toLowerCase().replace(/\d+/gu, '#');
}

/**
 * pages: [{page: 1-based page number, items: PDF.js TextItem[]}]
 * Returns [{title, page, level}], ordered by page then vertical position.
 * It deliberately misses uncertain headings; the caller owns async scanning.
 */
export function extractHeadings(pages) {
  if (!Array.isArray(pages)) return [];
  const records = pages.slice(0, MAX_OUTLINE_SCAN_PAGES)
    .filter(page => Number.isInteger(page?.page) && page.page > 0)
    .map(page => ({page: page.page, lines: textLines(page.items)}))
    .sort((a, b) => a.page - b.page);
  const edges = new Map();
  for (const record of records) {
    for (const line of new Set([record.lines[0], record.lines.at(-1)])) {
      if (!line) continue;
      const key = edgeKey(line);
      if (!edges.has(key)) edges.set(key, new Set());
      edges.get(key).add(record.page);
    }
  }
  const headings = [];
  for (const {page, lines} of records) {
    const typicalSize = bodySize(lines);
    for (const line of lines) {
      if ((line === lines[0] || line === lines.at(-1)) && edges.get(edgeKey(line))?.size >= 2) continue;
      const level = headingLevel(line, typicalSize);
      if (level) headings.push({title: normalized(line.text), page, level});
      if (headings.length >= MAX_HEADINGS) return headings;
    }
  }
  return headings;
}
