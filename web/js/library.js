/* NPL · 文献库页
 * 卡片列表 / 搜索 / 分类·标签·排序筛选 / 精读 / 删除
 * 依赖：api.js（api、toast、escapeHtml、shortAuthors、state、emit）
 */
import { api, toast, escapeHtml, shortAuthors, state, emit, md, on } from "./api.js";
import { createResourceCache } from "./resource-cache.js";
import { initCategoryPicker, setCategoryPaths, syncCategoryPicker } from "./category-picker.js";
import { mountPaperNotes } from './paper-notes.js';

const PAGE_SIZE = 50;
let offset = 0;
let totalPapers = 0;
let lastFilterKey = "";
const comparedPapers = new Map();
let detailGeneration = 0;
const filters = { q: "", category: "", tag: "", sort: "updated", missing: false };

let initialized = false;
let requestSeq = 0;
let viewMode = localStorage.getItem("pw-view") === "cards" ? "cards" : "compact";
let selectedId = "";
let pdfjsLib = null;
const detailPdf = { doc: null, observer: null };
const promptedCategories = new Set();
const tagsCache = createResourceCache(() => api.get("/tags"));
const categoriesCache = createResourceCache(() => api.get("/categories/tree"));
let renderedTags = null;
let renderedCategories = null;
function invalidateLibraryMetadata() {
  tagsCache.invalidate();
  categoriesCache.invalidate();
}
let lastToast = { message: "", at: 0 };

const byId = (id) => document.getElementById(id);

/* ---------- 错误提示 ---------- */
function notifyError(err, fallback = "请求失败") {
  const message = String((err && err.message) || fallback);
  const now = Date.now();
  if (lastToast.message === message && now - lastToast.at < 2500) return;
  lastToast = { message, at: now };
  toast(message, "error", 4500);
}

function isLlmMissing(err) {
  return /未配置|未设置|not configured|api[_ ]?key/i.test(String((err && err.message) || ""));
}

function failLlm(err, what) {
  if (isLlmMissing(err)) {
    toast("大模型还没配置：去「设置」页填 Base URL / API Key / 模型名", "error", 6000);
    return;
  }
  notifyError(err, `${what}失败`);
}

