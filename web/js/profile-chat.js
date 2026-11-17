/* 研究画像 / 通用 AI 对话：联网搜索、文件、网址、深度思考、Markdown、复制、清空 */
import { api, toast, escapeHtml, md } from "./api.js";
import { getConversation } from './conversation.js';
import {renderSources,renderProgress,captureProgress,restoreProgress,renderSessionControls,bindSessionControls} from './ai-feedback.js';
import {thinkingPanelHtml} from './thinking-panel.js';

const byId = (id) => document.getElementById(id);
const STORAGE_KEY = "pw.profile.chat.v1";
const GREETING = {
  role: "assistant",
  content:
    "你好，我是 NPL 的 AI 助手。可以聊研究方向、贴课题组网站链接、上传论文（PDF/Word），默认联网并深入分析；思考过程会展开显示，出正文后自动收成一行「已思考（用时 N 秒）」，点一下还能展开。",
};

let initialized = false;
const session = getConversation('profile');
let messages = [GREETING];
let attachments = [];
let pendingProfile = "";
let opening = false;

function syncConversation() {
  messages = session.data?.messages?.length ? [...session.data.messages] : [GREETING];
  attachments = session.data?.attachments || [];
  pendingProfile = session.data?.draft?.profile || '';
  if (session.pending) messages.push({role:'user',content:session.pending}, {role:'assistant',content:'',pending:true});
  byId('profile-chat-apply')?.classList.toggle('hidden', !pendingProfile || session.busy);
  if (byId('profile-chat-send')) byId('profile-chat-send').disabled = opening || session.busy || !session.data;
  if (byId('profile-chat-send')) byId('profile-chat-send').textContent = session.busy ? '思考中…' : '发送';
  if (byId('profile-chat-attach')) byId('profile-chat-attach').disabled = opening || session.busy || !session.data;
  renderMessages();
  renderAttachments();
}

function reasoningHtml(reasoning, thinkingSeconds = 0, webTrace = []) {
  const trace = Array.isArray(webTrace) ? webTrace : [];
  if (!reasoning && !trace.length) return "";
  return thinkingPanelHtml({text: reasoning, state: 'done', thinkingSeconds, trace});
}

function renderMessages() {
  const wrap = byId("profile-chat-messages");
  if (!wrap) return;
  const progressState=captureProgress(wrap),top=wrap.scrollTop,follow=wrap.scrollHeight-wrap.clientHeight-top<=24;
  wrap.innerHTML = messages
    .map((item, index) => {
      const isUser = item.role === "user";
      const body = item.pending ? renderProgress(session.progress) : isUser ? escapeHtml(item.content || "").replace(/\n/g, "<br>") : md(item.content || "");
      const copy = isUser || item.pending ? "" : `<button type="button" class="profile-copy" data-copy="${index}">复制</button>`;
      return `<div class="profile-msg ${isUser ? "user" : ""}">${isUser ? "" : reasoningHtml(item.reasoning)}${body}${renderSources(item.sources || [],item.retrieval_candidates || [])}${copy}</div>`;
    })
    .join("") + renderSessionControls(session);
  restoreProgress(wrap,progressState);
  wrap.scrollTop=follow?wrap.scrollHeight:top;
}

function renderAttachments() {
  const box = byId("profile-chat-attachments");
  if (!box) return;
  box.innerHTML = attachments
    .map(
      (item, index) =>
        `<span class="profile-file-chip">📄 ${escapeHtml(item.name)}<button type="button" data-remove-file="${index}" title="移除">✕</button></span>`,
    )
    .join("");
}

async function uploadFiles(fileList) {
  if (session.busy) return;
  const epoch = session.epoch;
  const files = Array.from(fileList || []);
  if (!files.length) return;
  const hint = byId("profile-chat-file-hint");
  const button = byId("profile-chat-attach");
  button.disabled = true;
  for (const file of files) {
    if (file.size > 25 * 1024 * 1024) {
      toast(`${file.name} 超过 25MB，跳过`, "error", 4500);
      continue;
    }
    if (hint) hint.textContent = `正在读取 ${file.name}…`;
    try {
      const result = await api.uploadFile("/ai/profile/upload", file, "file");
      if (epoch !== session.epoch) break;
      await session.load();
      await session.materials([...session.data.attachments, {name:result.name || file.name, text:String(result.text || '').slice(0,30000)}]);
      toast(`已读取：${result.name || file.name}（${result.chars || 0} 字）`, "success", 3500);
    } catch (err) {
      toast(`读取 ${file.name} 失败：${err.message}`, "error", 5500);
    }
  }
  syncConversation();
  if (hint) hint.textContent = "支持 PDF / Word(.docx) / TXT";
}

