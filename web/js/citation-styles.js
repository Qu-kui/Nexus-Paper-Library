/* 引文格式（自定义 / 外部导入）设置卡片。
 *
 * 后端契约（paperwhale_server/routers/cite.py，字段名固定）：
 *   GET    /api/cite/styles                 -> {items:[{id,name,builtin,numeric}]}
 *   GET    /api/cite/styles/{id}            -> {style, spec, preview:{inline,reference}}
 *   POST   /api/cite/styles/ai              {description, name?} -> {style, spec, preview}
 *   POST   /api/cite/styles/import          {csl, name?}        -> {style, spec, warnings, preview}
 *   POST   /api/cite/styles/{id}/preview    {spec?}             -> {inline, reference}
 *   DELETE /api/cite/styles/{id}            -> {ok:true}
 *
 * 挂载：settings.js 的 organizeSettings() 会清空 .settings-grid 再重建卡片，
 * 所以这里不在 index.html 里放东西，而是启动后把新卡片追加到 .settings-grid 末尾。
 */
import { api, toast } from "./api.js";

const LIST_LOADING = "加载中…";
const BUILTIN_FALLBACK_NOTE = "内置格式不可删除。";

let booted = false;
let scheduled = false;
let selectedId = "";
let previewRequest = 0;

const $ = (id) => document.getElementById(id);

function ensureStyleSheet() {
  if (document.getElementById("cs-style-sheet")) return;
  const style = document.createElement("style");
  style.id = "cs-style-sheet";
  style.textContent = `
    .cs-style-list { display: flex; flex-direction: column; gap: 6px; margin: 10px 0 4px; }
    .cs-style-row { display: flex; align-items: center; gap: 8px; padding: 6px 9px; border: 1px solid var(--line); border-radius: 8px; background: var(--surface); }
    .cs-style-row.selected { border-color: var(--accent); background: var(--accent-soft); }
    .cs-style-pick { flex: 1; min-width: 0; text-align: left; border: none; background: none; padding: 0; font-size: 13px; color: var(--ink); cursor: pointer; }
    .cs-style-pick:hover { color: var(--accent); }
    .cs-badge { flex-shrink: 0; font-size: 11px; padding: 1px 7px; border-radius: 999px; background: var(--line-soft); color: var(--ink-2); }
    .cs-badge.builtin { background: var(--accent-soft); color: var(--accent); }
    .cs-row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; margin: 6px 0 12px; }
    .cs-row input[type="text"] { flex: 1; min-width: 180px; }
    .cs-preview { margin-top: 10px; padding: 10px 12px; border: 1px solid var(--line); border-radius: 8px; background: var(--line-soft); }
    .cs-preview-line { margin: 4px 0; font-size: 13px; line-height: 1.6; overflow-wrap: anywhere; }
  `;
  document.head.append(style);
}

function skeleton() {
  return `
    <header><h3>引文格式</h3><span class="save-state muted small" id="cs-state" aria-live="polite"></span></header>
    <p class="muted small">内置 6 种；也可以用一句话描述你想要的格式，或导入 Zotero/CSL 的 .csl 文件。</p>
    <div id="cs-style-list" class="cs-style-list" aria-label="引文格式列表">${LIST_LOADING}</div>
    <label for="cs-description">用一句话描述你想要的格式</label>
    <textarea id="cs-description" rows="3" placeholder="只要前三个作者，多的写 et al.，年份放作者后面，期刊名斜体"></textarea>
    <div class="cs-row">
      <input id="cs-name" type="text" placeholder="格式名字（可选）" aria-label="格式名字（可选）" />
      <button class="btn primary" type="button" id="cs-generate">生成格式</button>
    </div>
    <label for="cs-csl-file">从 Zotero / CSL 导入</label>
    <div class="cs-row">
      <input id="cs-csl-file" type="file" accept=".csl,.xml,text/xml,application/xml" aria-label="选择 .csl 文件" />
      <button class="btn" type="button" id="cs-import">导入 .csl</button>
    </div>
    <p id="cs-warnings" class="muted small hidden" aria-live="polite"></p>
    <div id="cs-preview" class="cs-preview" aria-live="polite">
      <p class="cs-preview-line muted small">选中一个格式即可查看正文引文与参考文献的预览。</p>
    </div>
  `;
}

