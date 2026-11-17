/* Nexus Paper Library (NPL) —— PDF 阅读器模块
 *
 * 职责：
 *   1. PDF.js 懒渲染（先 3 页，IntersectionObserver 按需加载 / 释放显存）
 *   2. 自建 textLayer（绝对定位 span，透明文字，可选中）
 *   3. 划词工具条：高亮 / 翻译 / 解释 / 追问
 *   4. 右侧面板：AI 精读、与我相关、论文对话、批注、分类
 *
 * 对外导出：initReader(), openReader(paperId), closeReader()
 */

import * as pdfjsLib from "/vendor/pdfjs/pdf.mjs";
import { api, toast, md, emit, debounce, escapeHtml } from "./api.js";
import { getConversation } from './conversation.js';
import { renderSources } from './ai-feedback.js';
import { upsertThinkingPanel } from './thinking-panel.js';
import { webImagesHtml } from './web-images.js';
import { findTextMatches, findSourceMatch, saveReadingState, loadReadingState } from './reader-tools.js';
import {installReaderNavigation,renderPdfLinks} from './reader-navigation.js';
import {installAnnotationTools} from './reader-annotation-panel.js';
import {installOutlineNavigator} from './reader-outline-panel.js';
import {installReaderContext} from './reader-context.js';
import {installReaderRegions, cropRegion, copyRegionImage} from './reader-regions.js';
import {mountPaperNotes} from './paper-notes.js';

pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.mjs";

/* ==================== 常量 ==================== */

const RENDER_ROOT_MARGIN = "1000px 0px"; // 提前约一屏开始渲染
const RENDER_CONCURRENCY = 2;            // 同时最多渲染 2 页
const MAX_CANVAS_DPR = 2;                // 高 DPI 上限，避免超大 canvas
const MIN_ZOOM = 0.4;
const MAX_ZOOM = 3.5;
const HIGHLIGHT_COLOR = "#ffd666";
const SELECTION_TEXT_LIMIT = 4000;
const MAX_CHAT_IMAGES = 6;

const $ = (id) => document.getElementById(id);
const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

/* ==================== 状态 ==================== */

const state = {
  initialized: false,
  paperId: null,
  paper: null,

  // PDF
  doc: null,
  numPages: 0,
  currentPage: 1,
  zoom: 1,          // 1 = 适合宽度
  fitMode: "width",
  activeTab: "summary",
  restoring: false,
  fitScale: 1,
  firstSize: null,  // 第 1 页 scale=1 的 viewport 尺寸
  pageSizes: new Map(),   // pageNum -> {width,height}（scale=1）
  pages: new Map(),       // pageNum -> .pdf-page 容器
  pageProxies: new Map(), // pageNum -> PDFPageProxy

  // 渲染调度
  renderedPages: new Map(), // pageNum -> 渲染时的绝对 scale
  wanted: new Set(),        // 当前需要渲染的页（在视口附近）
  pinned: new Set(),        // 打开时强制渲染的前 3 页，不随滚动回收
  queued: new Set(),
  rendering: new Set(),
  renderQueue: [],
  activeRenders: 0,
  renderTasks: new Map(),   // pageNum -> RenderTask
  renderToken: 0,           // 换文档 / 缩放时递增，丢弃过期渲染结果
  renderObserver: null,
  currentObserver: null,

  // 批注与划词
  annotations: [],
  selection: null,
  floater: null,
  search: { query: "", matches: [], index: 0, busy: false, progress: 0 },
  chatAttachments: [],
  chatImages: [],
  chatUpload: null,
  chatSession: null,
  chatUnsubscribe: null,
  chatBranch: '',
  chatBranchPaper: '',
  chatBranches: [],
  chatQuote: '',
  readingSessionId: '',
  sidePaperId: '',
  sideAnnotations: [],
  sideScale: 1,
  coPapers: [],
  coTitles: {},
  compareOn: false,
  sideDoc: null,
  sideRenderToken: 0,
  openToken: 0,

  scrollTimer: null,
  resizeTimer: null,
  sidebarDragging: false,
  scannedWarned: false,
  chapters: [],
};
let navigation,annotationTools,outlineNavigator,readerContext,readerRegions;
let citationFocus = null;

function clearCitationFocus() {
  clearTimeout(citationFocus?.timer);
  citationFocus = null;
  $('pdf-pages')?.querySelectorAll('.reader-citation-layer').forEach(layer => layer.remove());
}

function paintCitationFocus(pageNum) {
  const focus = citationFocus;
  if (!focus || focus.page !== pageNum || focus.applied) return;
  const container = state.pages.get(pageNum);
  const spans = [...(container?.querySelectorAll('.textLayer span') || [])];
  if (!spans.length) return;
  const match = findSourceMatch(spans.map(span => ({str:span.textContent})), focus.snippet);
  if (!match) {
    clearCitationFocus();
    toast('已定位到引用页，未能精确匹配节选文字，可展开引用核对', 'info');
    return;
  }
  const layer = document.createElement('div');
  layer.className = 'reader-citation-layer';
  const bounds = container.getBoundingClientRect();
  for (const part of match.parts) {
    const node = spans[part.itemIndex]?.firstChild;
    if (!node) continue;
    const range = document.createRange();
    range.setStart(node, part.start);
    range.setEnd(node, part.end);
    for (const rect of range.getClientRects()) {
      const hit = document.createElement('div');
      hit.className = 'reader-citation-hit';
      Object.assign(hit.style, {left:`${rect.left-bounds.left}px`, top:`${rect.top-bounds.top}px`,
        width:`${rect.width}px`, height:`${rect.height}px`});
      layer.append(hit);
    }
  }
  container.append(layer);
  focus.applied = true;
  const first = layer.firstElementChild;
  if (first) {
    const scroll = $('pdf-scroll');
    scroll.scrollTop += first.getBoundingClientRect().top - scroll.getBoundingClientRect().top - scroll.clientHeight * .3;
  }
  clearTimeout(focus.timer);
  focus.timer = setTimeout(() => { if (citationFocus === focus) clearCitationFocus(); }, 2800);
}

function focusCitation(page, snippet) {
  clearCitationFocus();
  if (!snippet || page < 1 || page > state.numPages) return;
  const focus = {page, snippet, applied:false};
  citationFocus = focus;
  focus.timer = setTimeout(() => {
    if (citationFocus !== focus) return;
    clearCitationFocus();
    toast('已定位到引用页，暂时无法标记文字位置', 'info');
  }, 8000);
  gotoPage(page, {smooth:false});
  paintCitationFocus(page);
}

function eventTargetElement(event) {
  const target = event && event.target;
  if (!target) return null;
  if (target.nodeType === 1) return target;
  return target.parentElement || null;
}

function readerOwner() {
  const paperId = state.paperId, token = state.openToken;
  return () => state.paperId === paperId && state.openToken === token && !$("reader")?.classList.contains('hidden');
}
function readingPosition() {
  const scroll = $('pdf-scroll');
  let page = state.currentPage, offset = 0;
  if (scroll) for (const [number, node] of state.pages) {
    if (node.offsetTop <= scroll.scrollTop + 18) {
      page = number;
      offset = clamp((scroll.scrollTop - node.offsetTop + 18) / node.offsetHeight, 0, 1);
    } else break;
  }
  return {page, offset, zoom:state.zoom, fitMode:state.fitMode, tab:state.activeTab};
}
function rememberReading() {
  if (state.paperId && state.doc && !state.restoring) saveReadingState(localStorage, state.paperId, readingPosition());
}
function restorePosition(saved) {
  if (!state.doc || !saved) return;
  const page = clamp(Number(saved.page)||1, 1, state.numPages);
  gotoPage(page, {smooth:false,record:false});
  const node = state.pages.get(page);
  if (node) $('pdf-scroll').scrollTop = Math.max(0,node.offsetTop - 18 + clamp(Number(saved.offset)||0,0,1)*node.offsetHeight);
}

/* ==================== 通用小工具 ==================== */

function friendlyError(err) {
  const raw = String((err && err.message) || err || "未知错误");
  if (/未配置|not configured|api[\s_-]?key|LLM 未配置/i.test(raw)) {
    return "大模型还没配置：去「设置」页填 Base URL 和 API Key";
  }
  if (/501|尚未实现|not implemented|Not Implemented/i.test(raw)) {
    return "AI 模块还没接好，稍后再试";
  }
  return raw;
}

