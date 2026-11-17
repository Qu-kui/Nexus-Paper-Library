import {api, md, emit} from './api.js';

const drafts = new Map();

// Each visible host owns its requests; switching papers cannot paint late results in another paper.
export function mountPaperNotes(root, paperId) {
  if (!root || !paperId) return;
  if (root.dataset.paperNotes === paperId) { root.refreshPaperNotes?.(); return; }
  root.disposePaperNotes?.();
  root.dataset.paperNotes = paperId;
  root.classList.add('paper-notes');
  root.innerHTML = `<div class="paper-notes-head"><h4>论文笔记</h4><button type="button" class="btn tiny" data-note-reload>刷新笔记</button></div>
    <p class="muted small" data-note-status role="status">正在读取笔记…</p>
    <section class="paper-personal-notes"><h4>我的笔记</h4><div class="markdown" data-personal-notes></div></section>
    <div data-imported-notes></div>
    <form class="paper-note-compose"><label>追加笔记<textarea rows="3" maxlength="50000" placeholder="记录想法，支持 Markdown；保存时追加到现有笔记。"></textarea></label>
      <div><span class="muted small" data-note-save-status role="status"></span><button type="submit" class="btn tiny primary">保存追加笔记</button></div></form>`;
  const field=root.querySelector('textarea'), button=root.querySelector('[type="submit"]');
  const status=root.querySelector('[data-note-status]'), saveStatus=root.querySelector('[data-note-save-status]');
  const identity={}; root.noteOwner=identity;
  const owns=()=>root.isConnected && root.noteOwner===identity;
  let request, generation=0;
  field.value=drafts.get(paperId)||'';
  field.addEventListener('input',()=>{drafts.set(paperId,field.value);saveStatus.textContent=field.value?'尚未保存':'';});
  async function refresh() {
    request?.abort();request=new AbortController();const token=++generation;
    status.textContent='正在读取笔记…';
    try {
      const data=await api.get(`/papers/${encodeURIComponent(paperId)}/notes`,{signal:request.signal});
      if(!owns() || token!==generation)return;
      root.querySelector('[data-personal-notes]').innerHTML=data.personal?md(data.personal):'<p class="muted small">还没有手写笔记或已保存的 AI 回答。</p>';
      const imported=root.querySelector('[data-imported-notes]');imported.replaceChildren();
      const entries=Array.isArray(data.imported)?data.imported:[];
      for(const [index,note] of entries.entries()) {
        const section=document.createElement('details');section.className='paper-imported-note';section.open=entries.length<=3||index===0;
        const summary=document.createElement('summary');
        const source={zotero:'Zotero',endnote:'EndNote',obsidian:'Obsidian'}[note.source]||'迁移';
        const label=(note.label||'笔记').replace(/^(?:Zotero|EndNote|Obsidian)\s*/i,'');
        summary.textContent=`${source} · ${label} ${index+1}`;
        const body=document.createElement('div');body.className='markdown paper-note-body';body.innerHTML=md(note.content||'');
        section.append(summary,body);imported.append(section);
      }
      status.textContent=entries.length?`已保留 ${entries.length} 条迁移笔记 · 原笔记只读，想法可在下方追加`:'迁移笔记会在这里显示；已有文献可重新导入带笔记的导出文件补齐。';
    } catch(error) {
      if(!owns() || token!==generation || request.signal.aborted)return;
      status.textContent=`笔记读取失败：${error.message}。可点击「刷新笔记」重试。`;
    }
  }
  root.refreshPaperNotes=refresh;
  root.disposePaperNotes=()=>{generation++;request?.abort();root.noteOwner=null;delete root.dataset.paperNotes;};
  root.querySelector('[data-note-reload]').addEventListener('click',refresh);
  root.querySelector('form').addEventListener('submit',async event=>{
    event.preventDefault();const value=field.value.trim();if(!value||button.disabled)return;
    button.disabled=true;saveStatus.textContent='保存中…';
    try {
      await api.post(`/papers/${encodeURIComponent(paperId)}/notes/append`,{text:value});
      // Preserve any further typing made while this append was in flight.
      if(field.value.trim()===value && (drafts.get(paperId)||'').trim()===value) {drafts.delete(paperId);if(owns())field.value='';}
      if(owns())saveStatus.textContent=field.value?'已追加，仍有未保存内容':'已追加保存';
      emit('notes-changed',{paperId});
    } catch(error) {if(owns())saveStatus.textContent=`保存失败：${error.message}，内容已保留。`;}
    finally {if(owns())button.disabled=false;}
  });
  refresh();
}

document.addEventListener('pw:notes-changed',event=>{
  document.querySelectorAll('[data-paper-notes]').forEach(root=>{
    if(!event.detail?.paperId || root.dataset.paperNotes===event.detail.paperId)root.refreshPaperNotes?.();
  });
});
