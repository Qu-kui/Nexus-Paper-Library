/* 研究页：多篇论文脉络梳理 / 单篇投稿匹配，共用一套「分类 + 搜索」论文选择器 */
import { api, md, toast, escapeHtml, debounce } from "./api.js";

const MAX_MULTI = 20;
const MODES = {
  map: {
    skill: "literature-map",
    multi: true,
    hint: "先选分类收窄范围，再勾 2–20 篇同方向的论文，生成时间线、方法演化、共识分歧和空白机会。",
    button: "梳理脉络",
  },
  journal: {
    skill: "journal-fit",
    multi: false,
    hint: "先选分类收窄范围，再选 1 篇论文，得到候选期刊、命中风险、还缺哪些实验和投稿策略。",
    button: "匹配期刊",
  },
};

let papers = [];
let mode = "map";
let selected = new Set();
let busy = false;
let loaded = false;
let category = "";
let categoryCounts = new Map();

const el = (id) => document.getElementById(id);

function currentMode() {
  return MODES[mode] || MODES.map;
}

function visiblePapers() {
  const q = String(el("research-search")?.value || "").trim().toLowerCase();
  return papers.filter((paper) => {
    if (!q) return true;
    const hay = [paper.title, paper.authors, paper.venue, paper.category, paper.year].join(" ").toLowerCase();
    return hay.includes(q);
  });
}

function syncChrome() {
  const conf = currentMode();
  document.querySelectorAll("[data-research-mode]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.researchMode === mode);
  });
  const hint = el("research-hint");
  if (hint) hint.textContent = conf.hint;
  const run = el("btn-research-run");
  if (run) {
    run.textContent = busy ? "生成中…" : conf.button;
    run.disabled = busy || selected.size === 0 || (conf.multi ? selected.size < 2 : selected.size !== 1);
  }
  const count = el("research-count");
  if (count) {
    const scope = category ? `分类内 ${papers.length} 篇` : `全部 ${papers.length} 篇`;
    count.textContent = `${scope}${selected.size ? ` · 已选 ${selected.size}` : ""}`;
  }
  const selectAll = el("btn-research-selectall");
  if (selectAll) selectAll.hidden = !conf.multi;
}

function renderPapers() {
  const box = el("research-papers");
  if (!box) return;
  const rows = visiblePapers();
  if (!rows.length) {
    box.innerHTML = '<p class="muted small" style="padding:8px">这个范围里没有论文，换个分类或关键词试试。</p>';
    return;
  }
  box.innerHTML = rows
    .map((paper) => {
      const checked = selected.has(paper.id) ? " checked" : "";
      const meta = [paper.authors, paper.year, paper.venue].filter(Boolean).join(" · ").slice(0, 90);
      const tags = [paper.category, paper.status === "ready" ? "" : "未索引"].filter(Boolean).join(" · ");
      return `<label class="research-paper">
        <input type="checkbox" data-paper-id="${escapeHtml(paper.id)}"${checked}>
        <span><span class="rp-title">${escapeHtml(paper.title || "未命名论文")}</span>
        <span class="rp-meta">${escapeHtml(meta)}${tags ? " · " + escapeHtml(tags) : ""}</span></span>
      </label>`;
    })
    .join("");
  box.querySelectorAll("input[data-paper-id]").forEach((input) => {
    input.addEventListener("change", () => {
      const id = input.dataset.paperId;
      if (currentMode().multi) {
        if (input.checked && selected.size >= MAX_MULTI) {
          input.checked = false;
          toast(`最多选 ${MAX_MULTI} 篇，再多模型也读不过来`, "warn");
          return;
        }
        if (input.checked) selected.add(id);
        else selected.delete(id);
      } else {
        selected.clear();
        if (input.checked) selected.add(id);
        box.querySelectorAll("input[data-paper-id]").forEach((other) => {
          other.checked = other.dataset.paperId === id && input.checked;
        });
      }
      syncChrome();
    });
  });
}

function selectedCountFor(label) {
  return categoryCounts.get(label) || 0;
}

function renderCategories(paths) {
  const select = el("research-category");
  if (!select) return;
  categoryCounts = new Map();
  const majors = new Map();
  const leaves = [];
  for (const item of paths || []) {
    const levels = Array.isArray(item.path) ? item.path : String(item.label || "").split(" / ");
    const label = item.label || levels.join(" / ");
    const count = Number(item.count || 0);
    categoryCounts.set(label, count);
    leaves.push({ label, count });
    if (levels.length > 1) {
      const major = levels[0];
      const current = majors.get(major) || { count: 0, leaves: 0 };
      current.count += count;
      current.leaves += 1;
      majors.set(major, current);
    }
  }
  const options = ['<option value="">全部分类</option>'];
  if (majors.size) {
    options.push('<optgroup label="按大类（含全部子类）">');
    for (const [major, info] of [...majors.entries()].sort((a, b) => b[1].count - a[1].count)) {
      options.push(`<option value="${escapeHtml(major)}">${escapeHtml(major)}（${info.count} 篇）</option>`);
    }
    options.push("</optgroup>");
  }
  if (leaves.length) {
    options.push('<optgroup label="按小类">');
    for (const leaf of [...leaves].sort((a, b) => b.count - a.count)) {
      options.push(`<option value="${escapeHtml(leaf.label)}">${escapeHtml(leaf.label)}（${leaf.count} 篇）</option>`);
    }
    options.push("</optgroup>");
  }
  select.innerHTML = options.join("");
  select.value = category;
}

