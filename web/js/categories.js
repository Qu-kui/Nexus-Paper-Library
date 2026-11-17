/* 分类确认弹窗：支持任意层级分类路径；AI 新建议必须用户确认。 */
import { api, toast, escapeHtml, emit, state } from "./api.js";

const byId = (id) => document.getElementById(id);
let initialized = false;
let currentPaperId = "";
let currentPaper = null;
let paths = [];

function showModal(show) {
  const modal = byId("category-modal");
  if (modal) modal.classList.toggle("hidden", !show);
}

function resetForms() {
  byId("category-existing")?.classList.add("hidden");
  byId("category-new")?.classList.add("hidden");
  for (const id of ["cat-new-major", "cat-new-minor", "cat-new-third", "cat-new-fourth"]) {
    const input = byId(id);
    if (input) input.value = "";
  }
}

async function loadTree() {
  try {
    const data = await api.get("/categories/tree");
    paths = Array.isArray(data.paths)
      ? data.paths
      : (Array.isArray(data.items) ? data.items.map((item) => ({
          label: item.major,
          path: [item.major],
          count: item.count,
        })) : []);
  } catch {
    paths = [];
  }
  return paths;
}

function fillExistingSelectors() {
  const select = byId("cat-major-select");
  if (!select) return;
  select.innerHTML = paths
    .map((item) => `<option value="${escapeHtml(item.label)}">${escapeHtml(item.label)}（${item.count || 0}）</option>`)
    .join("");
}

function levelsFromSuggestion(suggestion) {
  if (!suggestion) return [];
  if (Array.isArray(suggestion.path) && suggestion.path.length) return suggestion.path;
  const levels = [];
  if (suggestion.major) levels.push(suggestion.major);
  if (suggestion.minor) levels.push(suggestion.minor);
  return levels;
}

export async function promptCategory(paperId) {
  if (!paperId) return;
  try {
    // 列表缓存可能早于精读完成，必须取最新论文，否则会误报“AI 没有给出建议”。
    currentPaper = await api.get(`/papers/${paperId}`);
  } catch (err) {
    toast(`加载分类信息失败：${err.message}`, "error");
    return;
  }
  currentPaperId = paperId;
  const suggestion = currentPaper.category_suggest || {};
  const levels = levelsFromSuggestion(suggestion);
  const desc = byId("category-modal-desc");
  const proposal = byId("cat-proposal");
  const approve = byId("btn-cat-approve");
  if (desc) {
    desc.textContent = `《${currentPaper.title || "未命名论文"}》还没有归档分类。分类会决定 PDF 在「论文文件」文件夹里的完整路径。`;
  }
  if (proposal) {
    proposal.innerHTML = levels.length
      ? `AI 建议分类：<b>${levels.map((item) => escapeHtml(item)).join(" / ")}</b>
         <div class="muted small" style="margin-top:4px">同意后会在「论文文件」下按整条路径新建文件夹并移动 PDF。</div>`
      : "AI 没有给出分类建议，你可以从现有分类里选，或自己新建一条路径。";
  }
  if (approve) approve.classList.toggle("hidden", !levels.length);
  resetForms();
  showModal(true);
}

async function assignPath(levels) {
  const path = (levels || []).map((item) => String(item || "").trim()).filter(Boolean);
  if (!path.length) {
    toast("至少填一级分类", "error");
    return false;
  }
  try {
    const result = await api.post("/categories/assign", { paper_id: currentPaperId, path });
    toast(`已归档到：${result.paper?.category || path.join(" / ")}`, "success");
    showModal(false);
    emit("library-changed");
    return true;
  } catch (err) {
    toast(`分类失败：${err.message}`, "error", 5000);
    return false;
  }
}

async function approve() {
  try {
    const result = await api.post("/categories/approve", { paper_id: currentPaperId });
    toast(`已归档到：${result.paper?.category || "分类"}`, "success");
    showModal(false);
    emit("library-changed");
  } catch (err) {
    toast(`确认分类失败：${err.message}`, "error", 5000);
  }
}

async function dismiss() {
  try {
    await api.post("/categories/dismiss", { paper_id: currentPaperId });
  } catch {
    /* 忽略 */
  }
  showModal(false);
}

export function initCategories() {
  if (initialized) return;
  initialized = true;
  byId("category-modal-close")?.addEventListener("click", () => showModal(false));
  byId("btn-cat-later")?.addEventListener("click", dismiss);
  byId("btn-cat-approve")?.addEventListener("click", approve);
  byId("btn-cat-choose")?.addEventListener("click", async () => {
    await loadTree();
    if (!paths.length) {
      toast("还没有已建立的分类，直接新建一条路径吧", "info");
      byId("btn-cat-new")?.click();
      return;
    }
    fillExistingSelectors();
    byId("category-existing")?.classList.remove("hidden");
    byId("category-new")?.classList.add("hidden");
  });
  byId("btn-cat-new")?.addEventListener("click", () => {
    byId("category-new")?.classList.remove("hidden");
    byId("category-existing")?.classList.add("hidden");
  });
  byId("btn-cat-existing-ok")?.addEventListener("click", () => {
    const label = byId("cat-major-select")?.value || "";
    if (!label) {
      toast("先选择一个现有分类", "error");
      return;
    }
    assignPath(label.split(" / "));
  });
  byId("btn-cat-new-ok")?.addEventListener("click", () => {
    const levels = ["cat-new-major", "cat-new-minor", "cat-new-third", "cat-new-fourth"]
      .map((id) => String(byId(id)?.value || "").trim())
      .filter(Boolean);
    if (!levels.length) {
      toast("至少填一级分类", "error");
      return;
    }
    assignPath(levels);
  });
  window.PW = window.PW || {};
  window.PW.promptCategory = promptCategory;
}
