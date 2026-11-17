import {api,escapeHtml,toast,emit} from "./api.js";
const batches = new Map();
let expanded = false;
let historyRequest = null;

export function updateTaskBatch(kind, status) {
  if (!status?.batch_id) return;
  batches.set(status.batch_id, {...status, kind: kind || status.kind});
  render();
}

function batchView(batch) {
  const total = Number(batch.total) || 0, done = Number(batch.done) || 0;
  const errors = Array.isArray(batch.errors) ? batch.errors : [];
  const label = batch.kind === 'pdf' || batch.kind === 'pdf_fetch' ? 'PDF 获取' : batch.kind === 'category' ? '分类整理' : batch.kind === 'methods' ? '方法参数' : batch.kind === 'cards' ? '复习卡片' : 'AI 精读';
  return {label, total, done, failed: Boolean(batch.failed),
    counts: `${done} / ${total} · 排队 ${batch.pending || 0} · 进行 ${batch.active || 0} · 成功 ${batch.succeeded || 0} · 失败 ${batch.failed || 0}`,
    errorCount: errors.length,
    errors: errors.map(error => `<p class="small">${escapeHtml(typeof error === 'string' ? error : (error.title || error.paper_id || '') + '：' + (error.error || error.message || ''))}</p>`).join('')};
}

function updateArticle(article, batch) {
  const view = batchView(batch), signature = JSON.stringify(view);
  if (article._taskSignature === signature) return;
  article._taskSignature = signature;
  article.querySelector('b').textContent = view.label;
  article.querySelector('span').textContent = batch.batch_id;
  article.querySelector('[data-counts]').textContent = view.counts;
  const progress = article.querySelector('progress');
  progress.max = Math.max(view.total, 1);
  progress.value = view.done;
  progress.setAttribute('aria-label', `${view.label}批次进度`);
  let details = article.querySelector('details');
  if (view.errorCount) {
    if (!details) {
      details = document.createElement('details');
      details.innerHTML = '<summary></summary><div data-errors></div>';
      article.insertBefore(details, article.querySelector('button'));
    }
    const summary = `失败详情（${view.errorCount}）`;
    if (details.querySelector('summary').textContent !== summary) details.querySelector('summary').textContent = summary;
    const errors = details.querySelector('[data-errors]');
    if (errors.innerHTML !== view.errors) errors.innerHTML = view.errors;
  } else details?.remove();
  let retry = article.querySelector('button');
  if (view.failed && !retry) {
    retry = document.createElement('button');
    retry.className = 'btn tiny';
    retry.dataset.retryBatch = batch.batch_id;
    retry.textContent = '仅重试本批失败项';
    article.appendChild(retry);
  } else if (!view.failed) retry?.remove();
}

function render() {
  const content = document.getElementById('task-center-content');
  if (!content) return;
  const focus = document.activeElement;
  const scrollTop = content.scrollTop;
  const panel = document.getElementById('task-center');
  const panelScroll = panel?.scrollTop;
  const existing = new Map([...content.querySelectorAll('[data-batch-id]')].map(el => [el.dataset.batchId, el]));
  const ordered = [...batches.values()].sort((a,b) => String(b.created_at || '9999').localeCompare(String(a.created_at || '9999')));
  if (ordered.length) content.querySelector('[data-empty]')?.remove();
  let previous = null;
  for (const batch of ordered) {
    let article = existing.get(batch.batch_id);
    if (!article) {
      article = document.createElement('article');
      article.className = 'task-batch';
      article.dataset.batchId = batch.batch_id;
      article.innerHTML = '<b></b><span class="muted small"></span><p data-counts></p><progress></progress>';
    }
    updateArticle(article, batch);
    const next = previous ? previous.nextElementSibling : content.firstElementChild;
    if (next !== article) content.insertBefore(article, next);
    previous = article;
  }
  if (!ordered.length && !content.querySelector('[data-empty]')) {
    const empty = document.createElement('p');
    empty.className = 'muted';
    empty.dataset.empty = '';
    empty.textContent = '暂无任务批次。开始补齐 PDF 或批量精读后，结果会保留在这里。';
    content.appendChild(empty);
  }
  if (focus?.isConnected && document.activeElement !== focus) focus.focus({preventScroll: true});
  content.scrollTop = scrollTop;
  if (panel) panel.scrollTop = panelScroll;
}

export function initTaskCenter() {
  const panel = document.getElementById('task-center'), button = document.getElementById('btn-task-center');
  const toggle = value => {
    expanded = value;
    panel?.classList.toggle('hidden', !value);
    button?.setAttribute('aria-expanded', String(value));
    if (value) loadHistory();
  };
  button?.addEventListener('click', () => toggle(!expanded));
  document.getElementById('task-center-close')?.addEventListener('click', () => { toggle(false); button?.focus(); });
  panel?.addEventListener('click', async event => {
    const retry = event.target.closest('[data-retry-batch]');
    if (!retry || retry.disabled) return;
    retry.disabled = true;
    try {
      const result = await api.post(`/tasks/batches/${encodeURIComponent(retry.dataset.retryBatch)}/retry`);
      updateTaskBatch(batches.get(retry.dataset.retryBatch)?.kind, result.status);
      toast(`已重新排队 ${result.queued || 0} 项`, 'success');
      emit('library-changed');
      await loadHistory();
    } catch (error) { toast(error.message, 'error'); }
    finally { if (retry.isConnected) retry.disabled = false; }
  });
  render();
  pollHistory();
}

async function loadHistory() {
  if (historyRequest) return historyRequest;
  historyRequest = (async () => {
    try {
      const params = new URLSearchParams();
      for (const batch of batches.values()) {
        if (Number(batch.pending || 0) + Number(batch.active || 0) > 0) params.append('batch_id', batch.batch_id);
      }
      const query = params.toString();
      const result = await api.get('/tasks/batches' + (query ? `?${query}` : ''));
      for (const batch of (Array.isArray(result) ? result : result.items || result.batches || [])) {
        if (batch.batch_id) batches.set(batch.batch_id, batch);
      }
      render();
    } catch { /* Keep the last snapshot and retry on the next poll. */ }
  })();
  try { await historyRequest; }
  finally { historyRequest = null; }
}

async function pollHistory() {
  await loadHistory();
  setTimeout(pollHistory, 3000);
}
