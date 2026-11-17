/* NPL · 知识库问答页
 * 跨文献库检索问答，答案 markdown 渲染，来源可点击打开阅读器
 * 依赖：api.js（api、toast、md、escapeHtml）
 */
import { api, toast, md, escapeHtml } from "./api.js";
import { getConversation } from './conversation.js';
import {renderSources,renderProgress,captureProgress,restoreProgress,renderSessionControls,bindSessionControls,answerActions,handleAnswerAction} from './ai-feedback.js';
import {thinkingPanelHtml} from './thinking-panel.js';
import { webImagesHtml } from './web-images.js';

const session = getConversation('library');

let initialized = false;
let pendingNode = null;
let pendingUpdateTimer = null;
let lastStructureKey = '';
let comparison = {paper_ids:[],titles:[]};

const byId = (id) => document.getElementById(id);

function isLlmMissing(err) {
  return /未配置|未设置|not configured|api[_ ]?key/i.test(String((err && err.message) || ""));
}

function scrollToBottom() {
  const container = byId("chat-messages");
  if (container) container.scrollTop = container.scrollHeight;
}

function hintNode() {
  const container = byId("chat-messages");
  return container ? container.querySelector(".chat-hint") : null;
}

function hideHint() {
  hintNode()?.classList.add("hidden");
}

function showHint() {
  hintNode()?.classList.remove("hidden");
}

/* ---------- 气泡渲染 ---------- */
function appendUserBubble(text) {
  const container = byId("chat-messages");
  if (!container) return;
  const node = document.createElement("div");
  node.className = "msg user";
  node.innerHTML = `<div class="msg-avatar">🧑</div><div class="msg-bubble">${escapeHtml(text).replace(/\n/g, "<br>")}</div>`;
  container.appendChild(node);
}

function buildAssistantBubble(answer, sources, reasoning, candidates, index, thinkingSeconds = 0, webTrace = [], images = []) {
  const node = document.createElement("div");
  node.className = "msg assistant";
  const sourcesHtml = renderSources(sources || [], candidates || []);
  const trace = Array.isArray(webTrace) ? webTrace : [];
  const reasoningHtml = reasoning || trace.length
    ? thinkingPanelHtml({text: reasoning, state: 'done', thinkingSeconds, trace})
    : "";
  let imagesHtml = '';
  try { imagesHtml = webImagesHtml(images); } catch { imagesHtml = ''; }
  node.innerHTML = `<div class="msg-avatar"><img src="/brand/npl-mark.svg" alt="NPL" width="24" height="24"></div><div class="msg-bubble markdown">${reasoningHtml}${md(answer)}${imagesHtml}${sourcesHtml}${answerActions(index,{sources,retrieval_candidates:candidates})}</div>`;
  return node;
}

function appendPendingBubble() {
  const container = byId("chat-messages");
  const node = document.createElement("div");
  node.className = "msg assistant";
  node.innerHTML = `<div class="msg-avatar"><img src="/brand/npl-mark.svg" alt="NPL" width="24" height="24"></div><div class="msg-bubble">${renderProgress(session.progress)}</div>`;
  if (container) container.appendChild(node);
  return node;
}

/* ---------- 发送 ---------- */
async function sendMessage() {
  const input = byId("chat-input");
  const question = String((input && input.value) || "").trim();
  if (!question) return;
  if (session.busy) {
    toast("AI 还在处理上一个问题，请稍等", "info", 2200);
    return;
  }

  const epoch = session.epoch;
  const sendButton = byId("btn-chat-send");
  if (sendButton) sendButton.disabled = true;
  if (input) input.value = "";

  try {
    await session.post('/ai/chat', { question, deep_thinking:true, search_web:true, paper_ids:comparison.paper_ids });
  } catch (err) {
    if (epoch !== session.epoch) return;
    const message = String((err && err.message) || "问答失败");
    if (isLlmMissing(err)) {
      toast("大模型还没配置：去「设置」页填 Base URL / API Key / 模型名", "error", 6200);
    } else {
      toast(`问答失败：${message}`, "error", 5200);
    }
    // 把问题放回输入框，方便直接重试
    if (input && !input.value) input.value = question;
  } finally {
    if (sendButton && sendButton.isConnected) sendButton.disabled = session.busy || !session.data;
  }
}

