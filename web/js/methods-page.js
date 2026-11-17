/* 方法 / 参数页：把已提取的实验条件、器件与材料参数汇总成可检索清单。
 *
 * 后端契约（paperwhale_server/routers/methods.py，字段名以契约为准）：
 *   GET  /api/methods?q=&state=all|done|pending
 *   GET  /api/methods/status
 *   POST /api/methods/extract {paper_ids?: [], only_new: true}
 *
 * 轮询只用递归 setTimeout 并显式清理：页面切走就停，避免切页后叠加多个定时器。
 */
import { api, toast, escapeHtml, on } from "./api.js";

const POLL_MS = 3000;
const SEARCH_DEBOUNCE_MS = 350;

let booted = false;
let listRequest = 0;
let pollTimer = null;
let polling = false;
let searchTimer = null;
let refreshQueued = false;

const $ = (id) => document.getElementById(id);

function pageRoot() {
  return document.querySelector('.page[data-page="methods"]');
}

function isActive() {
  const root = pageRoot();
  return Boolean(root && root.classList.contains("active"));
}

function openPaper(paperId) {
  if (!paperId) return;
  try {
    window.PW?.openPaper?.(paperId);
  } catch {
    /* 阅读器未就绪时保持列表可用 */
  }
}

function setStatus(text) {
  const node = $("methods-status");
  if (node) node.textContent = text || "";
}

/* ---------- 渲染 ---------- */

function chipHtml(param) {
  if (!param || typeof param !== "object") return "";
  const name = param.name ? escapeHtml(String(param.name)) : "";
  if (!name) return "";
  const value = param.value === 0 || param.value ? escapeHtml(String(param.value)) : "";
  const unit = param.unit ? escapeHtml(String(param.unit)) : "";
  const note = param.note ? ` title="${escapeHtml(String(param.note))}"` : "";
  return `<span class="method-chip"${note}><b>${name}</b>${value ? ` ${value}` : ""}${unit ? `<i>${unit}</i>` : ""}</span>`;
}

function tagText(entry) {
  if (entry === null || entry === undefined) return "";
  if (typeof entry === "string") return entry;
  return entry.name || "";
}

function tagGroup(label, values) {
  const names = (Array.isArray(values) ? values : []).map(tagText).filter(Boolean);
  if (!names.length) return "";
  const tags = names.map((name) => `<span class="method-tag">${escapeHtml(String(name))}</span>`).join("");
  return `<span class="method-tag-group"><span class="method-tag-label">${label}</span>${tags}</span>`;
}

function isExtracted(item) {
  if (!item) return false;
  if (item.status === "done") return true;
  if (item.method_summary) return true;
  return Array.isArray(item.parameters) && item.parameters.length > 0;
}

function renderCard(item) {
  const paperId = String(item.paper_id || "");
  const title = escapeHtml(item.title || "（未命名论文）");
  const meta = [item.venue, item.year].map((value) => (value ? escapeHtml(String(value)) : "")).filter(Boolean).join(" · ");
  const extracted = isExtracted(item);
  if (!extracted) {
    return `<article class="method-card method-pending" data-paper="${escapeHtml(paperId)}">
      <div class="method-head">
        <div class="method-pending-info"><button class="method-title" type="button" data-open-paper="${escapeHtml(paperId)}">${title}</button>
        <span class="method-meta small">${meta}</span></div>
        <span class="method-badge">${item.error ? "提取失败" : "待提取"}</span>
        <button class="btn ghost small" type="button" data-extract-paper="${escapeHtml(paperId)}">${item.error ? "重试" : "提取"}</button>
      </div>
      ${item.error ? `<p class="method-error small">${escapeHtml(String(item.error))}</p>` : ""}
    </article>`;
  }
  const chips = (Array.isArray(item.parameters) ? item.parameters : []).map(chipHtml).join("");
  const tags = [
    tagGroup("材料", item.materials),
    tagGroup("设备", item.equipment),
    tagGroup("条件", item.conditions),
  ].join("");
  const summary = extracted
    ? `<p class="method-summary">${escapeHtml(item.method_summary || "（未记录方法摘要）")}</p>`
    : `<p class="method-summary muted">还没有提取方法参数。</p>`;
  const error = item.error
    ? `<p class="method-error small">提取失败：${escapeHtml(String(item.error))}</p>`
    : "";
  const action = extracted
    ? ""
    : `<div class="method-actions"><button class="btn ghost small" type="button" data-extract-paper="${escapeHtml(paperId)}">提取</button></div>`;
  return `<article class="method-card" data-paper="${escapeHtml(paperId)}">
    <div class="method-head">
      <button class="method-title" type="button" data-open-paper="${escapeHtml(paperId)}">${title}</button>
      <span class="method-meta small">${meta}</span>
      <span class="method-badge${extracted ? " done" : ""}">${extracted ? "已提取" : "待提取"}</span>
    </div>
    ${summary}
    ${chips ? `<div class="method-chips">${chips}</div>` : ""}
    ${tags ? `<div class="method-tags">${tags}</div>` : ""}
    ${error}
    ${action}
  </article>`;
}

function renderList(data) {
  const host = $("methods-list");
  if (!host) return;
  const items = Array.isArray(data?.items) ? data.items : [];
  if (!items.length) {
    renderEmpty(host);
    return;
  }
  host.innerHTML = items.map(renderCard).join("");
  host.querySelectorAll("[data-open-paper]").forEach((node) => {
    node.addEventListener("click", () => openPaper(node.dataset.openPaper));
  });
  host.querySelectorAll("[data-extract-paper]").forEach((node) => {
    node.addEventListener("click", () => extractOne(node.dataset.extractPaper, node));
  });
}