function pickAiText(result, depth = 0) {
  if (!result || depth > 3) return "";
  if (typeof result === "string") return result;
  if (typeof result !== "object") return "";
  for (const key of ["text", "translation", "explanation", "answer", "result", "content"]) {
    const value = result[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  if (result.data && typeof result.data === "object") return pickAiText(result.data, depth + 1);
  return "";
}

function setBusy(button, busy, label = "处理中…", dark = true) {
  if (!button) return;
  if (busy) {
    if (button.dataset.originalLabel === undefined) {
      button.dataset.originalLabel = button.textContent || "";
    }
    const spinner = document.createElement("span");
    spinner.className = dark ? "spinner dark" : "spinner";
    button.replaceChildren(spinner, document.createTextNode(label));
    button.disabled = true;
  } else {
    button.textContent = button.dataset.originalLabel || "确定";
    delete button.dataset.originalLabel;
    button.disabled = false;
  }
}

/* ==================== 面板：Tab 切换 ==================== */

function switchTab(name) {
  if (!name) return;
  state.activeTab = name;
  if (name === 'map') renderBranchMap();
  if (name === 'chat') updateReaderChatHeader();
  rememberReading();
  document.querySelectorAll("#reader-tabs .tab").forEach((tab) => {
    tab.classList.toggle("active", tab.dataset.tab === name);
    tab.setAttribute("aria-selected", String(tab.dataset.tab === name));
  });
  document.querySelectorAll("#reader .tab-pane").forEach((pane) => {
    pane.classList.toggle("active", pane.dataset.pane === name);
  });
  if (name === "chat") scrollMiniChatToBottom();
  if (name === 'paper-notes') $('reader-paper-notes')?.refreshPaperNotes?.();
}

/* ==================== 面板：精读 / 关联 ==================== */

function renderPaperPanels(paper) {
  if (!paper) return;
  mountPaperNotes($('reader-paper-notes'),paper.id);

  const summaryEl = $("reader-summary");
  if (summaryEl) {
    if (paper.summary) summaryEl.innerHTML = md(paper.summary);
    else summaryEl.textContent = "还没有精读结果。点上面的「AI 精读」开始阅读。";
  }

  const keypointsEl = $("reader-keypoints");
  if (keypointsEl) {
    keypointsEl.replaceChildren();
    const points = Array.isArray(paper.key_points) ? paper.key_points : [];
    for (const point of points) {
      const item = document.createElement("div");
      item.className = "kp-item";
      item.textContent = String(point);
      keypointsEl.append(item);
    }
  }

  const tagsEl = $("reader-tags");
  if (tagsEl) {
    tagsEl.replaceChildren();
    const tags = Array.isArray(paper.tags) ? paper.tags : [];
    for (const tag of tags) {
      const chip = document.createElement("span");
      chip.className = "pc-tag";
      chip.textContent = String(tag);
      tagsEl.append(chip);
    }
  }

  if (paper.relevance) {
    const relevanceEl = $("reader-relevance");
    if (relevanceEl) relevanceEl.innerHTML = md(paper.relevance);
  }
}

function mergePaperUpdate(result) {
  const incoming = result && (result.paper || result.data);
  if (incoming && typeof incoming === "object") {
    state.paper = { ...(state.paper || {}), ...incoming };
    return state.paper;
  }
  if (result && typeof result === "object") {
    const patch = {};
    for (const key of ["summary", "key_points", "tags", "category", "relevance", "status"]) {
      if (result[key] !== undefined) patch[key] = result[key];
    }
    state.paper = { ...(state.paper || {}), ...patch };
  }
  return state.paper;
}

async function runAnalyze() {
  if (!state.paperId) return;
  const paperId = state.paperId;
  const owns = readerOwner();
  const button = $("btn-analyze");
  setBusy(button, true, "精读中…", false);
  try {
    const result = await api.post(`/ai/summarize/${encodeURIComponent(paperId)}`);
    window.PW?.emit?.('library-changed');
    if (!owns()) return;
    mergePaperUpdate(result || {});
    renderPaperPanels(state.paper);
    loadCategories(state.paper);
    switchTab("summary");
    const fixedFields = Array.isArray(result?.metadata_updates) ? result.metadata_updates : [];
    toast(
      fixedFields.length ? `精读完成，已按原文校正${fixedFields.join("、")}` : "精读完成",
      "success",
      fixedFields.length ? 5200 : 3200,
    );
    if (state.paper && state.paper.category_status === "pending") {
      window.PW?.promptCategory?.(state.paperId);
    }
  } catch (err) {
    if (!owns()) return;
    toast(`精读失败：${friendlyError(err)}`, "error", 5200);
  } finally {
    if (owns()) setBusy(button, false);
  }
}

async function runRelevance() {
  if (!state.paperId) return;
  const paperId = state.paperId;
  const owns = readerOwner();
  const button = $("btn-relevance");
  const target = $("reader-relevance");
  setBusy(button, true, "分析中…", true);
  try {
    const result = await api.post(`/ai/relevance/${encodeURIComponent(paperId)}`);
    window.PW?.emit?.('library-changed');
    if (!owns()) return;
    const text = pickAiText(result) || (result && typeof result.relevance === "string" ? result.relevance : "");
    if (!text) throw new Error("没拿到关联分析结果");
    if (state.paper) state.paper.relevance = text;
    if (target) target.innerHTML = md(text);
    toast("关联分析完成", "success");
  } catch (err) {
    if (!owns()) return;
    toast(`关联分析失败：${friendlyError(err)}`, "error", 5200);
  } finally {
    if (owns()) setBusy(button, false);
  }
}

/* ==================== 面板：分类 ==================== */

async function loadCategories(paper) {
  const select = $("reader-category-select");
  if (!select) return;
  const owns = readerOwner();
  const current = String((paper && paper.category) || "");
  let names = [];
  try {
    const result = await api.get("/categories");
    const items = (result && result.categories) || [];
    names = items.map((item) => String(item.name || "")).filter(Boolean);
  } catch (err) {
    console.warn("[reader] 分类加载失败", err);
  }
  if (!owns()) return;
  if (current && !names.includes(current)) names = [current, ...names];
  select.replaceChildren();
  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "未分类";
  select.append(empty);
  for (const name of names) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = name;
    select.append(option);
  }
  select.value = current;
}

async function onCategoryChange() {
  const select = $("reader-category-select");
  if (!select || !state.paperId) return;
  const owns = readerOwner();
  const paperId = state.paperId;
  const next = select.value;
  const previous = String((state.paper && state.paper.category) || "");
  if (next === previous) return;
  select.disabled = true;
  try {
    const updated = await api.patch(`/papers/${encodeURIComponent(paperId)}`, { category: next });
    if (!owns()) return;
    if (state.paper) state.paper.category = (updated && updated.category) ?? next;
    toast(next ? `已归到「${next}」` : "已移出分类", "success");
    window.PW?.emit?.("library-changed");
  } catch (err) {
    if (!owns()) return;
    select.value = previous;
    toast(`分类保存失败：${friendlyError(err)}`, "error", 4200);
  } finally {
    if (owns()) select.disabled = false;
  }
}

/* ==================== 面板：论文对话 ==================== */

let readerChatView = null;
function chatIsAtBottom(wrap) {
  return wrap.scrollHeight - wrap.clientHeight - wrap.scrollTop < 48;
}
function updateChatBottomButton() {
  const wrap = $("reader-chat-messages"), button = $("reader-chat-bottom");
  if (wrap && button) button.hidden = chatIsAtBottom(wrap);
}
function scrollMiniChatToBottom() {
  const wrap = $("reader-chat-messages");
  if (wrap) wrap.scrollTop = wrap.scrollHeight;
  updateChatBottomButton();
}

function resetReaderChat() {
  readerChatView = null;
  const wrap = $("reader-chat-messages");
  if (!wrap) return;
  wrap.replaceChildren();
  const hint = document.createElement("div");
  hint.className = "mini-msg assistant";
  hint.textContent = "就这篇论文提问，AI 会检索正文回答。试试：";
  wrap.append(hint);
  const questions = ["这篇论文的核心贡献是什么？", "实验是怎么设计的？", "方法的局限性有哪些？"];
  for (const question of questions) {
    const chip = document.createElement("button");
    chip.type = "button";
    chip.className = "chip";
    chip.textContent = question;
    chip.addEventListener("click", () => {
      const input = $("reader-chat-input");
      if (input) {
        input.value = question;
        input.focus();
      }
    });
    wrap.append(chip);
  }
}

function appendChatMessage(role, text) {
  const wrap = $("reader-chat-messages");
  if (!wrap) return null;
  const bubble = document.createElement("div");
  bubble.className = `mini-msg ${role}`;
  if (role === "user") bubble.textContent = text;
  else bubble.innerHTML = md(text);
  wrap.append(bubble);
  return bubble;
}

function renderReaderConversation(session) {
  if (state.chatSession !== session) return;
  if (!session.scope.startsWith('reading:') && state.paperId !== session.scope.slice(6).split('~')[0]) return;
  const wrap = $('reader-chat-messages');
  if (!wrap) return;
  const messages = session.data?.messages || [];
  const follow = !readerChatView || readerChatView.session !== session || chatIsAtBottom(wrap);
  if (!messages.length && !session.pending) {
    if (readerChatView?.session !== session || readerChatView.entries.length || readerChatView.pending) resetReaderChat();
    readerChatView = {session, entries: [], pending: null};
  } else {
    if (!readerChatView || readerChatView.session !== session || (!readerChatView.entries.length && !readerChatView.pending)) {
      wrap.replaceChildren();
      readerChatView = {session, entries: [], pending: null};
    }
    const view = readerChatView;
    // Retain unchanged history, including expanded reasoning and feedback controls.
    let same = 0;
    while (same < messages.length && view.entries[same]?.signature === JSON.stringify(messages[same])) same++;
    if (same < view.entries.length || same < messages.length) {
      view.pending?.user.remove(); view.pending?.answer.remove(); view.pending = null;
      for (const entry of view.entries.splice(same)) entry.bubble.remove();
      for (const [offset, item] of messages.slice(same).entries()) {
        const messageIndex = same + offset;
        const bubble = appendChatMessage(item.role, item.role === 'user' ? (item.display_question || item.content) : item.content);
        bubble.dataset.msgIndex = String(messageIndex);
        if (item.role === 'assistant') {
          appendChatImages(bubble, item.web_images || []);
          appendChatSources(bubble, item.sources, item.retrieval_candidates);
          appendReasoning(bubble, item.reasoning, {thinkingSeconds: item.thinking_seconds || 0, trace: item.web_trace || []});
          appendAnswerActions(bubble, item, state.paperId);
        }
        view.entries.push({signature: JSON.stringify(item), bubble});
      }
    }
    // The final response may arrive just before pending is cleared by the session.
    const completed = messages.at(-1)?.role === 'assistant' && messages.at(-2)?.role === 'user'
      && (messages.at(-2).display_question || messages.at(-2).content) === session.pending
      && !session.progress;
    if (session.pending && !completed) {
      if (view.pending?.question !== session.pending) {
        view.pending?.user.remove(); view.pending?.answer.remove();
        const user = appendChatMessage('user', session.pending), answer = appendChatMessage('assistant', '');
        answer.setAttribute('role', 'status');
        const body = document.createElement('div'), phase = document.createElement('div');
        phase.className = 'reader-ai-stage'; answer.append(phase, body);
        view.pending = {question: session.pending, user, answer, body, phase};
      }
      const pending = view.pending, progress = session.progress || {};
      const text = progress.answer || progress.stage || '准备读取论文…';
      if (pending.text !== text) { pending.body.innerHTML = md(text); pending.text = text; }
      const phase = progress.answer ? progress.stage || '' : '';
      if (pending.phase.textContent !== phase) pending.phase.textContent = phase;
      pending.phase.hidden = !phase;
      const answered = Boolean(progress.answer);
      const thinkingKey = `${(progress.reasoning || '').length}|${answered}|${progress.thinking_seconds || 0}|${(progress.web_trace || []).length}`;
      if (pending.thinkingKey !== thinkingKey) {
        if (progress.reasoning) {
          appendReasoning(pending.answer, progress.reasoning, {
            state: answered ? 'done' : 'running',
            thinkingSeconds: progress.thinking_seconds || 0,
            elapsedSeconds: progress.elapsed_seconds || progress.thinking_seconds || 0,
            stage: answered ? '' : progress.stage || '',
            trace: progress.web_trace || [],
          });
        }
        pending.thinkingKey = thinkingKey;
      }
    } else if (view.pending) {
      view.pending.user.remove(); view.pending.answer.remove(); view.pending = null;
    }
  }
  if (follow) scrollMiniChatToBottom();
  else updateChatBottomButton();
  if ($('reader-chat-undo')) {
    $('reader-chat-undo').hidden=!session.data?.undo_available;
    $('reader-chat-undo').disabled=session.busy;
  }
  if ($('reader-chat-stop')) $('reader-chat-stop').hidden=!session.busy;
  if ($('reader-chat-retry')) $('reader-chat-retry').hidden=session.busy || !['failed','stopped'].includes(session.progress?.state);
  state.chatAttachments = session.data?.attachments || [];
  renderChatAttachments();
  if ($('reader-chat-send')) $('reader-chat-send').disabled = session.busy || !session.data;
  if ($('reader-chat-attach')) $('reader-chat-attach').disabled = session.busy || !session.data || !!state.chatUpload;
  readerContext?.refresh();
}

const QUICK_PROMPTS_KEY = 'pw.readerChat.quick';
const DEFAULT_QUICK_PROMPTS = [
  '用三句话总结这篇的核心方法',
  '这一页的实验条件是什么',
  '把这段翻译成中文',
  '这个结论的局限是什么',
];

function renderReaderBranches() {
  const select = $('reader-chat-branch');
  if (!select) return;
  const current = state.chatBranch || '';
  select.replaceChildren();
  if (!state.chatBranches.length) {
    const option = document.createElement('option');
    option.value = '';
    option.textContent = '主对话';
    select.append(option);
  }
  for (const item of state.chatBranches) {
    const option = document.createElement('option');
    option.value = item.branch || '';
    option.textContent = `${item.branch ? '↳' : '●'} ${item.title || '未命名'}（${item.messages}）`;
    select.append(option);
  }
  select.value = current;
  const remove = $('reader-chat-branch-delete');
  if (remove) remove.disabled = !current;
}

async function loadReaderBranches(paperId) {
  try {
    const data = state.readingSessionId
      ? await api.get(`/ai/reading/branches?scope=${encodeURIComponent(readingBaseScope())}`)
      : await api.get(`/ai/branches/${encodeURIComponent(paperId)}`);
    state.chatBranches = data.items || [];
  } catch { state.chatBranches = []; }
  renderReaderBranches();
  renderBranchMap();
  updateReaderChatHeader();
}

function switchReaderBranch(branch) {
  state.chatBranch = String(branch || '');
  if (state.paperId) activateReaderConversation(state.paperId);
  updateReaderChatHeader();
  renderBranchMap();
}

async function createReaderBranch(messageIndex, quote = '') {
  if (!state.paperId) return;
  if (state.chatSession?.busy) { toast('先等这一轮回答完再分支', 'info'); return; }
  try {
    const result = state.readingSessionId
      ? await api.post('/ai/reading/branches', {
          scope: readingBaseScope(),
          from_branch: state.chatBranch || '',
          upto_index: Number(messageIndex),
        })
      : await api.post('/ai/branches', {
          paper_id: state.paperId,
          from_branch: state.chatBranch || '',
          upto_index: Number(messageIndex),
        });
    await loadReaderBranches(state.paperId);
    switchReaderBranch(result.branch);
    switchTab('chat');
    if (quote) setChatQuote(quote);
    toast(quote ? '已建新分支，引用内容已作为上文带入' : '已从这条回答分出新分支', 'success', 2600);
  } catch (err) { toast(`分支失败：${friendlyError(err)}`, 'error'); }
}

async function deleteReaderBranch() {
  if (!state.paperId || !state.chatBranch) return;
  if (state.chatSession?.busy) { toast('先等这一轮回答完再删分支', 'info'); return; }
  if (!window.confirm('删除当前分支？这条分支里的对话会被移除，主对话不受影响。')) return;
  try {
    if (state.readingSessionId) {
      await api.del('/ai/reading/branches', { json: { scope: readingBaseScope(), branch: state.chatBranch } });
    } else {
      await api.del('/ai/branches', { json: { paper_id: state.paperId, branch: state.chatBranch } });
    }
    state.chatBranch = '';
    await loadReaderBranches(state.paperId);
    if (state.paperId) activateReaderConversation(state.paperId);
    toast('分支已删除', 'success', 1800);
  } catch (err) { toast(`删除失败：${friendlyError(err)}`, 'error'); }
}

function renderChatQuote() {
  const box = $('reader-chat-quote');
  if (!box) return;
  const text = state.chatQuote || '';
  box.replaceChildren();
  box.hidden = !text;
  if (!text) return;
  const label = document.createElement('div');
  label.className = 'reader-chat-quote-text';
  label.textContent = text.length > 200 ? `${text.slice(0, 200)}…` : text;
  const remove = document.createElement('button');
  remove.type = 'button';
  remove.className = 'btn tiny';
  remove.textContent = '移除引用';
  remove.addEventListener('click', () => { state.chatQuote = ''; renderChatQuote(); });
  box.append(label, remove);
}

function setChatQuote(text) {
  state.chatQuote = String(text || '').slice(0, 3000);
  renderChatQuote();
}

function insertQuoteIntoInput(text) {
  if (!text) return;
  setChatQuote(text);
  $('reader-chat-input')?.focus();
}

function selectionMessageIndex(selection) {
  if (!selection || !selection.rangeCount) return -1;
  const node = selection.anchorNode;
  const element = node?.nodeType === 1 ? node : node?.parentElement;
  const holder = element?.closest?.('[data-msg-index]');
  if (!holder) return -1;
  const value = Number(holder.dataset.msgIndex);
  return Number.isFinite(value) ? value : -1;
}

function initQuoteFollowUp() {
  let box = null;
  const hide = () => { box?.remove(); box = null; };
  const show = (text, x, y, messageIndex) => {
    hide();
    box = document.createElement('div');
    box.className = 'reader-quote-bar';
    const quote = document.createElement('button');
    quote.type = 'button';
    quote.className = 'reader-quote-btn';
    quote.textContent = '引用这一段提问';
    quote.addEventListener('mousedown', (event) => event.preventDefault());
    quote.addEventListener('click', () => { insertQuoteIntoInput(text); hide(); });
    box.append(quote);
    if (messageIndex >= 0) {
      const branch = document.createElement('button');
      branch.type = 'button';
      branch.className = 'reader-quote-btn reader-quote-branch';
      branch.textContent = '新建分支对其提问';
      branch.addEventListener('mousedown', (event) => event.preventDefault());
      branch.addEventListener('click', () => { hide(); createReaderBranch(messageIndex, text); });
      box.append(branch);
    }
    box.style.left = `${Math.max(8, Math.min(x, window.innerWidth - 300))}px`;
    box.style.top = `${Math.max(8, Math.min(y + 14, window.innerHeight - 44))}px`;
    box.removeAttribute('inert');
    ($('reader') || document.body).append(box);
  };
  document.addEventListener('mouseup', (event) => {
    // 点在弹条自身上松手时不能把弹条收掉，否则 click 落不到按钮上（真实鼠标点击会失效）
    if (event.target?.closest?.('.reader-quote-bar')) return;
    if (!event.target?.closest?.('#reader-chat-messages')) { hide(); return; }
    const { clientX, clientY } = event;
    setTimeout(() => {
      const selection = window.getSelection();
      const text = (selection?.toString() || '').trim();
      if (text.length < 2) { hide(); return; }
      // 优先用选区自身的位置（比鼠标坐标稳，滚动后也不会飘到屏幕外）
      let x = clientX, y = clientY;
      try {
        const rect = selection.getRangeAt(0).getBoundingClientRect();
        if (rect && rect.width + rect.height > 0) { x = rect.left; y = rect.bottom; }
      } catch { /* 用鼠标坐标兜底 */ }
      show(text, x, y, selectionMessageIndex(selection));
    }, 8);
  });
  document.addEventListener('mousedown', (event) => {
    if (box && !event.target.closest?.('.reader-quote-bar')) hide();
  });
  window.addEventListener('scroll', hide, true);
}

function renderQuickPrompts() {
  const box = $('reader-chat-quick');
  if (!box) return;
  let items = [];
  try { items = JSON.parse(localStorage.getItem(QUICK_PROMPTS_KEY) || '[]'); } catch { items = []; }
  if (!Array.isArray(items) || !items.length) items = DEFAULT_QUICK_PROMPTS.slice();
  box.replaceChildren();
  items.forEach((text, index) => {
    const chip = document.createElement('button');
    chip.type = 'button';
    chip.className = 'reader-quick-chip';
    chip.textContent = text;
    chip.title = '点击填入输入框；双击编辑这句话';
    chip.addEventListener('click', () => {
      const input = $('reader-chat-input');
      if (!input) return;
      input.value = input.value ? `${input.value}\n${text}` : text;
      input.focus();
      input.setSelectionRange(input.value.length, input.value.length);
    });
    chip.addEventListener('dblclick', (event) => {
      event.preventDefault();
      const next = window.prompt('编辑这句追问（留空删除）', text);
      if (next === null) return;
      const updated = items.slice();
      if (next.trim()) updated[index] = next.trim(); else updated.splice(index, 1);
      localStorage.setItem(QUICK_PROMPTS_KEY, JSON.stringify(updated));
      renderQuickPrompts();
    });
    box.append(chip);
  });
}

function newReadingSessionId() {
  return 'r' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
}

function readingBaseScope() {
  return state.readingSessionId ? `reading:${state.readingSessionId}` : (state.paperId ? `paper:${state.paperId}` : '');
}

function readerChatScope(branch = null) {
  const base = readingBaseScope();
  const value = branch === null ? (state.chatBranch || '') : String(branch || '');
  return value ? `${base}~${value}` : base;
}

function ensureCoPaper(paperId) {
  if (!paperId) return;
  if (!state.coPapers.includes(paperId)) {
    state.coPapers = [...new Set([paperId, ...state.coPapers])].slice(0, CO_MAX);
    saveCoSet();
  }
}

async function refreshArchiveHint(paperId) {
  const button = $("reader-archive-hint");
  if (!button || !paperId) return;
  if (!state.readingSessionId) { button.hidden = true; return; }
  try {
    const data = await api.get(`/ai/conversations/${encodeURIComponent(`paper:${paperId}`)}`);
    const count = (data.messages || []).length;
    const sessionCount = (state.chatSession?.data?.messages || []).length;
    button.hidden = !(count > 0 && sessionCount === 0);
    button.textContent = `继续上次对话（${count} 条）`;
    button.dataset.paper = paperId;
  } catch {
    button.hidden = true;
  }
}

function updateReaderChatHeader() {
  const head = $('reader-chat-head');
  const label = $('reader-chat-branch-label');
  if (!head) return;
  head.hidden = !state.chatBranch;
  if (label) {
    const current = state.chatBranches.find((row) => row.branch === state.chatBranch);
    label.textContent = current ? `分支：${current.title}（${current.messages} 条）` : `分支：${state.chatBranch}`;
  }
}

function plainMapText(value) {
  return String(value || '')
    .replace(/\s+/g, ' ')
    .replace(/^[>\u3000\s]+/, '')
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/[#*`_~]+/g, '')
    .trim();
}

function branchDepth(byBranch, branch) {
  let depth = 0;
  let current = branch || '';
  const seen = new Set();
  while (current) {
    if (seen.has(current)) break;
    seen.add(current);
    const row = byBranch.get(current);
    if (!row) break;
    current = row.parent_branch || '';
    depth += 1;
    if (depth > 12) break;
  }
  return depth;
}

const CARD_W = 252, CARD_H = 172, GAP_X = 92, GAP_Y = 24, PAD = 18;

function branchMapPosKey(paperId) {
  return `pw.branchMap.pos.${paperId}`;
}

function loadBranchPositions(paperId) {
  try {
    const raw = localStorage.getItem(branchMapPosKey(paperId));
    const data = raw ? JSON.parse(raw) : {};
    return data && typeof data === 'object' ? data : {};
  } catch { return {}; }
}

function saveBranchPositions(paperId, positions) {
  try { localStorage.setItem(branchMapPosKey(paperId), JSON.stringify(positions)); } catch { /* ignore */ }
}

function layoutBranchCards(rows) {
  const byBranch = new Map(rows.map((row) => [row.branch || '', row]));
  const columns = new Map();
  const cards = [];
  for (const row of rows) {
    const depth = branchDepth(byBranch, row.branch || '');
    if (!columns.has(depth)) columns.set(depth, 0);
    const index = columns.get(depth);
    columns.set(depth, index + 1);
    cards.push({ row, x: PAD + depth * (CARD_W + GAP_X), y: PAD + index * (CARD_H + GAP_Y) });
  }
  return cards;
}

function branchPaths(cards) {
  const byBranch = new Map(cards.map((card) => [card.row.branch || '', card]));
  return cards.map((card) => {
    const parent = byBranch.get(card.row.parent_branch || '');
    if (!parent) return '';
    const x1 = parent.x + CARD_W;
    const y1 = parent.y + CARD_H / 2;
    const x2 = card.x;
    const y2 = card.y + CARD_H / 2;
    const dx = Math.max(36, (x2 - x1) * 0.5);
    return `<path data-for="${escapeHtml(card.row.branch || '')}" d="M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}" />`;
  }).join('');
}

function renderBranchMap() {
  const box = $('reader-branch-map');
  if (!box) return;
  const rows = state.chatBranches.length
    ? state.chatBranches
    : [{ branch: '', title: '主对话', messages: 0, parent_branch: '', preview: '' }];
  const positions = loadBranchPositions(state.paperId || '');
  const cards = layoutBranchCards(rows).map((card) => {
    const saved = positions[card.row.branch || ''];
    if (saved && Number.isFinite(saved.x) && Number.isFinite(saved.y)) {
      return { ...card, x: saved.x, y: saved.y };
    }
    return card;
  });
  const width = Math.max(...cards.map((c) => c.x + CARD_W), CARD_W) + PAD;
  const height = Math.max(...cards.map((c) => c.y + CARD_H), CARD_H) + PAD;
  const cardsHtml = cards.map(({ row, x, y }) => {
    const branch = row.branch || '';
    const active = branch === (state.chatBranch || '');
    const preview = plainMapText(row.preview || row.last_question || '') || (branch ? '这条分支还没提问' : '还没有开始提问');
    const title = plainMapText(row.title || '') || '未命名';
    return `<div class="branch-node${active ? ' active' : ''}" data-branch="${escapeHtml(branch)}" style="left:${x}px;top:${y}px;width:${CARD_W}px;height:${CARD_H}px">
      <div class="bn-head"><span class="bn-dot"></span><span class="bn-title">${escapeHtml(title)}</span></div>
      <div class="bn-preview">${escapeHtml(preview)}</div>
      <div class="bn-meta">
        <span>${branch ? '分支' : '主对话'} · ${Number(row.messages || 0)} 条</span>
        ${branch ? '<button type="button" class="bn-delete" title="删除这条分支">删除</button>' : ''}
      </div>
    </div>`;
  }).join('');
  box.innerHTML = `<div class="branch-tools">
      <button type="button" class="btn tiny" data-map-action="zoom-out">−</button>
      <span class="branch-zoom-value">100%</span>
      <button type="button" class="btn tiny" data-map-action="zoom-in">＋</button>
      <button type="button" class="btn tiny" data-map-action="reset">重排</button>
    </div>
    <div class="branch-viewport" data-map-viewport>
      <div class="branch-scaler" data-map-scaler style="width:${width}px;height:${height}px">
        <div class="branch-canvas" style="width:${width}px;height:${height}px">
          <svg class="branch-links" width="${width}" height="${height}" viewBox="0 0 ${width} ${height}" aria-hidden="true">${branchPaths(cards)}</svg>
          ${cardsHtml}
        </div>
      </div>
    </div>`;
  setupBranchMapInteractions(box);
}

function setupBranchMapInteractions(box) {
  const viewport = box.querySelector('[data-map-viewport]');
  const scaler = box.querySelector('[data-map-scaler]');
  const canvas = box.querySelector('.branch-canvas');
  const svg = box.querySelector('.branch-links');
  if (!viewport || !scaler || !canvas || !svg) return;

  const paperId = state.paperId || '';
  const positions = loadBranchPositions(paperId);
  const nodes = [...canvas.querySelectorAll('.branch-node')];
  let zoom = state.branchMapZoom || 1;
  const applyZoom = (next) => {
    zoom = Math.max(0.45, Math.min(1.6, next));
    state.branchMapZoom = zoom;
    const baseW = parseFloat(scaler.dataset.baseWidth || '0') || canvas.offsetWidth;
    const baseH = parseFloat(scaler.dataset.baseHeight || '0') || canvas.offsetHeight;
    if (!scaler.dataset.baseWidth) {
      scaler.dataset.baseWidth = String(baseW);
      scaler.dataset.baseHeight = String(baseH);
      scaler.style.overflow = 'visible';
    }
    canvas.style.transformOrigin = '0 0';
    canvas.style.transform = `scale(${zoom})`;
    scaler.style.width = `${baseW * zoom}px`;
    scaler.style.height = `${baseH * zoom}px`;
    const label = box.querySelector('.branch-zoom-value');
    if (label) label.textContent = `${Math.round(zoom * 100)}%`;
  };
  applyZoom(zoom);

  box.querySelectorAll('[data-map-action]').forEach((button) => {
    button.addEventListener('click', () => {
      const action = button.dataset.mapAction;
      if (action === 'zoom-in') applyZoom(zoom * 1.15);
      else if (action === 'zoom-out') applyZoom(zoom / 1.15);
      else if (action === 'reset') {
        saveBranchPositions(paperId, {});
        renderBranchMap();
      }
    });
  });

  const redrawLinks = () => {
    const cards = nodes.map((node) => ({
      row: { branch: node.dataset.branch || '', parent_branch: (state.chatBranches.find((r) => r.branch === node.dataset.branch) || {}).parent_branch || '' },
      x: parseFloat(node.style.left) || 0,
      y: parseFloat(node.style.top) || 0,
    }));
    svg.innerHTML = branchPaths(cards);
  };

  // 空白处拖动 = 平移画布
  let panning = null;
  viewport.addEventListener('mousedown', (event) => {
    if (event.target.closest('.branch-node') || event.target.closest('.branch-tools')) return;
    panning = { x: event.clientX, y: event.clientY, left: viewport.scrollLeft, top: viewport.scrollTop };
    viewport.classList.add('panning');
    event.preventDefault();
  });
  window.addEventListener('mousemove', (event) => {
    if (!panning) return;
    viewport.scrollLeft = panning.left - (event.clientX - panning.x);
    viewport.scrollTop = panning.top - (event.clientY - panning.y);
  });
  window.addEventListener('mouseup', () => {
    if (!panning) return;
    panning = null;
    viewport.classList.remove('panning');
  });

  for (const node of nodes) {
    node.addEventListener('click', (event) => {
      if (event.target.closest('button')) return;
      if (node.dataset.dragged === '1') { node.dataset.dragged = '0'; return; }
      switchReaderBranch(node.dataset.branch || '');
      switchTab('chat');
    });
    node.querySelector('.bn-delete')?.addEventListener('click', (event) => {
      event.stopPropagation();
      state.chatBranch = node.dataset.branch || '';
      deleteReaderBranch();
    });
    node.addEventListener('mousedown', (event) => {
      if (event.target.closest('.bn-delete')) return;
      const startX = event.clientX, startY = event.clientY;
      const originX = parseFloat(node.style.left) || 0;
      const originY = parseFloat(node.style.top) || 0;
      let moved = false;
      event.preventDefault();
      const move = (ev) => {
        const dx = (ev.clientX - startX) / zoom;
        const dy = (ev.clientY - startY) / zoom;
        if (!moved && Math.abs(dx) + Math.abs(dy) < 3) return;
        moved = true;
        node.style.left = `${Math.max(0, originX + dx)}px`;
        node.style.top = `${Math.max(0, originY + dy)}px`;
        redrawLinks();
      };
      const up = () => {
        window.removeEventListener('mousemove', move);
        window.removeEventListener('mouseup', up);
        if (!moved) return;
        node.dataset.dragged = '1';
        positions[node.dataset.branch || ''] = { x: parseFloat(node.style.left) || 0, y: parseFloat(node.style.top) || 0 };
        saveBranchPositions(paperId, positions);
      };
      window.addEventListener('mousemove', move);
      window.addEventListener('mouseup', up);
    });
  }
}

const CO_SET_KEY = 'pw.coRead.set';
const CO_MAX = 4;

function saveCoSet() {
  try { localStorage.setItem(CO_SET_KEY, JSON.stringify(state.coPapers)); } catch { /* ignore */ }
}

function loadCoSet(paperId) {
  let stored = [];
  try { stored = JSON.parse(localStorage.getItem(CO_SET_KEY) || '[]'); } catch { stored = []; }
  const ids = Array.isArray(stored) ? stored.filter((id) => typeof id === 'string' && id) : [];
  state.coPapers = [...new Set([paperId, ...ids])].slice(0, CO_MAX);
  saveCoSet();
}

async function ensureCoTitles() {
  const missing = state.coPapers.filter((id) => !state.coTitles[id]);
  await Promise.all(missing.map(async (id) => {
    try {
      const paper = await api.get(`/papers/${encodeURIComponent(id)}`);
      state.coTitles[id] = paper?.title || '未命名论文';
    } catch { state.coTitles[id] = '论文'; }
  }));
}

async function renderCoBar() {
  await ensureCoTitles();
  const select = $('reader-co-select');
  if (select) {
    select.replaceChildren();
    for (const id of state.coPapers) {
      const option = document.createElement('option');
      option.value = id;
      option.textContent = state.coTitles[id] || id;
      select.append(option);
    }
    select.value = state.paperId || '';
    select.hidden = state.coPapers.length < 2;
  }
  const compare = $('btn-co-compare');
  if (compare) {
    compare.hidden = state.coPapers.length < 2;
    compare.classList.toggle('primary', state.compareOn);
    compare.textContent = state.compareOn ? '关闭对比' : '左右对比';
  }
  const add = $('btn-co-add');
  if (add) add.textContent = state.coPapers.length > 1 ? `多篇共读（${state.coPapers.length}）` : '多篇共读';
}

function disposeSideDoc() {
  const doc = state.sideDoc;
  state.sideDoc = null;
  state.sideRenderToken += 1;
  if (doc) { try { doc.destroy(); } catch { /* ignore */ } }
  const pane = $('pdf-scroll-side');
  if (pane) pane.hidden = true;
  const box = $('pdf-pages-side');
  if (box) box.replaceChildren();
}

async function renderSidePdf(paperId) {
  const pane = $('pdf-scroll-side');
  const box = $('pdf-pages-side');
  if (!pane || !box) return;
  const token = ++state.sideRenderToken;
  pane.hidden = false;
  box.textContent = '正在打开对比页…';
  try {
    const worker = new pdfjsLib.PDFWorker({ name: `paperwhale-side-${paperId}-${Date.now()}` });
    const task = pdfjsLib.getDocument({
      url: `/api/papers/${encodeURIComponent(paperId)}/pdf`,
      withCredentials: false,
      worker,
    });
    const doc = await task.promise;
    if (token !== state.sideRenderToken) {
      try { await doc.destroy(); } catch { /* ignore */ }
      return;
    }
    state.sideDoc = doc;
    state.sidePaperId = paperId;
    try {
      const data = await api.get(`/papers/${encodeURIComponent(paperId)}/annotations`);
      state.sideAnnotations = (data.items || []).map(normalizeAnnotation);
    } catch { state.sideAnnotations = []; }
    await renderSidePages(token);
  } catch (err) {
    if (token === state.sideRenderToken) box.textContent = `对比页打开失败：${friendlyError(err)}`;
  }
}

async function renderSidePages(token) {
  const pane = $('pdf-scroll-side');
  const box = $('pdf-pages-side');
  const doc = state.sideDoc;
  if (!pane || !box || !doc) return;
  box.replaceChildren();
  const baseWidth = Math.max(320, Math.floor((pane.clientWidth || 520) - 26));
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  for (let index = 1; index <= doc.numPages; index += 1) {
    if (token !== state.sideRenderToken) return;
    const page = await doc.getPage(index);
    const first = page.getViewport({ scale: 1 });
    const viewport = page.getViewport({ scale: (baseWidth / first.width) * (state.sideScale || 1) });
    const container = document.createElement('div');
    container.className = 'pdf-page';
    container.dataset.page = String(index);
    container.dataset.side = '1';
    container.dataset.paperId = state.sidePaperId;
    container.style.width = `${Math.floor(viewport.width)}px`;
    container.style.height = `${Math.floor(viewport.height)}px`;
    const canvas = document.createElement('canvas');
    canvas.width = Math.max(1, Math.floor(viewport.width * dpr));
    canvas.height = Math.max(1, Math.floor(viewport.height * dpr));
    canvas.style.width = `${Math.floor(viewport.width)}px`;
    canvas.style.height = `${Math.floor(viewport.height)}px`;
    await page.render({
      canvasContext: canvas.getContext('2d'),
      viewport,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
    }).promise;
    container.append(canvas);
    try {
      container.append(await buildTextLayer(page, viewport));
    } catch { /* 没有文本层也能看 */ }
    container.append(buildHighlightLayer(index, state.sideAnnotations));
    if (token !== state.sideRenderToken) return;
    box.append(container);
  }
}

function refreshSideHighlights(pageNum) {
  const box = $('pdf-pages-side');
  if (!box) return;
  const container = [...box.querySelectorAll('.pdf-page')].find((el) => Number(el.dataset.page) === Number(pageNum));
  if (!container) return;
  const next = buildHighlightLayer(pageNum, state.sideAnnotations);
  const old = container.querySelector('.hl-layer');
  if (old) old.replaceWith(next);
  else container.append(next);
}

async function toggleReaderCompare() {
  if (state.coPapers.length < 2) { toast('先加入至少两篇论文，再开左右对比', 'info'); return; }
  state.compareOn = !state.compareOn;
  if (!state.compareOn) { disposeSideDoc(); renderCoBar(); return; }
  const other = state.coPapers.find((id) => id !== state.paperId) || state.coPapers[0];
  await renderSidePdf(other);
  renderCoBar();
}

let coCandidates = [];
function renderCoList(query = '') {
  const box = $('co-list');
  if (!box) return;
  box.replaceChildren();
  const keyword = String(query || '').trim().toLowerCase();
  const rows = coCandidates.filter((paper) => {
    if (!keyword) return true;
    return [paper.title, paper.authors, paper.category, paper.year].join(' ').toLowerCase().includes(keyword);
  });
  for (const paper of rows) {
    const row = document.createElement('label');
    row.className = 'co-item';
    const check = document.createElement('input');
    check.type = 'checkbox';
    check.checked = state.coPapers.includes(paper.id);
    check.addEventListener('change', () => {
      if (check.checked) {
        if (state.coPapers.length >= CO_MAX) { check.checked = false; toast(`最多共读 ${CO_MAX} 篇`, 'warn'); return; }
        state.coPapers.push(paper.id);
        state.coTitles[paper.id] = paper.title || '未命名论文';
      } else {
        state.coPapers = state.coPapers.filter((id) => id !== paper.id);
        if (!state.coPapers.length && state.paperId) state.coPapers = [state.paperId];
      }
      renderCoList(query);
      const status = $('co-status');
      if (status) status.textContent = `已选 ${state.coPapers.length} / ${CO_MAX} 篇`;
    });
    const text = document.createElement('span');
    const title = document.createElement('span');
    title.className = 'ci-title';
    title.textContent = paper.title || '未命名论文';
    const meta = document.createElement('span');
    meta.className = 'ci-meta';
    meta.textContent = [paper.authors, paper.year, paper.category].filter(Boolean).join(' · ').slice(0, 90);
    text.append(title, document.createElement('br'), meta);
    row.append(check, text);
    box.append(row);
  }
  if (!rows.length) box.textContent = '没有匹配的论文';
}

async function openCoModal() {
  const modal = $('co-modal');
  if (!modal) return;
  modal.removeAttribute('inert');
  modal.classList.remove('hidden');
  const search = $('co-search');
  if (search) search.value = '';
  const list = $('co-list');
  if (list) list.textContent = '读取文献库…';
  try {
    const data = await api.get('/papers?limit=300&sort=updated');
    coCandidates = data.items || [];
  } catch (err) {
    toast(`读取文献库失败：${friendlyError(err)}`, 'error');
    coCandidates = [];
  }
  renderCoList('');
  const status = $('co-status');
  if (status) status.textContent = `已选 ${state.coPapers.length} / ${CO_MAX} 篇`;
}

async function confirmCoModal() {
  const modal = $('co-modal');
  modal?.classList.add('hidden');
  if (!state.coPapers.length && state.paperId) state.coPapers = [state.paperId];
  saveCoSet();
  await renderCoBar();
  if (state.compareOn) {
    const other = state.coPapers.find((id) => id !== state.paperId);
    if (other) await renderSidePdf(other); else { state.compareOn = false; disposeSideDoc(); }
  }
}

function bindReaderChatExtras() {
  $('reader-chat-back-main')?.addEventListener('click', () => { switchReaderBranch(''); switchTab('chat'); });
  $('reader-archive-hint')?.addEventListener('click', async () => {
    const paperId = state.paperId;
    if (!paperId) return;
    try {
      const result = await api.post(`/ai/conversations/${encodeURIComponent(readerChatScope(""))}/restore`, {
        paper_id: paperId,
      });
      const session = getConversation(readerChatScope(""));
      session.data = result.conversation;
      state.chatSession = session;
      renderReaderConversation(session);
      await refreshArchiveHint(paperId);
      toast("已恢复上次对话", "success", 2200);
    } catch (err) {
      toast(`恢复失败：${friendlyError(err)}`, "error", 3200);
    }
  });
  $('branch-map-close')?.addEventListener('click', () => switchTab('chat'));
  $('btn-co-add')?.addEventListener('click', openCoModal);
  $('co-modal-close')?.addEventListener('click', () => $('co-modal')?.classList.add('hidden'));
  $('co-confirm')?.addEventListener('click', confirmCoModal);
  $('co-clear')?.addEventListener('click', () => {
    state.coPapers = state.paperId ? [state.paperId] : [];
    renderCoList($('co-search')?.value || '');
  });
  $('co-search')?.addEventListener('input', debounce((event) => renderCoList(event.target.value), 180));
  $('btn-co-compare')?.addEventListener('click', toggleReaderCompare);
  $('reader-co-select')?.addEventListener('change', async (event) => {
    const id = String(event.target.value || '');
    if (!id || id === state.paperId) return;
    await openReader(id);
    if (state.compareOn) {
      const other = state.coPapers.find((paperId) => paperId !== state.paperId);
      if (other) await renderSidePdf(other); else { state.compareOn = false; disposeSideDoc(); }
    }
  });
  initQuoteFollowUp();
  renderChatQuote();
}

function activateReaderConversation(id) {
  state.chatUnsubscribe?.();
  state.chatBranchPaper = id;
  const session = getConversation(readerChatScope());
  state.chatSession = session;
  refreshArchiveHint(id).catch(() => {});
  state.chatUnsubscribe = session.subscribe(renderReaderConversation);
  renderReaderConversation(session);
  session.load(true).catch(err => {
    if (state.chatSession === session) toast(`读取论文对话失败：${friendlyError(err)}`, 'error');
  });
  loadReaderBranches(id);
}

function appendChatImages(bubble, images) {
  let html = '';
  try { html = webImagesHtml(images || []); } catch { html = ''; }
  if (html) {
    const holder = document.createElement("div");
    holder.innerHTML = html;
    bubble.append(...holder.childNodes);
  }
}

function appendChatSources(bubble, sources, candidates = []) {
  if (!bubble) return;
  const wrap=document.createElement('div');
  wrap.innerHTML=renderSources(sources||[],candidates||[]);
  wrap.addEventListener('click',async event=>{
    const button=event.target.closest('[data-paper-id]');
    if(!button)return;
    const id=button.dataset.paperId, page=Number(button.dataset.page)||undefined;
    if(button.dataset.hasPdf==='false' && window.PW?.openPaperDetail) {
      await closeReader(); window.PW.openPaperDetail(id); return;
    }
    await openReader(id, page, button.dataset.snippet || '');
  });
  if(wrap.childNodes.length)bubble.append(wrap);
}

function renderChatAttachments() {
  const box = $("reader-chat-attachments");
  if (!box) return;
  box.replaceChildren();
  state.chatAttachments.forEach((item, index) => {
    const chip = document.createElement("span");
    chip.className = "profile-file-chip";
    chip.textContent = `📄 ${item.name}`;
    const remove = document.createElement("button");
    remove.type = "button";
    remove.textContent = "✕";
    remove.title = "移除";
    remove.disabled = !!state.chatSession?.busy || !!state.chatUpload;
    remove.addEventListener("click", async () => {
      const session = state.chatSession;
      if (!session || session.busy || state.chatUpload) return;
      try {
        await session.materials(session.data.attachments.filter((_, i) => i !== index));
      } catch (err) { toast(`移除失败：${friendlyError(err)}`, 'error'); }
    });
    chip.append(remove);
    box.append(chip);
  });
  for (const image of state.chatImages) {
    const chip = document.createElement('span');
    chip.className = 'profile-file-chip reader-image-chip';
    const thumbnail = document.createElement('img');
    thumbnail.src = image.data_url;
    thumbnail.alt = `第 ${image.page} 页截图`;
    const label = document.createElement('span');
    label.textContent = thumbnail.alt;
    const remove = makeReaderButton('', '✕', () => {
      state.chatImages = state.chatImages.filter(item => item !== image);
      renderChatAttachments();
    });
    remove.disabled = !!state.chatSession?.busy;
    remove.setAttribute('aria-label', `移除${thumbnail.alt}`);
    chip.append(thumbnail, label, remove);
    box.append(chip);
  }
}

async function uploadReaderChatFiles(files) {
  const session = state.chatSession;
  if (!session?.data || session.busy || state.chatUpload) return;
  const epoch = session.epoch;
  const operation = {};
  state.chatUpload = operation;
  const owns = () => state.chatSession === session && state.chatUpload === operation && session.epoch === epoch;
  const button = $("reader-chat-attach");
  if (button) button.disabled = true;
  readerContext?.refresh();
  try {
    for (const file of Array.from(files || [])) {
      if (!owns()) break;
      if (session.data.attachments.length >= 8) {
        toast('本篇对话最多保存 8 个文件，请先移除不用的附件', 'info');
        break;
      }
      if (file.size > 25 * 1024 * 1024) {
        toast(`${file.name} 超过 25MB，跳过`, "error", 4200);
        continue;
      }
      try {
        const result = await api.uploadFile("/ai/profile/upload", file, "file");
        if (!owns()) break;
        await session.materials([...session.data.attachments, { name: result.name || file.name, text: String(result.text || '').slice(0, 30000) }]);
        if (owns()) toast(`已读取：${result.name || file.name}`, "success", 2600);
      } catch (err) {
        if (owns()) toast(`读取 ${file.name} 失败：${friendlyError(err)}`, "error", 4500);
      }
    }
  } finally {
    if (state.chatUpload === operation) {
      state.chatUpload = null;
      if (state.chatSession === session) renderReaderConversation(session);
    }
  }
}

function consumeReaderImages(images = []) {
  state.chatImages = state.chatImages.filter(image => !images.includes(image));
  renderChatAttachments();
}

async function postReaderQuestion(question, {images = [], action = null, page, material} = {}) {
  const session = state.chatSession;
  const imageLabel = images.length ? `【截图 ${images.length} 张 · ${images.map(image => `第 ${image.page} 页`).join('、')}】\n` : '';
  const displayQuestion = (imageLabel + (material?.displayQuestion || question)).slice(0, 16000);
  const epoch = session.epoch;
  const owns = readerOwner();
  const quote = state.chatQuote || '';
  const quoteLabel = quote ? `> ${quote.slice(0, 600)}\n\n` : '';
  const displayWithQuote = (quoteLabel + displayQuestion).slice(0, 16000);
  await session.post("/ai/chat", {
    question: material?.question || question,
    display_question: displayWithQuote,
    quoted_text: quote,
    retrieval_question: question.slice(0, 16000),
    paper_id: state.paperId,
    deep_thinking: true,
    search_web: !action,
    page: page || material?.page || state.currentPage,
    selection: material?.selection || '',
    images,
    image_action: action,
    branch: state.chatBranch || '',
    reading_session: state.readingSessionId,
    co_paper_ids: state.coPapers.filter((id) => id !== state.paperId),
  }, displayQuestion);
  if (epoch === session.epoch) {
    if (session.lastRequest?.payload.images === images) session.lastRequest = null;
    if (owns()) consumeReaderImages(images);
    if (quote) setChatQuote('');
    if (state.paperId) loadReaderBranches(state.paperId);
  }
}

async function sendReaderChat() {
  const input = $("reader-chat-input");
  const draft = input.value;
  const images = [...state.chatImages];
  const question = draft.trim() || (images.length ? '请结合本篇论文解释这些截图。' : '');
  const session = state.chatSession;
  if (!question || !session?.data || session.busy || state.chatUpload || readerContext?.busy()) return;
  const owns = readerOwner();
  const epoch = session.epoch;
  const material = readerContext?.capture(question);
  input.value = "";
  try {
    await postReaderQuestion(question, {images, material});
  } catch (err) {
    if (!owns() || epoch !== session.epoch) return;
    if (!input.value) input.value = draft;
    toast(`论文对话失败：${friendlyError(err)}`, "error", 5200);
  }
}

async function handleReaderRegion(action, {source, pageNum, rect}) {
  if (action === 'highlight') {
    await createHighlight({pageNum, rects: [rect], text: '区域高亮'});
    return;
  }
  const owns = readerOwner();
  try {
    if (action === 'copy') {
      await copyRegionImage(cropRegion(source, rect));
      if (owns()) toast('已复制区域图像', 'success');
      return;
    }
    const session = state.chatSession;
    if (!session?.data || session.busy || state.chatUpload || readerContext?.busy()) {
      toast('请等待论文对话就绪后再试', 'info');
      return;
    }
    if (action === 'attach' && state.chatImages.length >= MAX_CHAT_IMAGES) {
      toast(`一轮最多发送 ${MAX_CHAT_IMAGES} 张截图，请先移除或发送已有截图`, 'info');
      return;
    }
    const data_url = cropRegion(source, rect, 1800).toDataURL('image/jpeg', 0.9);
    if (data_url.length > 2_000_000) throw new Error('截图过大，请缩小选区后重试');
    const image = {page: pageNum, data_url};
    if ($('reader').classList.contains('reader-sidebar-collapsed')) $('reader-sidebar-toggle').click();
    switchTab('chat');
    if (action === 'attach') {
      state.chatImages.push(image);
      renderChatAttachments();
      $('reader-chat-input').focus({preventScroll: true});
      return;
    }
    await postReaderQuestion(action === 'translate' ? '请学术翻译这张截图。' : '请结合本篇论文解释这张截图。',
      {images: [image], action, page: pageNum});
  } catch (err) {
    if (owns()) toast(`区域操作失败：${friendlyError(err)}`, 'error', 5200);
  }
}

/* ==================== 批注 ==================== */

function normalizeAnnotation(annotation) {
  const rects = Array.isArray(annotation && annotation.rects) ? annotation.rects : [];
  return { ...(annotation || {}), page: Number((annotation && annotation.page) || 1), rects };
}

function renderAnnotationList() {
  const wrap = $("reader-annotations");
  if (!wrap) return;
  annotationTools?.refresh();
  readerContext?.refresh();
  const editor=wrap.querySelector('.reader-note-editor');
  const editingId=editor?.closest('.ann-item')?.dataset.annotationId;
  const focused=editor?.contains(document.activeElement)?document.activeElement:null;
  const preserveEditor=()=>{
    if(!editor || (editingId&&!state.annotations.some(item=>String(item.id)===editingId)))return;
    const item=editingId?wrap.querySelector(`[data-annotation-id="${CSS.escape(editingId)}"]`):null;
    (item||wrap).prepend(editor);focused?.focus({preventScroll:true});
  };
  wrap.replaceChildren();
  if (state.annotations.length === 0) {
    const empty = document.createElement("div");
    empty.className = "muted small";
    empty.textContent = "还没有批注。选中正文，点「高亮」就能存一条。";
    wrap.append(empty);
    preserveEditor();
    return;
  }
  const sorted = annotationTools?.items() || state.annotations
    .slice()
    .sort((a, b) => Number(a.page || 0) - Number(b.page || 0) || Number(a.id || 0) - Number(b.id || 0));
  if(!sorted.length){const empty=document.createElement('p');empty.className='muted small';empty.textContent='当前筛选没有批注，可点上方“重置筛选”。';wrap.append(empty);}
  for (const annotation of sorted) {
    const item = document.createElement("div");
    item.className = "ann-item";
    item.dataset.page = String(annotation.page || 1);
    item.dataset.annotationId = String(annotation.id);
    if (/^#[0-9a-f]{6}$/i.test(String(annotation.color || ""))) {
      item.style.borderLeftColor = annotation.color;
    }

    const meta = document.createElement("div");
    meta.className = "ann-meta";
    const pageLabel = document.createElement("span");
    pageLabel.textContent = `第 ${annotation.page || 1} 页`;
    const actions = document.createElement("span");
    const editButton = document.createElement("button");
    editButton.type = "button";
    editButton.className = "icon-btn";
    editButton.textContent = "备注";
    editButton.dataset.editId = String(annotation.id);
    editButton.title = "编辑这条批注的备注";
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "icon-btn";
    deleteButton.textContent = "删除";
    deleteButton.dataset.annId = String(annotation.id);
    deleteButton.title = "删除这条批注";
    actions.append(editButton, deleteButton);
    meta.append(pageLabel, actions);

    const quote = document.createElement("div");
    quote.className = "ann-quote";
    quote.textContent = annotation.quote || "（无引文）";

    item.append(meta, quote);
    if (annotation.note) {
      const note = document.createElement("div");
      note.className = "ann-note";
      note.textContent = annotation.note;
      item.append(note);
    }
    wrap.append(item);
  }
  preserveEditor();
  readerContext?.refresh();
}

async function loadAnnotations(paperId) {
  const owns = readerOwner();
  try {
    const result = await api.get(`/papers/${encodeURIComponent(paperId)}/annotations`);
    if (!owns()) return;
    const items = (result && result.items) || [];
    state.annotations = items.map(normalizeAnnotation);
    renderAnnotationList();
    for (const pageNum of Array.from(state.renderedPages.keys())) refreshHighlights(pageNum);
  } catch (err) {
    console.warn("[reader] 批注加载失败", err);
  }
}

async function deleteAnnotation(annotationId) {
  if (!annotationId || !state.paperId) return;
  const owns = readerOwner();
  const target = state.annotations.find((item) => Number(item.id) === Number(annotationId));
  try {
    await api.del(`/annotations/${encodeURIComponent(annotationId)}`);
    if (!owns()) return;
    state.annotations = state.annotations.filter((item) => Number(item.id) !== Number(annotationId));
    renderAnnotationList();
    if (target) refreshHighlights(target.page);
    toast("批注已删除", "success");
    window.PW?.refreshStats?.();
  } catch (err) {
    if (!owns()) return;
    toast(`删除失败：${friendlyError(err)}`, "error", 4200);
  }
}

/* ==================== 高亮渲染 ==================== */

function buildHighlightLayer(pageNum, annotationList = state.annotations) {
  const layer = document.createElement("div");
  layer.className = "hl-layer";
  for (const annotation of annotationList || []) {
    if (Number(annotation.page) !== Number(pageNum)) continue;
    const rects = Array.isArray(annotation.rects) ? annotation.rects : [];
    for (const rect of rects) {
      if (!rect || typeof rect !== "object") continue;
      const x = Number(rect.x);
      const y = Number(rect.y);
      const w = Number(rect.w);
      const h = Number(rect.h);
      if (![x, y, w, h].every(Number.isFinite) || w <= 0 || h <= 0) continue;
      const node = document.createElement("div");
      const kind = String(annotation.kind || "highlight");
      node.className = "hl-rect" + (kind === "note" ? " note" : "") + (kind === "underline" ? " underline" : "") + (kind === "strikeout" ? " strikeout" : "");
      node.style.left = `${(clamp(x, 0, 1) * 100).toFixed(3)}%`;
      node.style.top = `${(clamp(y, 0, 1) * 100).toFixed(3)}%`;
      node.style.width = `${(clamp(w, 0, 1) * 100).toFixed(3)}%`;
      node.style.height = `${(clamp(h, 0, 1) * 100).toFixed(3)}%`;
      if (annotation.quote) node.title = String(annotation.quote).slice(0, 300);
      const color = String(annotation.color || "").toLowerCase();
      if (/^#[0-9a-f]{6}$/.test(color)) {
        if (kind === "underline") node.style.borderBottomColor = color;
        else if (kind === "strikeout") node.style.borderTopColor = color;
        else if (color !== HIGHLIGHT_COLOR) node.style.background = `${color}66`;
      }
      layer.append(node);
    }
  }
  return layer;
}

function refreshHighlights(pageNum) {
  const container = state.pages.get(Number(pageNum));
  if (!container || !state.renderedPages.has(Number(pageNum))) return;
  const nextLayer = buildHighlightLayer(pageNum);
  const oldLayer = container.querySelector(".hl-layer");
  if (oldLayer) {
    oldLayer.replaceWith(nextLayer);
    return;
  }
  const canvas = container.querySelector("canvas");
  if (canvas) canvas.after(nextLayer);
  else container.prepend(nextLayer);
}

/* ==================== PDF：尺寸与缩放 ==================== */

function currentScale() {
  return (state.fitScale || 1) * (state.zoom || 1);
}

function computeFitScale() {
  const scrollEl = $("pdf-scroll");
  const base = state.firstSize;
  if (!scrollEl || !base || !base.width) return 1;
  const available = Math.max(240, scrollEl.clientWidth - 32);
  if (state.fitMode === "actual") return 1;
  const width = available / base.width;
  const height = Math.max(120, scrollEl.clientHeight - 36) / base.height;
  return clamp(state.fitMode === "page" ? Math.min(width,height) : width, 0.2, 3);
}

function buildPlaceholders() {
  const pagesEl = $("pdf-pages");
  if (!pagesEl) return;
  pagesEl.replaceChildren();
  state.pages.clear();
  const fragment = document.createDocumentFragment();
  const scale = currentScale();
  for (let pageNum = 1; pageNum <= state.numPages; pageNum += 1) {
    const container = document.createElement("div");
    container.className = "pdf-page";
    container.dataset.page = String(pageNum);
    const size = state.pageSizes.get(pageNum) || state.firstSize;
    if (size) {
      container.style.width = `${Math.round(size.width * scale)}px`;
      container.style.height = `${Math.round(size.height * scale)}px`;
    }
    fragment.append(container);
    state.pages.set(pageNum, container);
  }
  pagesEl.append(fragment);
}

function discardRendered(pageNum) {
  const container = state.pages.get(pageNum);
  if (!container) return;
  const task = state.renderTasks.get(pageNum);
  if (task) {
    try {
      task.cancel();
    } catch {
      /* 忽略 */
    }
    state.renderTasks.delete(pageNum);
  }
  const canvas = container.querySelector("canvas");
  if (canvas) {
    canvas.width = 0;
    canvas.height = 0;
  }
  if (container.childElementCount > 0) container.replaceChildren();
  state.renderedPages.delete(pageNum);
}

function relayoutPages() {
  const scale = currentScale();
  for (const [pageNum, container] of state.pages) {
    discardRendered(pageNum);
    const size = state.pageSizes.get(pageNum) || state.firstSize;
    if (size) {
      container.style.width = `${size.width * scale}px`;
      container.style.height = `${size.height * scale}px`;
    }
  }
  // 缩放后视口附近的页必然要重绘；即便 IntersectionObserver 暂时把 wanted 清空，
  // 也至少把当前页与相邻页加回来，避免出现「缩放后白屏」。
  const anchor = Math.max(1, state.currentPage || 1);
  for (let offset = -1; offset <= 1; offset += 1) {
    const pageNum = anchor + offset;
    if (pageNum >= 1 && pageNum <= state.numPages) state.wanted.add(pageNum);
  }
  for (const pageNum of Array.from(state.wanted)) queueRender(pageNum);
}

function zoomBy(factor) {
  if (!state.doc) return;
  const next = clamp(state.zoom * factor, MIN_ZOOM, MAX_ZOOM);
  if (Math.abs(next - state.zoom) < 0.001) return;
  const position = readingPosition();
  state.zoom = next;
  relayoutPages();
  restorePosition(position);
  rememberReading();
}

/* ==================== PDF：渲染调度 ==================== */

function pumpQueue() {
  while (state.activeRenders < RENDER_CONCURRENCY && state.renderQueue.length > 0) {
    const pageNum = state.renderQueue.shift();
    state.queued.delete(pageNum);
    if (!state.doc || !state.wanted.has(pageNum)) continue;
    if (state.rendering.has(pageNum)) continue;
    if (state.renderedPages.get(pageNum) === currentScale()) continue;
    state.activeRenders += 1;
    const doc = state.doc;
    renderPage(pageNum)
      .catch((err) => console.warn("[reader] 渲染调度异常", err))
      .finally(() => {
        if (state.doc !== doc) return;
        state.activeRenders = Math.max(0, state.activeRenders - 1);
        pumpQueue();
      });
  }
}

function queueRender(pageNum) {
  if (!state.doc || !state.pages.has(pageNum)) return;
  if (!state.wanted.has(pageNum)) return;
  if (state.renderedPages.get(pageNum) === currentScale()) return;
  if (state.rendering.has(pageNum) || state.queued.has(pageNum)) return;
  state.queued.add(pageNum);
  state.renderQueue.push(pageNum);
  pumpQueue();
}

function removeFromQueue(pageNum) {
  const index = state.renderQueue.indexOf(pageNum);
  if (index >= 0) state.renderQueue.splice(index, 1);
  state.queued.delete(pageNum);
}

function unloadPage(pageNum) {
  if (state.pinned.has(pageNum)) return; // 首屏 3 页常驻，避免刚渲染就被回收
  state.wanted.delete(pageNum);
  removeFromQueue(pageNum);
  discardRendered(pageNum);
}

async function renderPage(pageNum) {
  const container = state.pages.get(pageNum);
  const doc = state.doc;
  if (!container || !doc) return;
  if (state.rendering.has(pageNum)) return;
  const scale = currentScale();
  if (state.renderedPages.get(pageNum) === scale) return;

  const token = state.renderToken;
  const docRef = doc;
  state.rendering.add(pageNum);

  try {
    const pdfPage = await docRef.getPage(pageNum);
    if (token !== state.renderToken || state.doc !== docRef || currentScale() !== scale) return;
    state.pageProxies.set(pageNum, pdfPage);

    if (!state.pageSizes.has(pageNum)) {
      state.pageSizes.set(pageNum, pdfPage.getViewport({ scale: 1 }));
    }
    const viewport = pdfPage.getViewport({ scale });
    container.style.width = `${viewport.width}px`;
    container.style.height = `${viewport.height}px`;

    const dpr = Math.min(window.devicePixelRatio || 1, MAX_CANVAS_DPR);
    const canvas = document.createElement("canvas");
    canvas.width = Math.max(1, Math.floor(viewport.width * dpr));
    canvas.height = Math.max(1, Math.floor(viewport.height * dpr));
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    const context = canvas.getContext("2d");
    if (!context) throw new Error("浏览器拿不到 canvas 2d 上下文");

    const task = pdfPage.render({
      canvasContext: context,
      viewport,
      transform: dpr !== 1 ? [dpr, 0, 0, dpr, 0, 0] : null,
    });
    state.renderTasks.set(pageNum, task);
    try {
      await task.promise;
    } finally {
      if (state.renderTasks.get(pageNum) === task) state.renderTasks.delete(pageNum);
    }
    if (token !== state.renderToken || state.doc !== docRef || currentScale() !== scale) return;

    container.replaceChildren(canvas, buildHighlightLayer(pageNum));
    state.renderedPages.set(pageNum, scale);

    let textLayer = null;
    try {
      textLayer = await buildTextLayer(pdfPage, viewport);
    } catch (err) {
      console.warn(`[reader] 第 ${pageNum} 页文本层构建失败`, err);
    }
    if (token !== state.renderToken || state.doc !== docRef || currentScale() !== scale) return;
    if (textLayer && container.contains(canvas)) {
      container.append(textLayer);
      paintSearchPage(pageNum);
      if (state.search.matches[state.search.index]?.page === pageNum) focusSearchHit(false);
      paintCitationFocus(pageNum);
      const charCount = Number(textLayer.dataset.chars || 0);
      if (pageNum === 1 && !state.scannedWarned && charCount < 10) {
        state.scannedWarned = true;
        toast("这份 PDF 像扫描件：正文选不中，只能看图和用 AI 精读", "info", 6200);
      }
    }
    if(token===state.renderToken&&state.doc===docRef)renderPdfLinks({pdfPage,viewport,container,doc:docRef,
      isCurrent:()=>token===state.renderToken&&state.doc===docRef&&currentScale()===scale&&container.contains(canvas)&&container.isConnected,
      navigate:gotoPage,onError:error=>toast(friendlyError(error),'error')});
  } catch (err) {
    if (err && err.name === "RenderingCancelledException") return;
    console.warn(`[reader] 第 ${pageNum} 页渲染失败`, err);
    if (token === state.renderToken && state.doc === docRef) {
      toast(`第 ${pageNum} 页渲染失败：${(err && err.message) || err}`, "error", 4600);
    }
  } finally {
    if (state.doc === docRef) state.rendering.delete(pageNum);
    // 渲染期间若缩放了 / 被取消，按需重新排队
    if (state.doc === docRef && state.wanted.has(pageNum) && state.renderedPages.get(pageNum) !== currentScale()) {
      queueRender(pageNum);
    }
  }
}

/* ==================== PDF：自建 textLayer ==================== */

let measureContext = null;
const ascentRatioCache = new Map();

function getMeasureContext() {
  if (!measureContext) {
    const canvas = document.createElement("canvas");
    canvas.width = 8;
    canvas.height = 8;
    measureContext = canvas.getContext("2d");
  }
  return measureContext;
}

function fontAscentRatio(fontFamily) {
  const key = String(fontFamily || "");
  const cached = ascentRatioCache.get(key);
  if (cached !== undefined) return cached;
  let ratio = 0.8;
  try {
    const context = getMeasureContext();
    context.font = `100px ${key || "sans-serif"}`;
    const metrics = context.measureText("Hg");
    const ascent = metrics.fontBoundingBoxAscent;
    const descent = Math.abs(metrics.fontBoundingBoxDescent);
    if (ascent && ascent + descent > 0) ratio = ascent / (ascent + descent);
  } catch {
    /* 用默认值 */
  }
  ascentRatioCache.set(key, ratio);
  return ratio;
}

/** 参考 pdf.js TextLayer 的定位算法，按视口像素绝对定位 span。 */
async function buildTextLayer(pdfPage, viewport) {
  const textContent = await pdfPage.getTextContent();
  const styles = textContent.styles || {};
  const layer = document.createElement("div");
  layer.className = "textLayer";
  const measure = getMeasureContext();
  const scale = viewport.scale;
  let charCount = 0;

  for (const [itemIndex,item] of textContent.items.entries()) {
    const text = typeof item.str === "string" ? item.str : "";
    if (!text) continue;
    charCount += text.trim().length;

    const style = styles[item.fontName] || {};
    const fontFamily = style.fontFamily || "sans-serif";
    const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
    let angle = Math.atan2(tx[1], tx[0]);
    if (style.vertical) angle += Math.PI / 2;

    const fontHeight = Math.hypot(tx[2], tx[3]);
    if (!Number.isFinite(fontHeight) || fontHeight <= 0) continue;
    const ascent = fontHeight * fontAscentRatio(fontFamily);
    const left = angle === 0 ? tx[4] : tx[4] + ascent * Math.sin(angle);
    const top = angle === 0 ? tx[5] - ascent : tx[5] - ascent * Math.cos(angle);

    const span = document.createElement("span");
    span.textContent = text;
    span.dataset.itemIndex = String(itemIndex);
    span.style.left = `${left.toFixed(2)}px`;
    span.style.top = `${top.toFixed(2)}px`;
    span.style.fontSize = `${fontHeight.toFixed(2)}px`;
    span.style.fontFamily = fontFamily;
    if (item.dir) span.dir = item.dir;

    const transforms = [];
    const measuredWidth = style.vertical ? Number(item.height) || 0 : Number(item.width) || 0;
    if (measuredWidth > 0 && text.trim()) {
      try {
        measure.font = `${fontHeight.toFixed(2)}px ${fontFamily}`;
        const width = measure.measureText(text).width;
        if (width > 0) transforms.push(`scaleX(${((measuredWidth * scale) / width).toFixed(4)})`);
      } catch {
        /* 量不出来就不缩放 */
      }
    }
    if (angle !== 0) transforms.push(`rotate(${((angle * 180) / Math.PI).toFixed(3)}deg)`);
    if (transforms.length > 0) span.style.transform = transforms.join(" ");

    layer.append(span);
  }

  layer.dataset.chars = String(charCount);
  return layer;
}

/* ==================== PDF：滚动 / 翻页同步 ==================== */

function setCurrentPage(pageNum) {
  const total = Math.max(1, state.numPages);
  const page = clamp(Math.round(Number(pageNum) || 1), 1, total);
  state.currentPage = page;
  updatePageIndicator();
  return page;
}

function updatePageIndicator() {
  const input = $("reader-page-input");
  if (input) {
    if (document.activeElement !== input) input.value = String(state.currentPage);
    input.max = String(Math.max(1, state.numPages));
  }
  const total = $("reader-page-total");
  if (total && state.numPages > 0) total.textContent = String(state.numPages);
  const prev = $("reader-prev");
  if (prev) prev.disabled = state.currentPage <= 1;
  const next = $("reader-next");
  if (next) next.disabled = state.currentPage >= Math.max(1, state.numPages);
  readerContext?.refresh();
}

function gotoPage(pageNum, { smooth = true, record = true, offset = 0 } = {}) {
  const origin=readingPosition();
  const page = setCurrentPage(pageNum);
  const container = state.pages.get(page);
  const scrollEl = $("pdf-scroll");
  if (!container || !scrollEl) return;
  if(record&&!state.restoring)navigation?.record(origin,{page,offset});
  if (!state.wanted.has(page)) {
    state.wanted.add(page);
    queueRender(page);
  }
  const top = Math.max(0, container.offsetTop - 18 + clamp(offset,0,1)*container.offsetHeight);
  if (typeof scrollEl.scrollTo === "function") {
    scrollEl.scrollTo({ top, behavior: smooth ? "smooth" : "auto" });
  } else {
    scrollEl.scrollTop = top;
  }
}

function probeCurrentPage() {
  if (!state.doc || state.pages.size === 0) return;
  const scrollEl = $("pdf-scroll");
  if (!scrollEl) return;
  const bounds = scrollEl.getBoundingClientRect();
  const probe = bounds.top + bounds.height * 0.35;
  for (const [pageNum, container] of state.pages) {
    const rect = container.getBoundingClientRect();
    if (rect.top <= probe && rect.bottom > probe) {
      setCurrentPage(pageNum);
      return;
    }
  }
}

/* ==================== PDF：加载与释放 ==================== */

function setupObservers() {
  teardownObservers();
  const root = $("pdf-scroll");
  if (!root || typeof IntersectionObserver === "undefined") return;

  state.renderObserver = new IntersectionObserver(
    (entries) => {
      for (const entry of entries) {
        const pageNum = Number(entry.target.dataset.page);
        if (!pageNum) continue;
        if (entry.isIntersecting) {
          state.wanted.add(pageNum);
          queueRender(pageNum);
        } else {
          unloadPage(pageNum);
        }
      }
    },
    { root, rootMargin: RENDER_ROOT_MARGIN, threshold: 0 }
  );

  // 只留视口中线附近的一条窄带，取交叉比例最大的页作为当前页
  state.currentObserver = new IntersectionObserver(
    (entries) => {
      let bestPage = 0;
      let bestRatio = 0;
      for (const entry of entries) {
        if (!entry.isIntersecting) continue;
        const pageNum = Number(entry.target.dataset.page) || 0;
        if (pageNum && entry.intersectionRatio >= bestRatio) {
          bestRatio = entry.intersectionRatio;
          bestPage = pageNum;
        }
      }
      if (bestPage) setCurrentPage(bestPage);
    },
    { root, rootMargin: "-45% 0px -45% 0px", threshold: [0, 0.05, 0.5, 1] }
  );

  for (const container of state.pages.values()) {
    state.renderObserver.observe(container);
    state.currentObserver.observe(container);
  }
}

function teardownObservers() {
  if (state.renderObserver) {
    state.renderObserver.disconnect();
    state.renderObserver = null;
  }
  if (state.currentObserver) {
    state.currentObserver.disconnect();
    state.currentObserver = null;
  }
}

function resetPageIndicatorLoading() {
  const total = $("reader-page-total");
  if (total) total.textContent = "…";
  const input = $("reader-page-input");
  if (input) input.value = "1";
}

function pdfErrorMessage(err) {
  const name = String((err && err.name) || "");
  const raw = String((err && err.message) || err || "");
  if (/UnexpectedResponse|404|Not Found/i.test(`${name} ${raw}`)) {
    return "这篇论文没有可用的 PDF（可能没上传文件或文件丢了）";
  }
  if (/InvalidPDF|Invalid PDF/i.test(`${name} ${raw}`)) return "PDF 打不开，文件可能损坏了";
  if (/MissingPDF/i.test(`${name} ${raw}`)) return "找不到 PDF 文件";
  return `PDF 加载失败：${raw || "未知错误"}`;
}

function renderPdfPlaceholder(message) {
  const pagesEl = $("pdf-pages");
  if (!pagesEl) return;
  const box = document.createElement("div");
  box.style.cssText =
    "max-width:520px;margin:70px auto;padding:28px;background:var(--surface);border:1px solid var(--line);" +
    "border-radius:var(--radius-card);text-align:center;color:var(--ink-2);font-size:var(--fs-14);line-height:1.8;" +
    "box-shadow:var(--shadow);";
  const mark = document.createElement("img");
  mark.src = "/brand/npl-mark.svg";
  mark.alt = "NPL";
  mark.width = 36;
  mark.height = 36;
  mark.style.cssText = "display:block;margin:0 auto 8px;";
  const text = document.createElement("div");
  text.textContent = message;
  box.append(mark, text);
  pagesEl.replaceChildren(box);
  state.numPages = 0;
  state.pages.clear();
  const total = $("reader-page-total");
  if (total) total.textContent = "0";
  updatePageIndicator();
}

function updateSearchCount() {
  const search = state.search, count = $('reader-search-count');
  if (count) count.textContent = search.busy ? `搜索 ${search.progress}/${state.numPages} 页 · ${search.matches.length} 处` : search.cancelled ? `已停止 · ${search.matches.length} 处` : !search.query ? '' : search.matches.length ? `第 ${search.index+1} / ${search.matches.length} 处` : '无结果';
  if ($('reader-search-cancel')) $('reader-search-cancel').hidden = !search.busy;
}
function paintSearchPage(pageNum) {
  const container = state.pages.get(pageNum);
  if (!container) return;
  container.querySelector('.reader-search-layer')?.remove();
  if (!state.search.matches.length) return;
  const layer = document.createElement('div');
  layer.className = 'reader-search-layer';
  const bounds = container.getBoundingClientRect();
  for (const [index,match] of state.search.matches.entries()) {
    if (match.page !== pageNum) continue;
    for (const part of match.parts) {
      const span = container.querySelector(`.textLayer span[data-item-index="${part.itemIndex}"]`);
      if (!span?.firstChild || part.end>span.firstChild.length) continue;
      const range = document.createRange();
      range.setStart(span.firstChild,part.start); range.setEnd(span.firstChild,part.end);
      for (const rect of range.getClientRects()) {
        const hit = document.createElement('div');
        hit.className = 'reader-search-hit' + (index===state.search.index ? ' active' : '');
        hit.dataset.hit = String(index);
        Object.assign(hit.style,{left:`${rect.left-bounds.left}px`,top:`${rect.top-bounds.top}px`,width:`${rect.width}px`,height:`${rect.height}px`});
        layer.append(hit);
      }
    }
  }
  container.append(layer);
}
function focusSearchHit(navigate = true) {
  const search = state.search, match = search.matches[search.index];
  if (!match) return;
  if (navigate) gotoPage(match.page,{smooth:false});
  for (const number of state.renderedPages.keys()) paintSearchPage(number);
  const hit = state.pages.get(match.page)?.querySelector('.reader-search-hit.active');
  if (hit) {
    const scroll = $('pdf-scroll'), rect=hit.getBoundingClientRect(), bounds=scroll.getBoundingClientRect();
    scroll.scrollTop += rect.top - bounds.top - scroll.clientHeight*.3;
  }
  updateSearchCount();
}
function cancelPdfSearch() {
  state.search.busy = false;
  state.search.cancelled = true;
  updateSearchCount();
}
async function runPdfSearch(rawQuery) {
  cancelPdfSearch();
  const query = String(rawQuery||'').trim(), doc = state.doc, owns = readerOwner();
  const search = {query,matches:[],index:0,busy:!!query,progress:0};
  state.search = search;
  for (const number of state.renderedPages.keys()) paintSearchPage(number);
  updateSearchCount();
  if (!query || !doc) { search.busy=false; updateSearchCount(); return; }
  const active = () => owns() && state.doc===doc && state.search===search && search.busy;
  try {
    for (let pageNum=1;pageNum<=doc.numPages;pageNum++) {
      const page = await doc.getPage(pageNum);
      if (!active()) return;
      const content = await page.getTextContent();
      if (!active()) return;
      search.matches.push(...findTextMatches(content.items||[],query).map(hit=>({...hit,page:pageNum})));
      search.progress=pageNum; updateSearchCount();
      paintSearchPage(pageNum);
      await new Promise(resolve=>setTimeout(resolve,0));
      if (!active()) return;
    }
    search.busy=false; updateSearchCount(); focusSearchHit();
  } catch (err) {
    if (!active()) return;
    search.busy=false; updateSearchCount();
    toast(`搜索失败：${friendlyError(err)}`,'error');
  }
}
function stepPdfSearch(delta) {
  const search = state.search;
  if (!search.matches.length) return;
  search.index=(search.index+delta+search.matches.length)%search.matches.length;
  focusSearchHit();
}

async function loadPdf(paperId, saved) {
  const token = ++state.renderToken;
  resetPageIndicatorLoading();
  try {
    // 每份文档用独立 worker：全局 worker 在 doc.destroy() 后会被一并销毁，
    // 复用会导致「关闭再打开后渲染任务永远挂起」。
    const worker = new pdfjsLib.PDFWorker({ name: `paperwhale-${paperId}-${Date.now()}` });
    const loadingTask = pdfjsLib.getDocument({
      url: `/api/papers/${encodeURIComponent(paperId)}/pdf`,
      withCredentials: false,
      worker,
    });
    state.loadingTask = loadingTask;
    const doc = await loadingTask.promise;
    if (token !== state.renderToken || state.paperId !== paperId) {
      try {
        await doc.destroy();
      } catch {
        /* 忽略 */
      }
      return;
    }
    state.doc = doc;
    state.numPages = doc.numPages;
    outlineNavigator?.load(doc);
    const total = $("reader-page-total");
    if (total) total.textContent = String(doc.numPages);

    const firstPage = await doc.getPage(1);
    if (token !== state.renderToken || state.doc !== doc) return;
    state.firstSize = firstPage.getViewport({ scale: 1 });
    state.pageSizes.set(1, state.firstSize);
    state.fitScale = computeFitScale();
    state.currentPage = 1;
    state.zoom = clamp(Number(saved?.zoom)||1,MIN_ZOOM,MAX_ZOOM);
    state.scannedWarned = false;

    buildPlaceholders();
    setupObservers();
    updatePageIndicator();

    const initialPages = Math.min(3, doc.numPages);
    state.pinned.clear();
    for (let pageNum = 1; pageNum <= initialPages; pageNum += 1) {
      state.pinned.add(pageNum);
      state.wanted.add(pageNum);
      queueRender(pageNum);
    }
    const scrollEl = $("pdf-scroll");
    if (scrollEl) scrollEl.scrollTop = 0;
  } catch (err) {
    if (token !== state.renderToken) return;
    console.warn("[reader] PDF 加载失败", err);
    const message = pdfErrorMessage(err);
    toast(message, "error", 6200);
    renderPdfPlaceholder(message);
  }
}

async function teardownDocument() {
  clearCitationFocus();
  outlineNavigator?.reset();
  navigation?.reset();
  readerContext?.reset();
  cancelPdfSearch();
  state.renderToken += 1;
  teardownObservers();
  for (const task of state.renderTasks.values()) {
    try {
      task.cancel();
    } catch {
      /* 忽略 */
    }
  }
  state.renderTasks.clear();
  state.renderQueue.length = 0;
  state.activeRenders = 0;
  state.queued.clear();
  state.rendering.clear();
  state.wanted.clear();
  state.pinned.clear();
  state.renderedPages.clear();

  const pagesEl = $("pdf-pages");
  if (pagesEl) {
    for (const canvas of pagesEl.querySelectorAll("canvas")) {
      try {
        canvas.width = 0;
        canvas.height = 0;
      } catch {
        /* 忽略 */
      }
    }
    pagesEl.replaceChildren();
  }
  state.pages.clear();
  state.pageSizes.clear();
  state.pageProxies.clear();
  state.numPages = 0;
  state.firstSize = null;
  state.fitScale = 1;

  const outlineBox = $("reader-outline");
  if (outlineBox) outlineBox.replaceChildren();
  if (state.scrollTimer) {
    clearTimeout(state.scrollTimer);
    state.scrollTimer = null;
  }

  const doc = state.doc;
  state.doc = null;
  state.loadingTask = null;
  if (doc) {
    try {
      await doc.destroy();
    } catch (err) {
      console.warn("[reader] PDF 释放失败", err);
    }
  }
}

/* ==================== 划词工具条 ==================== */

function mergeLineRects(rects) {
  const sorted = rects.slice().sort((a, b) => a.y - b.y || a.x - b.x);
  const merged = [];
  for (const rect of sorted) {
    const last = merged[merged.length - 1];
    if (last && Math.abs(last.y - rect.y) < 0.004 && rect.x <= last.x + last.w + 0.004) {
      const right = Math.max(last.x + last.w, rect.x + rect.w);
      last.w = right - last.x;
      last.h = Math.max(last.h, rect.h);
    } else {
      merged.push({ ...rect });
    }
  }
  return merged;
}

function hideToolbar() {
  const toolbar = $("selection-toolbar");
  if (toolbar) toolbar.classList.add("hidden");
  state.selection = null;
}

function showToolbar(snapshot) {
  const toolbar = $("selection-toolbar");
  if (!toolbar) return;
  state.selection = snapshot;
  readerContext?.select({...snapshot, paperId: snapshot.paperId || state.paperId});
  toolbar.classList.remove("hidden");
  const toolbarRect = toolbar.getBoundingClientRect();
  // 桌面模式下顶部 36px 是应用自己的标题栏（z-index 9000），划词工具条不能藏在它下面。
  const minTop = document.body.classList.contains("desktop-shell") ? 44 : 8;
  const anchor = snapshot.rect;
  let left = anchor.left + anchor.width / 2 - toolbarRect.width / 2;
  let top = anchor.top - toolbarRect.height - 8;
  if (top < minTop) top = anchor.bottom + 8;
  left = clamp(left, 8, Math.max(8, window.innerWidth - toolbarRect.width - 8));
  top = clamp(top, minTop, Math.max(minTop, window.innerHeight - toolbarRect.height - 8));
  toolbar.style.left = `${left}px`;
  toolbar.style.top = `${top}px`;
}

function refreshSelection() {
  const reader = $("reader");
  if (!reader || reader.classList.contains("hidden")) return;
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || selection.rangeCount === 0) {
    hideToolbar();
    return;
  }
  const range = selection.getRangeAt(0);
  const text = selection.toString().replace(/\s+/g, " ").trim();
  if (!text) {
    hideToolbar();
    return;
  }
  const anchorNode = range.startContainer;
  const anchorEl = anchorNode.nodeType === 1 ? anchorNode : anchorNode.parentElement;
  const pageEl = anchorEl ? anchorEl.closest(".pdf-page") : null;
  const pagesHost = pageEl ? pageEl.closest("#pdf-pages, #pdf-pages-side") : null;
  if (!pageEl || !pagesHost) {
    hideToolbar();
    return;
  }
  const onSide = pagesHost.id === "pdf-pages-side";
  const pageRect = pageEl.getBoundingClientRect();
  if (!pageRect.width || !pageRect.height) {
    hideToolbar();
    return;
  }
  const pageNum = Number(pageEl.dataset.page) || 1;
  const rects = [];
  for (const rect of range.getClientRects()) {
    if (rect.width < 1 || rect.height < 1) continue;
    const x = (rect.left - pageRect.left) / pageRect.width;
    const y = (rect.top - pageRect.top) / pageRect.height;
    const w = rect.width / pageRect.width;
    const h = rect.height / pageRect.height;
    if (x >= 1 || y >= 1 || x + w <= 0 || y + h <= 0) continue;
    const cx = clamp(x, 0, 1);
    const cy = clamp(y, 0, 1);
    const cw = clamp(Math.min(x + w, 1) - cx, 0, 1);
    const ch = clamp(Math.min(y + h, 1) - cy, 0, 1);
    if (cw <= 0 || ch <= 0) continue;
    rects.push({ x: +cx.toFixed(5), y: +cy.toFixed(5), w: +cw.toFixed(5), h: +ch.toFixed(5) });
  }
  if (rects.length === 0) {
    hideToolbar();
    return;
  }
  showToolbar({
    text: text.slice(0, SELECTION_TEXT_LIMIT),
    pageNum,
    rects: mergeLineRects(rects),
    rect: range.getBoundingClientRect(),
    paperId: onSide ? (state.sidePaperId || state.paperId) : state.paperId,
    side: onSide,
  });
}

async function createAnnotation(snapshot, kind = "highlight", color = HIGHLIGHT_COLOR, note = "") {
  if (!snapshot || !Array.isArray(snapshot.rects) || snapshot.rects.length === 0) {
    toast("这段没框到内容，换个位置再试", "error", 3200);
    return;
  }
  const owns = readerOwner();
  const paperId = snapshot.paperId || state.paperId;
  const labels = { highlight: "高亮", underline: "下划线", strikeout: "删除线", note: "笔记" };
  try {
    const created = await api.post(`/papers/${encodeURIComponent(paperId)}/annotations`, {
      page: snapshot.pageNum,
      color,
      rects: snapshot.rects,
      quote: snapshot.text,
      note,
      kind,
    });
    if (!owns()) return;
    if (snapshot.side) {
      if (created && created.id) state.sideAnnotations.push(normalizeAnnotation(created));
      refreshSideHighlights(snapshot.pageNum);
    } else {
      if (created && created.id) state.annotations.push(normalizeAnnotation(created));
      refreshHighlights(snapshot.pageNum);
      renderAnnotationList();
    }
    toast(`已添加${labels[kind] || "批注"}`, "success");
    window.getSelection()?.removeAllRanges();
    window.PW?.refreshStats?.();
    return created;
  } catch (err) {
    if (!owns()) return;
    toast(`添加批注失败：${friendlyError(err)}`, "error", 4200);
    throw err;
  }
}

function createHighlight(snapshot, color = HIGHLIGHT_COLOR) {
  return createAnnotation(snapshot, "highlight", color).catch(() => {});
}


/* ==================== AI 浮层（翻译 / 解释） ==================== */

function closeFloater() {
  if (state.floater) {
    state.floater.remove();
    state.floater = null;
  }
}

function ensureFloater(title, anchorRect) {
  closeFloater();
  const reader = $("reader");
  if (!reader) return null;

  const root = document.createElement("div");
  root.className = "pw-floater";
  root.style.cssText =
    "position:fixed;z-index:210;width:min(380px, calc(100vw - 24px));max-height:50vh;display:flex;" +
    "flex-direction:column;background:var(--surface);border:1px solid var(--line);border-radius:var(--radius-card);" +
    "box-shadow:var(--shadow-lg);overflow:hidden;";

  const head = document.createElement("div");
  head.style.cssText =
    "display:flex;align-items:center;justify-content:space-between;gap:8px;padding:9px 12px;" +
    "border-bottom:1px solid var(--line-soft);font-size:var(--fs-13);color:var(--ink-2);flex:0 0 auto;";
  const label = document.createElement("strong");
  label.textContent = title;
  label.style.fontWeight = "600";
  const closeButton = document.createElement("button");
  closeButton.type = "button";
  closeButton.className = "icon-btn";
  closeButton.textContent = "×";
  closeButton.title = "关闭";
  closeButton.addEventListener("click", closeFloater);
  head.append(label, closeButton);

  const body = document.createElement("div");
  body.className = "markdown";
  body.style.cssText = "padding:12px 14px;overflow:auto;font-size:var(--fs-13);line-height:1.7;";
  root.append(head, body);
  reader.append(root);

  const width = root.getBoundingClientRect().width || 380;
  let left = anchorRect ? anchorRect.left : 80;
  left = clamp(left, 12, Math.max(12, window.innerWidth - width - 12));
  root.style.left = `${left}px`;
  root.style.top = "12px";
  const height = root.getBoundingClientRect().height || 180;
  let top = anchorRect ? anchorRect.bottom + 10 : 80;
  if (top + height > window.innerHeight - 12) {
    top = Math.max(12, (anchorRect ? anchorRect.top : 200) - height - 10);
  }
  root.style.top = `${top}px`;

  state.floater = root;
  return { root, body };
}

async function openAiInChat(kind, snapshot) {
  const session = state.chatSession;
  const paperId = snapshot.paperId || state.paperId;
  if (!session || !paperId || session.busy) {
    toast('请等待当前论文对话完成', 'info');
    return;
  }
  if ($("reader").classList.contains("reader-sidebar-collapsed")) $("reader-sidebar-toggle").click();
  const isTranslate = kind === "translate";
  const label = isTranslate ? "翻译" : "解释";
  switchTab("chat");
  try {
    const payload = {
      text: snapshot.text,
      paper_id: paperId,
      page: snapshot.pageNum || state.currentPage,
      scope: session.scope,
    };
    await session.post(isTranslate ? '/ai/translate' : '/ai/explain', payload, `【${label} · 第 ${payload.page} 页】\n${snapshot.text}`);
  } catch (err) {
    if (state.chatSession !== session) return;
    const message = friendlyError(err);
    toast(`${label}失败：${message}`, "error", 5200);
  } finally {
    if (state.chatSession === session) {
      window.getSelection()?.removeAllRanges();
      hideToolbar();
    }
  }
}

async function openAiFloater(kind, snapshot) {
  return openAiInChat(kind, snapshot);
}

/* ==================== 初始化与打开 ==================== */

function resetPanels() {
  readerRegions?.clear();
  state.chatImages = [];
  state.chatUpload = null;
  const paperNotes=$('reader-paper-notes');
  paperNotes?.disposePaperNotes?.();paperNotes?.replaceChildren();
  if ($('reader-chat-input')) $('reader-chat-input').value = '';
  setBusy($('btn-analyze'), false);
  setBusy($('btn-relevance'), false);
  const title = $("reader-title");
  if (title) title.textContent = "加载中…";
  const summary = $("reader-summary");
  if (summary) summary.textContent = "还没有精读结果。点上面的「AI 精读」开始阅读。";
  const keypoints = $("reader-keypoints");
  if (keypoints) keypoints.replaceChildren();
  const tags = $("reader-tags");
  if (tags) tags.replaceChildren();
  const relevance = $("reader-relevance");
  if (relevance) relevance.textContent = "还没有关联分析。";
  const annotations = $("reader-annotations");
  if (annotations) annotations.replaceChildren();
  const category = $("reader-category-select");
  if (category) {
    category.disabled = false;
    category.replaceChildren();
    const empty = document.createElement("option");
    empty.value = "";
    empty.textContent = "未分类";
    category.append(empty);
    category.value = "";
  }
  resetReaderChat();
  state.chatAttachments = [];
  renderChatAttachments();
  switchTab("summary");
}

function makeReaderButton(id, text, action) {
  const button=document.createElement('button');
  button.type='button'; button.id=id; button.className='btn tiny'; button.textContent=text;
  button.addEventListener('click',action); return button;
}
function initReaderEnhancements() {
  if (!document.querySelector('link[data-reader-enhancements]')) {
    const css=document.createElement('link'); css.rel='stylesheet'; css.href='/css/reader-enhancements.css';
    css.dataset.readerEnhancements=''; document.head.append(css);
  }
  const reader=$('reader'), panel=reader?.querySelector('.reader-panel'), body=reader?.querySelector('.reader-body');
  const tools=reader?.querySelector('.reader-tools');
  const toolActions=reader?.querySelector('#rt-actions') || tools;
  const toggle=makeReaderButton('reader-sidebar-toggle','收起侧栏',()=>{
    const collapsed=reader.classList.toggle('reader-sidebar-collapsed');
    toggle.textContent=collapsed?'展开侧栏':'收起侧栏'; toggle.setAttribute('aria-expanded',String(!collapsed));
    try {localStorage.setItem('pw-reader-sidebar-collapsed',String(collapsed));} catch {}
    resizeReaderLayout();
  });
  panel.id='reader-side-panel'; toggle.setAttribute('aria-controls',panel.id);
  toggle.setAttribute('aria-expanded','true'); toolActions?.insertBefore(toggle,$('reader-close'));
  const separator=document.createElement('div'); separator.className='reader-sidebar-resizer'; separator.tabIndex=0;
  separator.setAttribute('role','separator'); separator.setAttribute('aria-orientation','vertical'); separator.setAttribute('aria-label','调整阅读侧栏宽度');
  separator.setAttribute('aria-controls',panel.id);
  body?.insertBefore(separator,panel);
  const setWidth=(width, commit=true)=>{
    const maximum=Math.max(260,Math.min(760,reader.clientWidth-260));
    const value=clamp(width,260,maximum);
    reader.style.setProperty('--reader-panel-width',`${value}px`);
    separator.setAttribute('aria-valuenow',String(Math.round(value)));
    separator.setAttribute('aria-valuemin','260'); separator.setAttribute('aria-valuemax',String(Math.round(maximum)));
    if (commit) {
      try {localStorage.setItem('pw-reader-sidebar-width',String(value));} catch {}
      resizeReaderLayout();
    }
  };
  let drag=null, widthFrame=null, pendingWidth=null;
  const flushWidth=()=>{widthFrame=null;if(pendingWidth!==null){setWidth(pendingWidth,false);pendingWidth=null;}};
  const finishDrag=()=>{
    if(!drag)return;
    if(widthFrame!==null)cancelAnimationFrame(widthFrame);
    flushWidth();drag=null;state.sidebarDragging=false;
    setWidth(parseFloat(reader.style.getPropertyValue('--reader-panel-width')) || panel.getBoundingClientRect().width);
  };
  separator.addEventListener('pointerdown',e=>{
    if(e.button!==0)return;
    e.preventDefault();drag={x:e.clientX,width:panel.getBoundingClientRect().width};state.sidebarDragging=true;
    clearTimeout(state.resizeTimer);separator.setPointerCapture(e.pointerId);
  });
  separator.addEventListener('pointermove',e=>{
    if(!drag)return;
    pendingWidth=drag.width+drag.x-e.clientX;
    if(widthFrame===null)widthFrame=requestAnimationFrame(flushWidth);
  });
  separator.addEventListener('pointerup',finishDrag);
  separator.addEventListener('pointercancel',finishDrag);
  separator.addEventListener('lostpointercapture',finishDrag);
  separator.addEventListener('keydown',e=>{if(!['ArrowLeft','ArrowRight','Home'].includes(e.key))return;e.preventDefault();setWidth(e.key==='Home'?430:panel.getBoundingClientRect().width+(e.key==='ArrowLeft'?24:-24));});
  try {
    const width=Number(localStorage.getItem('pw-reader-sidebar-width'));
    if(width>0) reader.style.setProperty('--reader-panel-width',`${clamp(width,260,760)}px`);
    if(localStorage.getItem('pw-reader-sidebar-collapsed')==='true') toggle.click();
  } catch {}
  const cancel=makeReaderButton('reader-search-cancel','停止搜索',cancelPdfSearch); cancel.hidden=true;
  $('reader-search-count')?.after(cancel);
  $('reader-search-count')?.setAttribute('aria-live','polite');
  $('reader-search')?.setAttribute('aria-label','全文搜索，回车开始');
  $('reader-search')?.addEventListener('input',e=>{if(!e.target.value) runPdfSearch('');});
  $('reader-zoom-in')?.setAttribute('aria-label','放大 PDF'); $('reader-zoom-out')?.setAttribute('aria-label','缩小 PDF');
  $('reader-page-input')?.setAttribute('aria-label','PDF 页码');
  $('reader-tabs')?.setAttribute('role','tablist');
  reader.querySelectorAll('#reader-tabs .tab').forEach(tab=>{tab.setAttribute('role','tab');tab.setAttribute('aria-selected',String(tab.classList.contains('active')));});
  const bottom=makeReaderButton('reader-chat-bottom','回到底部',scrollMiniChatToBottom);
  bottom.hidden=true; $('reader-chat-messages')?.after(bottom);
  $('reader-chat-messages')?.addEventListener('scroll',updateChatBottomButton,{passive:true});
  const undo=makeReaderButton('reader-chat-undo','撤销清空',async()=>{
    const session=state.chatSession; if(!session?.undo)return;
    undo.disabled=true;
    try {await session.undo();} catch(err){if(state.chatSession===session) toast(`恢复失败：${friendlyError(err)}`,'error');}
    finally {if(state.chatSession===session) undo.disabled=false;}
  });
  undo.hidden=true; $('reader-chat-clear')?.after(undo);
  $('reader-chat-clear')?.setAttribute('title','清空本篇论文的对话与附件；清空后可以撤销恢复');
  const stop=makeReaderButton('reader-chat-stop','停止生成',async()=>{const session=state.chatSession;try{await session?.stop?.();}catch(err){if(state.chatSession===session)toast(`停止失败：${friendlyError(err)}`,'error');}}); stop.hidden=true; undo.after(stop);
  const retry=makeReaderButton('reader-chat-retry','重试',async()=>{
    const session = state.chatSession;
    if (!session || state.chatUpload) return;
    const owns = readerOwner();
    const epoch = session.epoch;
    const payload = session.lastRequest?.payload;
    try {
      const result = await session.retry();
      if (result?.conversation && owns() && epoch === session.epoch) {
        consumeReaderImages(payload?.images);
        if (payload?.images?.length) session.lastRequest = null;
        const input = $('reader-chat-input');
        if (!payload?.image_action && input.value.trim() === payload?.retrieval_question) input.value = '';
      }
    } catch (err) {
      if (owns() && epoch === session.epoch) toast(`重试失败：${friendlyError(err)}`,'error');
    }
  });
  retry.hidden=true; stop.after(retry);
  window.addEventListener('pagehide',rememberReading);
}
function resizeReaderLayout() {
  readerRegions?.clear();
  if(!state.doc || state.sidebarDragging)return;
  const position=readingPosition(); state.fitScale=computeFitScale(); relayoutPages(); restorePosition(position);
}
function showNoteEditor(parent, value, save) {
  if(!parent)return;
  const previous=$('reader-note-editor');
  previous?.remove();
  const owns=readerOwner(), box=document.createElement('form');
  let saveNote=save;box.id='reader-note-editor';box.className='reader-note-editor';
  const input=document.createElement('textarea');input.value=value;input.rows=4;input.setAttribute('aria-label','批注备注');
  const status=document.createElement('span');status.className='reader-note-status';status.setAttribute('role','status');status.textContent='编辑备注';
  input.addEventListener('input',()=>{box.dataset.dirty='true';status.textContent='未保存';});
  const submit=document.createElement('button');submit.type='submit';submit.className='btn tiny primary reader-note-save';submit.textContent='保存备注';
  const cancel=makeReaderButton('reader-note-cancel','取消',()=>box.remove());
  box.append(input,submit,cancel,status); box.addEventListener('click',e=>e.stopPropagation());
  box.addEventListener('submit',async e=>{
    e.preventDefault(); if(submit.disabled)return;
    submit.disabled=true;status.textContent='保存中…';
    try {
      const saved=await saveNote(input.value.trim()); if(!owns())return;
      if(saved?.id) {
        document.querySelector(`#reader-annotations [data-annotation-id="${CSS.escape(String(saved.id))}"]`)?.prepend(box);
        saveNote=async note=>{
          const updated=await api.patch(`/annotations/${encodeURIComponent(saved.id)}`,{note});
          if(!owns())return;
          const current=state.annotations.find(a=>a.id===saved.id);
          if(current)Object.assign(current,normalizeAnnotation(updated));
          renderAnnotationList();refreshHighlights(saved.page);return updated;
        };
      }
      status.textContent='已保存';box.dataset.dirty='false';submit.disabled=false;
    }
    catch(err){if(!owns())return;status.textContent=`保存失败：${friendlyError(err)}`;submit.disabled=false;}
  });
  box.addEventListener('keydown',e=>{if(e.key==='Escape'){e.stopPropagation();box.remove();}if(e.key==='Enter'&&(e.ctrlKey||e.metaKey)){e.preventDefault();box.requestSubmit();}});
  parent.prepend(box);input.focus();
}
const noteSaves = new Map();
function appendAnswerActions(bubble,item,paperId) {
  if(!bubble)return;
  const row=document.createElement('div');row.className='reader-answer-actions';
  const copy=makeReaderButton('','复制',async()=>{try{await navigator.clipboard.writeText(item.content);copy.textContent='已复制';}catch{copy.textContent='复制失败，请选中文字复制';}});
  const save=makeReaderButton('','存入本篇笔记',async()=>{
    const owns=readerOwner();save.disabled=true;save.textContent='保存中…';
    const previous=noteSaves.get(paperId)||Promise.resolve();
    const operation=previous.catch(()=>{}).then(async()=>{
      const updated=await api.post(`/papers/${encodeURIComponent(paperId)}/notes/append`,{text:'### AI 问答记录\n'+item.content});
      emit('notes-changed',{paperId});
      window.PW?.emit?.('library-changed');
      if(owns()&&state.paper)state.paper.notes=updated.notes;
    });
    noteSaves.set(paperId,operation);
    try{await operation;if(owns())save.textContent='已存入笔记';}
    catch(err){if(owns()){save.textContent='重试保存';toast(`笔记保存失败：${friendlyError(err)}`,'error');}}
    finally {if(noteSaves.get(paperId)===operation)noteSaves.delete(paperId);if(owns())save.disabled=false;}
  });
  copy.classList.add('reader-answer-copy');save.classList.add('reader-answer-save');row.append(copy,save);bubble.append(row);
}
function appendReasoning(bubble,text,opts={}) {
  const trace=Array.isArray(opts.trace)?opts.trace:[];
  if(!bubble||(!text&&!trace.length))return null;
  return upsertThinkingPanel(bubble,{text,state:opts.state||'done',trace,
    thinkingSeconds:opts.thinkingSeconds||0,elapsedSeconds:opts.elapsedSeconds||0,stage:opts.stage||''});
}

export function initReader() {
  if (state.initialized) return;
  state.initialized = true;
  window.PW = window.PW || {};
  initReaderEnhancements();
  navigation=installReaderNavigation({position:readingPosition,restore:restorePosition});
  annotationTools=installAnnotationTools({getPaper:()=>state.paper,getAnnotations:()=>state.annotations,getChapters:()=>state.chapters,onChange:renderAnnotationList});
  readerContext=installReaderContext({getState:()=>state,getChapters:()=>state.chapters});
  readerRegions = installReaderRegions({
    pages: $('pdf-pages'), scroll: $('pdf-scroll'),
    onStart() { window.getSelection()?.removeAllRanges(); hideToolbar(); },
    onAction: handleReaderRegion,
  });
  outlineNavigator=installOutlineNavigator({getState:()=>state,navigate:gotoPage,onChange:()=>{renderAnnotationList();readerContext?.refresh();}});
  window.PW.readerState = state;  // 调试/自动化测试用

  $("reader-close")?.addEventListener("click", () => {
    closeReader();
  });
  $("reader-prev")?.addEventListener("click", () => gotoPage(state.currentPage - 1));
  $("reader-next")?.addEventListener("click", () => gotoPage(state.currentPage + 1));
  $("reader-zoom-in")?.addEventListener("click", () => zoomBy(1.15));
  $("reader-zoom-out")?.addEventListener("click", () => zoomBy(1 / 1.15));
  let wheelZoomFrame = 0;
  let wheelZoomSteps = 0;
  $("pdf-scroll")?.addEventListener("wheel", (event) => {
    if (!event.ctrlKey) return;
    const mode = event.deltaMode;
    const delta = event.deltaY;
    if (!delta) return;
    event.preventDefault();
    if (!state.doc || state.restoring) return;
    // A wheel burst should relayout and rerender the PDF at most once per frame.
    wheelZoomSteps -= delta / (mode === 1 ? 3 : mode === 2 ? 1 : 100);
    if (wheelZoomFrame) return;
    const owns = readerOwner();
    wheelZoomFrame = requestAnimationFrame(() => {
      const steps = wheelZoomSteps;
      wheelZoomFrame = 0;
      wheelZoomSteps = 0;
      if (owns()) zoomBy(1.15 ** steps);
    });
  }, {passive: false});
  $("reader-search")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      runPdfSearch(event.target.value);
    }
  });
  $("reader-search-prev")?.addEventListener("click", () => stepPdfSearch(-1));
  $("reader-search-next")?.addEventListener("click", () => stepPdfSearch(1));

  const pageInput = $("reader-page-input");
  pageInput?.addEventListener("change", (event) => gotoPage(Number(event.target.value)));
  pageInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      gotoPage(Number(event.target.value));
      event.target.blur();
    }
  });
  pageInput?.addEventListener("blur", () => {
    pageInput.value = String(state.currentPage);
  });

  document.querySelectorAll("#reader-tabs .tab").forEach((tab) => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  $("btn-analyze")?.addEventListener("click", runAnalyze);
  $("btn-relevance")?.addEventListener("click", runRelevance);
  $("reader-category-select")?.addEventListener("change", onCategoryChange);

  $("reader-chat-send")?.addEventListener("click", sendReaderChat);
  bindReaderChatExtras();
  $('pdf-scroll-side')?.addEventListener('wheel', (event) => {
    if (!event.ctrlKey || !state.sideDoc) return;
    event.preventDefault();
    state.sideScale = Math.max(0.6, Math.min(2.2, (state.sideScale || 1) * (event.deltaY < 0 ? 1.1 : 1 / 1.1)));
    renderSidePages(++state.sideRenderToken).catch(() => {});
  }, { passive: false });
  $('reader-chat-clear')?.addEventListener('click', async () => {
    const session = state.chatSession;
    if (!session) return;
    const button=$('reader-chat-clear');
    if(button?.disabled)return;
    if(button)button.disabled=true;
    try {
      await session.clear();
      if (state.chatSession === session) {
        state.chatImages = [];
        renderChatAttachments();
        readerContext?.reset();
      }
      if (state.chatSession === session && $('reader-chat-input')) $('reader-chat-input').value = '';
    } catch (err) { if(state.chatSession===session)toast(`清空失败：${friendlyError(err)}`, 'error'); }
    finally {if(button)button.disabled=false;}
  });
  $("reader-chat-attach")?.addEventListener("click", () => $("reader-chat-file")?.click());
  $("reader-chat-file")?.addEventListener("change", (event) => {
    uploadReaderChatFiles(event.target.files);
    event.target.value = "";
  });
  $("reader-chat-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey && !event.isComposing) {
      event.preventDefault();
      sendReaderChat();
    }
  });

  $("reader-annotations")?.addEventListener("click", async (event) => {
    const deleteButton = event.target.closest("button[data-ann-id]");
    if (deleteButton) {
      event.stopPropagation();
      deleteAnnotation(deleteButton.dataset.annId);
      return;
    }
    const editButton = event.target.closest("button[data-edit-id]");
    if (editButton) {
      event.stopPropagation();
      const target = state.annotations.find((item) => Number(item.id) === Number(editButton.dataset.editId));
      if (!target) return;
      showNoteEditor(editButton.closest('.ann-item'), target.note || '', async note => {
        const owns = readerOwner();
        const updated = await api.patch(`/annotations/${encodeURIComponent(target.id)}`,{note});
        if (!owns()) return;
        Object.assign(target,normalizeAnnotation(updated));
        renderAnnotationList(); refreshHighlights(target.page);
        return updated;
      });
      return;
    }
    const item = event.target.closest(".ann-item");
    if (item && item.dataset.page) gotoPage(Number(item.dataset.page));
  });

  // 划词工具条：mousedown 阻止默认行为，避免点按钮时选区被清掉
  const toolbar = $("selection-toolbar");
  toolbar?.addEventListener("mousedown", (event) => event.preventDefault());
  toolbar?.addEventListener("click", (event) => {
    const button = event.target.closest("button[data-action]");
    if (!button) return;
    const snapshot = state.selection;
    if (!snapshot) return;
    const action = button.dataset.action;
    if (action === "chat") {
      switchTab("chat");
      const input = $("reader-chat-input");
      if (input) {
        input.value = snapshot.text;
        input.focus();
      }
      hideToolbar();
      return;
    }
    const color = button.dataset.color || HIGHLIGHT_COLOR;
    if (action === "translate" || action === "explain") {
      hideToolbar();
      openAiInChat(action, snapshot);
      return;
    }
    hideToolbar();
    if (action === "note") {
      if ($("reader").classList.contains("reader-sidebar-collapsed")) $("reader-sidebar-toggle").click();
      switchTab("notes");
      showNoteEditor($("reader-annotations"), "", note => createAnnotation(snapshot, "note", "#14a3a3", note));
    } else if (action === "underline" || action === "strikeout") {
      createAnnotation(snapshot, action, color).catch(() => {});
    } else {
      createHighlight(snapshot, color);
    }
  });

  const scrollEl = $("pdf-scroll");
  scrollEl?.addEventListener(
    "scroll",
    () => {
      if (state.selection) hideToolbar();
      if (state.scrollTimer) clearTimeout(state.scrollTimer);
      state.scrollTimer = setTimeout(() => { probeCurrentPage(); rememberReading(); }, 140);
    },
    { passive: true }
  );

  // 选区相关：拖动结束、键盘选择后刷新工具条；点到别处就收起
  document.addEventListener("mouseup", (event) => {
    if (event.button !== 0 || readerRegions?.active()) return;
    const reader = $("reader");
    if (!reader || reader.classList.contains("hidden")) return;
    const targetEl = eventTargetElement(event);
    if (targetEl && (targetEl.closest("#selection-toolbar") || targetEl.closest(".pw-floater"))) return;
    setTimeout(refreshSelection, 10);
  });
  document.addEventListener("keyup", (event) => {
    if (!event.shiftKey) return;
    const reader = $("reader");
    if (!reader || reader.classList.contains("hidden")) return;
    setTimeout(refreshSelection, 10);
  });
  document.addEventListener("selectionchange", () => {
    const reader = $("reader");
    if (!reader || reader.classList.contains("hidden")) return;
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed) hideToolbar();
  });
  document.addEventListener(
    "mousedown",
    (event) => {
      if (!state.selection) return;
      const targetEl = eventTargetElement(event);
      if (targetEl && (targetEl.closest("#selection-toolbar") || targetEl.closest(".pw-floater"))) return;
      hideToolbar();
    },
    true
  );

  // Esc：先关掉浮层 / 工具条，再交给 app.js 关阅读器
  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Escape") return;
      if (event.target.closest('dialog[open]')) return;
      if (readerRegions?.active()) {
        event.preventDefault();
        event.stopPropagation();
        readerRegions.clear();
        return;
      }
      if (state.floater) {
        event.preventDefault();
        event.stopPropagation();
        closeFloater();
        return;
      }
      if (state.selection) {
        event.preventDefault();
        event.stopPropagation();
        hideToolbar();
      }
    },
    true
  );

  window.addEventListener("resize", () => {
    if (!state.doc || state.sidebarDragging) return;
    if (state.resizeTimer) clearTimeout(state.resizeTimer);
    state.resizeTimer = setTimeout(() => {
      if (!state.doc || state.sidebarDragging) return;
      const next = computeFitScale();
      if (Math.abs(next - state.fitScale) < 0.002) return;
      const position = readingPosition();
      state.fitScale = next;
      relayoutPages();
      restorePosition(position);
    }, 220);
  });
}