/* ---------- 工具 ---------- */
function snippet(text, max = 220) {
  const raw = String(text || "")
    .replace(/```[\s\S]*?```/g, " ")
    .replace(/[#>*_`~]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  return raw.length > max ? `${raw.slice(0, max)}…` : raw;
}

function statusInfo(paper) {
  const pdf = paper.pdf_state || (paper.has_pdf ? "available" : "not_acquired");
  const fileLabel = {available:"PDF 已获取", missing:"PDF 文件丢失", not_acquired:"PDF 未获取"}[pdf] || pdf;
  const indexed = paper.index_state || (paper.status === "indexed" || paper.status === "analyzed" ? "indexed" : "pending");
  const analyzed = paper.analysis_state || (paper.status === "analyzed" ? "analyzed" : "pending");
  const states = {indexed:"已索引", available:"已索引", done:"已完成", succeeded:"已完成", analyzed:"已精读", pending:"待处理", new:"待处理", failed:"失败", running:"处理中", not_indexed:"待索引", not_analyzed:"待精读"};
  return {cls: pdf === "available" ? "" : "nopdf", label: fileLabel, compact:`${pdf === "available" ? "PDF ✓" : pdf === "missing" ? "文件丢失" : "未获取 PDF"} · ${indexed === "indexed" ? "索引 ✓" : indexed === "failed" ? "索引失败" : "待索引"} · ${analyzed === "analyzed" ? "精读 ✓" : analyzed === "failed" ? "精读失败" : "待精读"}`, detail: `${fileLabel} · 索引：${states[indexed] || indexed} · 精读：${states[analyzed] || analyzed}`};
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    try {
      const area = document.createElement("textarea");
      area.value = text;
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(area);
      return ok;
    } catch (fallbackError) {
      return false;
    }
  }
}

async function copyPaperTitle(paperId) {
  const paper = (state.papers || []).find((item) => item && item.id === paperId);
  if (!paper) return;
  const title = String(paper.title || "").trim();
  if (!title) return;
  const ok = await copyText(title);
  if (ok) {
    toast(`已复制：${title.slice(0, 40)}${title.length > 40 ? "…" : ""}`, "success");
  } else {
    toast("复制失败，请手动选中标题", "error");
  }
}

function openPaper(paperId) {
  if (!paperId) return;
  const opener = window.PW && window.PW.openPaper;
  if (typeof opener === "function") opener(paperId);
  else toast("阅读器还没准备好，稍后再点一次", "error");
}

function hasFilter() {
  return Boolean(filters.q || filters.category || filters.tag || filters.missing);
}

/* ---------- 卡片渲染 ---------- */
function cardHtml(paper) {
  const id = escapeHtml(paper.id || "");
  const title = escapeHtml(paper.title || "未命名论文");
  const meta = [];
  if (paper.authors) meta.push(escapeHtml(shortAuthors(paper.authors, 3)));
  if (paper.year) meta.push(escapeHtml(paper.year));
  if (paper.venue) meta.push(escapeHtml(paper.venue));

  const category = paper.category
    ? `<span class="pc-badge" title="分类">${escapeHtml(paper.category)}</span>`
    : "";

  const rawSummary = paper.summary || paper.abstract || "";
  const summary = rawSummary
    ? escapeHtml(snippet(rawSummary))
    : "还没有摘要 —— 点右侧的「AI 精读」让 AI 先读一遍。";

  const tagList = Array.isArray(paper.tags) ? paper.tags.filter(Boolean) : [];
  const shownTags = tagList.slice(0, 5);
  const moreTags = tagList.length - shownTags.length;
  const tags = shownTags.length
    ? `<div class="pc-tags">${shownTags
        .map((tag) => `<span class="pc-tag">${escapeHtml(tag)}</span>`)
        .join("")}${moreTags > 0 ? `<span class="pc-tag">+${moreTags}</span>` : ""}</div>`
    : "";

  const status = statusInfo(paper);
  if (!paper.has_pdf) {
    status.cls = "nopdf";
    status.label = paper.pdf_state === "missing" ? "PDF 文件丢失" : "PDF 未获取";
  }
  const pending =
    paper.category_status === "pending"
      ? `<span class="pc-pending" title="AI 建议了新分类，等你确认">待分类</span>`
      : "";
  const annotations = Number(paper.annotation_count) || 0;
  const statusText = annotations > 0 ? `${status.detail} · ${annotations} 批注` : status.detail;
  const noPdfHint = !paper.has_pdf
    ? `<div class="pc-nopdf">无法自动扒取 PDF？把它拖到这里补全</div>`
    : "";
  const selected = paper.id === selectedId ? " selected" : "";

  return `<article class="paper-card${paper.has_pdf ? "" : " needs-pdf"}${selected}" data-id="${id}" tabindex="0" role="button" title="单击看详情，双击打开阅读器">
      <div class="pc-top">
        <h3><input class="compare-paper" type="checkbox" data-compare="${id}" aria-label="选择比较：${title}" ${comparedPapers.has(paper.id) ? "checked" : ""}>${title}${pending}</h3>
        ${category}
      </div>
      <div class="pc-meta">${meta.join(" · ") || "未知作者"}</div>
      <p class="pc-summary">${summary}</p>
      ${tags}
      ${noPdfHint}
      <div class="pc-foot">
        <span class="pc-status ${status.cls}"><i class="sdot"></i>${escapeHtml(statusText)}</span>
        <div class="pc-actions">
          <button type="button" class="icon-btn" data-action="copy-title" title="复制论文名" aria-label="复制论文名"><svg class="ico"><use href="#i-copy"/></svg></button>
          <button type="button" class="icon-btn" data-action="summarize" title="${paper.has_pdf ? "AI 精读" : "先补 PDF 才能精读"}" aria-label="AI 精读" ${paper.has_pdf ? "" : "disabled"}><svg class="ico"><use href="#i-spark"/></svg></button>
          <button type="button" class="icon-btn" data-action="delete" title="删除" aria-label="删除"><svg class="ico"><use href="#i-close"/></svg></button>
        </div>
      </div>
    </article>`;
}

function rowStatusHtml(paper) {
  const pdf = paper.pdf_state || (paper.has_pdf ? "available" : "not_acquired");
  const indexed = paper.index_state || (paper.status === "indexed" || paper.status === "analyzed" ? "indexed" : "pending");
  const analyzed = paper.analysis_state || (paper.status === "analyzed" ? "analyzed" : "pending");
  const pdfCls = pdf === "available" ? "st-ok" : pdf === "missing" ? "st-bad" : "st-idle";
  const pdfLabel = pdf === "available" ? "PDF 已获取" : pdf === "missing" ? "PDF 文件丢失" : "PDF 未获取";
  const idxCls = indexed === "indexed" ? "st-ok" : indexed === "failed" ? "st-bad" : indexed === "running" ? "st-run" : "st-idle";
  const idxLabel = indexed === "indexed" ? "已索引" : indexed === "failed" ? "索引失败" : "待索引";
  const anaCls = analyzed === "analyzed" ? "st-ok" : analyzed === "failed" ? "st-bad" : analyzed === "running" ? "st-run" : "st-idle";
  const anaLabel = analyzed === "analyzed" ? "已精读" : analyzed === "failed" ? "精读失败" : "待精读";
  const pendingCat = paper.category_status === "pending";
  const summary = `${pdfLabel} · 索引：${idxLabel} · 精读：${anaLabel}${pendingCat ? " · 分类：待分类" : ""}`;
  return `<span class="pr-status" aria-label="${escapeHtml(summary)}">` +
    `<i class="st-dot ${pdfCls}" data-tip="${escapeHtml(pdfLabel)}"></i>` +
    `<i class="st-dot ${idxCls}" data-tip="索引：${escapeHtml(idxLabel)}"></i>` +
    `<i class="st-dot ${anaCls}" data-tip="精读：${escapeHtml(anaLabel)}"></i>` +
    (pendingCat ? '<i class="st-dot st-warn" data-tip="分类：待分类"></i>' : "") +
    `</span>`;
}

/* 状态圆点的浮层：自绘在圆点右上方，避免系统 title 被光标挡住。
   原生 title 的位置和大小不可控，所以统一走 data-tip + 一个复用元素。 */
let dotTipEl = null;

function ensureDotTip() {
  if (dotTipEl && document.contains(dotTipEl)) return dotTipEl;
  dotTipEl = document.createElement("div");
  dotTipEl.className = "pw-dot-tip";
  dotTipEl.setAttribute("role", "tooltip");
  document.body.appendChild(dotTipEl);
  return dotTipEl;
}

function showDotTip(dot) {
  const tip = ensureDotTip();
  tip.textContent = dot.dataset.tip || "";
  tip.classList.add("show");
  const rect = dot.getBoundingClientRect();
  const margin = 8;
  let left = rect.right + margin;
  let top = rect.top - tip.offsetHeight - margin;
  if (left + tip.offsetWidth > window.innerWidth - margin) {
    left = window.innerWidth - tip.offsetWidth - margin;
  }
  if (top < margin) top = rect.bottom + margin;
  tip.style.left = `${Math.round(left)}px`;
  tip.style.top = `${Math.round(top)}px`;
}

function hideDotTip() {
  dotTipEl?.classList.remove("show");
}

document.addEventListener("mouseover", (event) => {
  const dot = event.target.closest?.(".st-dot[data-tip]");
  if (dot) showDotTip(dot);
});
document.addEventListener("mouseout", (event) => {
  if (event.target.closest?.(".st-dot[data-tip]")) hideDotTip();
});
window.addEventListener("scroll", hideDotTip, true);
window.addEventListener("resize", hideDotTip);

function rowHtml(paper) {
  const id = escapeHtml(paper.id || "");
  const pending = paper.category_status === "pending" ? " · 待分类" : "";
  const noPdf = paper.has_pdf ? "" : " · 缺 PDF";
  const status = statusInfo(paper).detail + pending;
  const selected = paper.id === selectedId ? " selected" : "";
  return `<div class="paper-row-item${paper.has_pdf ? "" : " needs-pdf"}${selected}" data-id="${id}" tabindex="0" role="button" title="单击看详情，双击打开阅读器">
      <span class="pr-title"><input class="compare-paper" type="checkbox" data-compare="${id}" aria-label="选择比较：${escapeHtml(paper.title)}" ${comparedPapers.has(paper.id) ? "checked" : ""}><span class="pr-title-text" title="${escapeHtml(paper.title || '未命名论文')}">${escapeHtml(paper.title || "未命名论文")}</span><button type="button" class="row-copy" data-action="copy-title" title="复制论文名">⧉</button></span>
      <span class="pr-venue">${escapeHtml(paper.venue || "—")}</span>
      <span class="pr-authors">${escapeHtml(shortAuthors(paper.authors, 3) || "未知作者")}</span>
      <span class="pr-year">${escapeHtml(paper.year || "—")}</span>
      ${rowStatusHtml(paper)}
    </div>`;
}

/* ---------- 右侧详情侧栏 ---------- */
function setDetailOpen(open) {
  const split = document.querySelector(".library-split");
  if (!split) return;
  const reader = byId("reader");
  const willOpen = Boolean(open) && (!reader || reader.classList.contains("hidden"));
  split.classList.toggle("detail-open", willOpen);
  const rail = byId("btn-detail-toggle");
  if (rail) {
    rail.setAttribute("aria-expanded", String(willOpen));
    rail.setAttribute("title", willOpen ? "收起详情" : "展开详情");
    const label = rail.querySelector(".detail-rail-text");
    if (label) label.textContent = willOpen ? "收起" : "展开";
  }
  if (!willOpen) cleanupDetailPdf();
}

function detailPlaceholder() {
  cleanupDetailPdf();
  const panel = byId("paper-detail");
  if (!panel) return;
  panel.innerHTML = `<button type="button" class="detail-close" id="detail-close" title="关闭侧栏" aria-label="关闭侧栏"><svg class="ico"><use href="#i-close"/></svg></button>
    <div class="detail-placeholder">
      <div class="detail-placeholder-mark">◧</div>
      <p>点选一篇论文，这里会显示摘要、分类、PDF 缩略图。</p>
    </div>`;
}

function cleanupDetailPdf() {
  detailGeneration += 1;
  if (detailPdf.observer) {
    detailPdf.observer.disconnect();
    detailPdf.observer = null;
  }
  if (detailPdf.doc) {
    detailPdf.doc.destroy().catch(() => {});
    detailPdf.doc = null;
  }
}

async function getPdfjs() {
  if (!pdfjsLib) {
    pdfjsLib = await import("/vendor/pdfjs/pdf.mjs");
    pdfjsLib.GlobalWorkerOptions.workerSrc = "/vendor/pdfjs/pdf.worker.mjs";
  }
  return pdfjsLib;
}

async function renderDetailThumbs(paper) {
  const box = byId("detail-thumbs");
  if (!box) return;
  cleanupDetailPdf();
  const generation = detailGeneration;
  box.innerHTML = "";
  box.classList.toggle("is-empty", !paper.has_pdf);
  if (!paper.has_pdf) {
    box.innerHTML = `<div class="detail-empty-pdf">还没有 PDF。<br>把 PDF 拖到卡片上即可补全。</div>`;
    return;
  }
  try {
    const pdfjs = await getPdfjs();
    const doc = await pdfjs.getDocument({ url: `/api/papers/${encodeURIComponent(paper.id)}/pdf` }).promise;
    if (generation !== detailGeneration || selectedId !== paper.id || !box.isConnected) { await doc.destroy(); return; }
    detailPdf.doc = doc;
    const total = Math.min(doc.numPages, 40);
    for (let pageNumber = 1; pageNumber <= total; pageNumber += 1) {
      const holder = document.createElement("div");
      holder.className = "detail-thumb";
      holder.dataset.page = String(pageNumber);
      holder.innerHTML = `<div class="detail-thumb-label">第 ${pageNumber} 页</div>`;
      box.appendChild(holder);
    }
    const render = async (holder) => {
      if (generation !== detailGeneration || detailPdf.doc !== doc || holder.dataset.rendered === "1") return;
      holder.dataset.rendered = "1";
      try {
        const page = await doc.getPage(Number(holder.dataset.page));
        const base = page.getViewport({ scale: 1 });
        const width = box.clientWidth || 300;
        const viewport = page.getViewport({ scale: Math.min(2, width / base.width) });
        const canvas = document.createElement("canvas");
        canvas.width = Math.floor(viewport.width);
        canvas.height = Math.floor(viewport.height);
        const context = canvas.getContext("2d");
        await page.render({ canvasContext: context, viewport }).promise;
        if (generation === detailGeneration && holder.isConnected) holder.prepend(canvas);
      } catch {
        /* 单页失败忽略 */
      }
    };
    const observer = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (entry.isIntersecting) render(entry.target);
        });
      },
      { root: box, rootMargin: "120px" },
    );
    detailPdf.observer = observer;
    box.querySelectorAll(".detail-thumb").forEach((holder) => observer.observe(holder));
    if (total) render(box.querySelector(".detail-thumb"));
  } catch (err) {
    if (generation !== detailGeneration || !box.isConnected) return;
    box.innerHTML = `<div class="detail-empty-pdf">PDF 加载失败：${escapeHtml(err.message)}</div>`;
  }
}

