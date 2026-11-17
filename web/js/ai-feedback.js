/* Shared evidence and truthful AI feedback for each conversation surface. */
import {api, escapeHtml, md, toast, emit} from './api.js';
import {thinkingPanelHtml} from './thinking-panel.js';

export function renderProgress(progress = {}) {
  const {stage='等待服务响应',reasoning='',answer=''} = progress || {};
  // 思考阶段：展开显示；一旦开始出正文就自动缩成一行「已思考（用时 N 秒）」
  const trace = Array.isArray(progress.web_trace) ? progress.web_trace : [];
  const panel = (reasoning || trace.length)
    ? thinkingPanelHtml({
        text: reasoning,
        state: answer ? 'done' : 'running',
        stage: answer ? '' : stage,
        thinkingSeconds: progress.thinking_seconds || 0,
        elapsedSeconds: progress.elapsed_seconds || progress.thinking_seconds || 0,
        trace,
      })
    : '';
  const stageLine = answer ? '' : `<div class="ai-stage">${escapeHtml(stage)}</div>`;
  return `<div class="ai-progress" role="status" aria-live="polite">${stageLine}${panel}${answer ? `<div class="markdown ai-answer-preview">${md(answer)}</div>` : ''}</div>`;
}

// Capture before replacing a conversation's DOM. A reader who scrolled up or
// collapsed the analysis keeps that position while new model text arrives.
export function captureProgress(container) {
  const progress=container?.querySelector('.ai-progress');
  return progress?{top:progress.scrollTop,follow:progress.scrollHeight-progress.clientHeight-progress.scrollTop<=24,open:progress.querySelector('details')?.open}:null;
}
export function restoreProgress(container, state) {
  const progress=container?.querySelector('.ai-progress');
  if(!progress)return;
  const details=progress.querySelector('details');
  if(details&&state?.open!==undefined)details.open=state.open;
  progress.scrollTop=!state||state.follow?progress.scrollHeight:state.top;
}

const labels = {original:'原文节选',abstract:'论文摘要',generated_summary:'AI 概述',attachment:'附件',web:'网页'};
export function renderSources(sources = [], candidates = []) {
  function card(item, used) {
    const kind = item.source_kind || (item.url ? 'web' : item.page ? 'original' : 'abstract');
    const label = labels[kind] || kind;
    const index = item.citation_index ?? item.index;
    const heading = `${used && index ? `[${index}] ` : ''}${item.title || '未命名来源'}`;
    const page = Number(item.page || 0);
    const details = `<details class="ai-source-card"><summary title="${escapeHtml(heading)}"><b>${escapeHtml(heading)}</b>${page>0?`<span class="ai-source-page">第 ${page} 页</span>`:''}</summary><div class="ai-source-detail"><span class="ai-source-kind">${escapeHtml(label)}</span>${item.snippet?`<span class="ai-source-snippet">${escapeHtml(item.snippet)}</span>`:''}</div></details>`;
    let action = '';
    if (item.url && /^https?:\/\//i.test(item.url)) action = `<a class="ai-source-open" href="${escapeHtml(item.url)}" target="_blank" rel="noopener noreferrer">查看网页 →</a>`;
    else if (item.paper_id) action = `<button type="button" class="src-item ai-source-open" data-paper-id="${escapeHtml(item.paper_id)}" data-page="${page}" data-has-pdf="${!!item.has_pdf}" data-snippet="${escapeHtml(kind==='original' ? item.locator || item.snippet || '' : '')}">${item.has_pdf?'打开原文':'查看摘要 / 补齐 PDF'} →</button>`;
    return `<div class="ai-source-row">${details}${action}</div>`;
  }
  return `${sources.length?`<div class="ai-evidence"><div class="ai-evidence-title">本轮实际引用</div>${sources.map(row=>card(row,true)).join('')}</div>`:''}${candidates.length?`<details class="ai-candidates"><summary>检索候选（未被本轮编号引用，${candidates.length} 条）</summary>${candidates.map(row=>card(row,false)).join('')}</details>`:''}`;
}

export function renderSessionControls(session) {
  return `<div class="ai-session-controls">${session.busy?'<button type="button" data-ai-action="stop">停止生成</button>':''}${session.data?.undo_available?'<span>已清空本会话和附件。</span><button type="button" data-ai-action="undo">撤销清空</button>':''}${session.error?`<span role="alert">${escapeHtml(session.error)}</span>`:''}${!session.busy && session.lastRequest && (session.error || session.progress?.state==='stopped')?'<button type="button" data-ai-action="retry">重新生成</button>':''}</div>`;
}

export function bindSessionControls(container, session) {
  container?.addEventListener('click', async event => {
    const action = event.target.closest('[data-ai-action]')?.dataset.aiAction;
    if (!action || !['stop','retry','undo'].includes(action)) return;
    try { await session[action](); }
    catch (error) { toast(error.message,'error'); }
  });
}

export function answerActions(index, message, extraPapers = []) {
  const papers = new Map();
  for (const row of [...(message.sources||[]),...(message.retrieval_candidates||[]),...extraPapers]) {
    if (row.paper_id) papers.set(row.paper_id,row.title||'未命名论文');
  }
  return `<div class="ai-answer-actions" data-answer-index="${index}"><button type="button" data-answer-copy>复制回答</button>${papers.size?`<label>存入论文笔记 <select aria-label="选择要保存回答的论文">${[...papers].map(([id,title])=>`<option value="${escapeHtml(id)}">${escapeHtml(title)}</option>`).join('')}</select></label><button type="button" data-answer-save>保存回答</button>`:''}<span class="ai-action-result" role="status"></span></div>`;
}

export async function handleAnswerAction(event, messages) {
  const button = event.target.closest('[data-answer-copy],[data-answer-save]');
  if (!button) return false;
  const group = button.closest('[data-answer-index]');
  const item = messages[Number(group.dataset.answerIndex)];
  if (!item) return true;
  const result = group.querySelector('.ai-action-result');
  button.disabled = true;
  try {
    if (button.hasAttribute('data-answer-copy')) {
      await navigator.clipboard.writeText(item.content || '');
      if (result) result.textContent = '已复制';
    } else {
      const pid = group.querySelector('select')?.value;
      if (!pid) return true;
      await api.post(`/papers/${encodeURIComponent(pid)}/notes/append`, {text:'### AI 问答记录\n'+(item.content||'')});
      emit('notes-changed',{paperId:pid});
      if (result) result.textContent = '已存入笔记';
      emit('library-changed');
    }
  } catch (error) { if (result) result.textContent=error.message; toast(error.message,'error'); }
  finally {button.disabled=false;}
  return true;
}