/* ---------- 清空 / 来源点击 ---------- */
async function clearChat() {
  try { await session.clear(); }
  catch (err) { toast(`清空失败：${err.message}`, 'error'); return; }
  toast("对话已清空", "success", 1800);
  if (byId('chat-input')) byId('chat-input').value = '';
  byId("chat-input")?.focus();
}

function sessionStructureKey() {
  const messages = session.data?.messages || [];
  return [session.data?.revision, messages.length, session.pending, session.busy, session.error].join('|');
}

// 进度变化只刷新「正在生成」那条气泡，避免把整段对话重新 markdown 渲染一遍。
function refreshPendingBubble() {
  if (pendingUpdateTimer) return;
  // 文本越长，整段 markdown 重渲染越贵；自动放宽刷新间隔，保证滚动和打字不卡
  const length = String(session.progress?.answer || '').length;
  const delay = length > 12000 ? 220 : length > 4000 ? 110 : 55;
  pendingUpdateTimer = setTimeout(() => {
    pendingUpdateTimer = null;
    if (!pendingNode?.isConnected || !session.busy) return;
    const bubble = pendingNode.querySelector('.msg-bubble');
    if (!bubble) return;
    const wasOpen = bubble.querySelector('.thinking-panel')?.open;
    bubble.innerHTML = renderProgress(session.progress);
    const panel = bubble.querySelector('.thinking-panel');
    if (panel && wasOpen) panel.open = true;
    const container = byId('chat-messages');
    if (container && container.scrollHeight - container.clientHeight - container.scrollTop <= 40) {
      container.scrollTop = container.scrollHeight;
    }
  }, delay);
}

function onSessionChange() {
  const key = sessionStructureKey();
  if (key !== lastStructureKey) {
    lastStructureKey = key;
    renderConversation();
    return;
  }
  if (session.busy) refreshPendingBubble();
}

function renderConversation() {
  const container = byId("chat-messages");
  if (!container) return;
  const progressState=captureProgress(container),top=container.scrollTop,follow=container.scrollHeight-container.clientHeight-top<=24;
  container.querySelectorAll('.msg,.ai-compare-scope').forEach(node => node.remove());
  if (comparison.paper_ids.length) {
    const scope=document.createElement('div'); scope.className='ai-compare-scope';
    scope.innerHTML=`本轮比较范围：${comparison.titles.map(escapeHtml).join('；')}<button type="button" data-clear-comparison>退出比较</button>`;
    container.appendChild(scope);
  }
  const messages = session.data?.messages || [];
  for (const [index,item] of messages.entries()) {
    if (item.role === 'user') appendUserBubble(item.content);
    else container.appendChild(buildAssistantBubble(item.content, item.sources, item.reasoning, item.retrieval_candidates, index, item.thinking_seconds || 0, item.web_trace || [], item.web_images || []));
  }
  if (session.pending) {
    appendUserBubble(session.pending);
    pendingNode = appendPendingBubble();
  } else {
    pendingNode = null;
  }
  const controls=document.createElement('div'); controls.className='msg ai-controls-message'; controls.innerHTML=renderSessionControls(session); container.appendChild(controls);
  if (messages.length || session.pending) hideHint(); else showHint();
  if (byId('btn-chat-send')) byId('btn-chat-send').disabled = session.busy || !session.data;
  restoreProgress(container,progressState);
  container.scrollTop=follow?container.scrollHeight:top;
}

function openSource(node) {
  const paperId = node && node.dataset ? node.dataset.paperId : "";
  if (!paperId) return;
  const page = Number((node.dataset && node.dataset.page) || 0);
  const opener = window.PW && window.PW.openPaper;
  if (typeof opener === "function") opener(paperId, page || 1, node.dataset.snippet || '');
  else toast("阅读器还没准备好，稍后再试", "error");
}

/* ---------- 知识库文件列表 ---------- */
const kbFiles = { items: [], query: "" };