function showDetail(paper) {
  const panel = byId("paper-detail");
  if (!panel || !paper || selectedId !== paper.id) return;
  setDetailOpen(true);
  const tagList = Array.isArray(paper.tags) ? paper.tags : [];
  const abstract = paper.summary || paper.abstract || "还没有摘要，可以点「一键精读」让 AI 读一遍。";
  panel.innerHTML = `
    <button type="button" class="detail-close" id="detail-close" title="关闭侧栏" aria-label="关闭侧栏"><svg class="ico"><use href="#i-close"/></svg></button>
    <div class="detail-head">
      <h3>${escapeHtml(paper.title || "未命名论文")}</h3>
    </div>
    <div class="detail-meta"><div class="detail-facts">
      <div><dt>作者</dt><dd class="facts-authors" title="${escapeHtml(paper.authors || "未知作者")}">${escapeHtml(paper.authors || "未知作者")}</dd></div>
      ${paper.year ? `<div><dt>年份</dt><dd>${escapeHtml(paper.year)}</dd></div>` : ""}
      ${paper.venue ? `<div><dt>期刊</dt><dd class="facts-one-line" title="${escapeHtml(paper.venue)}">${escapeHtml(paper.venue)}</dd></div>` : ""}
      ${paper.volume || paper.issue || paper.pages ? `<div><dt>卷期页</dt><dd class="facts-one-line">${escapeHtml([paper.volume && (paper.issue ? `${paper.volume}(${paper.issue})` : paper.volume), paper.pages].filter(Boolean).join(": "))}</dd></div>` : ""}
      <div><dt>分类</dt><dd class="facts-one-line" title="${escapeHtml(paper.category || "未分类")}">${escapeHtml(paper.category || "未分类")}${paper.category_status === "pending" ? "（待确认）" : ""}</dd></div>
      <div><dt>状态</dt><dd>${escapeHtml(statusInfo(paper).label)}</dd></div>
      ${paper.annotation_count ? `<div><dt>批注</dt><dd>${paper.annotation_count} 条</dd></div>` : ""}
    </div></div>
    ${[ ["分类", paper.category_error], ["索引", paper.index_error], ["精读", paper.analysis_error] ].filter(([,error]) => error).map(([label,error]) => `<div class="pdf-error">${label}失败：${escapeHtml(error)}</div>`).join("")}
    ${paper.pdf_error ? `<div class="pdf-error">上次抓取失败：${escapeHtml(paper.pdf_error)}</div>` : ""}
    ${tagList.length ? `<div class="pc-tags">${tagList.slice(0, 8).map((tag) => `<span class="pc-tag">${escapeHtml(tag)}</span>`).join("")}</div>` : ""}
    <div class="detail-actions">
      <button class="btn primary tiny" id="detail-open">打开阅读</button>
      <button class="btn tiny" id="detail-copy-title">复制论文名</button>
      <button class="btn tiny" id="detail-analyze" ${paper.has_pdf ? "" : "disabled title=\"先补 PDF 才能精读\""}>一键精读</button>
      <button class="btn tiny" id="detail-retag" ${paper.has_pdf ? "" : "disabled"}>重生成标签</button>
      ${paper.has_pdf ? "" : `<button class="btn tiny" id="detail-fetch">自动找 PDF</button>
      <button class="btn tiny" id="detail-lookup">联网查找原文</button>
      <button class="btn tiny" id="detail-attach">重新关联 PDF</button><input id="detail-attach-file" type="file" accept=".pdf" aria-label="重新关联 PDF 文件" hidden>`}
      <button class="btn tiny" id="detail-category">${paper.category_status === "pending" ? "确认分类" : "修改分类"}</button>
    </div>
    <div class="detail-section">
      <h4>PDF 预览</h4>
      <div class="detail-thumbs" id="detail-thumbs"></div>
    </div>
    <div class="detail-section">
      <h4>摘要</h4>
      <div class="detail-text markdown">${md(abstract)}</div>
    </div><div class="detail-section" id="detail-paper-notes"></div>`;

  mountPaperNotes(byId('detail-paper-notes'),paper.id);

  byId("detail-attach")?.addEventListener("click", () => byId("detail-attach-file")?.click());
  byId("detail-attach-file")?.addEventListener("change", async event => {
    const file = event.target.files[0];
    if (!file) return;
    try {
      const result = await api.uploadFile(`/papers/${encodeURIComponent(paper.id)}/attach-pdf`, file);
      emit("library-changed");
      if (selectedId === paper.id && result.paper) showDetail(result.paper);
      toast("PDF 已重新关联", "success");
    } catch(err) { notifyError(err, "关联 PDF 失败"); }
  });
  byId("detail-open")?.addEventListener("click", () => openPaper(paper.id));
  byId("detail-copy-title")?.addEventListener("click", () => copyPaperTitle(paper.id));
  byId("detail-analyze")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "精读中…";
    try {
      const result = await api.post(`/ai/summarize/${encodeURIComponent(paper.id)}`);
      toast("精读完成", "success");
      emit("library-changed");
      if (selectedId === paper.id && result && result.paper) showDetail(result.paper);
      if (selectedId === paper.id && result && result.paper && result.paper.category_status === "pending" && window.PW.promptCategory) {
        window.PW.promptCategory(paper.id);
      }
    } catch (err) {
      notifyError(err, "精读失败");
    } finally {
      button.disabled = false;
      button.textContent = "一键精读";
    }
  });
  byId("detail-fetch")?.addEventListener("click", async () => {
    const button=byId('detail-fetch');
    if (button) { button.disabled=true; button.textContent='正在获取…'; }
    try {
      const result = await api.post(`/pdf-fetch/one/${encodeURIComponent(paper.id)}`);
      if (result && result.ok) {
        toast("PDF 已补上", "success");
        emit("library-changed");
      } else {
        toast((result && result.error) || "没找到可下载的 PDF", "info");
      }
      const updated=await api.get(`/papers/${encodeURIComponent(paper.id)}`);
      if (selectedId===paper.id) {
        showDetail(updated);
      }
    } catch (err) {
      notifyError(err, "自动找 PDF 失败");
    } finally {
      if (button?.isConnected) { button.disabled=false; button.textContent='自动找 PDF'; }
    }
  });
  byId("detail-lookup")?.addEventListener("click", async () => {
    try {
      toast("正在按标题联网查找…", "info", 2500);
      const result = await api.post(`/papers/${encodeURIComponent(paper.id)}/lookup`);
      toast(result.matched ? "已补全信息，PDF 抓取已排队" : "没搜到匹配条目", result.matched ? "success" : "info", 4500);
      emit("library-changed");
      if (selectedId === paper.id && result.paper) showDetail(result.paper);
    } catch (err) {
      notifyError(err, "联网查找失败");
    }
  });
  byId("detail-retag")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    button.disabled = true;
    button.textContent = "整理中…";
    try {
      const result = await api.post(`/ai/retag/${encodeURIComponent(paper.id)}`);
      toast(`标签已更新：${(result.tags || []).join("、") || "无"}`, "success", 4000);
      emit("library-changed");
      if (selectedId === paper.id && result.paper) showDetail(result.paper);
    } catch (err) {
      notifyError(err, "重新生成标签失败");
    } finally {
      button.disabled = false;
      button.textContent = "重生成标签";
    }
  });
  byId("detail-category")?.addEventListener("click", () => {
    window.PW?.promptCategory?.(paper.id);
  });
  renderDetailThumbs(paper);
}

