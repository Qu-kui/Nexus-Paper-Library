// Pure annotation tools: the list filter never changes the stored annotations.
const text = value => String(value ?? '');
const pageNumber = item => Math.max(1, Number(item?.page) || 1);

function chapterIndex(item, chapters) {
  let index = -1;
  let latestPage = -Infinity;
  chapters.forEach((chapter, candidate) => {
    const page = Number(chapter?.page);
    if (Number.isFinite(page) && page <= pageNumber(item) && page >= latestPage) {
      latestPage = page;
      index = candidate;
    }
  });
  return index;
}

/** chapter is a string index into chapters; equal-page headings use the last entry. */
export function filterAnnotations(items = [], filters = {}, chapters = []) {
  const color = text(filters.color).trim().toLowerCase();
  const query = text(filters.query).trim().toLowerCase();
  const chapter = text(filters.chapter);
  return items.filter(item => {
    if (color && text(item.color).trim().toLowerCase() !== color) return false;
    const hasNote = Boolean(text(item.note).trim());
    if (filters.note === 'with' && !hasNote) return false;
    if (filters.note === 'without' && hasNote) return false;
    if (query && !`${text(item.quote)}\n${text(item.note)}`.toLowerCase().includes(query)) return false;
    if (chapter && String(chapterIndex(item, chapters)) !== chapter) return false;
    return true;
  }).sort((a, b) => pageNumber(a) - pageNumber(b)
    || (Number(a.id) || 0) - (Number(b.id) || 0));
}

function escapeMarkdown(value) {
  return text(value).replace(/\r\n?/g, '\n')
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/[\\`*_[\]{}()#!|~]/g, '\\$&')
    .replace(/^(\s*)([-+]|\d+\.) /gm, (_, indent, marker) => `${indent}${marker.replace(/[-+.]/g, '\\$&')} `);
}

function inline(value) {
  return escapeMarkdown(text(value).replace(/\s+/g, ' ').trim());
}

/** UTF-8-ready Markdown from source strings only; no DOM or rendered HTML is read. */
export function annotationsMarkdown(paper = {}, items = [], chapters = []) {
  const sorted = filterAnnotations(items, {}, chapters);
  const authors = Array.isArray(paper.authors) ? paper.authors.join('; ') : paper.authors;
  const lines = [`# ${inline(paper.title) || '未命名论文'} — 批注`, ''];
  if (text(authors).trim()) lines.push(`作者：${inline(authors)}`, '');
  if (text(paper.year).trim()) lines.push(`年份：${inline(paper.year)}`, '');
  if (text(paper.doi).trim()) lines.push(`DOI：${inline(paper.doi)}`, '');
  lines.push(`共 ${sorted.length} 条批注`, '');
  sorted.forEach((item, index) => {
    const chapter = chapters[chapterIndex(item, chapters)];
    lines.push(`## ${index + 1}. 第 ${pageNumber(item)} 页${chapter ? ` · ${inline(chapter.title)}` : ''}`, '');
    const quote = text(item.quote).trim() || '（无引文）';
    lines.push(...escapeMarkdown(quote).split('\n').map(line => `> ${line}`), '');
    const note = text(item.note).trim();
    if (note) lines.push(`备注：${escapeMarkdown(note)}`, '');
  });
  return lines.join('\n');
}

export function annotationExportFilename(paper = {}) {
  const title = text(paper.title).trim().replace(/[<>:"/\\|?*\x00-\x1f\x7f]/g, '_').replace(/[. ]+$/g, '');
  return `${[...(title || '未命名论文')].slice(0, 100).join('')}-批注.md`;
}
