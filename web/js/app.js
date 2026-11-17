/* NPL 前端入口：导航、统计、模块初始化 */
import { initAccessibility } from "./accessibility.js";
import { initTaskCenter, updateTaskBatch } from "./task-center.js";
import { api, toast, state, emit, on } from "./api.js";
import { initLibrary, refreshLibrary } from "./library.js";
import { initImport } from "./import.js";
import { initSettings, refreshSettings } from "./settings.js";
import { initChat } from "./chat.js";
import { initCategories } from "./categories.js";
import { initDiscover } from "./discover.js";
import { initProfileChat } from "./profile-chat.js";
import { initCategoryAssistant } from "./category-assistant.js";
import { initScholar } from "./scholar.js";
import { initReader, openReader } from "./reader.js";
import { initMethods } from "./methods-page.js";
import { initTerms } from "./terms-page.js";
import { initResearch } from "./research-page.js";
import { initNewsfeed } from "./newsfeed-page.js";
import { initClipboard } from "./clipboard.js";
import { initReferenceExpand } from "./references.js";
import { initCitationStyles } from "./citation-styles.js";
import { applyAppearance, DEFAULT_THEME } from "./theme.js";
import { initOnboarding } from "./onboarding.js";
import { initAnswerVisuals } from "./answer-visuals.js";

// A decorative module must never prevent the actual application from booting.
const startup = window.__NPL_STARTUP__?.requested
  ? import('./startup.js').then(module => module.initStartup()).catch(error => {
    const launch = window.__NPL_STARTUP__;
    launch?.finish?.();
    clearTimeout(launch?.failsafe);
    delete document.documentElement.dataset.nplStartup;
    if (launch) launch.requested = false;
    console.warn('NPL startup animation unavailable', error);
    return {ready() {}};
  })
  : Promise.resolve({ready() {}});

/* ---------- 导航 ---------- */
export function goto(page) {
  document.querySelectorAll(".nav-item").forEach((btn) => {
    const active = btn.dataset.page === page;
    btn.classList.toggle("active", active);
    if (active) btn.setAttribute("aria-current", "page");
    else btn.removeAttribute("aria-current");
  });
  document.querySelectorAll(".page").forEach((section) => {
    section.classList.toggle("active", section.dataset.page === page);
  });
  if (page === "library") refreshLibrary();
  if (page === "chat") window.dispatchEvent(new CustomEvent("pw:chat-shown"));
  if (page === "methods") window.dispatchEvent(new CustomEvent("pw:methods-shown"));
  if (page === "terms") window.dispatchEvent(new CustomEvent("pw:terms-shown"));
  if (page === "research") window.dispatchEvent(new CustomEvent("pw:research-shown"));
  if (page === "news") window.dispatchEvent(new CustomEvent("pw:news-shown"));
  if (page === "discover") document.getElementById("discover-input")?.focus();
  emit('page-changed', {page});
}

document.querySelectorAll(".nav-item").forEach((btn) => {
  btn.addEventListener("click", () => goto(btn.dataset.page));
});
document.querySelectorAll("[data-goto]").forEach((btn) => {
  btn.addEventListener("click", () => goto(btn.dataset.goto));
});

document.getElementById("btn-quick-upload")?.addEventListener("click", () => {
  const input = document.getElementById("file-input");
  if (input) input.click();
});

/* ---------- 统计 ---------- */
export async function refreshStats() {
  try {
    const stats = await api.get("/stats");
    const set = (id, value) => { const el = document.getElementById(id); if (el) el.textContent = value; };
    set("stat-papers", stats.papers);
    set("stat-annotations", stats.annotations);
    set("stat-categories", stats.categories);
    set("missing-pdf-count", stats.missing_pdf || 0);
    state.stats = stats;
  } catch (err) {
    console.warn("stats failed", err);
  }
}