async function selectPaper(paperId, options = {}) {
  if (!paperId) return;
  selectedId = paperId;
  document.querySelectorAll(".paper-card, .paper-row-item").forEach((node) => {
    node.classList.toggle("selected", node.dataset.id === paperId);
  });
  let paper = (state.papers || []).find((item) => item.id === paperId);
  if (!paper) {
    try {
      paper = await api.get(`/papers/${encodeURIComponent(paperId)}`);
    } catch (err) {
      notifyError(err, "加载论文详情失败");
      return;
    }
  }
  if (selectedId !== paperId) return;
  showDetail(paper);
  if (options.scroll && byId("paper-detail")) {
    byId("paper-detail").scrollTop = 0;
  }
  if (paper.category_status === "pending" && window.PW?.promptCategory && !promptedCategories.has(paperId)) {
    promptedCategories.add(paperId);
    window.PW.promptCategory(paperId);
  }
}

function renderCards(items) {
  const grid = byId("paper-grid");
  if (!grid) return;
  if (viewMode === "compact") {
    grid.className = 'paper-list';
    delete grid.dataset.colsBound;
    grid.innerHTML =
      `<div class="paper-list-head">
        <span class="plh-cell"><span class="plh-label">论文名</span><span class="col-resizer" data-col="0" title="拖动调整论文名列宽"></span></span>
        <span class="plh-cell"><span class="plh-label">期刊名</span><span class="col-resizer" data-col="1" title="拖动调整期刊列宽"></span></span>
        <span class="plh-cell"><span class="plh-label">作者</span><span class="col-resizer" data-col="2" title="拖动调整作者列宽"></span></span>
        <span class="plh-cell"><span class="plh-label">年份</span><span class="col-resizer" data-col="3" title="拖动调整年份列宽"></span></span>
        <span class="plh-cell"><span class="plh-label" title="实心 = 已完成，空心 = 待处理：PDF · 索引 · 精读 · 分类">状态</span></span>
      </div>` +
      items.map(rowHtml).join("");
    applyCompactColumns(grid);
    bindColumnResizers(grid);
  } else {
    grid.className = "paper-grid";
    grid.innerHTML = items.map(cardHtml).join("");
  }
  document.querySelectorAll("#view-toggle button").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === viewMode);
  });
  renderEmpty(items.length === 0);
  if (selectedId) {
    const selected = items.find((item) => item.id === selectedId);
    if (selected) {
      document.querySelectorAll(".paper-card, .paper-row-item").forEach((node) => {
        node.classList.toggle("selected", node.dataset.id === selectedId);
      });
      if (byId("detail-thumbs") && byId("detail-open")) {
        /* 详情已展示，不重复渲染 PDF */
      } else {
        showDetail(selected);
      }
    } else {
      selectedId = "";
      detailPlaceholder();
    }
  }
}