function setState(text) {
  const node = $("cs-state");
  if (node) node.textContent = text || "";
}

function setPreviewMessage(text) {
  const host = $("cs-preview");
  if (!host) return;
  host.replaceChildren();
  const line = document.createElement("p");
  line.className = "cs-preview-line muted small";
  line.textContent = text;
  host.append(line);
}

function renderPreview(preview) {
  const host = $("cs-preview");
  if (!host) return;
  host.replaceChildren();
  const inline = document.createElement("p");
  inline.className = "cs-preview-line";
  inline.textContent = `正文引文：${(preview && preview.inline) || "（暂无预览）"}`;
  const reference = document.createElement("p");
  reference.className = "cs-preview-line";
  reference.textContent = `参考文献：${(preview && preview.reference) || "（暂无预览）"}`;
  host.append(inline, reference);
}

function isCustom(item) {
  // 只有后端明确说 builtin=false 才当自定义，避免契约未就绪时误删内置格式。
  return item && item.builtin === false;
}

function badge(text, className) {
  const span = document.createElement("span");
  span.className = className ? `cs-badge ${className}` : "cs-badge";
  span.textContent = text;
  return span;
}

function renderStyleList(items) {
  const host = $("cs-style-list");
  if (!host) return;
  const rows = Array.isArray(items) ? items : [];
  host.replaceChildren();
  if (!rows.length) {
    const empty = document.createElement("p");
    empty.className = "muted small";
    empty.textContent = "还没有可用的引文格式。";
    host.append(empty);
    return;
  }
  for (const item of rows) {
    const row = document.createElement("div");
    row.className = "cs-style-row";
    row.dataset.styleId = String(item.id || "");
    if (item.id === selectedId) row.classList.add("selected");

    const pick = document.createElement("button");
    pick.type = "button";
    pick.className = "cs-style-pick";
    pick.textContent = item.name || item.id || "未命名格式";
    pick.addEventListener("click", () => selectStyle(item.id));

    row.append(pick);
    if (item.builtin === true) row.append(badge("内置", "builtin"));
    if (item.numeric === true) row.append(badge("数字"));
    if (isCustom(item)) {
      row.append(badge("自定义"));
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "btn tiny cs-delete";
      remove.textContent = "删除";
      remove.addEventListener("click", (event) => {
        event.stopPropagation();
        deleteStyle(item, remove);
      });
      row.append(remove);
    }
    row.addEventListener("click", (event) => {
      if (event.target.closest(".cs-delete")) return;
      selectStyle(item.id);
    });
    host.append(row);
  }
}

async function loadStyles() {
  const host = $("cs-style-list");
  if (!host) return;
  host.textContent = LIST_LOADING;
  try {
    const data = await api.get("/cite/styles");
    renderStyleList(data && Array.isArray(data.items) ? data.items : []);
  } catch (err) {
    host.textContent = `加载失败：${err.message || "请稍后再试"}`;
    toast(`引文格式加载失败：${err.message || "请稍后再试"}`, "error");
  }
}

async function selectStyle(id) {
  if (!id) return;
  selectedId = id;
  document.querySelectorAll(".cs-style-row").forEach((row) => {
    row.classList.toggle("selected", row.dataset.styleId === String(id));
  });
  const requestId = ++previewRequest;
  setState("加载预览中…");
  try {
    const data = await api.get(`/cite/styles/${encodeURIComponent(id)}`);
    if (requestId !== previewRequest) return;
    renderPreview(data && data.preview);
    setState("");
  } catch (err) {
    if (requestId !== previewRequest) return;
    setPreviewMessage(`预览加载失败：${err.message || "请稍后再试"}`);
    toast(`预览加载失败：${err.message || "请稍后再试"}`, "error");
    setState("");
  }
}

