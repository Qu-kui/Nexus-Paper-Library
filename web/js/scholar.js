/* 谷歌学术邮件订阅 */
import { api, toast, escapeHtml } from "./api.js";

const byId = (id) => document.getElementById(id);
let initialized = false;
let state = { configured: false, items: [] };

function formatLocalTime(value) {
  if (!value) return "还没同步";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 16);
  const pad = (number) => String(number).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

function render() {
  const box = byId("scholar-items");
  const sub = byId("scholar-sub");
  if (!box) return;
  if (sub) {
    sub.textContent = state.configured
      ? `已连接 ${state.email || "邮箱"}，上次同步：${state.last_sync_display || formatLocalTime(state.last_sync)} · 未处理 ${state.unread ?? 0} 条`
      : "连接邮箱后，每天把 Google Scholar 提醒同步到这里，可以阅读或加入文献库。";
  }
  const items = (Array.isArray(state.items) ? state.items : []).filter((item) => item.status === "new");
  if (!state.configured) {
    box.innerHTML = `<div class="scholar-empty">还没有配置邮箱。点右上角「账号设置」，填入学校/个人邮箱和 IMAP 授权码。</div>`;
    return;
  }
  if (!items.length) {
    box.innerHTML = `<div class="scholar-empty">没有未处理的提醒。已加入文献库的会自动隐藏。</div>`;
    return;
  }
  box.innerHTML = items
    .map((item) => {
      const saved = item.status === "saved";
      const meta = [item.authors, item.venue, item.published].filter(Boolean).join(" · ");
      const url = /^https?:\/\//i.test(item.url || "") ? item.url : "";
      const titleZh = String(item.title_zh || "").trim();
      const snippetZh = String(item.snippet_zh || "").trim();
      const titleHtml = titleZh
        ? `<h4>${escapeHtml(titleZh)}</h4><div class="scholar-title-en">${escapeHtml(item.title || "")}</div>`
        : `<h4>${escapeHtml(item.title || "未命名")}</h4>`;
      const snippetHtml = snippetZh
        ? `<p>${escapeHtml(snippetZh)}</p>`
        : item.snippet ? `<p>${escapeHtml(item.snippet)}</p>` : "";
      return `<article class="scholar-item${saved ? " saved" : ""}" data-id="${escapeHtml(item.id)}">
        ${titleHtml}
        <div class="scholar-meta">${escapeHtml(meta || "谷歌学术")}</div>
        ${snippetHtml}
        <div class="scholar-item-actions">
          ${url ? `<a class="btn tiny" href="${escapeHtml(url)}" target="_blank" rel="noreferrer">阅读原文</a>` : ""}
          ${saved ? `<span class="muted small">已加入文献库</span>` : `<button class="btn primary tiny" data-scholar-add="${escapeHtml(item.id)}">加入文献库</button>`}
          <button class="btn tiny ghost" data-scholar-ignore="${escapeHtml(item.id)}">忽略</button>
        </div>
      </article>`;
    })
    .join("");
}

async function load() {
  try {
    state = await api.get("/scholar");
    fillConfig(state);
    render();
  } catch (err) {
    toast(`读取谷歌学术订阅失败：${err.message}`, "error", 5000);
  }
}

function fillConfig(data) {
  if (!data) return;
  if (byId("scholar-email")) byId("scholar-email").value = data.email || "";
  if (byId("scholar-host")) byId("scholar-host").value = data.imap_host || "imap.gmail.com";
  if (byId("scholar-folder")) byId("scholar-folder").value = data.folder || "INBOX";
  if (byId("scholar-auto")) byId("scholar-auto").checked = Boolean(data.auto_sync);
  const password = byId("scholar-password");
  if (password) {
    password.value = "";
    password.placeholder = data.configured
      ? "已保存（出于安全不显示；要修改请重新输入授权码）"
      : "Gmail 用应用专用密码；QQ/163 用授权码";
  }
}

async function saveConfig() {
  const payload = {
    email: byId("scholar-email")?.value.trim() || "",
    imap_host: byId("scholar-host")?.value.trim() || "",
    password: byId("scholar-password")?.value || "",
    folder: byId("scholar-folder")?.value.trim() || "INBOX",
    auto_sync: Boolean(byId("scholar-auto")?.checked),
  };
  const hint = byId("scholar-config-hint");
  if (!payload.email) {
    toast("先填邮箱", "info");
    return;
  }
  try {
    if (hint) hint.textContent = "保存中…";
    await api.put("/scholar/config", payload);
    if (hint) hint.textContent = "正在测试连接…";
    await api.post("/scholar/test");
    if (hint) hint.textContent = "连接成功，正在同步提醒邮件…";
    toast("邮箱连接成功，正在同步提醒", "success", 3500);
    await load();
    await syncNow();
    if (hint) hint.textContent = "连接成功，已同步；授权码不会回显";
  } catch (err) {
    if (hint) hint.textContent = "";
    toast(`保存/连接失败：${err.message}`, "error", 6000);
  }
}

async function syncNow() {
  if (!state.configured) {
    setConfigOpen(true);
    byId("scholar-email")?.focus();
    toast("先配置邮箱", "info");
    return;
  }
  const button = byId("btn-scholar-sync");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "同步中…";
  try {
    const result = await api.post("/scholar/sync");
    state.items = result.items || [];
    await load();
    if (!result.added) {
      toast("连接正常，但邮箱里暂时没有新的 Scholar 提醒", "info", 5000);
    } else {
      toast(`同步完成：新增 ${result.added || 0} 条，翻译 ${result.translated || 0} 条`, "success", 4500);
    }
  } catch (err) {
    toast(`同步失败：${err.message}`, "error", 6000);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function addItem(itemId) {
  try {
    await api.post(`/scholar/items/${encodeURIComponent(itemId)}/add`);
    toast("已加入文献库，正在尝试补 PDF", "success", 4000);
    await load();
  } catch (err) {
    toast(`加入失败：${err.message}`, "error", 5000);
  }
}

async function ignoreItem(itemId) {
  try {
    await api.post(`/scholar/items/${encodeURIComponent(itemId)}/ignore`);
    state.items = (state.items || []).filter((item) => item.id !== itemId);
    render();
  } catch (err) {
    toast(`忽略失败：${err.message}`, "error", 4000);
  }
}

function setConfigOpen(open) {
  byId("scholar-config")?.classList.toggle("hidden", !open);
  byId("btn-scholar-config")?.setAttribute("aria-expanded", String(open));
}

export function initScholar() {
  if (initialized) return;
  initialized = true;
  byId("btn-scholar-config")?.setAttribute("aria-controls", "scholar-config");
  setConfigOpen(false);
  byId("btn-scholar-sync")?.addEventListener("click", syncNow);
  byId("btn-scholar-config")?.addEventListener("click", () => {
    const open = byId("scholar-config")?.classList.contains("hidden");
    setConfigOpen(open);
    if (open) byId("scholar-email")?.focus();
  });
  byId("btn-scholar-save")?.addEventListener("click", saveConfig);
  byId("scholar-items")?.addEventListener("click", (event) => {
    const add = event.target.closest("[data-scholar-add]");
    const ignore = event.target.closest("[data-scholar-ignore]");
    if (add) addItem(add.dataset.scholarAdd);
    else if (ignore) ignoreItem(ignore.dataset.scholarIgnore);
  });
  load();
}