async function send() {
  const input = byId("profile-chat-input");
  const text = String((input && input.value) || "").trim();
  if (!text || session.busy || opening) return;
  const epoch = session.epoch;
  input.value = "";
  try {
    await session.post("/ai/profile-chat", {
      messages: [{role:'user',content:text}],
      search_web: true,
      deep_thinking: true,
    }, text);
  } catch (err) {
    if (epoch !== session.epoch) return;
    toast(`对话失败：${err.message}`, "error", 6000);
    if (!input.value) input.value = text;
  } finally {
    syncConversation();
  }
}

async function clearChat() {
  try {
    await session.clear();
    if (byId('profile-chat-input')) byId('profile-chat-input').value = '';
  }
  catch (err) { toast(`清空失败：${err.message}`, 'error'); }
}

async function applyProfile() {
  if (!pendingProfile || session.busy) return;
  try {
      const result = await api.post('/ai/conversations/profile/apply', {revision:session.data.revision});
      session.accept(result.conversation);
      const textarea = byId('set-profile');
      if (textarea) {
        textarea.value = result.profile;
        textarea.dispatchEvent(new Event('input', {bubbles:true}));
      }
      toast("画像已填入并保存", "success");
      byId("profile-chat-modal")?.classList.add("hidden");
      byId("btn-profile-ai")?.focus();
  } catch (err) {
    toast(`保存失败：${err.message}`, 'error');
    await session.load(true).catch(() => {});
  }
}

async function openModal() {
  const modal = byId("profile-chat-modal");
  if (!modal || opening) return;
  opening = true;
  modal.classList.remove("hidden");
  syncConversation();
  try {
    await session.load(true);
    if (!session.data.migrated) {
      let legacy = [];
      try { legacy = JSON.parse(localStorage.getItem(STORAGE_KEY) || '[]'); } catch {}
      const imported = await api.post('/ai/conversations/profile/migrate', {messages:Array.isArray(legacy) ? legacy.slice(-40) : []});
      session.accept(imported);
      localStorage.removeItem(STORAGE_KEY);
    }
  } catch (err) { toast(`读取画像对话失败：${err.message}`, 'error'); }
  if (!session.data?.messages?.length) {
    try {
      const existing = await api.get("/ai/profile");
      const profile = String(existing?.profile || "").trim();
      if (profile) {
        GREETING.content =
              "我已经读到你保存的研究画像：\n\n" +
              profile +
              "\n\n想改哪一部分？直接说，我会在原文基础上更新；也可以上传新论文或贴课题组主页让我重新提炼。";
      }
    } catch (err) {
      /* 读取画像失败就保持默认问候 */
    }
  }
  opening = false;
  syncConversation();
  byId("profile-chat-input")?.focus();
}

export function initProfileChat() {
  if (initialized) return;
  initialized = true;
  session.subscribe(syncConversation);
  bindSessionControls(byId('profile-chat-messages'),session);
  byId('profile-chat-messages')?.addEventListener('click',event=>{const source=event.target.closest('.src-item');if(source)window.PW?.openPaper?.(source.dataset.paperId,Number(source.dataset.page)||1);});
  byId("btn-profile-ai")?.addEventListener("click", openModal);
  byId("profile-chat-close")?.addEventListener("click", () => byId("profile-chat-modal")?.classList.add("hidden"));
  byId("profile-chat-send")?.addEventListener("click", send);
  byId("profile-chat-apply")?.addEventListener("click", applyProfile);
  byId("profile-chat-clear")?.addEventListener("click", clearChat);
  byId("profile-chat-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  byId("profile-chat-attach")?.addEventListener("click", () => byId("profile-chat-file")?.click());
  byId("profile-chat-file")?.addEventListener("change", (event) => {
    uploadFiles(event.target.files);
    event.target.value = "";
  });
  byId("profile-chat-attachments")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-remove-file]");
    if (!button) return;
    if (session.busy || !session.data) return;
    try { await session.materials(session.data.attachments.filter((_,i) => i !== Number(button.dataset.removeFile))); }
    catch (err) { toast(`移除失败：${err.message}`, 'error'); }
  });
  byId("profile-chat-messages")?.addEventListener("click", async (event) => {
    const button = event.target.closest("[data-copy]");
    if (!button) return;
    const item = messages[Number(button.dataset.copy)];
    if (!item) return;
    try {
      await navigator.clipboard.writeText(item.content || "");
      toast("已复制", "success", 1500);
    } catch (err) {
      toast("复制失败，请手动选中", "error");
    }
  });
}
