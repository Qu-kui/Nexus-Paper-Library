/* Persistent AI literature discovery. Each topic owns its requests, draft and actions. */
import {api, escapeHtml as esc, shortAuthors, emit, toast, on, md} from './api.js';
import {getConversation} from './conversation.js';
import {renderProgress,captureProgress,restoreProgress} from './ai-feedback.js';

const byId = id => document.getElementById(id);
const topics = new Map();
let initialized = false, selected = '', viewToken = 0, creating = false, listError = '', lastDeleted = null;
const statusLabels = {pending:'待处理',added:'已入库',ignored:'已忽略'};
const examples = [
  '查找大语言模型最近一年的最新进展，推荐值得读的论文并说明原因。',
  '帮我找到 Attention Is All You Need 这篇论文，并找出与它相关的研究。',
  '梳理 Transformer 的历史发展脉络，按阶段列出代表论文和关键变化。',
];
function safeUrl(value) { try { const url=new URL(String(value||'')); return ['http:','https:'].includes(url.protocol)?url.href:''; } catch {return '';} }
function active(owner) { return selected===owner.id&&topics.get(owner.id)===owner; }
function nativeChineseTitle(title) {
  const value=String(title||''),chinese=(value.match(/[\u3400-\u9fff]/gu)||[]).length,latin=(value.match(/[a-z]/gi)||[]).length;
  return chinese>0&&chinese/(chinese+latin)>=0.4;
}
function translatedTitle(item) {
  const value=typeof item.title_zh==='string'?item.title_zh.trim():'';
  return /[\u3400-\u9fff]/u.test(value)?value:'';
}
function needsTranslation(item) { return !nativeChineseTitle(item.title)&&!translatedTitle(item); }
function remember(id) { try {localStorage.setItem('pw-discovery-topic',id);} catch {} }
function topic(record) {
  if (!topics.has(record.id)) {
    const owner={id:record.id,session:record,items:[],runs:[],draft:'',filter:'pending',error:'',loading:false,submitting:false,requestToken:0,loadToken:0,translationToken:0,translating:false,translationNote:'',deleting:false,dataRevision:0,actions:new Map(),itemDecisions:new Map(),conversation:getConversation(`discovery:${record.id}`)};
    owner.unsubscribe=owner.conversation.subscribe(()=>{if(active(owner))render(owner);});
    topics.set(record.id,owner);
  }
  const owner=topics.get(record.id); owner.session={...owner.session,...record}; return owner;
}
function renderTopics() {
  const select=byId('discover-topic');
  select.innerHTML=[...topics.values()].map(owner=>`<option value="${esc(owner.id)}">${esc(owner.session.title||'新研究话题')}${owner.submitting||owner.conversation.busy?' · 研究中':''}</option>`).join('');
  select.value=selected;select.disabled=!topics.size;
  byId('btn-discover-new').disabled=creating;
  byId('btn-discover-delete').disabled=!topics.get(selected)||!!topics.get(selected)?.deleting;
}
function renderHistory(owner) {
  const container=byId('discover-history'),open=container.querySelector('details')?.open;
  const messages=(owner.conversation.data?.messages||[]).filter(message=>message.role==='user');
  container.innerHTML=messages.length?`<details ${open?'open':''}><summary>研究请求记录 · ${messages.length} 轮</summary><div class="discovery-messages">${messages.map(message=>`<div class="discovery-message question"><b>你</b><div>${esc(message.content)}</div></div>`).join('')}</div></details>`:'';
  for (const run of owner.runs.filter(run=>run.intent==='file' && run.id!==owner.run?.id)) {
    container.insertAdjacentHTML('beforeend',`<details><summary>附件阅读 · ${esc(run.question.slice(0,70))}</summary><div class="markdown">${md(run.answer)}</div></details>`);
  }
}
function renderSummary(owner) {
  const run=owner.run||owner.runs[0],container=byId('discover-summary');
  if(!run){container.innerHTML='';return;}
  const names=[...new Set(owner.items.filter(item=>item.is_current!==false).flatMap(item=>(item.sources||[]).map(source=>typeof source==='string'?source:source.name)).filter(Boolean))];
  const range=[run.date_from,run.date_to].filter(Boolean).join(' → '),open=container.querySelector('details')?.open;
  if(run.intent==='file') {
    container.innerHTML=`<section class="discovery-run-card"><h4>附件阅读</h4><p class="muted small">依据本话题的附件文字节选</p><div class="markdown">${md(run.answer)}</div></section>`;
    return;
  }
  const {seed, choices=[] ,basis} = run.tracking || {};
  const hasCurrent = run.references?.length || owner.items.some(item=>item.run_id===run.id);
  const warnings = run.warnings || [];
  const incomplete = warnings.find(warning=>/查询失败|未返回|限流|访问受限|超时|时间已到/.test(warning));
  const citationIssue = warnings.find(warning=>warning.startsWith('OpenAlex 直接引用'));
  const link = safeUrl(seed?.url);
  const buttonsDisabled = owner.submitting || owner.conversation.busy || owner.materialBusy || owner.loading ? 'disabled' : '';
  const start = run.date_from || '';
  const direct = owner.items.filter(item=>item.is_current && item.relation==='direct').length;
  const paperCard = seed ? `<section class="discovery-run-card"><h4>正在追踪的原文</h4>
    <strong>${link?`<a href="${esc(link)}" target="_blank" rel="noopener noreferrer">${esc(seed.title)}</a>`:esc(seed.title)}</strong>
    <p class="muted small">${[seed.venue,seed.published,seed.doi].filter(Boolean).map(esc).join(' · ')}</p>
    <p>${basis==='preprint'?'自预印本发布起':basis==='custom'?'自选范围':'自原文发表起'} · ${esc(range||'日期待核实')} · ${direct} 篇已核实的直接引用${run.tracking?.options?.citations_only?' · 本轮仅查直接引用':''}</p>
    ${citationIssue&&hasCurrent?`<p class="discovery-warning">${esc(citationIssue)}</p>`:''}
    <div class="discovery-run-actions"><label>起始日期 <input type="date" data-discovery-date value="${esc(start)}"></label>
    <button class="btn tiny" data-discovery-run="date" ${buttonsDisabled}>按此日期重查</button>
    ${seed.arxiv_id?'':`<button class="btn tiny" data-discovery-run="preprint" ${buttonsDisabled}>从预印本起查</button>`}
    ${citationIssue?`<button class="btn tiny" data-discovery-run="citations" ${buttonsDisabled}>重试引用来源</button>`:''}
    ${hasCurrent&&run.tracking?.options?.citations_only?`<button class="btn tiny" data-discovery-run="broaden" ${buttonsDisabled}>扩大到同方向研究</button>`:''}
    <label><input type="checkbox" data-discovery-direct ${owner.directOnly?'checked':''}> 仅看直接引用</label></div></section>` : '';
  const empty = !hasCurrent ? `<section class="discovery-run-card"><h4>${choices.length?'选择原文':seed?'本轮未筛出可用候选':'本轮检索未返回候选'}</h4>
    <p>${esc(run.intent==='followup'&&!seed?run.answer:incomplete?`${incomplete} 本轮覆盖有限。`:'本轮没有筛出符合条件的论文。可以调整问题后再查。')}</p>
    ${choices.map((paper,index)=>`<button class="btn tiny discovery-seed-choice" data-discovery-run="choose" data-seed-index="${index}" ${buttonsDisabled}>${esc([paper.title,paper.venue,paper.published,paper.doi].filter(Boolean).join(' · '))}</button>`).join('')}
    <div class="discovery-run-actions"><button class="btn tiny" data-discovery-run="retry" ${buttonsDisabled}>重试本轮</button>
    ${seed?`<button class="btn tiny" data-discovery-run="broaden" ${buttonsDisabled}>扩大到同方向研究</button>`:''}
    ${run.intent==='followup'&&!seed?'<button class="btn tiny" data-discovery-run="edit">补充原文信息</button>':''}</div></section>`:'';
  container.innerHTML=paperCard+empty+`<details class="discovery-search-bounds" ${open?'open':''}><summary>检索详情${run.queries?.length?` · ${run.queries.length} 个检索式`:''}${warnings.length?` · ${warnings.length} 条提示`:''}</summary>${range?`<div>检索日期范围：${esc(range)}</div>`:''}${names.length?`<div>来源：${esc(names.join(' · '))}</div>`:''}${(run.queries||[]).map(query=>`<div>${esc(typeof query==='string'?query:query.query||query.q||'')}</div>`).join('')}${warnings.map(warning=>`<p class="discovery-warning">${esc(warning)}</p>`).join('')}</details>`;
}
function rowHtml(owner,item) {
  const operation=owner.actions.get(item.id),status=statusLabels[item.status]||statusLabels.pending;
  const url=safeUrl(item.url)||safeUrl(item.doi?`https://doi.org/${item.doi}`:'')||safeUrl(item.arxiv_id?`https://arxiv.org/abs/${encodeURIComponent(item.arxiv_id)}`:'');
  const authors=Array.isArray(item.authors)?item.authors.join('; '):item.authors;
  const sources=(item.sources||[]).map(source=>{const name=typeof source==='string'?source:source.name;const href=safeUrl(source.url);return href?`<a href="${esc(href)}" target="_blank" rel="noopener noreferrer">${esc(name||'来源')}</a>`:esc(name||'');}).filter(Boolean).join(' · ');
  const meta=[shortAuthors(authors,3),item.year||String(item.published||'').slice(0,10),item.venue].filter(Boolean).join(' · ');
  const index=item.citation_index!=null?`[${item.citation_index}]`:'此前';
  const saved=item.status==='added'||item.in_library;
  const translated=translatedTitle(item),translation=translated&&translated!==String(item.title||'').trim()?`<div class="discovery-title-zh" lang="zh-CN">${esc(translated)}</div>`:needsTranslation(item)?'<div class="discovery-translation-missing">中文题名待补齐</div>':'';
  const reason = item.is_current && item.relation && item.reason ? item.reason : '';
  return `<article class="discovery-row" id="discovery-candidate-${esc(item.id)}" data-candidate-id="${esc(item.id)}" ${item.citation_index!=null?`data-candidate-index="${esc(item.citation_index)}"`:''}>
    <div class="discovery-index">${esc(index)}</div><div class="discovery-paper"><div class="discovery-title" title="${esc(item.title)}">${url?`<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(item.title||'未命名论文')}</a>`:esc(item.title||'未命名论文')}</div>${translation}<div class="discovery-meta">${esc(meta)}</div>${reason?`<p class="discovery-visible-reason">${esc(reason.length>150?reason.slice(0,150)+'…':reason)}</p>`:''}<details class="discovery-details"><summary>推荐理由与详情${item.stage?` · ${esc(item.stage)}`:''}</summary><h4>${esc(item.title)}</h4>${item.reason&&(!reason||reason.length>150)?`<p>${esc(item.reason)}</p>`:''}${item.stage?`<p>阶段：${esc(item.stage)}</p>`:''}${item.abstract?`<p>${esc(item.abstract)}</p>`:''}${sources?`<p>来源：${sources}</p>`:''}${item.doi?`<p>DOI：${esc(item.doi)}</p>`:''}${item.arxiv_id?`<p>arXiv：${esc(item.arxiv_id)}</p>`:''}</details></div>
    <div class="discovery-state">${esc(status)}${item.in_library&&item.status!=='added'?'<small>文献库已有</small>':''}</div><div class="discovery-row-actions">${url?`<a class="btn tiny" href="${esc(url)}" target="_blank" rel="noopener noreferrer">查看论文</a>`:''}<button type="button" class="btn tiny ${saved?'':'primary'}" data-candidate-action="add" ${saved||operation?.busy?'disabled':''}>${saved?'已入库':operation?.busy&&operation.action==='add'?'加入中…':'加入文献库'}</button><button type="button" class="btn tiny ghost" data-candidate-action="${item.status==='ignored'?'restore':'ignore'}" ${operation?.busy?'disabled':''}>${item.status==='ignored'?'恢复待处理':'忽略'}</button>${operation?.error?`<span class="discovery-row-error" role="alert">${esc(operation.error)}</span>`:''}</div></article>`;
}
function renderRows(owner) {
  const container=byId('discover-results');
  const expanded=new Set([...container.querySelectorAll('[data-candidate-id] details[open]')].map(node=>node.closest('[data-candidate-id]').dataset.candidateId));
  const items=owner.items||[],counts={pending:0,added:0,ignored:0};for(const item of items)counts[item.status]=(counts[item.status]||0)+1;
  byId('discover-count').textContent=`共 ${items.length} 篇 · 待处理 ${counts.pending} · 已入库 ${counts.added} · 已忽略 ${counts.ignored}`;
  byId('discover-filter').value=owner.filter;
  const missing=items.filter(needsTranslation).length,translate=byId('btn-discover-translate');
  translate.hidden=!missing&&!owner.translating;translate.disabled=owner.translating||owner.submitting||owner.conversation.busy||owner.deleting;
  translate.textContent=owner.translating?'正在补齐中文题名…':`补齐中文题名（${missing}）`;
  byId('discover-translation-note').textContent=owner.translationNote;
  const tracking = !!owner.run?.tracking?.seed;
  const shown=items.filter(item=>(owner.filter==='all'||item.status===owner.filter) && (!tracking || !owner.directOnly || (item.is_current && item.relation==='direct')));
  const groups = tracking ? [
    ['直接引用原文', shown.filter(item=>item.is_current && item.relation==='direct')],
    ['同方向研究 · 未核实引用关系', shown.filter(item=>item.is_current && item.relation!=='direct')],
    ['此前的候选', shown.filter(item=>!item.is_current)],
  ] : [['',shown]];
  container.innerHTML=shown.length?groups.filter(([,rows])=>rows.length).map(([label,rows])=>
    (label?`<h4 class="discovery-group-title">${esc(label)} · ${rows.length} 篇</h4>`:'')+rows.map(item=>rowHtml(owner,item)).join('')).join(''):
    `<div class="discovery-empty">${tracking&&owner.directOnly?'当前筛选下没有已核实的直接引用。':items.length?'这个筛选下没有候选论文。':owner.loading?'正在读取话题…':owner.run?'本轮没有新的候选论文。':'用中文描述你想了解的问题，研究结果会保留在这个话题中。'}</div>`;
  container.querySelectorAll('[data-candidate-id]').forEach(row=>{if(expanded.has(row.dataset.candidateId))row.querySelector('details').open=true;});
}