/* ---------- 模型状态 ---------- */
export async function refreshLlmPill() {
  const pill = document.getElementById("llm-pill");
  const text = document.getElementById("llm-pill-text");
  if (!pill || !text) return;
  try {
    const [settings, connection] = await Promise.all([api.get("/settings"),api.get("/settings/connection-status").catch(()=>({state:"untested"}))]);
    state.settings = settings;
    if (settings.ui_accent || settings.ui_theme) {
      applyAppearance({ theme: settings.ui_theme || DEFAULT_THEME, accent: settings.ui_accent });
    }
    pill.classList.remove("ok", "bad");
    if (connection.state === "success" || connection.state === "failed" || settings.api_key_configured || /localhost|127\.0\.0\.1/.test(settings.base_url || "")) {
      pill.classList.toggle("ok", connection.state === "success");
      pill.classList.toggle("bad", connection.state === "failed");
      text.textContent = `${settings.model || "未选模型"} · ${{untested:"已配置未测试",success:"最近连接成功",failed:"最近连接失败"}[connection.state] || "已配置未测试"}`;
      pill.title = connection.error || (connection.checked_at ? `测试时间：${connection.checked_at}` : "在设置中测试连接");
    } else {
      pill.classList.add("bad");
      text.textContent = "未配置 API Key";
    }
  } catch {
    pill.classList.add("bad");
    text.textContent = "服务未响应";
  }
}

/* ---------- 首次使用引导 ---------- */
async function maybeShowSetupBanner() {
  const banner = document.getElementById("setup-banner");
  if (!banner) return;
  if (localStorage.getItem("pw-setup-dismissed") === "1") return;
  try {
    const settings = state.settings || (await api.get("/settings"));
    state.settings = settings;
    const local = /localhost|127\.0\.0\.1/.test(settings.base_url || "");
    if (!settings.api_key_configured && !local) banner.classList.remove("hidden");
  } catch {
    /* 忽略 */
  }
}

document.getElementById("btn-setup-go")?.addEventListener("click", () => {
  document.getElementById("setup-banner")?.classList.add("hidden");
  goto("settings");
});
document.getElementById("btn-setup-dismiss")?.addEventListener("click", () => {
  document.getElementById("setup-banner")?.classList.add("hidden");
  localStorage.setItem("pw-setup-dismissed", "1");
});

/* ---------- 右下角任务进度 ---------- */
const jobProgress = { pdf: null, analyze: null, category: null, methods: null };

function renderJobProgress() {
  const widget = document.getElementById("job-progress");
  if (!widget) return;
  const rows = [];
  const addRow = (label, status) => {
    if (!status) return;
    if (!status.batch_id) return;
    const active = (status.pending || 0) + (status.active || 0);
    if (active <= 0) return;
    const total = Number(status.total) || 0;
    const percent = total ? Math.min(100, Math.round(((status.done || 0) / total) * 100)) : 0;
    const failed = status.failed ? ` · 失败 ${status.failed}` : "";
    rows.push(`<div class="job-row">
      <div class="job-row-head"><span>${label}</span><span>${status.done || 0} / ${total}${failed}</span></div>
      <div class="job-bar"><div class="job-bar-fill" style="width:${percent}%"></div></div>
    </div>`);
  };
  addRow("正在扒取 PDF", jobProgress.pdf);
  addRow("正在批量精读", jobProgress.analyze);
  addRow("正在批量分类", jobProgress.category);
  addRow("正在提取方法参数", jobProgress.methods);
  widget.innerHTML = rows.join("");
  widget.classList.toggle("hidden", rows.length === 0);
}

/* ---------- 自动补齐 PDF ---------- */
let pdfWasActive = false;