export async function loadResearchCategories() {
  if (el("research-category")?.options.length > 1) return;
  try {
    const data = await api.get("/categories/tree");
    renderCategories(data.paths || []);
  } catch (error) {
    toast(`读取分类失败：${error.message}`, "error");
  }
}

export async function loadResearchPapers(force = false) {
  if (loaded && !force) return;
  const box = el("research-papers");
  if (box && force) box.innerHTML = '<p class="muted small" style="padding:8px">读取中…</p>';
  try {
    const query = new URLSearchParams({ limit: "300", sort: "updated" });
    if (category) query.set("category", category);
    const data = await api.get(`/papers?${query.toString()}`);
    papers = data.items || [];
    loaded = true;
    if (force) selected.clear();
    renderPapers();
    syncChrome();
  } catch (error) {
    toast(`读取论文列表失败：${error.message}`, "error");
  }
}

function selectAllVisible() {
  if (!currentMode().multi) return;
  const rows = visiblePapers();
  selected.clear();
  for (const paper of rows.slice(0, MAX_MULTI)) selected.add(paper.id);
  renderPapers();
  syncChrome();
  if (rows.length > MAX_MULTI) toast(`本范围共 ${rows.length} 篇，选了前 ${MAX_MULTI} 篇`, "info");
}

async function runSkill() {
  if (busy) return;
  const conf = currentMode();
  const ids = [...selected];
  if (!ids.length) return;
  busy = true;
  syncChrome();
  const status = el("research-status");
  const output = el("research-output");
  if (status) status.textContent = "正在整理资料并生成，长文任务通常 20–90 秒…";
  if (output) output.innerHTML = "";
  try {
    const payload = conf.multi ? { paper_ids: ids } : { paper_id: ids[0] };
    const result = await api.post(`/skills/${conf.skill}/run`, payload);
    const mapping = (result.papers || [])
      .map((paper) => `[${paper.index}] ${escapeHtml(paper.title)}`)
      .join("<br>");
    const mapHtml = mapping ? `<p class="muted small" style="margin-top:8px">引用编号：<br>${mapping}</p>` : "";
    if (output) output.innerHTML = md(result.text || "（模型没有返回内容）") + mapHtml;
    if (status) status.textContent = `完成 · ${new Date().toLocaleTimeString("zh-CN")}`;
  } catch (error) {
    if (status) status.textContent = "";
    toast(`生成失败：${error.message}`, "error");
  } finally {
    busy = false;
    syncChrome();
  }
}

export function initResearch() {
  document.querySelectorAll("[data-research-mode]").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (busy) return;
      mode = btn.dataset.researchMode || "map";
      if (!currentMode().multi) {
        // 切到单篇模式时只留第一篇，避免清空用户刚选的东西还不知道
        const first = [...selected][0];
        selected.clear();
        if (first) selected.add(first);
      }
      renderPapers();
      syncChrome();
    });
  });
  el("btn-research-run")?.addEventListener("click", runSkill);
  el("btn-research-refresh")?.addEventListener("click", async () => {
    await loadResearchCategories();
    await loadResearchPapers(true);
  });
  el("btn-research-selectall")?.addEventListener("click", selectAllVisible);
  el("btn-research-clear")?.addEventListener("click", () => {
    selected.clear();
    renderPapers();
    syncChrome();
  });
  el("research-category")?.addEventListener("change", async (event) => {
    if (busy) return;
    category = String(event.target.value || "");
    loaded = false;
    await loadResearchPapers(true);
  });
  el("research-search")?.addEventListener(
    "input",
    debounce(() => renderPapers(), 200)
  );
  el("btn-research-copy")?.addEventListener("click", async () => {
    const text = el("research-output")?.innerText || "";
    if (!text.trim()) return;
    try {
      await navigator.clipboard.writeText(text);
      toast("已复制", "success");
    } catch {
      toast("复制失败，请手动选择", "error");
    }
  });
  window.addEventListener("pw:research-shown", async () => {
    await loadResearchCategories();
    await loadResearchPapers(false);
  });
  syncChrome();
}