export async function openReader(paperId, targetPage, sourceSnippet = '') {
  let id = paperId;
  if (id && typeof id === "object") id = id.id;
  const explicitPage = targetPage !== undefined && targetPage !== null && Number(targetPage)>0;
  const wantedPage = Math.max(1, Number(targetPage)||1);
  if (!id) {
    toast("没拿到论文 id", "error", 3200);
    return;
  }
  const reader = $("reader");
  if (!reader) return;

  // A deep link may open before the launch intro finishes; release its focus/inert ownership first.
  window.__NPL_STARTUP__?.finish?.();
  emit("reader-opening");

  if (state.paperId === id && state.doc) {
    reader.classList.remove("hidden");
    if (explicitPage) gotoPage(wantedPage);
    if (sourceSnippet) focusCitation(wantedPage, sourceSnippet);
    emit('reader-opened');
    return;
  }

  reader.classList.remove("hidden");
  const freshSession = !state.readingSessionId;
  if (freshSession) {
    state.readingSessionId = newReadingSessionId();
    state.chatBranch = '';
    state.chatBranches = [];
    state.chatQuote = '';
    state.compareOn = false;
  }
  $("reader-note-editor")?.remove();
  hideToolbar();
  closeFloater();
  window.getSelection()?.removeAllRanges();

  rememberReading();
  const saved = loadReadingState(localStorage,id);
  state.restoring = true;
  const openToken = ++state.openToken;
  await teardownDocument();
  if (state.openToken !== openToken) return;

  state.paperId = id;
  if (freshSession) loadCoSet(id); else ensureCoPaper(id);
  state.paper = null;
  state.annotations = [];
  annotationTools?.reset();
  if($('reader-chat-input'))$('reader-chat-input').value='';
  state.currentPage = 1;
  state.zoom = 1;
  state.fitMode = ["width","page","actual"].includes(saved?.fitMode) ? saved.fitMode : "width";
  state.scannedWarned = false;
  resetPanels();
  disposeSideDoc();
  renderCoBar();
  if (saved?.tab && document.querySelector(`#reader-tabs [data-tab="${CSS.escape(saved.tab)}"]`)) switchTab(saved.tab);
  state.search = { query: "", matches: [], index: 0, busy: false, progress:0 };
  const searchInput = $("reader-search");
  if (searchInput) searchInput.value = "";
  updateSearchCount();
  renderAnnotationList();
  if (freshSession) {
    resetReaderChat();
    activateReaderConversation(id);
  } else if (state.chatSession) {
    renderReaderConversation(state.chatSession);
  }

  try {
    const paper = await api.get(`/papers/${encodeURIComponent(id)}`);
    if (state.openToken !== openToken || state.paperId !== id) return;
    state.paper = paper;
    readerContext?.refresh();annotationTools?.refresh();
    const title = $("reader-title");
    if (title) title.textContent = paper.title || "未命名论文";
    renderPaperPanels(paper);
    loadCategories(paper);
  } catch (err) {
    if (state.openToken !== openToken) return;
    toast(`论文信息加载失败：${friendlyError(err)}`, "error", 5200);
    const title = $("reader-title");
    if (title) title.textContent = "论文";
  }

  if (state.openToken !== openToken) return;
  await loadPdf(id, saved);
  if (state.openToken !== openToken || state.paperId !== id) return;
  loadAnnotations(id);
  if (state.compareOn) {
    const other = state.coPapers.find((pid) => pid !== state.paperId);
    if (other) renderSidePdf(other).catch(() => {});
  }
  restorePosition(explicitPage ? {page:wantedPage,offset:0} : saved);
  state.restoring=false;
  rememberReading();
  if (sourceSnippet) focusCitation(wantedPage, sourceSnippet);
  emit('reader-opened');
}