function renderEmpty(showEmpty) {
  const empty = byId("library-empty");
  if (!empty) return;
  empty.classList.toggle("hidden", !showEmpty);
  if (!showEmpty) return;
  const title = empty.querySelector("h3");
  const desc = empty.querySelector("p");
  const button = empty.querySelector("button");
  if (button) { button.textContent = hasFilter() ? "清空筛选" : "去导入"; button.onclick = () => hasFilter() ? clearFilters() : window.PW?.goto?.("import"); }
  if (hasFilter()) {
    if (title) title.textContent = "没找到匹配的论文";
    if (desc) desc.textContent = "换个关键词，或者在工具栏里清空分类 / 标签筛选。";
  } else {
    if (title) title.textContent = "这里还是一片空海";
    if (desc) desc.textContent = "拖入 PDF、导入 Zotero / Obsidian 文献库，或者去「发现论文」搜一篇。";
  }
}

/* ---------- 紧凑列表列宽拖动 ---------- */
const COMPACT_COLS_KEY = "pw.compactCols";
const COMPACT_DEFAULT_COLS = [240, 160, 140, 56, 80];
const COMPACT_MIN_WIDTH = 1;
/* 状态列固定宽度，不参与拖动、也不读取历史保存的宽度。 */
const COMPACT_FIXED_COLS = { 4: 80 };

function normalizeCompactCols(widths) {
  return widths.map((width, index) => COMPACT_FIXED_COLS[index] ?? width);
}

function readCompactCols() {
  try {
    const raw = JSON.parse(localStorage.getItem(COMPACT_COLS_KEY) || "null");
    if (Array.isArray(raw) && raw.length === COMPACT_DEFAULT_COLS.length && raw.every(value=>Number.isFinite(Number(value))&&Number(value)>=0)) {
      return normalizeCompactCols(raw.map((value,index) => Math.max(COMPACT_MIN_WIDTH, Number(value) || COMPACT_DEFAULT_COLS[index])));
    }
  } catch {
    /* 忽略损坏的本地配置 */
  }
  return null;
}

function writeCompactColumns(grid,widths,manual) {
  grid.style.setProperty('--pw-compact-cols',widths.map((width,index)=>index===0&&!manual?'minmax(0, 1fr)':`${width}px`).join(' '));
  // Include the four grid gaps, row padding and borders. Wider manual columns scroll.
  const total=widths.reduce((sum,width,index)=>sum+(index===0&&!manual?1:width),0)+66;
  grid.style.minWidth=`${total}px`;
}

function applyCompactColumns(grid) {
  if (!grid) return;
  const saved = readCompactCols();
  const widths = normalizeCompactCols(saved || COMPACT_DEFAULT_COLS);
  writeCompactColumns(grid,widths,!!saved);
}

function bindColumnResizers(grid) {
  if (!grid || grid.dataset.colsBound === "1") return;
  grid.dataset.colsBound = "1";
  grid.querySelectorAll(".col-resizer").forEach((handle) => {
    handle.addEventListener("mousedown", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const index = Number(handle.dataset.col);
      if (index in COMPACT_FIXED_COLS) return;
      const header = grid.querySelector(".paper-list-head");
      if (!header) return;
      const cells = Array.from(header.children);
      const widths = cells.map((cell) => cell.getBoundingClientRect().width || COMPACT_MIN_WIDTH);
      const startX = event.clientX;
      const startWidths = [...widths];
      const onMove = (moveEvent) => {
        const delta = moveEvent.clientX - startX;
        const next = [...startWidths];
        next[index] = Math.max(COMPACT_MIN_WIDTH, startWidths[index] + delta);

        writeCompactColumns(grid,next.map(width=>Math.round(width)),true);
        grid.dataset.dragCols = JSON.stringify(next.map((width) => Math.round(width)));
      };
      const onUp = () => {
        document.removeEventListener("mousemove", onMove);
        document.removeEventListener("mouseup", onUp);
        document.body.classList.remove("col-resizing");
        try {
          const saved = JSON.parse(grid.dataset.dragCols || "null");
          if (Array.isArray(saved)) localStorage.setItem(COMPACT_COLS_KEY, JSON.stringify(saved));
          delete grid.dataset.dragCols;
        } catch {
          /* 忽略 */
        }
      };
      document.body.classList.add("col-resizing");
      document.addEventListener("mousemove", onMove);
      document.addEventListener("mouseup", onUp);
    });
  });
}

function updateSub(total, shown) {
  const sub = byId("library-sub");
  if (!sub) return;
  const start = total ? offset + 1 : 0;
  sub.textContent = `${filters.missing ? "未收录或丢失 PDF · " : ""}共 ${total} 篇 · 显示 ${start}–${Math.min(offset + shown,total)} · 单击详情，双击阅读`;
  const label = byId("library-page-label");
  if (label) label.textContent = `第 ${Math.floor(offset / PAGE_SIZE) + 1} / ${Math.max(1, Math.ceil(total / PAGE_SIZE))} 页`;
  if (byId("library-prev")) byId("library-prev").disabled = offset === 0;
  if (byId("library-next")) byId("library-next").disabled = offset + shown >= total;
}

/* ---------- 筛选控件 ---------- */
function readFilters() {
  const search = byId("library-search");
  if (search) filters.q = String(search.value || "").trim();
  const category = byId("filter-category");
  if (category) filters.category = category.value || "";
  const tag = byId("filter-tag");
  if (tag) filters.tag = tag.value || "";
  const sort = byId("filter-sort");
  if (sort) filters.sort = sort.value || "updated";
}