async function pollPdfFetch() {
  try {
    const status = await api.get("/pdf-fetch/status");
    jobProgress.pdf = status;
    updateTaskBatch("pdf",status);
    renderJobProgress();
    const active = (status.pending || 0) + (status.active || 0);
    if (active > 0 && !pdfWasActive) {
      pdfWasActive = true;
      toast("正在后台为没有全文的论文找 PDF…", "info", 2600);
    }
    if (pdfWasActive) {
      await Promise.allSettled([refreshLibrary(), refreshStats()]);
    }
    if (active === 0 && pdfWasActive) {
      pdfWasActive = false;
      await Promise.allSettled([refreshLibrary(), refreshStats()]);
      const failed = status.failed || 0;
      toast(
        `PDF 补齐完成：成功 ${status.succeeded || 0} 篇` + (failed ? `，失败 ${failed} 篇` : ""),
        failed ? "info" : "success",
        5000,
      );
    }
  } catch {
    /* 忽略轮询错误 */
  }
  setTimeout(pollPdfFetch, 2500);
}

document.getElementById("btn-fetch-pdfs")?.addEventListener("click", async () => {
  try {
    const result = await api.post("/pdf-fetch/start", { all_missing: true });
    if (result.queued) {
      jobProgress.pdf = result.status || null;
      renderJobProgress();
      toast(`已加入抓取队列：${result.queued} 篇`, "success");
    } else {
      toast("所有论文都已经有 PDF 了", "info");
    }
  } catch (err) {
    toast(`启动失败：${err.message}`, "error");
  }
});