function askSaveSession(count) {
  return new Promise((resolve) => {
    const reader = $("reader");
    if (!reader) { resolve("discard"); return; }
    const root = document.createElement("div");
    root.className = "reader-save-dialog";
    root.innerHTML = `<div class="reader-save-dialog-body">
      <h3>保存这次阅读的对话？</h3>
      <p>共 ${count} 条消息。保存后会存到这篇论文的对话记录里，下次打开阅读器可以继续。</p>
      <div class="rsd-actions">
        <button type="button" class="btn primary" data-choice="save">保存对话</button>
        <button type="button" class="btn ghost" data-choice="discard">不保存</button>
        <button type="button" class="btn ghost" data-choice="cancel">取消</button>
      </div>
    </div>`;
    root.addEventListener("click", (event) => {
      const button = event.target.closest("button[data-choice]");
      if (!button) return;
      root.remove();
      resolve(button.dataset.choice);
    });
    reader.append(root);
  });
}

export async function closeReader() {
  const reader = $("reader");
  if (!reader || reader.classList.contains("hidden")) return;
  const count = (state.chatSession?.data?.messages || []).length;
  if (count > 0 && state.readingSessionId && state.paperId) {
    const choice = await askSaveSession(count);
    if (choice === "cancel") return;
    if (choice === "save") {
      try {
        await api.post(`/ai/conversations/${encodeURIComponent(readerChatScope(""))}/archive`, {
          paper_id: state.paperId,
        });
        toast("对话已保存到这篇论文", "success", 2400);
        return finishCloseReader({ skipPurge: true });
      } catch (err) {
        toast(`保存失败，阅读器没有关闭，可以重试：${friendlyError(err)}`, "error", 4800);
        return;
      }
    }
  }
  return finishCloseReader();
}

async function finishCloseReader({ skipPurge = false } = {}) {
  const reader = $("reader");
  if (!reader || reader.classList.contains("hidden")) return;
  rememberReading();
  readerRegions?.clear();
  state.chatImages = [];
  state.chatUpload = null;
  $("reader-note-editor")?.remove();
  hideToolbar();
  closeFloater();
  window.getSelection()?.removeAllRanges();
  reader.classList.add("hidden");
  const closeToken = ++state.openToken;
  state.chatUnsubscribe?.();
  state.chatUnsubscribe = null;
  state.chatSession = null;
  state.compareOn = false;
  disposeSideDoc();
  const endedSession = state.readingSessionId;
  state.readingSessionId = '';
  state.chatBranch = '';
  state.chatBranches = [];
  state.chatQuote = '';
  state.sidePaperId = '';
  state.sideAnnotations = [];
  if (endedSession && !skipPurge) {
    api.del('/ai/reading/session', { json: { scope: `reading:${endedSession}` } }).catch(() => {});
  }
  await teardownDocument();
  if (state.openToken !== closeToken) return;
  state.paperId = null;
  state.paper = null;
  state.annotations = [];
  state.currentPage = 1;
  const title = $("reader-title");
  if (title) title.textContent = "论文";
}