function renderMaterials(owner, busy) {
  const files = owner?.conversation.data?.attachments || [];
  const locked = !owner || busy || owner.loading || owner.deleting || owner.materialBusy;
  byId('btn-discover-attach').disabled = !!locked || files.length >= 8;
  byId('btn-discover-read').disabled = !!locked || !files.length;
  byId('discover-materials').innerHTML = files.map((file,index)=>
    `<span class="discovery-file">${esc(file.name)} <button type="button" class="btn tiny ghost" data-discovery-file="${index}" aria-label="移除 ${esc(file.name)}" ${locked?'disabled':''}>移除</button></span>`).join('')+
    (owner?.materialNotice?`<span class="muted small">${esc(owner.materialNotice)}</span>`:'');
}

async function changeMaterials(owner, files, removeIndex) {
  if (!owner || owner.materialBusy || owner.submitting || owner.conversation.busy || owner.deleting) return;
  owner.materialBusy = true;
  owner.materialNotice = files.length ? '正在读取文件文字…' : '正在移除附件…';
  if (active(owner)) render(owner);
  try {
    await owner.conversation.load();
    const attachments = [...(owner.conversation.data?.attachments || [])];
    if (removeIndex !== undefined) {
      if (!attachments[removeIndex]) return;
      attachments.splice(removeIndex,1);
    }
    const messages = [];
    let added = 0;
    for (const file of files) {
      if (attachments.length >= 8) { messages.push('每个话题最多保留 8 个附件。'); break; }
      if (owner.deleting || topics.get(owner.id)!==owner) return;
      try {
        const material = await api.uploadFile('/ai/profile/upload',file);
        if (attachments.some(item=>item.name===material.name && item.text===material.text)) continue;
        attachments.push({name:material.name,text:material.text});
        added++;
      } catch(error) { messages.push(`${file.name} · ${error.message}`); }
    }
    if (owner.deleting || topics.get(owner.id)!==owner) return;
    if (added || removeIndex !== undefined) await owner.conversation.materials(attachments);
    owner.materialNotice = messages.join(' · ') || (files.length ? (added?`已读取 ${added} 个文件`:'所选文件已在本话题中') : '附件已移除');
  } catch(error) { owner.materialNotice=error.message; }
  finally { owner.materialBusy=false; if(active(owner))render(owner); }
}