function fillSelect(select, items, placeholder) {
  if (!select) return;
  const current = select.value;
  const options = [`<option value="">${escapeHtml(placeholder)}</option>`];
  for (const item of items) {
    const name = typeof item === "string" ? item : item && item.name;
    if (!name) continue;
    const count = typeof item === "object" && item ? Number(item.count) || 0 : 0;
    const label = count ? `${name}（${count}）` : String(name);
    options.push(`<option value="${escapeHtml(name)}">${escapeHtml(label)}</option>`);
  }
  select.innerHTML = options.join("");
  const exists = Array.from(select.options).some((option) => option.value === current);
  if (exists) {
    select.value = current;
  } else if (current) {
    const option = document.createElement("option");
    option.value = current;
    option.textContent = current;
    select.appendChild(option);
    select.value = current;
  }
}

function fillCategorySelect(paths) {
  // 分类筛选改成「层级选择器」：面板一次只显示当前这一级，点进去才展开下一级。
  // 这里只把路径喂给选择器，不再拼成一长串 <option>。
  setCategoryPaths(paths || []);
}

function renderTagCloud() {
  const cloud = byId("tag-cloud");
  if (!cloud) return;
  const tags = (Array.isArray(state.tags) ? state.tags : []).slice(0, 30);
  cloud.innerHTML = tags
    .map((tag) => {
      const name = String((tag && tag.name) || tag || "");
      if (!name) return "";
      const count = Number((tag && tag.count) || 0);
      const active = filters.tag === name ? " active" : "";
      const title = count ? `${count} 篇` : "";
      return `<button type="button" class="tag-chip${active}" data-tag="${escapeHtml(name)}" title="${escapeHtml(title)}">#${escapeHtml(name)}</button>`;
    })
    .join("");
}

/* ---------- 排序 ---------- */
function sortPapers(items) {
  const list = Array.isArray(items) ? [...items] : [];
  const byTitle = (a, b) =>
    String(a.title || "").localeCompare(String(b.title || ""), "zh-Hans-CN", { numeric: true });
  if (filters.sort === "year") {
    list.sort((a, b) => (Number(b.year) || 0) - (Number(a.year) || 0) || byTitle(a, b));
  } else if (filters.sort === "venue") {
    list.sort(
      (a, b) =>
        String(a.venue || "zzzz").localeCompare(String(b.venue || "zzzz"), "zh-Hans-CN", {
          numeric: true,
        }) || byTitle(a, b),
    );
  } else if (filters.sort === "title") {
    list.sort(byTitle);
  } else if (filters.sort === "created") {
    list.sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  } else {
    list.sort((a, b) => String(b.updated_at || "").localeCompare(String(a.updated_at || "")));
  }
  return list;
}

/* ---------- 加载列表 ---------- */
export async function refreshLibrary({ forceMetadata = false } = {}) {
  if (forceMetadata) invalidateLibraryMetadata();
  const grid = byId("paper-grid");
  if (!grid) return;
  readFilters();
  const key = JSON.stringify(filters);
  if (key !== lastFilterKey) { offset = 0; lastFilterKey = key; }
  const seq = ++requestSeq;
  const params = new URLSearchParams();
  if (filters.q) params.set("q", filters.q);
  if (filters.category) params.set("category", filters.category);
  if (filters.tag) params.set("tag", filters.tag);
  if (filters.missing) params.set("missing_pdf", "1");
  params.set("sort", filters.sort);
  params.set("offset", String(offset));
  params.set("limit", String(PAGE_SIZE));

  try {
    const [listRes, tagsRes, categoriesRes] = await Promise.all([
      api.get(`/papers?${params.toString()}`),
      tagsCache.read().catch(() => null),
      categoriesCache.read().catch(() => null),
    ]);
    if (seq !== requestSeq) return;

    const items = Array.isArray(listRes && listRes.items)
      ? listRes.items
      : Array.isArray(listRes)
        ? listRes
        : [];
    const total = Number(listRes && listRes.total) || items.length;
    totalPapers = total;
    if (!items.length && offset > 0 && total > 0) { offset = Math.max(0, Math.floor((total-1)/PAGE_SIZE)*PAGE_SIZE); return refreshLibrary(); }
    state.papers = items;

    if (tagsRes && tagsRes !== renderedTags && Array.isArray(tagsRes.tags)) {
      renderedTags = tagsRes;
      state.tags = tagsRes.tags;
      fillSelect(byId("filter-tag"), tagsRes.tags, "全部标签");
      renderTagCloud();
    }
    if (categoriesRes && categoriesRes !== renderedCategories && Array.isArray(categoriesRes.items)) {
      renderedCategories = categoriesRes;
      fillCategorySelect(categoriesRes.paths || []);
    }
    // 标签 / 分类下拉会重建，重读一次，保证筛选条件不丢
    readFilters();

    const sorted = items;
    renderCards(sorted);
    updateSub(total, sorted.length);
    return true;
  } catch (err) {
    if (seq !== requestSeq) return false;
    notifyError(err, "文献库加载失败");
    return false;
  }
}

