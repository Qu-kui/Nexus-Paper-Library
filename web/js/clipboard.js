/* 剪贴板监听提示条：复制 DOI / arXiv / 像标题的文字时，左下角问一句要不要入库。 */
import { api, toast, emit } from "./api.js";

const POLL_MS = 4000;
const KIND_LABEL = { doi: "DOI", arxiv: "arXiv", title: "标题" };

let current = null;
let busy = false;
let timer = null;
const dismissed = new Set();

function elements() {
  return {
    prompt: document.getElementById("clipboard-prompt"),
    kind: document.getElementById("cp-kind"),
    value: document.getElementById("cp-value"),
    hint: document.getElementById("cp-hint"),
    importBtn: document.getElementById("cp-import"),
    dismissBtn: document.getElementById("cp-dismiss"),
    closeBtn: document.getElementById("cp-close"),
  };
}

function render(item) {
  const el = elements();
  if (!el.prompt || !item) return;
  el.kind.textContent = KIND_LABEL[item.kind] || "论文";
  el.value.textContent = item.value;
  el.value.title = item.value;
  el.hint.textContent = item.kind === "title" ? "先按标题查元数据" : "回车即入库";
  el.prompt.classList.remove("hidden");
}

function hide() {
  current = null;
  elements().prompt?.classList.add("hidden");
}

async function accept() {
  if (!current || busy) return;
  const item = current;
  busy = true;
  const el = elements();
  if (el.importBtn) {
    el.importBtn.disabled = true;
    el.importBtn.textContent = "入库中…";
  }
  if (el.dismissBtn) el.dismissBtn.disabled = true;
  if (el.closeBtn) el.closeBtn.disabled = true;
  try {
    const result = await api.post(`/clipboard/${encodeURIComponent(item.id)}/accept`);
    const paper = result && result.paper ? result.paper : null;
    const title = paper && paper.title ? paper.title : item.value;
    toast(`已入库：${title}`, "success", 4200);
    if (result && result.pdf_fetch_queued) {
      toast(`已排进 PDF 队列：${result.pdf_fetch_queued} 篇`, "info", 3000);
    }
    hide();
    emit("library-changed");
  } catch (error) {
    toast(`入库失败：${error.message}`, "error", 5000);
    hide();
  } finally {
    busy = false;
    if (el.importBtn) {
      el.importBtn.disabled = false;
      el.importBtn.textContent = "入库";
    }
    if (el.dismissBtn) el.dismissBtn.disabled = false;
    if (el.closeBtn) el.closeBtn.disabled = false;
  }
}

async function dismiss() {
  if (!current || busy) return;
  const item = current;
  dismissed.add(item.id);
  if (dismissed.size > 64) dismissed.delete(dismissed.values().next().value);
  hide();
  try {
    await api.post(`/clipboard/${encodeURIComponent(item.id)}/dismiss`);
  } catch {
    /* 已经处理过就算了 */
  }
}

async function poll() {
  try {
    const data = await api.get("/clipboard/candidates");
    if (!busy) {
      const first = (data?.items || []).find(item => !dismissed.has(item.id));
      if (!first) {
        if (current) hide();
      } else if (!current || current.id !== first.id) {
        current = first;
        render(first);
      }
    }
  } catch {
    /* 服务没起来时静默 */
  }
  timer = setTimeout(poll, POLL_MS);
}

export function initClipboard() {
  const el = elements();
  if (!el.prompt) return;
  document.getElementById("cp-import")?.addEventListener("click", accept);
  document.getElementById("cp-dismiss")?.addEventListener("click", dismiss);
  document.getElementById("cp-close")?.addEventListener("click", dismiss);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && current && !busy && el.prompt.contains(event.target)) {
      event.preventDefault();
      event.stopImmediatePropagation();
      dismiss();
    }
  }, true);
  if (timer) clearTimeout(timer);
  poll();
}