async function withBusy(button, busyText, work) {
  const previous = button ? button.textContent : "";
  if (button) {
    button.disabled = true;
    button.textContent = busyText;
  }
  try {
    return await work();
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = previous;
    }
  }
}

function optionalName() {
  const input = $("cs-name");
  const name = (input && input.value ? input.value : "").trim();
  return name ? { name } : {};
}

async function generateFromDescription() {
  const description = ($("cs-description") && $("cs-description").value ? $("cs-description").value : "").trim();
  if (!description) {
    toast("先描述一下你想要的格式", "error");
    $("cs-description")?.focus();
    return;
  }
  const button = $("cs-generate");
  await withBusy(button, "生成中…", async () => {
    try {
      const data = await api.post("/cite/styles/ai", { description, ...optionalName() });
      const style = (data && data.style) || {};
      toast(`已生成格式：${style.name || "自定义格式"}`, "success");
      await loadStyles();
      if (style.id) await selectStyle(style.id);
      else if (data && data.preview) renderPreview(data.preview);
    } catch (err) {
      toast(err.message || "生成格式失败", "error");
    }
  });
}

async function importCsl() {
  const fileInput = $("cs-csl-file");
  const file = fileInput && fileInput.files && fileInput.files[0];
  if (!file) {
    toast("先选择一个 .csl 文件", "error");
    return;
  }
  const button = $("cs-import");
  await withBusy(button, "导入中…", async () => {
    try {
      const csl = await file.text();
      const data = await api.post("/cite/styles/import", { csl, ...optionalName() });
      const warnings = data && Array.isArray(data.warnings) ? data.warnings.filter(Boolean) : [];
      const warningHost = $("cs-warnings");
      if (warningHost) {
        warningHost.textContent = warnings.length ? `警告：${warnings.join("；")}` : "";
        warningHost.classList.toggle("hidden", !warnings.length);
      }
      const style = (data && data.style) || {};
      toast(`已导入格式：${style.name || "自定义格式"}`, "success");
      await loadStyles();
      if (style.id) await selectStyle(style.id);
      else if (data && data.preview) renderPreview(data.preview);
    } catch (err) {
      toast(err.message || "导入 .csl 失败", "error");
    } finally {
      if (fileInput) fileInput.value = "";
    }
  });
}

async function deleteStyle(item, button) {
  if (!item || !item.id) return;
  const name = item.name || item.id;
  if (!confirm(`确定删除自定义格式「${name}」？`)) return;
  await withBusy(button, "删除中…", async () => {
    try {
      await api.del(`/cite/styles/${encodeURIComponent(item.id)}`);
      toast(`已删除格式：${name}`, "success");
      if (selectedId === item.id) {
        selectedId = "";
        setPreviewMessage("选中的格式已删除，请重新选择。");
      }
      await loadStyles();
    } catch (err) {
      toast(err.message || "删除格式失败", "error");
    }
  });
}

function mount(grid) {
  ensureStyleSheet();
  const section = document.createElement("section");
  section.className = "card settings-group";
  section.dataset.settingsGroup = "citation";
  section.innerHTML = skeleton();
  grid.append(section);

  $("cs-generate")?.addEventListener("click", generateFromDescription);
  $("cs-import")?.addEventListener("click", importCsl);
  $("cs-description")?.addEventListener("keydown", (event) => {
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      generateFromDescription();
    }
  });
  setPreviewMessage("选中一个格式即可查看正文引文与参考文献的预览。");
  if ($("cs-state")) $("cs-state").textContent = "";
  loadStyles();
}

export function initCitationStyles() {
  if (booted) return;
  const grid = document.querySelector(".settings-grid");
  if (!grid) {
    // initSettings 应该先跑；极少数时序问题下等一帧再挂，且只重试一次。
    if (!scheduled) {
      scheduled = true;
      setTimeout(() => {
        scheduled = false;
        initCitationStyles();
      }, 120);
    }
    return;
  }
  booted = true;
  mount(grid);
}