/* ---------- 卡片操作 ---------- */
async function summarizePaper(paperId, button) {
  if (!paperId) return;
  const paper = (state.papers || []).find((item) => item && item.id === paperId);
  if (paper && !paper.has_pdf) {
    toast("这篇还没有 PDF，先补齐再精读", "info");
    return;
  }
  const original = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = "…";
  }
  try {
    await api.post(`/ai/summarize/${encodeURIComponent(paperId)}`);
    toast("精读完成", "success");
    emit("library-changed");
  } catch (err) {
    failLlm(err, "精读");
  } finally {
    if (button && button.isConnected) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

async function deletePaper(paperId, card) {
  if (!paperId) return;
  const paper = (state.papers || []).find((item) => item && item.id === paperId);
  const name = (paper && paper.title) || "这篇论文";
  if (!window.confirm(`确定删除《${name}》吗？论文会移入回收站，可在「设置 → 数据与存储」恢复。`)) return;
  if (card) card.style.opacity = "0.5";
  try {
    await api.del(`/papers/${encodeURIComponent(paperId)}`);
    toast("已删除", "success");
    emit("library-changed");
  } catch (err) {
    if (card) card.style.opacity = "";
    notifyError(err, "删除失败");
  }
}

function bindPdfDrop() {
  const grid = byId("paper-grid");
  if (!grid) return;
  let activeCard = null;
  const targetCard = (event) => {
    const node = event.target;
    return node && node.closest ? node.closest(".paper-card.needs-pdf, .paper-row-item.needs-pdf") : null;
  };
  grid.addEventListener("dragover", (event) => {
    const card = targetCard(event);
    if (!card) return;
    event.preventDefault();
    if (activeCard !== card) {
      activeCard?.classList.remove("drag-over");
      activeCard = card;
      card.classList.add("drag-over");
    }
  });
  grid.addEventListener("dragleave", (event) => {
    const card = targetCard(event);
    if (card && !card.contains(event.relatedTarget)) card.classList.remove("drag-over");
  });
  grid.addEventListener("drop", async (event) => {
    const card = targetCard(event);
    if (!card) return;
    event.preventDefault();
    event.stopPropagation();
    card.classList.remove("drag-over");
    activeCard = null;
    const file = (event.dataTransfer?.files || [])[0];
    if (!file) return;
    if (!/\.pdf$/i.test(file.name)) {
      toast("只支持拖入 PDF 文件", "error");
      return;
    }
    const paperId = card.dataset.id;
    card.classList.add("uploading");
    try {
      const result = await api.uploadFile(
        `/papers/${encodeURIComponent(paperId)}/attach-pdf`,
        file,
      );
      toast(`已补上 PDF：${(result.paper && result.paper.title) || ""}`.slice(0, 60), "success");
      emit("library-changed");
    } catch (err) {
      toast(`补 PDF 失败：${err.message}`, "error", 5000);
    } finally {
      card.classList.remove("uploading");
    }
  });
}

/* ---------- 右键菜单 / 编辑信息 ---------- */
let contextPaperId = "";
let editPaperId = "";

function paperById(paperId) {
  return (state.papers || []).find((item) => item && item.id === paperId) || null;
}

function hideContextMenu() {
  byId("paper-context")?.classList.add("hidden");
}

function showContextMenu(event, paperId) {
  const menu = byId("paper-context");
  if (!menu) return;
  contextPaperId = paperId;
  menu.classList.remove("hidden");
  const width = 210;
  const height = menu.offsetHeight || 300;
  const left = Math.min(event.clientX, window.innerWidth - width - 8);
  const top = Math.min(event.clientY, window.innerHeight - height - 8);
  menu.style.left = `${Math.max(8, left)}px`;
  menu.style.top = `${Math.max(8, top)}px`;
}

async function copyCitation(paperId) {
  try {
    const data = await api.get(`/cite/paper/${encodeURIComponent(paperId)}?style=apa`);
    await navigator.clipboard.writeText(data.reference || "");
    toast("已复制 APA 引文", "success");
  } catch (err) {
    notifyError(err, "复制引文失败");
  }
}

function openEditModal(paperId) {
  const paper = paperById(paperId);
  if (!paper) return;
  editPaperId = paperId;
  byId("edit-title").value = paper.title || "";
  byId("edit-authors").value = paper.authors || "";
  byId("edit-year").value = paper.year || "";
  byId("edit-venue").value = paper.venue || "";
  byId("edit-doi").value = paper.doi || "";
  byId("edit-volume").value = paper.volume || "";
  byId("edit-issue").value = paper.issue || "";
  byId("edit-pages").value = paper.pages || "";
  byId("edit-abstract").value = paper.abstract || "";
  byId("paper-edit-modal")?.classList.remove("hidden");
}

function closeEditModal() {
  byId("paper-edit-modal")?.classList.add("hidden");
  editPaperId = "";
}

async function savePaperEdit() {
  if (!editPaperId) return;
  const button = byId("paper-edit-save");
  const original = button.textContent;
  button.disabled = true;
  button.textContent = "保存中…";
  try {
    const updated = await api.patch(`/papers/${encodeURIComponent(editPaperId)}`, {
      title: byId("edit-title").value.trim(),
      authors: byId("edit-authors").value.trim(),
      year: byId("edit-year").value.trim(),
      venue: byId("edit-venue").value.trim(),
      doi: byId("edit-doi").value.trim(),
      volume: byId("edit-volume").value.trim(),
      issue: byId("edit-issue").value.trim(),
      pages: byId("edit-pages").value.trim(),
      abstract: byId("edit-abstract").value.trim(),
    });
    toast("已保存", "success");
    closeEditModal();
    emit("library-changed");
    if (updated && selectedId === updated.id) showDetail(updated);
  } catch (err) {
    notifyError(err, "保存失败");
  } finally {
    button.disabled = false;
    button.textContent = original;
  }
}

async function handleContextAction(action) {
  const paperId = contextPaperId;
  hideContextMenu();
  if (!paperId) return;
  const paper = paperById(paperId);
  if (!paper) return;
  if (action === "open") {
    openPaper(paperId);
  } else if (action === "copy-title") {
    await copyPaperTitle(paperId);
  } else if (action === "reveal") {
    try {
      const result = await api.post(`/papers/${encodeURIComponent(paperId)}/reveal`);
      toast(result.selected ? "已在文件夹中定位 PDF" : "已打开 PDF 文件夹", "success");
    } catch (err) {
      notifyError(err, "打开文件夹失败");
    }
  } else if (action === "edit") {
    openEditModal(paperId);
  } else if (action === "cite") {
    await copyCitation(paperId);
  } else if (action === "lookup") {
    try {
      toast("正在联网查找这篇论文的条目…", "info", 2500);
      const result = await api.post(`/papers/${encodeURIComponent(paperId)}/lookup`);
      if (result.matched) {
        toast("已补全原文信息，正在尝试抓取 PDF", "success", 4000);
      } else {
        toast("没搜到匹配条目，可以手动补 DOI 或原文链接", "info", 5000);
      }
      emit("library-changed");
      if (selectedId === paperId && result.paper) showDetail(result.paper);
    } catch (err) {
      notifyError(err, "联网查找失败");
    }
  } else if (action === "references") {
    window.PW?.openReferenceExpand?.(paperId);
  } else if (action === "analyze") {
    summarizePaper(paperId);
  } else if (action === "retag") {
    try {
      toast("正在重新生成标签…", "info", 2000);
      const result = await api.post(`/ai/retag/${encodeURIComponent(paperId)}`);
      toast(`标签已更新：${(result.tags || []).join("、") || "无"}`, "success", 4000);
      emit("library-changed");
    } catch (err) {
      notifyError(err, "重新生成标签失败");
    }
  } else if (action === "category") {
    window.PW?.promptCategory?.(paperId);
  } else if (action === "delete") {
    await deletePaper(paperId, null);
  }
}

function bindGrid() {
  const grid = byId("paper-grid");
  if (!grid) return;
  grid.addEventListener("click", (event) => {
    const item = event.target.closest(".paper-card, .paper-row-item");
    const actionButton = event.target.closest("[data-action]");
    if (!item) return;
    if (event.target.closest("[data-compare]")) { const paper = paperById(item.dataset.id); if (event.target.checked) comparedPapers.set(paper.id,paper.title); else comparedPapers.delete(paper.id); updateCompare(); return; }
    const paperId = item.dataset.id;
    if (!actionButton) {
      selectPaper(paperId);
      return;
    }
    event.stopPropagation();
    event.preventDefault();
    const action = actionButton.dataset.action;
    if (action === "summarize") summarizePaper(paperId, actionButton);
    else if (action === "copy-title") copyPaperTitle(paperId);
    else if (action === "delete") deletePaper(paperId, item);
  });
  grid.addEventListener("dblclick", (event) => {
    const item = event.target.closest(".paper-card, .paper-row-item");
    if (!item) return;
    event.preventDefault();
    openPaper(item.dataset.id);
  });
  grid.addEventListener("keydown", (event) => {
    if (event.target.matches("button,input")) return;
    if (event.key === "ContextMenu" || (event.shiftKey && event.key === "F10")) { const item = event.target.closest(".paper-card,.paper-row-item"); if(item){event.preventDefault();const rect=item.getBoundingClientRect();showContextMenu({clientX:rect.x+30,clientY:rect.y+30},item.dataset.id);byId("paper-context")?.querySelector("button")?.focus();} return; }
    if (event.key !== "Enter" && event.key !== " ") return;
    const item = event.target.closest(".paper-card, .paper-row-item");
    if (!item) return;
    event.preventDefault();
    selectPaper(item.dataset.id);
  });
  grid.addEventListener("contextmenu", (event) => {
    const item = event.target.closest(".paper-card, .paper-row-item");
    if (!item) return;
    event.preventDefault();
    event.stopPropagation();
    showContextMenu(event, item.dataset.id);
  });
}

function bindTagCloud() {
  const cloud = byId("tag-cloud");
  if (!cloud) return;
  cloud.addEventListener("click", (event) => {
    const chip = event.target.closest("[data-tag]");
    if (!chip) return;
    const tag = chip.dataset.tag || "";
    filters.tag = filters.tag === tag ? "" : tag;
    const select = byId("filter-tag");
    if (select) {
      const exists = Array.from(select.options).some((option) => option.value === filters.tag);
      if (!exists && filters.tag) {
        const option = document.createElement("option");
        option.value = filters.tag;
        option.textContent = filters.tag;
        select.appendChild(option);
      }
      select.value = filters.tag;
    }
    refreshLibrary();
  });
}

/* ---------- 初始化 ---------- */
export function initLibrary() {
  if (initialized) return;
  initialized = true;
  on("library-changed", invalidateLibraryMetadata);
  on("reader-opening", () => setDetailOpen(false));

  const search = byId("library-search");
  if (search) {
    search.addEventListener("keydown", (event) => {
      if (event.key === "Enter") {
        event.preventDefault();
        refreshLibrary();
      } else if (event.key === "Escape") {
        search.value = "";
        refreshLibrary();
      }
    });
  }
  window.PW.openPaperDetail = async paperId => { window.PW?.goto?.("library"); await refreshLibrary(); await selectPaper(paperId, {scroll:true}); };
  initLibraryControls();
  byId("filter-category")?.addEventListener("change", refreshLibrary);
  byId("filter-tag")?.addEventListener("change", refreshLibrary);
  byId("filter-sort")?.addEventListener("change", refreshLibrary);
  byId("btn-missing-pdf")?.addEventListener("click", () => {
    filters.missing = !filters.missing;
    byId("btn-missing-pdf")?.classList.toggle("active", filters.missing);
    refreshLibrary();
  });
  document.querySelectorAll("#view-toggle button").forEach((button) => {
    button.addEventListener("click", () => {
      viewMode = button.dataset.view === "compact" ? "compact" : "cards";
      localStorage.setItem("pw-view", viewMode);
      refreshLibrary();
    });
  });
  byId("btn-detail-toggle")?.addEventListener("click", () => {
    const split = document.querySelector(".library-split");
    const open = !split?.classList.contains("detail-open");
    if (open && !selectedId) detailPlaceholder();
    setDetailOpen(open);
  });
  byId("paper-detail")?.addEventListener("click", (event) => {
    if (event.target.closest("#detail-close")) setDetailOpen(false);
  });

  byId("btn-refresh-library")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "刷新中…";
    try {
      const ok = await refreshLibrary({ forceMetadata: true });
      if (ok) toast("已刷新", "success", 1600);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });

  byId("paper-context")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-action]");
    if (!button) return;
    event.preventDefault();
    event.stopPropagation();
    handleContextAction(button.dataset.action);
  });
  document.addEventListener("click", hideContextMenu);
  document.addEventListener("scroll", hideContextMenu, true);
  window.addEventListener("resize", hideContextMenu);
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      hideContextMenu();
      closeEditModal();
    }
  });
  byId("paper-edit-close")?.addEventListener("click", closeEditModal);
  byId("paper-edit-cancel")?.addEventListener("click", closeEditModal);
  byId("paper-edit-save")?.addEventListener("click", savePaperEdit);

  bindGrid();
  bindPdfDrop();
  bindTagCloud();
  initCategoryPicker();
}

