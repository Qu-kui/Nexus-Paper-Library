import { filterAnnotations, annotationsMarkdown, annotationExportFilename } from './reader-annotations.js';

/** Installs list-only annotation filters. The reader retains ownership of rendering. */
export function installAnnotationTools({ getPaper, getAnnotations, getChapters, onChange }) {
  const list = document.getElementById('reader-annotations');
  if (!list) throw new Error('Annotation list is unavailable');
  const tools = document.createElement('div');
  tools.className = 'reader-annotation-tools';
  const controls = {};
  function control(key, label, type = 'select') {
    const wrap = document.createElement('label');
    const caption = document.createElement('span');
    caption.textContent = label;
    const input = document.createElement(type);
    input.setAttribute('aria-label', label);
    if (type === 'input') input.type = 'search';
    wrap.append(caption, input);
    tools.append(wrap);
    controls[key] = input;
    return input;
  }
  const color = control('color', '批注颜色');
  const note = control('note', '备注筛选');
  const query = control('query', '搜索批注', 'input');
  query.placeholder = '搜索引文或备注';
  const chapter = control('chapter', '批注章节');
  function setOptions(select, entries) {
    const previous = select.value;
    // Keep an active filter even if deleting its last annotation removes that option.
    if (previous && !entries.some(([value]) => value === previous)) {
      entries.push([previous, select.selectedOptions[0]?.textContent || previous]);
    }
    select.replaceChildren(...entries.map(([value, label]) => {
      const option = document.createElement('option');
      option.value = value;
      option.textContent = label;
      return option;
    }));
    select.value = entries.some(([value]) => value === previous) ? previous : entries[0][0];
  }
  setOptions(note, [['all', '全部备注'], ['with', '有备注'], ['without', '无备注']]);
  const count = document.createElement('div');
  count.className = 'filter-count';
  count.setAttribute('role', 'status');
  count.setAttribute('aria-live', 'polite');
  const resetButton = document.createElement('button');
  resetButton.type = 'button';
  resetButton.textContent = '重置筛选';
  const exportButton = document.createElement('button');
  exportButton.type = 'button';
  exportButton.textContent = '导出筛选批注';
  tools.append(count, resetButton, exportButton);
  list.before(tools);

  const filters = () => Object.fromEntries(Object.entries(controls).map(([key, input]) => [key, input.value]));
  const items = () => filterAnnotations(getAnnotations(), filters(), getChapters());
  function refresh() {
    const all = getAnnotations();
    const colors = [...new Set(all.map(item => String(item.color || '').trim().toLowerCase()).filter(Boolean))];
    const names={'#ffd666':'黄色','#8ee6a8':'绿色','#8ec5ff':'蓝色','#ff9f9f':'红色','#14a3a3':'青色'};
    setOptions(color, [['', '全部颜色'], ...colors.map(value => [value, names[value]||`自定义颜色 ${value}`])]);
    setOptions(chapter, [['', '全部章节'], ...getChapters().map((item, index) => [String(index), `${item.title} · 第 ${item.page} 页`])]);
    const shown = items().length;
    count.textContent = `显示 ${shown} / ${all.length} 条批注`;
    exportButton.disabled = shown === 0 || !getPaper();
  }
  function reset() {
    color.value = '';
    note.value = 'all';
    query.value = '';
    chapter.value = '';
    refresh();
  }
  function changed() {
    refresh();
    onChange();
  }
  for (const input of [color, note, chapter]) input.addEventListener('change', changed);
  query.addEventListener('input', changed);
  resetButton.addEventListener('click', () => { reset(); onChange(); });
  exportButton.addEventListener('click', () => {
    const selected = items();
    const paper = getPaper();
    if (!selected.length || !paper) return;
    const blob = new Blob([annotationsMarkdown(paper, selected, getChapters())], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const anchor = document.createElement('a');
    anchor.href = url;
    anchor.download = annotationExportFilename(paper);
    document.body.append(anchor);
    try { anchor.click(); } finally {
      anchor.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    }
  });
  refresh();
  return { items, refresh, reset };
}