function runAction(owner, button) {
  const run = owner?.run;
  if (!run) return;
  const action = button.dataset.discoveryRun;
  if (action==='edit') {
    const input = byId('discover-input');
    input.value = owner.draft = run.question;
    input.focus();
    return;
  }
  const tracking = run.tracking || {};
  const options = {...tracking.options};
  const paper = action==='choose' ? tracking.choices?.[Number(button.dataset.seedIndex)] : tracking.seed;
  if (paper) {
    options.mode = 'followup';
    options.seed_query = paper.doi || (paper.arxiv_id ? `arXiv:${paper.arxiv_id}` : paper.title);
  }
  if (action==='preprint') { options.date_basis='preprint'; options.date_from=''; }
  if (action==='citations') options.citations_only=true;
  if (action==='date') {
    options.date_basis='publication';
    options.date_from=byId('discover-summary').querySelector('[data-discovery-date]').value;
  }
  if (action==='broaden') { options.broaden=true; options.citations_only=false; owner.directOnly=false; }
  research(owner,run.question,options);
}

function render(owner=topics.get(selected)) {
  renderTopics();const busy=!!owner&&(owner.submitting||owner.conversation.busy);
  byId('btn-discover').disabled=!owner||busy||owner.loading||owner.deleting||owner.materialBusy;
  byId('btn-discover').textContent=busy?'研究中…':'开始研究';
  renderMaterials(owner,busy);
  const progressState=captureProgress(byId('discover-feedback'));
  if(!owner){for(const id of ['discover-history','discover-summary','discover-results'])byId(id).innerHTML='';byId('discover-count').textContent='共 0 篇';byId('btn-discover-translate').hidden=true;byId('discover-translation-note').textContent='';byId('discover-feedback').innerHTML=listError?`<p role="alert">${esc(listError)}</p><button class="btn tiny" data-discovery-control="reload">重新读取话题</button>`:'';return;}
  const conversation=owner.conversation;
  byId('discover-feedback').innerHTML=`${busy?renderProgress(conversation.progress||{stage:'准备本轮研究'}):''}<div class="ai-session-controls">${busy?'<button type="button" data-discovery-control="stop">停止研究</button>':''}${owner.error?`<span role="alert">${esc(owner.error)}</span>`:''}${!busy&&conversation.lastRequest&&(owner.error||conversation.progress?.state==='stopped')?'<button type="button" data-discovery-control="retry">重新研究</button>':''}${lastDeleted?'<span>话题已删除，可在回收站恢复。</span><button type="button" data-discovery-control="restore-topic">撤销删除</button>':''}${conversation.data?.undo_available?'<button type="button" data-discovery-control="undo">撤销清空</button>':''}</div>`;
  restoreProgress(byId('discover-feedback'),progressState);
  renderHistory(owner);renderSummary(owner);renderRows(owner);
}
function applyResult(owner,data,decisionAfter=null) {
  if(data.session)owner.session={...owner.session,...data.session};
  if(Array.isArray(data.items))owner.items=data.items.map(item=>{
    const decision=owner.itemDecisions.get(item.id);
    // Preserve user decisions that committed after this research snapshot began.
    // Bibliographic metadata and citation indices always belong to the new result.
    return decisionAfter!==null&&decision?.revision>decisionAfter?{...item,...decision.state}:item;
  });
  if(Array.isArray(data.runs))owner.runs=data.runs;
  if(data.run){owner.run=data.run;owner.runs=[data.run,...owner.runs.filter(run=>run.id!==data.run.id)];}
  else if(data.runs)owner.run=data.runs[0];
}
async function loadTopic(owner) {
  const token=++owner.loadToken,request=owner.requestToken,dataRevision=owner.dataRevision;
  owner.loading=true;if(active(owner))render(owner);
  try {
    const data=await api.get(`/discover/sessions/${encodeURIComponent(owner.id)}`);
    if(token!==owner.loadToken||request!==owner.requestToken||dataRevision!==owner.dataRevision)return;
    applyResult(owner,data);owner.error=owner.conversation.error||'';if(data.conversation&&!owner.conversation.busy)owner.conversation.accept(data.conversation);
  }catch(error){if(token===owner.loadToken&&request===owner.requestToken)owner.error=error.message;}
  finally{if(token===owner.loadToken){owner.loading=false;if(active(owner))render(owner);}}
}
async function selectTopic(id) {
  const owner=topics.get(id);if(!owner)return;
  topics.get(selected)&&(topics.get(selected).draft=byId('discover-input').value);
  selected=id;++viewToken;remember(id);byId('discover-input').value=owner.draft;render(owner);
  await loadTopic(owner);
}
async function newTopic(expectedView=viewToken) {
  if(creating)return;creating=true;listError='';render();
  try {const record=await api.post('/discover/sessions',{});const owner=topic(record.session||record);if(expectedView===viewToken)await selectTopic(owner.id);else renderTopics();}
  catch(error){listError=error.message;const owner=topics.get(selected);if(owner)owner.error=error.message;}
  finally{creating=false;render();}
}
async function loadTopics() {
  const token=viewToken;
  try {const data=await api.get('/discover/sessions');if(token!==viewToken)return;for(const record of data.items||[])topic(record);if(lastDeleted&&topics.has(lastDeleted.id))lastDeleted=null;
    let stored='';try{stored=localStorage.getItem('pw-discovery-topic')||'';}catch{}
    if(topics.size)await selectTopic(topics.has(stored)?stored:topics.keys().next().value);else await newTopic(token);
  }catch(error){listError=error.message;render();}
}
async function research(owner,question,options={}) {
  if(!owner||owner.deleting||owner.submitting||owner.conversation.busy||owner.materialBusy)return;
  question=String(question||'').trim();if(!question){byId('discover-input').focus();return;}
  const token=++owner.requestToken; ++owner.loadToken;++owner.translationToken;owner.translating=false;owner.loading=false;owner.submitting=true;owner.error='';owner.draft=question;
  if(active(owner))render(owner);
  const epoch=owner.conversation.epoch,dataRevision=owner.dataRevision;
  const payload={session_id:owner.id,question,...options};
  owner.conversation.lastRequest={path:'/discover/ai',payload,displayQuestion:question};
  try {
    await owner.conversation.load();
    if(token!==owner.requestToken||epoch!==owner.conversation.epoch)return;
    const result=await owner.conversation.post('/discover/ai',payload,question);
    if(token!==owner.requestToken||epoch!==owner.conversation.epoch)return;
    const decisionsChanged=owner.dataRevision!==dataRevision;
    ++owner.dataRevision;applyResult(owner,result,dataRevision);
    if(decisionsChanged)await loadTopic(owner);
  }catch(error){if(token===owner.requestToken&&epoch===owner.conversation.epoch)owner.error=error.message;}
  finally{if(token===owner.requestToken){owner.submitting=false;if(active(owner))render(owner);else renderTopics();}}
}
async function control(action) {
  if(action==='reload'){await loadTopics();return;}
  if(action==='restore-topic'){await restoreDeleted();return;}
  const owner=topics.get(selected);if(!owner)return;
  const token=owner.requestToken;
  try {
    if(action==='stop'){++owner.requestToken;owner.submitting=false;if(owner.conversation.busy)await owner.conversation.stop();else owner.conversation.progress={state:'stopped'};}
    else if(action==='retry'){const {session_id,question,...options}=owner.conversation.lastRequest?.payload||{};await research(owner,question,options);}
    else if(action==='undo'){await owner.conversation.undo();}
  }catch(error){if(action==='stop'||token===owner.requestToken)owner.error=error.message;}
  finally{if(active(owner))render(owner);}
}
async function candidateAction(owner,id,action) {
  if(!owner||owner.deleting||owner.actions.get(id)?.busy)return;
  const operation={busy:true,action,error:''};owner.actions.set(id,operation);if(active(owner))renderRows(owner);
  try {
    const result=action==='add'?await api.post(`/discover/candidates/${encodeURIComponent(id)}/add`,{}):await api.post(`/discover/candidates/${encodeURIComponent(id)}/state`,{status:action==='restore'?'pending':'ignored'});
    if(owner.actions.get(id)!==operation)return;
    ++owner.dataRevision;
    if(result.item){
      const state=Object.fromEntries(['status','paper_id','in_library'].filter(key=>key in result.item).map(key=>[key,result.item[key]]));
      owner.itemDecisions.set(id,{revision:owner.dataRevision,state});
      owner.items=owner.items.map(item=>item.id===id?{...item,...state}:item);
    }
    if(action==='add')emit('library-changed');
  }catch(error){if(owner.actions.get(id)===operation)operation.error=error.message;}
  finally{operation.busy=false;if(active(owner))renderRows(owner);}
}
async function translateTitles(owner) {
  if(!owner||owner.translating||owner.submitting||owner.conversation.busy||owner.deleting)return;
  const token=++owner.translationToken,request=owner.requestToken;
  owner.translating=true;owner.translationNote='';renderRows(owner);
  try {
    const result=await api.post(`/discover/sessions/${encodeURIComponent(owner.id)}/translate`,{});
    if(topics.get(owner.id)!==owner||token!==owner.translationToken||request!==owner.requestToken)return;
    const translations=new Map((result.items||[]).map(item=>[item.id,item.title_zh]));
    owner.items=owner.items.map(item=>translations.has(item.id)?{...item,title_zh:translations.get(item.id)}:item);
    ++owner.dataRevision;
    const remaining=owner.items.filter(needsTranslation).length;
    owner.translationNote=[remaining?`仍有 ${remaining} 篇待补齐，可再次尝试。`:'中文题名已补齐。',...(result.warnings||[])].join(' ');
  }catch(error){if(token===owner.translationToken)owner.translationNote=`补齐失败：${error.message}，可重试。`;}
  finally{if(token===owner.translationToken){owner.translating=false;if(active(owner))renderRows(owner);}}
}
async function deleteTopic(owner) {
  if(!owner||owner.deleting)return;
  owner.deleting=true;++owner.requestToken;++owner.loadToken;++owner.translationToken;
  owner.submitting=false;owner.translating=false;owner.loading=false;render(owner);
  try {
    const result=await api.del(`/discover/sessions/${encodeURIComponent(owner.id)}`);
    ++owner.conversation.epoch;clearTimeout(owner.conversation.pollTimer);
    owner.conversation.busy=false;owner.conversation.pending=null;owner.conversation.data=null;owner.conversation.loading=null;owner.conversation.progress=null;owner.conversation.error=null;owner.conversation.lastRequest=null;
    owner.unsubscribe?.();owner.actions.clear();topics.delete(owner.id);
    if(result.trash_id)lastDeleted={id:owner.id,trashId:result.trash_id};
    ++viewToken;
    if(selected===owner.id){selected='';remember('');byId('discover-input').value='';if(topics.size)await selectTopic(topics.keys().next().value);else await newTopic(viewToken);}
    else render();
    toast('话题已移入回收站，已加入文献库的论文仍保留。','success');
  }catch(error){owner.deleting=false;owner.error=error.message;if(active(owner))render(owner);}
}
async function restoreDeleted() {
  const deleted=lastDeleted;if(!deleted||deleted.restoring)return;deleted.restoring=true;
  try {
    await api.post(`/maintenance/trash/${encodeURIComponent(deleted.trashId)}/restore`,{});
    lastDeleted=null;
    const data=await api.get('/discover/sessions');for(const record of data.items||[])topic(record);
    await selectTopic(deleted.id);toast('研究话题已恢复','success');
  }catch(error){deleted.restoring=false;toast(error.message,'error');}
}
export function initDiscover() {
  if(initialized)return;initialized=true;
  byId('discover-examples').innerHTML=examples.map((text,index)=>`<button type="button" data-discovery-example="${index}">${['了解最新进展','查找指定论文','梳理发展脉络'][index]}</button>`).join('');
  byId('discover-examples').addEventListener('click',event=>{const button=event.target.closest('[data-discovery-example]');if(button){byId('discover-input').value=examples[Number(button.dataset.discoveryExample)];const owner=topics.get(selected);if(owner)owner.draft=byId('discover-input').value;byId('discover-input').focus();}});
  byId('discover-input').addEventListener('input',()=>{const owner=topics.get(selected);if(owner)owner.draft=byId('discover-input').value;});
  byId('discover-input').addEventListener('keydown',event=>{if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();research(topics.get(selected),byId('discover-input').value);}});
  byId('btn-discover').addEventListener('click',()=>research(topics.get(selected),byId('discover-input').value));
  const picker = byId('discover-files');
  byId('btn-discover-attach').addEventListener('click',()=>picker.click());
  picker.addEventListener('change',()=>{
    const files = Array.from(picker.files || []);
    picker.value = '';
    if (files.length) changeMaterials(topics.get(selected),files);
  });
  byId('btn-discover-read').addEventListener('click',()=>research(topics.get(selected),
    byId('discover-input').value.trim() || '概括附件的研究问题、主要内容和可以进一步查证的线索。',{mode:'file'}));
  byId('discover-materials').addEventListener('click',event=>{
    const button = event.target.closest('[data-discovery-file]');
    if (button) changeMaterials(topics.get(selected),[],Number(button.dataset.discoveryFile));
  });
  byId('discover-summary').addEventListener('click',event=>{
    const button = event.target.closest('[data-discovery-run]');
    if (button) runAction(topics.get(selected),button);
  });
  byId('discover-summary').addEventListener('change',event=>{
    if (!event.target.matches('[data-discovery-direct]')) return;
    const owner = topics.get(selected);
    if (owner) { owner.directOnly=event.target.checked; renderRows(owner); }
  });
  byId('discover-topic').addEventListener('change',event=>selectTopic(event.target.value));
  byId('btn-discover-new').addEventListener('click',()=>newTopic());
  byId('btn-discover-delete').addEventListener('click',()=>deleteTopic(topics.get(selected)));
  byId('btn-discover-translate').addEventListener('click',()=>translateTitles(topics.get(selected)));
  on('recovery-changed',()=>loadTopics());
  byId('discover-filter').addEventListener('change',event=>{const owner=topics.get(selected);if(owner){owner.filter=event.target.value;renderRows(owner);}});
  byId('discover-feedback').addEventListener('click',event=>{const button=event.target.closest('[data-discovery-control]');if(button)control(button.dataset.discoveryControl);});
  byId('discover-results').addEventListener('click',event=>{const button=event.target.closest('[data-candidate-action]');if(button)candidateAction(topics.get(selected),button.closest('[data-candidate-id]').dataset.candidateId,button.dataset.candidateAction);});
  loadTopics();
}
