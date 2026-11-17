/* 分类筛选器：先只列第一级，点某一级才展开下一级，不再把所有分类路径平铺成一长串。
 *
 * 和旧 <select> 的差别：
 * - 面板一次只显示「当前这一层」，有子级的行带 › 箭头；点它就进这一级（顺带按它筛选）。
 * - 可以一路点到叶子；面板顶部有「← 返回上级」和「全部分类」。
 * - 仍然写回隐藏的 #filter-category，并派发 change 事件，library.js 原有逻辑不用改。
 *
 * 纯函数（buildCategoryTree / levelView / renderPanelHtml）刻意不碰 DOM，方便在 node 里直接测。
 */
const SEPARATOR = " / ";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  })[char]);
}

function pathText(levels) {
  return (levels || []).join(SEPARATOR);
}

function splitPath(label) {
  return String(label || "")
    .split(/\s*\/\s*/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/** 把 ["人工智能 / 光神经网络", …] 这种路径列表折成树；count 是「该路径自身的篇数」。 */
export function buildCategoryTree(paths) {
  const root = {name: "", path: [], own: 0, total: 0, children: new Map()};
  for (const item of paths || []) {
    const label = typeof item === "string" ? item : String((item && (item.label || item.name)) || "");
    const levels = splitPath(label);
    if (!levels.length) continue;
    const count = typeof item === "object" && item ? Number(item.count) || 0 : 0;
    let node = root;
    for (const level of levels) {
      if (!node.children.has(level)) {
        node.children.set(level, {
          name: level,
          path: [...node.path, level],
          own: 0,
          total: 0,
          children: new Map(),
        });
      }
      node = node.children.get(level);
    }
    node.own += count;
  }
  const sum = (node) => {
    let total = node.own;
    for (const child of node.children.values()) total += sum(child);
    node.total = total;
    return total;
  };
  sum(root);
  return root;
}

/** 只看某一层：返回这一层的条目（不会把整棵树平铺出来）。 */
export function levelView(tree, trail = []) {
  let node = tree;
  for (const level of trail || []) {
    const child = node && node.children ? node.children.get(level) : null;
    if (!child) return {items: [], trail: [...(tree && tree.path ? tree.path : [])]};
    node = child;
  }
  const items = [...node.children.values()]
    .sort((a, b) => b.total - a.total || a.name.localeCompare(b.name, "zh-Hans-CN"))
    .map((child) => ({
      name: child.name,
      path: [...child.path],
      total: child.total,
      hasChildren: child.children.size > 0,
    }));
  return {items, trail: [...node.path]};
}

export function renderPanelHtml(view, selected = "") {
  const trail = (view && view.trail) || [];
  const rows = [];
  const allActive = !selected ? " active" : "";
  rows.push(
    `<button type="button" class="category-picker-item${allActive}" data-picker-path="" role="option" aria-selected="${!selected}">` +
      `<span class="category-picker-name">全部分类</span></button>`,
  );
  if (trail.length) {
    const parentLabel = trail.length > 1 ? trail[trail.length - 2] : "全部分类";
    rows.push(
      `<button type="button" class="category-picker-back" data-picker-back>` +
        `<span aria-hidden="true">←</span> 返回${trail.length > 1 ? `「${escapeHtml(parentLabel)}」` : "全部分类"}</button>`,
    );
    rows.push(
      `<div class="category-picker-current" title="${escapeHtml(pathText(trail))}">${escapeHtml(pathText(trail))}</div>`,
    );
  }
  for (const item of (view && view.items) || []) {
    const value = pathText(item.path);
    const active = selected === value ? " active" : "";
    const caret = item.hasChildren
      ? '<span class="category-picker-caret" aria-hidden="true">›</span>'
      : '<span class="category-picker-caret" aria-hidden="true"></span>';
    const count = item.total ? `<span class="category-picker-count">${item.total}</span>` : "";
    rows.push(
      `<button type="button" class="category-picker-item${active}" role="option" aria-selected="${selected === value}"` +
        ` data-picker-path="${escapeHtml(value)}" data-picker-has-children="${item.hasChildren ? "1" : "0"}">` +
        `${caret}<span class="category-picker-name">${escapeHtml(item.name)}</span>${count}</button>`,
    );
  }
  if (!((view && view.items) || []).length) {
    rows.push('<p class="category-picker-empty">这一级还没有子分类</p>');
  }
  return rows.join("");
}

let tree = null;
let trail = [];
let selected = "";

/** 点击某一行的结果（纯函数，方便测试）：选中它；有子级就进这一级，没有子级就收面板。 */
export function pickRowOutcome(value, hasChildren) {
  const path = value ? String(value).split(SEPARATOR) : [];
  return {
    selected: value || "",
    trail: hasChildren ? [...path] : path.slice(0, -1),
    close: !hasChildren,
  };
}

const byId = (id) => document.getElementById(id);

function buttonLabel() {
  return selected ? (selected.split(SEPARATOR).join(" › ")) : "全部分类";
}

function renderPanel() {
  const panel = byId("filter-category-panel");
  if (!panel) return;
  panel.innerHTML = renderPanelHtml(levelView(tree, trail), selected);
}

function syncButton() {
  const button = byId("filter-category-button");
  if (button) {
    button.textContent = buttonLabel();
    button.title = selected || "全部分类";
  }
}

function closePanel() {
  const panel = byId("filter-category-panel");
  const button = byId("filter-category-button");
  panel?.classList.add("hidden");
  button?.setAttribute("aria-expanded", "false");
}

function openPanel() {
  const panel = byId("filter-category-panel");
  const button = byId("filter-category-button");
  if (!panel) return;
  if (!trail.length && selected) trail = selected.split(SEPARATOR).slice(0, -1);
  renderPanel();
  panel.classList.remove("hidden");
  button?.setAttribute("aria-expanded", "true");
}

function applySelection(value) {
  selected = value;
  const input = byId("filter-category");
  if (input) {
    input.value = value;
    input.dispatchEvent(new Event("change", {bubbles: true}));
  }
  syncButton();
}

/** 重建分类树（每次刷新文献库都会调用）。 */
export function setCategoryPaths(paths) {
  tree = buildCategoryTree(paths);
  const panel = byId("filter-category-panel");
  if (panel && !panel.classList.contains("hidden")) renderPanel();
}

/** 外部重置筛选时同步按钮文字（隐藏 input 的 value 由调用方设置）。 */
export function syncCategoryPicker(value = "") {
  selected = String(value || "");
  trail = selected ? selected.split(SEPARATOR).slice(0, -1) : [];
  syncButton();
}

export function initCategoryPicker() {
  const button = byId("filter-category-button");
  const panel = byId("filter-category-panel");
  const input = byId("filter-category");
  if (!button || !panel || !input) return;
  selected = input.value || "";
  syncButton();

  button.addEventListener("click", (event) => {
    event.stopPropagation();
    if (panel.classList.contains("hidden")) openPanel();
    else closePanel();
  });
  panel.addEventListener("click", (event) => {
    event.stopPropagation();
    if (event.target.closest("[data-picker-back]")) {
      trail = trail.slice(0, -1);
      renderPanel();
      return;
    }
    const row = event.target.closest("[data-picker-path]");
    if (!row) return;
    const value = row.dataset.pickerPath || "";
    const outcome = pickRowOutcome(value, row.dataset.pickerHasChildren === "1");
    applySelection(outcome.selected);
    trail = outcome.trail;
    if (outcome.close) closePanel();
    else renderPanel();
  });
  document.addEventListener("click", () => closePanel());
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closePanel();
  });
}
