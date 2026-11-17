/* 参考文献 / 被引外扩：以一篇论文为种子，拉 OpenAlex 的引用关系，勾选入库。 */
import { api, toast, escapeHtml, emit } from "./api.js";

let seedId = "";
let items = [];
let selected = new Set();
let loading = false;

function byId(id) {
  return document.getElementById(id);
}

function modal() {
  return byId("reference-modal");
}

function setStatus(text) {
  const el = byId("reference-status");
  if (el) el.textContent = text || "";
}

function updateImportButton() {
  const button = byId("btn-reference-import");
  if (!button) return;
  button.disabled = selected.size === 0 || loading;
  button.textContent = `入库选中（${selected.size}）`;
}

function itemLabel(item) {
  const bits = [];
  if (item.authors) bits.push(shorten(item.authors));
  if (item.year) bits.push(String(item.year));
  if (item.venue) bits.push(String(item.venue));
  if (item.cited_by_count) bits.push(`被引 ${item.cited_by_count}`);
  return bits.join(" · ");
}

function shorten(authors) {
  const list = String(authors || "").split(/[;；,，]/).map((part) => part.trim()).filter(Boolean);
  if (list.length <= 3) return list.join(", ");
  return `${list.slice(0, 3).join(", ")} 等`;
}

function render(data) {
  const list = byId("reference-list");
  if (!list) return;
  const seedTitle = (data && data.seed && data.seed.title) || "这篇论文";
  const direction = (data && data.direction) === "citations" ? "引用了" : "被它引用";
  setStatus(
    `${seedTitle}：${items.length} 条${data && data.truncated ? "（已截断）" : ""}`,
  );
  byId("reference-title").textContent =
    direction === "引用了" ? "谁引用了这篇论文" : "这篇论文引用的文献";
  if (!items.length) {
    list.innerHTML = '<p class="muted small">OpenAlex 没有返回这一侧的文献。</p>';
    return;
  }
  list.innerHTML = items
    .map((item, index) => {
      const existing = Boolean(item.existing_paper_id);
      const checked = selected.has(index) ? " checked" : "";
      return `<label class="reference-item${existing ? " in-library" : ""}">
        <input type="checkbox" data-index="${index}"${existing ? " disabled" : checked} />
        <div>
          <div class="rp-title" data-title="${index}">${escapeHtml(item.title || "（无标题）")}</div>
          <div class="rp-meta">${escapeHtml(itemLabel(item))}</div>
        </div>
        ${existing ? '<span class="rp-flag">已在库</span>' : ""}
      </label>`;
    })
    .join("");
}

async function load() {
  if (!seedId) return;
  loading = true;
  selected = new Set();
  const direction = (byId("reference-direction") || {}).value || "references";
  setStatus("正在问 OpenAlex…");
  byId("reference-list").innerHTML = "";
  updateImportButton();
  try {
    const data = await api.post("/references/expand", {
      paper_id: seedId,
      direction,
      limit: 40,
    });
    items = Array.isArray(data.items) ? data.items : [];
    items.forEach((item, index) => {
      if (!item.existing_paper_id) selected.add(index);
    });
    render(data);
  } catch (error) {
    items = [];
    setStatus(error.message);
    byId("reference-list").innerHTML = "";
  } finally {
    loading = false;
    updateImportButton();
  }
}

function toggle(index, checked) {
  if (checked) selected.add(index);
  else selected.delete(index);
  updateImportButton();
}

async function doImport() {
  if (!selected.size || loading) return;
  const button = byId("btn-reference-import");
  const chosen = [...selected].sort((a, b) => a - b).map((index) => items[index]).filter(Boolean);
  const fetchPdf = Boolean((byId("reference-fetch-pdf") || {}).checked);
  button.disabled = true;
  button.textContent = "入库中…";
  try {
    const result = await api.post("/references/import", { items: chosen, fetch_pdf: fetchPdf });
    const parts = [`新增 ${result.created || 0} 篇`];
    if (result.skipped) parts.push(`已存在 ${result.skipped} 篇`);
    if (result.failed && result.failed.length) parts.push(`失败 ${result.failed.length} 篇`);
    toast(parts.join("，"), result.failed && result.failed.length ? "info" : "success", 5000);
    if (result.queued) toast(`已排进 PDF 队列：${result.queued} 篇`, "info", 3000);
    emit("library-changed");
    if (result.created) close();
    else load();
  } catch (error) {
    toast(`入库失败：${error.message}`, "error", 5000);
  } finally {
    loading = false;
    updateImportButton();
  }
}

export async function openReferenceExpand(paperId) {
  if (!paperId) return;
  seedId = paperId;
  items = [];
  selected = new Set();
  const box = modal();
  if (!box) return;
  box.classList.remove("hidden");
  byId("reference-list").innerHTML = "";
  setStatus("正在问 OpenAlex…");
  updateImportButton();
  await load();
}

function close() {
  modal()?.classList.add("hidden");
  seedId = "";
  items = [];
  selected = new Set();
}

export function initReferenceExpand() {
  const box = modal();
  if (!box) return;
  window.PW = window.PW || {};
  window.PW.openReferenceExpand = openReferenceExpand;

  byId("reference-close")?.addEventListener("click", close);
  byId("reference-cancel")?.addEventListener("click", close);
  byId("btn-reference-load")?.addEventListener("click", load);
  byId("reference-direction")?.addEventListener("change", load);
  byId("btn-reference-import")?.addEventListener("click", doImport);

  byId("reference-list")?.addEventListener("change", (event) => {
    const input = event.target.closest("input[type=checkbox][data-index]");
    if (!input) return;
    toggle(Number(input.dataset.index), input.checked);
  });

  byId("reference-list")?.addEventListener("click", (event) => {
    const title = event.target.closest("[data-title]");
    if (!title) return;
    const index = Number(title.dataset.title);
    const item = items[index];
    if (!item) return;
    if (item.existing_paper_id) {
      close();
      window.PW?.openPaper?.(item.existing_paper_id);
      return;
    }
    event.preventDefault();
    const checkbox = byId("reference-list").querySelector(`input[data-index="${index}"]`);
    if (checkbox) {
      checkbox.checked = !checkbox.checked;
      toggle(index, checkbox.checked);
    }
  });

  byId("reference-select-all")?.addEventListener("click", () => {
    const fresh = items.map((item, index) => (item.existing_paper_id ? -1 : index)).filter((index) => index >= 0);
    const allSelected = fresh.length > 0 && fresh.every((index) => selected.has(index));
    selected = allSelected ? new Set() : new Set(fresh);
    items.forEach((item, index) => {
      const checkbox = byId("reference-list")?.querySelector(`input[data-index="${index}"]`);
      if (checkbox && !item.existing_paper_id) checkbox.checked = selected.has(index);
    });
    updateImportButton();
  });

  box.addEventListener("click", (event) => {
    if (event.target === box) close();
  });

  document.addEventListener(
    "keydown",
    (event) => {
      if (event.key !== "Escape") return;
      if (box.classList.contains("hidden")) return;
      event.stopPropagation();
      close();
    },
    true,
  );
}