function renderEmpty(host) {
  const keyword = ($("methods-search")?.value || "").trim();
  const filter = $("methods-state")?.value || "all";
  if (keyword || filter !== "all") {
    host.innerHTML = `<div class="empty"><h3>没有匹配的论文</h3><p>换个关键词，或把筛选切回「全部论文」。</p></div>`;
    return;
  }
  host.innerHTML = `<div class="empty">
    <h3>还没有论文可以提取</h3>
    <p>先去文献库导入 PDF，再回来点「提取全部」，方法参数会汇总到这里。</p>
    <button class="btn primary" type="button" data-goto-library>去文献库</button>
  </div>`;
  host.querySelector("[data-goto-library]")?.addEventListener("click", () => {
    try {
      window.PW?.goto?.("library");
    } catch {
      /* 导航不可用时保持当前页 */
    }
  });
}

/* ---------- 数据 ---------- */

async function loadList() {
  const host = $("methods-list");
  if (!host) return;
  const requestId = ++listRequest;
  const stateFilter = $("methods-state")?.value || "all";
  const keyword = ($("methods-search")?.value || "").trim();
  let path = `/methods?state=${encodeURIComponent(stateFilter)}`;
  if (keyword) path += `&q=${encodeURIComponent(keyword)}`;
  try {
    const data = await api.get(path);
    if (requestId !== listRequest) return;
    renderList(data);
  } catch (err) {
    if (requestId !== listRequest) return;
    host.innerHTML = `<div class="empty"><h3>方法参数暂时取不到</h3><p>${escapeHtml(err.message || "请稍后再试")}</p></div>`;
  }
}

function renderBatchStatus(status) {
  if (!status || !status.batch_id) {
    setStatus("");
    return;
  }
  const done = status.done || 0;
  const total = status.total || 0;
  const failed = status.failed || 0;
  setStatus(`完成 ${done} / 总 ${total} · 失败 ${failed}`);
}

async function syncStatus() {
  try {
    const status = await api.get("/methods/status");
    renderBatchStatus(status);
    const active = (status?.pending || 0) + (status?.active || 0);
    if (status?.batch_id && active > 0) startPolling();
    return status;
  } catch {
    return null;
  }
}

/* ---------- 提取任务 ---------- */

async function queueExtract(body, label) {
  try {
    const data = await api.post("/methods/extract", body);
    const queued = data && typeof data.queued === "number" ? data.queued : 0;
    toast(`${label}：已排队 ${queued} 篇`, "success");
    setStatus("已提交提取任务，正在处理…");
    syncStatus();
    startPolling();
  } catch (err) {
    toast(err.message || "提交提取任务失败", "error");
  }
}

function extractAll() {
  queueExtract({ only_new: true }, "提取全部");
}

function extractOne(paperId, button) {
  if (!paperId) return;
  if (button) {
    button.disabled = true;
    button.textContent = "已排队";
  }
  queueExtract({ paper_ids: [paperId] }, "提取这篇");
}

/* ---------- 轮询（只在方法页可见时运行） ---------- */

function stopPolling() {
  polling = false;
  if (pollTimer) {
    clearTimeout(pollTimer);
    pollTimer = null;
  }
}

function startPolling() {
  if (polling || !isActive()) return;
  polling = true;
  pollTimer = setTimeout(pollOnce, POLL_MS);
}

async function pollOnce() {
  pollTimer = null;
  if (!polling) return;
  if (!isActive()) {
    stopPolling();
    return;
  }
  let status = null;
  try {
    status = await api.get("/methods/status");
  } catch {
    /* 网络抖动时继续等下一轮 */
  }
  if (!polling) return;
  if (!isActive()) {
    stopPolling();
    return;
  }
  if (!status) {
    pollTimer = setTimeout(pollOnce, POLL_MS);
    return;
  }
  renderBatchStatus(status);
  const active = (status.pending || 0) + (status.active || 0);
  if (active > 0) {
    pollTimer = setTimeout(pollOnce, POLL_MS);
    return;
  }
  stopPolling();
  const failed = status.failed || 0;
  toast(
    `方法参数提取完成：成功 ${status.succeeded || 0} 篇` + (failed ? `，失败 ${failed} 篇` : ""),
    failed ? "info" : "success",
    5000,
  );
  await loadList();
}

/* ---------- 初始化 ---------- */

function refresh() {
  if (refreshQueued) return;
  refreshQueued = true;
  setTimeout(async () => {
    refreshQueued = false;
    if (!isActive()) return;
    await loadList();
    await syncStatus();
  }, 0);
}

export function initMethods() {
  if (booted) return;
  booted = true;

  $("btn-methods-refresh")?.addEventListener("click", () => {
    loadList();
    syncStatus();
  });
  $("btn-methods-extract")?.addEventListener("click", extractAll);
  $("methods-state")?.addEventListener("change", () => loadList());

  const search = $("methods-search");
  if (search) {
    search.addEventListener("input", () => {
      clearTimeout(searchTimer);
      searchTimer = setTimeout(loadList, SEARCH_DEBOUNCE_MS);
    });
    search.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      clearTimeout(searchTimer);
      loadList();
    });
  }

  const shown = () => refresh();
  // app.js 目前向 window 派发；契约写的是 document，两处都监听，refresh 自带合并。
  on("methods-shown", shown);
  window.addEventListener("pw:methods-shown", shown);
}
