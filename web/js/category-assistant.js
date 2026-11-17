/* 分类助手：对话确认偏好 → 批量整理未分类论文 */
import { api, toast, escapeHtml, emit, md } from "./api.js";
import { getConversation } from './conversation.js';
import {renderProgress,captureProgress,restoreProgress,renderSessionControls,bindSessionControls,answerActions,handleAnswerAction} from './ai-feedback.js';
import { updateTaskBatch } from './task-center.js';

const byId = (id) => document.getElementById(id);
let initialized = false;
const session = getConversation('categories');
let currentGuide = "";
let polling = false;

function addBubble(role, text) {
  const box = byId("category-chat-messages");
  if (!box) return;
  const node = document.createElement("div");
  node.className = `chat-bubble ${role}`;
  if (role === "assistant") {
    node.classList.add("markdown");
    node.innerHTML = md(text);
  } else {
    node.textContent = text;
  }
  box.appendChild(node);
  box.scrollTop = box.scrollHeight;
}

function setGuide(guide) {
  currentGuide = String(guide || "").trim();
  const button = byId("btn-apply-categories");
  const allButton = byId("btn-apply-categories-all");
  const moveWrap = byId("category-move-wrap");
  const hint = byId("category-guide-hint");
  if (!button) return;
  if (currentGuide) {
    button.classList.remove("hidden");
    button.textContent = "按这套偏好整理未分类论文";
    allButton?.classList.remove("hidden");
    moveWrap?.classList.remove("hidden");
    if (hint) hint.textContent = "AI 已整理出分类偏好，确认后可批量归档。";
  } else {
    button.classList.add('hidden');
    allButton?.classList.add('hidden');
    moveWrap?.classList.add('hidden');
    if (hint && !polling) hint.textContent = '';
  }
}

async function send() {
  const input = byId("category-chat-input");
  const button = byId("category-chat-send");
  const text = String(input?.value || "").trim();
  if (!text || session.busy) return;
  const epoch = session.epoch;
  input.value = "";
  try {
    await session.post('/categories/assistant/chat', {messages:[{role:'user',content:text}]}, text);
  } catch (err) {
    if (epoch !== session.epoch) return;
    toast(`分类助手调用失败：${err.message}`, "error", 5000);
    if (!input.value) input.value = text;
  } finally {
    renderConversation();
  }
}

function renderConversation() {
  const box = byId('category-chat-messages');
  if (!box) return;
  const progressState=captureProgress(box),top=box.scrollTop,follow=box.scrollHeight-box.clientHeight-top<=24;
  box.replaceChildren();
  const messages = session.data?.messages || [];
  if (!messages.length && !session.pending) addBubble('assistant', '我会结合已保存的研究画像、分类树和偏好给建议。你这次想调整什么？');
  for (const [index,item] of messages.entries()) {
    addBubble(item.role, item.content);
    if (item.role==='assistant') box.lastElementChild.insertAdjacentHTML('beforeend',answerActions(index,item));
  }
  if (session.pending) {
    addBubble('user', session.pending);
    const progress=document.createElement('div');progress.className='chat-bubble assistant';progress.innerHTML=renderProgress(session.progress);box.appendChild(progress);
  }
  box.insertAdjacentHTML('beforeend',renderSessionControls(session));
  setGuide(session.data?.draft?.guide || '');
  if (currentGuide) addBubble('assistant', '【分类偏好草稿】\n'+currentGuide);
  restoreProgress(box,progressState);
  box.scrollTop=follow?box.scrollHeight:top;
  if (byId('category-chat-send')) byId('category-chat-send').disabled = session.busy || !session.data;
  if (byId('category-chat-send')) byId('category-chat-send').textContent = session.busy ? '思考中…' : '发送';
}

async function pollStatus() {
  if (polling) return;
  polling = true;
  const hint = byId("category-guide-hint");
  try {
    // 不限次数：整理跑在程序后端，弹窗关掉也在跑；进度同时写进任务中心。
    for (let index = 0; index < 7200; index += 1) {
      await new Promise((resolve) => setTimeout(resolve, 2000));
      const status = await api.get("/categories/assistant/status");
      updateTaskBatch("category", status);
      const active = (status.pending || 0) + (status.active || 0);
      if (hint) hint.textContent = `正在整理：完成 ${status.done || 0}，剩余 ${active}…（可以关掉这个窗口，进度在任务中心）`;
      if (active <= 0) {
        toast(`分类完成：成功 ${status.succeeded || 0} 篇，失败 ${status.failed || 0} 篇`, "success", 6000);
        emit("library-changed");
        if (hint) hint.textContent = "整理完成，可以关掉窗口查看分类树。";
        return;
      }
    }
  } catch (err) {
    toast(`查看分类进度失败：${err.message}`, "error", 4000);
  } finally {
    polling = false;
  }
}