function renderKbFiles() {
  const list = byId("kb-file-list");
  const count = byId("kb-file-count");
  if (!list) return;
  const query = kbFiles.query.trim().toLowerCase();
  const items = query
    ? kbFiles.items.filter((paper) =>
        `${paper.title || ""} ${paper.authors || ""} ${(paper.tags || []).join(" ")} ${paper.category || ""}`
          .toLowerCase()
          .includes(query),
      )
    : kbFiles.items;
  if (count) count.textContent = `${items.length} / ${kbFiles.items.length}`;
  if (!items.length) {
    list.innerHTML = `<div class="kb-empty">${
      kbFiles.items.length ? "没有匹配的文件" : "知识库还是空的，先去导入论文吧"
    }</div>`;
    return;
  }
  list.innerHTML = items
    .map((paper) => {
      const meta = [paper.year, paper.category].filter(Boolean).join(" · ");
      const tags = Array.isArray(paper.tags) ? paper.tags.slice(0, 3).join(" / ") : "";
      return `<button type="button" class="kb-file" data-id="${escapeHtml(paper.id)}">
        <span class="kb-file-title">${escapeHtml(paper.title || "未命名论文")}</span>
        <span class="kb-file-meta">${escapeHtml([meta, tags].filter(Boolean).join(" · ") || "无标签")}</span>
        <span class="kb-file-action">${paper.has_pdf ? "打开原文" : "查看摘要 / 补齐 PDF"} →</span>
      </button>`;
    })
    .join("");
}

async function loadKbFiles() {
  try {
    const items=[];
    let offset=0;
    do {
      const result=await api.get(`/papers?limit=200&offset=${offset}&sort=updated`);
      const page=Array.isArray(result.items)?result.items:[];
      items.push(...page); offset+=page.length;
      if (!page.length || offset >= (result.total || 0)) break;
    } while (offset < 100000);
    kbFiles.items=items;
    renderKbFiles();
  } catch (err) {
    const list = byId("kb-file-list");
    if (list) list.innerHTML = `<div class="kb-empty">文件列表加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function initKbFiles() {
  byId("kb-file-search")?.addEventListener("input", (event) => {
    kbFiles.query = event.target.value || "";
    renderKbFiles();
  });
  byId("kb-file-list")?.addEventListener("click", (event) => {
    const node = event.target.closest(".kb-file");
    const paperId = node && node.dataset ? node.dataset.id : "";
    if (!paperId) return;
    const opener = window.PW && window.PW.openPaper;
    if (typeof opener === "function") opener(paperId);
    else toast("阅读器还没准备好，稍后再试", "error");
  });
  document.addEventListener("pw:library-changed", loadKbFiles);
  loadKbFiles();
}

function fillChip(chip) {
  const input = byId("chat-input");
  if (!input) return;
  input.value = String(chip.textContent || "").trim();
  input.focus();
}

/* ---------- 初始化 ---------- */
export function initChat() {
  if (initialized) return;
  initialized = true;
  session.subscribe(onSessionChange);
  session.load().catch(err => toast(`读取全库对话失败：${err.message}`, 'error'));

  const input = byId("chat-input");
  if (input) {
    input.addEventListener("keydown", (event) => {
      if (event.key !== "Enter" || event.shiftKey || event.isComposing) return;
      event.preventDefault();
      sendMessage();
    });
  }

  byId("btn-chat-send")?.addEventListener("click", () => sendMessage());
  byId("btn-clear-chat")?.addEventListener("click", () => clearChat());

  const container = byId("chat-messages");
  if (container) {
    bindSessionControls(container,session);
    container.addEventListener("click", (event) => {
      if (event.target.closest("[data-clear-comparison]")) {if(session.busy)return;comparison={paper_ids:[],titles:[]}; renderConversation(); return;}
      handleAnswerAction(event,session.data?.messages||[]);
      const chip = event.target.closest(".chip");
      if (chip) {
        fillChip(chip);
        return;
      }
      const source = event.target.closest(".src-item");
      if (source) openSource(source);
    });
  }

  window.addEventListener("pw:chat-shown", () => {
    session.load(true).catch(err => toast(`读取全库对话失败：${err.message}`, 'error'));
    byId("chat-input")?.focus();
    scrollToBottom();
    loadKbFiles();
  });

  window.addEventListener('pw:compare-papers',event=>{
    if (session.busy) {toast('请先停止或等待当前回答，再更改比较范围','info');return;}
    const ids=Array.isArray(event.detail?.paper_ids)?event.detail.paper_ids:[];
    if (ids.length<2) {toast('请选择至少两篇论文进行比较','info');return;}
    comparison={paper_ids:[...new Set(ids)].slice(0,12),titles:event.detail.titles||ids};
    window.PW?.goto?.('chat');
    if (byId('chat-input')) byId('chat-input').value='请比较这些论文的研究问题、方法、主要结果与局限，并指出证据缺口。';
    renderConversation(); byId('chat-input')?.focus();
  });
  initKbFiles();
}