document.getElementById("btn-analyze-all")?.addEventListener("click", async () => {
  const button = document.getElementById("btn-analyze-all");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "排队中…";
  try {
    let result = await api.post("/ai/analyze-batch", { only_new: true });
    if (!result.queued) {
      const total = Number(result.total_with_pdf) || 0;
      if (!total) {
        toast("还没有带 PDF 的论文，先导入或补齐 PDF", "info", 4000);
        return;
      }
      const confirmed = window.confirm(
        `这 ${total} 篇论文都已经精读过了。\n\n要全部重新精读吗？会重新生成摘要、分类建议和标签，可能消耗较多 token。`,
      );
      if (!confirmed) return;
      result = await api.post("/ai/analyze-batch", { only_new: false });
    }
    if (result.queued) {
      jobProgress.analyze = result.status || null;
      renderJobProgress();
      toast(`已加入精读队列：${result.queued} 篇`, "success");
    } else {
      toast("没有可以精读的论文（可能都缺 PDF）", "info");
    }
  } catch (err) {
    toast(`一键精读失败：${err.message}`, "error", 5000);
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
});

let analyzeWasActive = false;

async function pollAnalyze() {
  try {
    const status = await api.get("/ai/analyze-status");
    jobProgress.analyze = status;
    updateTaskBatch("analyze",status);
    renderJobProgress();
    const active = (status.pending || 0) + (status.active || 0);
    if (active > 0 && !analyzeWasActive) {
      analyzeWasActive = true;
      toast("AI 正在批量精读论文…", "info", 2600);
    }
    if (analyzeWasActive && active > 0) {
      await Promise.allSettled([refreshLibrary(), refreshStats()]);
    }
    if (active === 0 && analyzeWasActive) {
      analyzeWasActive = false;
      await Promise.allSettled([refreshLibrary(), refreshStats()]);
      toast(
        `精读完成：成功 ${status.succeeded || 0} 篇` +
          (status.failed ? `，失败 ${status.failed} 篇` : ""),
        status.failed ? "info" : "success",
        5000,
      );
    }
  } catch {
    /* 忽略 */
  }
  setTimeout(pollAnalyze, 3000);
}

/* ---------- 后台批量分类（任务中心里也能看到） ---------- */
let categoryWasActive = false;

async function pollCategories() {
  try {
    const status = await api.get("/categories/assistant/status");
    const active = (status.pending || 0) + (status.active || 0);
    jobProgress.category = status.batch_id ? status : null;
    if (status.batch_id) updateTaskBatch("category", status);
    renderJobProgress();
    if (active > 0 && !categoryWasActive) categoryWasActive = true;
    if (categoryWasActive && active === 0) {
      categoryWasActive = false;
      await Promise.allSettled([refreshLibrary(), refreshStats()]);
      const failed = status.failed || 0;
      toast(
        `分类完成：成功 ${status.succeeded || 0} 篇` + (failed ? `，失败 ${failed} 篇` : ""),
        failed ? "info" : "success",
        5000,
      );
    }
  } catch {
    /* 忽略轮询错误 */
  }
  setTimeout(pollCategories, 4000);
}

/* ---------- 方法参数 / 复习卡片批次（任务中心里也能看到） ---------- */
const featureWasActive = { methods: false };

async function pollFeatures() {
  for (const [kind, path, label] of [
    ["methods", "/methods/status", "方法参数"],
  ]) {
    try {
      const status = await api.get(path);
      jobProgress[kind] = status.batch_id ? status : null;
      if (status.batch_id) updateTaskBatch(kind, status);
      renderJobProgress();
      const active = (status.pending || 0) + (status.active || 0);
      if (active > 0) featureWasActive[kind] = true;
      if (featureWasActive[kind] && active === 0) {
        featureWasActive[kind] = false;
        const failed = status.failed || 0;
        toast(
          `${label}完成：成功 ${status.succeeded || 0} 篇` + (failed ? `，失败 ${failed} 篇` : ""),
          failed ? "info" : "success",
          5000,
        );
        emit("library-changed");
      }
    } catch {
      /* 后端未就绪时忽略 */
    }
  }
  setTimeout(pollFeatures, 5000);
}

/* ---------- 初始化 ---------- */
async function boot() {
  initAnswerVisuals();
  initAccessibility();
  initTaskCenter();
  initLibrary();
  initImport();
  initSettings();
  initChat();
  initDiscover();
  initCategories();
  initCategoryAssistant();
  initScholar();
  initProfileChat();
  initReader();
  initMethods();
  initTerms();
  initResearch();
  initNewsfeed();
  initClipboard();
  initReferenceExpand();
  initCitationStyles();

  window.PW.goto = goto;
  window.PW.openPaper = openReader;
  window.PW.refreshStats = refreshStats;
  window.PW.refreshLlmPill = refreshLlmPill;

  await Promise.allSettled([refreshStats(), refreshLibrary(), refreshSettings(), refreshLlmPill()]);
  maybeShowSetupBanner();
  pollPdfFetch();
  pollAnalyze();
  pollCategories();
  pollFeatures();

  on("library-changed", async () => {
    await Promise.allSettled([refreshStats(), refreshLibrary()]);
  });
  on("settings-changed", async () => {
    await refreshLlmPill();
    state.settings = await api.get("/settings").catch(() => state.settings);
    if (state.settings?.ui_accent || state.settings?.ui_theme) {
      applyAppearance({ theme: state.settings.ui_theme || DEFAULT_THEME, accent: state.settings.ui_accent });
    }
    if (state.settings?.api_key_configured) {
      document.getElementById("setup-banner")?.classList.add("hidden");
    }
  });

  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      const reader = document.getElementById("reader");
      if (reader && !reader.classList.contains("hidden")) {
        document.getElementById("reader-close")?.click();
      }
    }
  });

  // 深链：#paper/<id> 直接打开阅读器（方便分享/调试）
  const deep = (location.hash || "").match(/^#paper\/([a-f0-9]{32})$/i);
  if (deep) {
    openReader(deep[1]).catch((err) => toast(`打开论文失败：${err.message}`, "error"));
  }

  initOnboarding({goto});

  console.log("%cNPL ready", "color:#7855e8;font-weight:bold");
}

boot().catch((err) => {
  console.error(err);
  toast(`初始化失败：${err.message}`, "error", 6000);
}).finally(() => startup.then(controller => controller.ready()));