async function resumeStatus() {
  // 重新打开弹窗 / 刷新页面时，接着显示还在跑的那批分类
  if (polling) return;
  try {
    const status = await api.get("/categories/assistant/status");
    if (!status.batch_id) return;
    updateTaskBatch("category", status);
    const active = (status.pending || 0) + (status.active || 0);
    if (active > 0) {
      const hint = byId("category-guide-hint");
      if (hint) hint.textContent = `正在整理：完成 ${status.done || 0}，剩余 ${active}…（后台在跑，进度在任务中心）`;
      pollStatus();
    }
  } catch {
    /* 离线/未配置时忽略 */
  }
}

async function apply(scope = "unclassified") {
  const allScope = scope === "all";
  const button = byId(allScope ? "btn-apply-categories-all" : "btn-apply-categories");
  const moveFiles = byId("category-move-files")?.checked !== false;
  if (allScope) {
    let total = "";
    try {
      const preview = await api.get("/categories/assistant/preview?scope=all");
      total = String(preview.total || preview.count || "");
    } catch {
      /* 拿不到数量也允许继续 */
    }
    const ok = window.confirm(
      `要对全部${total ? ` ${total} ` : " "}篇论文重新分类吗？\n` +
        `· 已有分类会被按当前偏好覆盖\n` +
        `· ${moveFiles ? "PDF 会移动到新的分类目录" : "只改分类，PDF 不移动"}\n` +
        `· 会逐篇调用 AI，篇数多时跑得久；可以关掉这个窗口，进度在「任务中心」里看`,
    );
    if (!ok) return;
  }
  if (button) {
    button.disabled = true;
    button.textContent = "开始整理…";
  }
  try {
    if (!session.data || session.busy || !currentGuide) return;
    const result = await api.post("/ai/conversations/categories/apply", {
      guide: currentGuide,
      scope: allScope ? "all" : "unclassified",
      move_files: moveFiles,
      revision: session.data.revision,
    });
    session.accept(result.conversation);
    if (!result.queued) {
      toast(allScope ? "没有可以重新分类的论文" : "没有未分类论文需要整理", "info", 3500);
      return;
    }
    toast(`已加入分类队列：${result.queued} 篇（进度见任务中心）`, "success", 3500);
    pollStatus();
  } catch (err) {
    toast(`整理失败：${err.message}`, "error", 5000);
    await session.load(true).catch(() => {});
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = allScope ? "对所有论文重新分类…" : "按这套偏好整理未分类论文";
    }
  }
}

export function initCategoryAssistant() {
  if (initialized) return;
  initialized = true;
  session.subscribe(renderConversation);
  bindSessionControls(byId('category-chat-messages'),session);
  byId('category-chat-messages')?.addEventListener('click',event=>handleAnswerAction(event,session.data?.messages||[]));
  byId("btn-category-assistant")?.addEventListener("click", () => {
    byId("category-assistant-modal")?.classList.remove("hidden");
    byId("category-chat-input")?.focus();
    session.load(true).catch(err => toast(`读取分类对话失败：${err.message}`, 'error'));
    resumeStatus();
  });
  byId("category-assistant-close")?.addEventListener("click", () => {
    byId("category-assistant-modal")?.classList.add("hidden");
  });
  byId("category-chat-send")?.addEventListener("click", send);
  byId("category-chat-input")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      send();
    }
  });
  byId("btn-apply-categories")?.addEventListener("click", () => apply("unclassified"));
  byId("btn-apply-categories-all")?.addEventListener("click", () => apply("all"));
  byId('category-chat-clear')?.addEventListener('click', async () => {
    try {
      await session.clear();
      if (byId('category-chat-input')) byId('category-chat-input').value = '';
    }
    catch (err) { toast(`清空失败：${err.message}`, 'error'); }
  });
}