function clearFilters() {
  filters.q = filters.category = filters.tag = "";
  filters.missing = false;
  for (const id of ["library-search","filter-category","filter-tag"]) if (byId(id)) byId(id).value="";
  syncCategoryPicker("");
  byId("btn-missing-pdf")?.classList.remove("active");
  offset=0;
  refreshLibrary();
}
function updateCompare() {
  const button = byId("btn-compare-papers");
  if (button) { button.textContent = `比较已选（${comparedPapers.size}）`; button.disabled=comparedPapers.size<2; }
}
function initLibraryControls() {
  byId("library-empty")?.querySelector("button")?.removeAttribute("data-goto");
  byId("library-prev")?.addEventListener("click",()=>{offset=Math.max(0,offset-PAGE_SIZE);refreshLibrary();});
  byId("library-next")?.addEventListener("click",()=>{if(offset+PAGE_SIZE<totalPapers){offset+=PAGE_SIZE;refreshLibrary();}});
  byId("btn-clear-filters")?.addEventListener("click",clearFilters);
  byId("btn-compare-papers")?.addEventListener("click",()=>{
    window.dispatchEvent(new CustomEvent("pw:compare-papers",{detail:{paper_ids:[...comparedPapers.keys()],titles:[...comparedPapers.values()]}}));
    window.PW?.goto?.("chat");
  });
}
