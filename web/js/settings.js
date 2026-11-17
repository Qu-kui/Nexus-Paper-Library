/* NPL · 设置页
 * 大模型 API 配置 + 研究画像保存 + 连接测试
 * 依赖：api.js（api、toast、emit、state）
 */
import { api, toast, emit, state } from "./api.js";
import {
  ACCENT_PRESETS, DEFAULT_ACCENT, DEFAULT_THEME, THEME_PRESETS,
  applyAppearance, getTheme, normalizeAccent,
} from "./theme.js";

let initialized = false;
let hintTimer = null;
const originalValues = new Map();
const groupFields = {model:["base_url","api_key","model","reasoning_model","vision_model","app_api_enabled","thinking_mode","web_search_mode","paper_file_mode","answer_context_chars","auto_summary"],pdf:["openalex_api_key","openalex_content_enabled","unpaywall_email","pdf_browser","pdf_browser_profile","pdf_browser_session_enabled"],clipboard:["clipboard_watch","clipboard_title_guess"],reading:["language","profile_name","profile"],storage:["pdf_dir"],appearance:["ui_theme","ui_accent"]};
const fieldIds={base_url:"set-base-url",api_key:"set-api-key",model:"set-model",reasoning_model:"set-reasoning-model",vision_model:"set-vision-model",openalex_api_key:"set-openalex-key",openalex_content_enabled:"set-openalex-content",unpaywall_email:"set-unpaywall-email",pdf_browser:"set-pdf-browser",pdf_browser_profile:"set-pdf-browser-profile",pdf_browser_session_enabled:"set-pdf-browser-session",clipboard_watch:"set-clipboard-watch",clipboard_title_guess:"set-clipboard-title",language:"set-language",profile:"set-profile",profile_name:"set-profile-name",pdf_dir:"set-pdf-dir",ui_theme:"set-ui-theme",ui_accent:"set-ui-accent",app_api_enabled:"set-app-api",thinking_mode:"set-thinking-mode",web_search_mode:"set-web-search-mode",paper_file_mode:"set-paper-file-mode",answer_context_chars:"set-context-chars",auto_summary:"set-auto-summary"};
const inputValue=input=>input.type==="checkbox"?input.checked:input.value;
const isDirty=input=>originalValues.has(input.id)&&originalValues.get(input.id)!==inputValue(input);
function updateDirty() {
  let count = 0;
  document.querySelectorAll('[data-settings-group]').forEach(group => {
    const dirty = [...group.querySelectorAll('input,textarea,select')].some(isDirty);
    const label = group.querySelector('.save-state');
    if (label) label.textContent = dirty ? '有未保存修改' : '已保存';
    group.classList.toggle('is-dirty', dirty);
    const link = document.querySelector(`[data-settings-target="${group.dataset.settingsGroup}"]`);
    if (link) {
      link.classList.toggle('is-dirty', dirty);
      link.setAttribute('aria-label', `${link.textContent}${dirty ? '，有未保存修改' : ''}`);
    }
    if (dirty) count++;
  });
  const summary = byId('settings-save-summary');
  if (summary) summary.textContent = count ? `${count} 组有未保存修改` : '设置已保存';
}
function acceptSaved(payload){for(const [field,value] of Object.entries(payload)){const input=byId(fieldIds[field]);if(input)originalValues.set(input.id,value);}updateDirty();}


const byId = (id) => document.getElementById(id);

/* ---------- 外观：主题预设 + 强调色 ---------- */
function accentValue() {
  return normalizeAccent(byId("set-ui-accent")?.value || DEFAULT_ACCENT);
}

function themeValue() {
  const key = byId("set-ui-theme")?.value || DEFAULT_THEME;
  return THEME_PRESETS.some((preset) => preset.key === key) ? key : DEFAULT_THEME;
}

function previewAppearance() {
  applyAppearance({ theme: themeValue(), accent: accentValue() });
  syncAppearancePickers();
  updateDirty();
}

function syncAppearancePickers() {
  const accentHost = byId("accent-swatches");
  if (accentHost) {
    const current = accentValue();
    [...accentHost.children].forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.accent === current));
    });
  }
  const themeHost = byId("theme-presets");
  if (themeHost) {
    const current = themeValue();
    [...themeHost.children].forEach((button) => {
      button.setAttribute("aria-pressed", String(button.dataset.theme === current));
    });
  }
}

function buildAccentSwatches() {
  const host = byId("accent-swatches");
  if (!host) return;
  host.replaceChildren();
  for (const preset of ACCENT_PRESETS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "accent-swatch";
    button.dataset.accent = preset.value;
    button.style.background = preset.value;
    button.title = preset.name;
    button.setAttribute("aria-label", preset.name);
    button.setAttribute("aria-pressed", "false");
    button.addEventListener("click", () => {
      const input = byId("set-ui-accent");
      if (input) input.value = preset.value;
      previewAppearance();
    });
    host.append(button);
  }
  syncAppearancePickers();
}

function buildThemePresets() {
  const host = byId("theme-presets");
  if (!host) return;
  host.replaceChildren();
  for (const preset of THEME_PRESETS) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "theme-preset";
    button.dataset.theme = preset.key;
    button.title = preset.name;
    button.setAttribute("aria-label", preset.name);
    button.setAttribute("aria-pressed", "false");
    const chip = document.createElement("span");
    chip.className = "theme-preset-chip";
    chip.style.background = `linear-gradient(135deg, ${preset.shell.bg} 0 46%, ${preset.content.bg} 46% 100%)`;
    const dot = document.createElement("span");
    dot.className = "theme-preset-dot";
    dot.style.background = preset.accent;
    chip.append(dot);
    const label = document.createElement("span");
    label.className = "theme-preset-name";
    label.textContent = preset.name;
    button.append(chip, label);
    button.addEventListener("click", () => {
      const themeInput = byId("set-ui-theme");
      const accentInput = byId("set-ui-accent");
      if (themeInput) themeInput.value = preset.key;
      if (accentInput) accentInput.value = preset.accent;
      previewAppearance();
    });
    host.append(button);
  }
  syncAppearancePickers();
}

/* ---------- 小工具 ---------- */
async function runWithBusy(button, label, task) {
  if (!button) return task();
  const original = button.textContent;
  button.disabled = true;
  button.textContent = label;
  try {
    return await task();
  } finally {
    if (button.isConnected) {
      button.disabled = false;
      button.textContent = original;
    }
  }
}

function truncate(text, max = 60) {
  const clean = String(text || "").replace(/\s+/g, " ").trim();
  return clean.length > max ? `${clean.slice(0, max)}…` : clean;
}

function resultText(result) {
  if (result == null) return "";
  if (typeof result === "string") return result;
  const candidates = [
    result.text,
    result.translation,
    result.data && result.data.text,
    result.content,
  ];
  for (const value of candidates) {
    if (typeof value === "string" && value.trim()) return value;
  }
  try {
    return JSON.stringify(result);
  } catch {
    return "";
  }
}

function isLlmMissing(err) {
  return /未配置|未设置|not configured|api[_ ]?key/i.test(String((err && err.message) || ""));
}

/* ---------- 表单读写 ---------- */
function readForm() {
  const value = (id) => {
    const input = byId(id);
    return input ? input.value : "";
  };

  const payload = {
    base_url: String(value("set-base-url") || "").trim(),
    api_key: String(value("set-api-key") || "").trim(),
    model: String(value("set-model") || "").trim(),
    reasoning_model: String(value("set-reasoning-model") || "").trim(),
    vision_model: String(value("set-vision-model") || "").trim(),
    language: value("set-language") || "zh",
    pdf_dir: String(value("set-pdf-dir") || "").trim(),
    profile: value("set-profile") || "",
    profile_name: String(value("set-profile-name") || "").trim(),
    openalex_api_key:String(value('set-openalex-key') || '').trim(),
    openalex_content_enabled:Boolean(byId('set-openalex-content')?.checked),
    unpaywall_email:String(value('set-unpaywall-email') || '').trim(),
    pdf_browser:value('set-pdf-browser') || 'Edge',
    pdf_browser_profile:String(value('set-pdf-browser-profile') || 'Default').trim(),
    pdf_browser_session_enabled:Boolean(byId('set-pdf-browser-session')?.checked),
    clipboard_watch:Boolean(byId('set-clipboard-watch')?.checked),
    clipboard_title_guess:Boolean(byId('set-clipboard-title')?.checked),
    thinking_mode:value('set-thinking-mode') || 'on',
    app_api_enabled:Boolean(byId('set-app-api')?.checked),
    web_search_mode:value('set-web-search-mode') || 'auto',
    paper_file_mode:value('set-paper-file-mode') || 'scanned',
    answer_context_chars:Number(value('set-context-chars') || 60000),
    auto_summary:Boolean(byId('set-auto-summary')?.checked),
    ui_theme: themeValue(),
    ui_accent: accentValue(),
  };

  // API Key 输入框被清空时，不要顺手把已保存的 Key 抹掉
  const savedKey = state.settings && state.settings.api_key;
  if (!payload.api_key && savedKey) delete payload.api_key;
  if (!payload.openalex_api_key) delete payload.openalex_api_key;
  return payload;
}

function isEditingForm() {
  const active = document.activeElement;
  if (!active || !active.closest) return false;
  if (!active.closest('[data-page="settings"]')) return false;
  return active.matches("input, textarea, select");
}

function fillForm(settings) {
  if (!settings) return;

  const setValue = (id, value) => {
    const input = byId(id);
    if (input && !isDirty(input) && value !== undefined && value !== null) { input.value = value; originalValues.set(id,inputValue(input)); }
  };
  setValue("set-base-url", settings.base_url ?? "");
  setValue("set-api-key", settings.api_key ?? "");
  setValue("set-model", settings.model ?? "");
  setValue("set-reasoning-model", settings.reasoning_model ?? "");
  setValue("set-vision-model", settings.vision_model ?? "");
  setValue("set-thinking-mode", settings.thinking_mode || "on");
  setValue("set-web-search-mode", settings.web_search_mode || "auto");
  setValue("set-paper-file-mode", settings.paper_file_mode || "scanned");
  setValue("set-context-chars", settings.answer_context_chars ?? 60000);
  const summaryToggle = byId("set-auto-summary");
  if (summaryToggle) summaryToggle.checked = settings.auto_summary !== false;
  const appApiToggle = byId("set-app-api");
  if (appApiToggle && settings.app_api_enabled !== undefined && settings.app_api_enabled !== null) {
    appApiToggle.checked = Boolean(settings.app_api_enabled);
  }
  setValue("set-language", settings.language || "zh");
  setValue("set-pdf-dir", settings.pdf_dir ?? "");
  setValue("set-profile", settings.profile ?? "");
  setValue("set-profile-name", settings.profile_name ?? "");
  setValue('set-pdf-browser',settings.pdf_browser || 'Edge');
  setValue('set-pdf-browser-profile',settings.pdf_browser_profile || 'Default');
  const browserSession=byId('set-pdf-browser-session'); if(browserSession && !isDirty(browserSession)){browserSession.checked=settings.pdf_browser_session_enabled!==false;originalValues.set(browserSession.id,browserSession.checked);}
  setValue('set-unpaywall-email',settings.unpaywall_email || '');
  setValue('set-openalex-key','');
  const content=byId('set-openalex-content');if(content && !isDirty(content)){content.checked=Boolean(settings.openalex_content_enabled);originalValues.set(content.id,content.checked);}
  const clipboardWatch=byId('set-clipboard-watch');if(clipboardWatch && !isDirty(clipboardWatch)){clipboardWatch.checked=settings.clipboard_watch!==false;originalValues.set(clipboardWatch.id,clipboardWatch.checked);}
  const clipboardTitle=byId('set-clipboard-title');if(clipboardTitle && !isDirty(clipboardTitle)){clipboardTitle.checked=settings.clipboard_title_guess!==false;originalValues.set(clipboardTitle.id,clipboardTitle.checked);}
  if (byId('set-openalex-key')) byId('set-openalex-key').placeholder=settings.openalex_api_key_configured ? '已保存 Key；留空保留，填写可替换' : '填写自己的 OpenAlex Key';
  setValue("set-ui-theme", settings.ui_theme || DEFAULT_THEME);
  setValue("set-ui-accent", normalizeAccent(settings.ui_accent || DEFAULT_ACCENT));
  const accentInput = byId("set-ui-accent");
  const themeInput = byId("set-ui-theme");
  const appearanceEdited = (accentInput && isDirty(accentInput)) || (themeInput && isDirty(themeInput));
  if (!appearanceEdited) applyAppearance({ theme: settings.ui_theme || DEFAULT_THEME, accent: settings.ui_accent || DEFAULT_ACCENT });
  syncAppearancePickers();
  updateDirty();
  loadBrowserProfiles();
  const effective = byId("pdf-dir-effective");
  if (effective) effective.textContent = settings.pdf_dir_effective || "—";
}

function showProfileHint(text) {
  const hint = byId("profile-saved-hint");
  if (!hint) return;
  hint.textContent = text;
  clearTimeout(hintTimer);
  hintTimer = setTimeout(() => {
    hint.textContent = "";
  }, 4000);
}

async function loadBrowserProfiles() {
  try {
    const profiles=await api.get('/browser/profiles');
    const list=byId('pdf-browser-profiles');
    if (!list) return;
    list.replaceChildren();
    for (const name of profiles[byId('set-pdf-browser')?.value || 'Edge'] || []) {
      const option=document.createElement('option');
      option.value=name;
      list.append(option);
    }
  } catch {}
}

/* ---------- 保存 ---------- */
async function migratePdfs(oldDir = "", button = null) {
  const run = async () => {
    const result = await api.post("/settings/pdf-dir/migrate", { old_dir: oldDir || "" });
    const moved = Number(result.moved) || 0;
    const errors = Array.isArray(result.errors) ? result.errors : [];
    if (errors.length) {
      toast(`已迁移 ${moved} 篇，${errors.length} 篇失败：${errors[0]}`, "error", 6000);
    } else {
      toast(`已迁移 ${moved} 篇 PDF 到当前目录`, "success", 4000);
    }
    emit("library-changed");
    if (result.new_root) {
      const effective = byId("pdf-dir-effective");
      if (effective) effective.textContent = result.new_root;
    }
  };
  if (button) {
    await runWithBusy(button, "迁移中…", run);
  } else {
    await run();
  }
}

async function saveSettings(button, group = "") {
  const form = readForm();
  const payload = group ? Object.fromEntries(Object.entries(form).filter(([field])=>groupFields[group].includes(field))) : form;
  await runWithBusy(button, "保存中…", async () => {
    try {
      const saved = await api.put("/settings", payload);
      state.settings = saved || state.settings;
      acceptSaved(payload);
      fillForm(saved);
      if (saved) applyAppearance({ theme: saved.ui_theme || DEFAULT_THEME, accent: saved.ui_accent || DEFAULT_ACCENT });
      toast("设置已保存", "success");
      emit("settings-changed");
      const pending = Number(saved && saved.pending_migrations) || 0;
      if (pending > 0) {
        const confirmed = window.confirm(
          `检测到 ${pending} 篇已有 PDF 还在旧目录。\n\n是否现在迁移到新的保存目录？迁移后数据库路径会同步更新。`,
        );
        if (confirmed) {
          await migratePdfs((saved && saved.previous_pdf_dir) || "");
        }
      }
    } catch (err) {
      toast(`保存失败：${err.message || "未知错误"}`, "error", 5000);
    }
  });
}

async function saveProfile(button) {
  const payload = {
    profile: (byId("set-profile") && byId("set-profile").value) || "",
    profile_name: String((byId("set-profile-name") && byId("set-profile-name").value) || "").trim(),
  };
  await runWithBusy(button, "保存中…", async () => {
    try {
      const saved = await api.put("/settings", payload);
      state.settings = saved || state.settings;
      acceptSaved(payload);
      showProfileHint("画像已保存 ✓");
      toast("研究画像已保存", "success");
      emit("settings-changed");
    } catch (err) {
      toast(`保存失败：${err.message || "未知错误"}`, "error", 5000);
    }
  });
}

/* ---------- 测试连接 ---------- */
async function testLlm(button) {
  await runWithBusy(button,"测试中…",async()=>{
    const payload=Object.fromEntries(Object.entries(readForm()).filter(([field])=>groupFields.model.includes(field)));
    const status=byId('model-connection-status');
    try{
      const saved=await api.put('/settings',payload);state.settings=saved||state.settings;acceptSaved(payload);
      const result=await api.post('/settings/test-connection');
      if(status){status.textContent=result.state==='success'?`最近连接成功 · ${result.model||''}`:`最近连接失败：${result.error||'未收到响应'}`;status.classList.toggle('connection-error',result.state==='failed');}
      emit('settings-changed');toast(result.state==='success'?'连接成功':`连接失败：${result.error||'未知错误'}`,result.state==='success'?'success':'error');
    }catch(error){if(status){status.textContent=`连接失败：${error.message}`;status.classList.add('connection-error');}toast(error.message,'error');}
  });
}

/* ---------- 加载 ---------- */
export async function refreshSettings() {
  try {
    const settings = await api.get("/settings");
    state.settings = settings;
    fillForm(settings);
  } catch (err) {
    toast(`读取设置失败：${err.message || "未知错误"}`, "error", 4500);
  }
}

/* ---------- 浏览器 / Word 插件 ---------- */
async function loadPluginState() {
  try {
    const data = await api.get("/plugins");
    const extState = byId("plugin-ext-state");
    const wordState = byId("plugin-word-state");
    if (extState) {
      extState.textContent = data.extension_exists ? "已随安装包附带" : "未找到 browser-extension 目录";
    }
    if (wordState) {
      const installed = Array.isArray(data.word_installed) ? data.word_installed : [];
      wordState.textContent = data.word_ok
        ? "已安装到 Word（重启 Word 生效）"
        : installed.length
          ? `部分安装：${installed.join("、")}`
          : "未安装到 Word";
    }
    const installButton = byId("btn-install-word");
    if (installButton) installButton.classList.toggle("hidden", Boolean(data.word_ok));
    const hint = byId("plugin-hint");
    if (hint) {
      hint.textContent = data.extension_exists
        ? "浏览器插件已随安装包附带：打开扩展文件夹后，在 Chrome/Edge 开发者模式里「加载已解压的扩展程序」。Word 插件装到 Word 的 STARTUP 目录，重启 Word 后功能区会多一个 NPL 标签。"
        : "安装包里没找到 browser-extension，请重新安装完整包。";
    }
  } catch (err) {
    const hint = byId("plugin-hint");
    if (hint) hint.textContent = `插件状态读取失败：${err.message}`;
  }
}

/* ---------- 初始化 ---------- */
export function initSettings() {
  if (initialized) return;
  initialized = true;
  organizeSettings();
  byId('set-pdf-browser')?.addEventListener('change',loadBrowserProfiles);

  byId("btn-save-settings")?.addEventListener("click", (event) => {
    saveSettings(event.currentTarget);
  });
  byId("btn-save-profile")?.addEventListener("click", (event) => {
    saveProfile(event.currentTarget);
  });
  byId("btn-test-llm")?.addEventListener("click", (event) => {
    testLlm(event.currentTarget);
  });
  byId("btn-migrate-pdfs")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const confirmed = window.confirm(
      "把已有 PDF 迁移到当前保存目录？\n\n会按「论文文件/大类/小类」重新归档，并更新数据库里的路径。",
    );
    if (!confirmed) return;
    try {
      await migratePdfs("", button);
    } catch (err) {
      toast(`迁移失败：${err.message}`, "error", 5000);
    }
  });
  byId("btn-open-extension")?.addEventListener("click", async () => {
    try {
      await api.post("/plugins/open", { kind: "extension" });
    } catch (err) {
      toast(`打开失败：${err.message}`, "error");
    }
  });
  byId("btn-open-word")?.addEventListener("click", async () => {
    try {
      await api.post("/plugins/open", { kind: "word" });
    } catch (err) {
      toast(`打开失败：${err.message}`, "error");
    }
  });
  byId("btn-install-word")?.addEventListener("click", async (event) => {
    const button = event.currentTarget;
    const original = button.textContent;
    button.disabled = true;
    button.textContent = "安装中…";
    try {
      await api.post("/plugins/install-word");
      toast("Word 插件已安装，重启 Word 后生效", "success", 5000);
      await loadPluginState();
    } catch (err) {
      toast(`安装失败：${err.message}`, "error", 5000);
    } finally {
      button.disabled = false;
      button.textContent = original;
    }
  });
  byId("btn-quit-app")?.addEventListener("click", async () => {
    if (!window.confirm("确定退出 NPL 吗？正在进行的任务会中断。")) return;
    try {
      await api.post("/app/quit");
    } catch {
      /* 进程退出时请求可能被中断，属于正常现象 */
    }
    window.close();
  });
  loadPluginState();
}

function organizeSettings(){
  const grid=document.querySelector('.settings-grid');if(!grid)return;
  const createGroup=(key,title)=>{const section=document.createElement('section');section.className='card settings-group';section.dataset.settingsGroup=key;section.innerHTML=`<header><h3>${title}</h3><span class="save-state muted small" aria-live="polite">已保存</span></header>`;grid.append(section);return section;};
  const oldCards=[...grid.children];
  const sections={model:createGroup('model','模型与连接'),pdf:createGroup('pdf','PDF 来源与登录'),clipboard:createGroup('clipboard','剪贴板监听'),reading:createGroup('reading','阅读与 AI / 研究画像'),storage:createGroup('storage','数据与存储'),appearance:createGroup('appearance','外观')};
  for(const [group,fields]of Object.entries(groupFields))for(const field of fields){const input=byId(fieldIds[field]);const label=input?.closest('label');if(label)sections[group].append(label);}
  const themePresets=byId('theme-presets');if(themePresets)sections.appearance.append(themePresets);
  const accentSwatches=byId('accent-swatches');if(accentSwatches)sections.appearance.append(accentSwatches);
  const accentReset=byId('btn-accent-reset');if(accentReset)sections.appearance.append(accentReset);
  const datalist=byId('pdf-browser-profiles');if(datalist)sections.pdf.append(datalist);
  sections.model.insertAdjacentHTML('beforeend','<p id="model-connection-status" class="muted small" role="status">已配置未测试</p><p class="muted small">测试连接会先保存本组配置。API Key 留空保留现有值。</p>');
  sections.pdf.insertAdjacentHTML('beforeend','<p class="muted small">OpenAlex 托管下载可能消耗账户额度。浏览器配置应选择已登录学校或出版社的配置；保存后用于下次获取。</p>');
  sections.clipboard.insertAdjacentHTML('beforeend','<p class="muted small">在本机盯着剪贴板：复制论文 DOI / arXiv 链接，或复制像论文题目的文字，窗口左下角会问要不要入库。只在本机内存里比对最近几条，不写日志、不上传。</p><p class="muted small">从自己库里复制标题不会弹（已在库的直接跳过）。</p>');
  sections.reading.insertAdjacentHTML('beforeend','<p class="muted small">AI 默认联网和深度思考。阅读位置、缩放、列表密度和列设置自动保存在本机，无需点保存。</p>');
  const profileActions=document.querySelector('.profile-actions');if(profileActions)sections.reading.append(profileActions);
  for(const id of ['pdf-dir-effective','btn-migrate-pdfs']){const node=byId(id);if(node)sections.storage.append(id==='pdf-dir-effective'?node.parentElement:node);}
  sections.storage.insertAdjacentHTML('beforeend','<button class="btn tiny" id="btn-open-pdf-dir">打开当前 PDF 目录</button><p class="muted small">目录修改需先保存本组，再迁移已有 PDF。</p><div class="maintenance-actions"><button class="btn tiny" id="btn-backfill-bibliographic" title="按 DOI 从 Crossref 补全缺失的卷/期/页码">补齐卷期页</button><button class="btn tiny" id="btn-backup-data">下载数据备份</button><button class="btn tiny" id="btn-restore-data">从备份恢复</button><input id="restore-data-file" type="file" accept=".zip" aria-label="选择数据备份" hidden><button class="btn tiny" id="btn-load-trash">查看回收站</button></div><p class="muted small">「补齐卷期页」只补空值，不改已有数据；参考文献排版要用到卷期页。备份包含文献元数据、笔记、批注及对话，不包含 PDF 文件和 API 密钥。请另外备份 PDF 目录；恢复会替换当前元数据，恢复前会自动生成本地备份；PDF 实体和连接密钥不会被覆盖。</p><div id="maintenance-results" aria-live="polite"></div>');
  oldCards.forEach(card=>card.remove());
  organizeSettingsNavigation(grid, sections);
  buildThemePresets();
  buildAccentSwatches();
  const accentInput=byId('set-ui-accent');accentInput?.addEventListener('input',previewAppearance);
  byId('btn-accent-reset')?.addEventListener('click',()=>{
    const themeInput=byId('set-ui-theme');if(themeInput)themeInput.value=DEFAULT_THEME;
    const input=byId('set-ui-accent');if(input)input.value=DEFAULT_ACCENT;
    previewAppearance();
  });
  for(const[key,section]of Object.entries(sections)){const button=document.createElement('button');button.className='btn tiny group-save';button.textContent='保存本组';button.addEventListener('click',()=>saveSettings(button,key));section.append(button);}
  document.querySelectorAll('[data-page="settings"] input,[data-page="settings"] textarea,[data-page="settings"] select').forEach(input=>{originalValues.set(input.id,inputValue(input));input.addEventListener('input',updateDirty);input.addEventListener('change',updateDirty);});
  byId('btn-open-pdf-dir')?.addEventListener('click',async()=>{try{await api.post('/settings/pdf-dir/open');}catch(error){toast(`打开目录失败：${error.message}`,'error');}});
  byId('btn-backfill-bibliographic')?.addEventListener('click',async event=>{
    const target=byId('maintenance-results');
    await runWithBusy(event.currentTarget,'启动中…',async()=>{
      try{
        const result=await api.post('/papers/backfill-bibliographic',{limit:300});
        if(!result.started){if(target)target.textContent='已经在补齐了，稍后看结果。';return;}
        if(target)target.textContent=`已开始：共 ${result.total} 篇待补。`;
        toast('开始补齐卷期页（按 DOI 查 Crossref）','success');
        const poll=async()=>{
          const status=await api.get('/papers/backfill-bibliographic/status').catch(()=>null);
          if(!status)return;
          const failed=Array.isArray(status.failed)?status.failed.length:0;
          if(target)target.textContent=`补齐卷期页：${status.done||0}/${status.total||0}，已更新 ${status.updated||0} 篇${failed?`，失败 ${failed} 篇`:''}`;
          if(status.running){setTimeout(poll,1500);return;}
          toast(`卷期页补齐完成：更新 ${status.updated||0} 篇`,'success',5000);
          emit('library-changed');
        };
        poll();
      }catch(error){toast(`补齐失败：${error.message}`,'error');}
    });
  });
  byId('btn-backup-data')?.addEventListener('click',async event=>{await runWithBusy(event.currentTarget,'备份中…',async()=>{try{const result=await api.post('/maintenance/backup');const link=document.createElement('a');link.href=result.download_url;link.download='';link.click();toast('备份已生成，正在下载','success');}catch(error){toast(error.message,'error');}});});
  byId('btn-restore-data')?.addEventListener('click',()=>byId('restore-data-file').click());
  byId('restore-data-file')?.addEventListener('change',async event=>{
    const file=event.target.files[0];if(!file)return;
    try{
      const result=await api.uploadFile('/maintenance/restore',file);
      toast('备份恢复完成','success');
      if(result.reload_required){window.location.reload();return;}
      byId('maintenance-results').textContent=result.message||'数据已恢复。';
      emit('library-changed');await refreshSettings();
    }catch(error){toast(`恢复失败：${error.message}`,'error');}
    finally{event.target.value='';}
  });
  byId('btn-load-trash')?.addEventListener('click',loadTrash);
  api.get('/settings/connection-status').then(result=>{const el=byId('model-connection-status');if(el){el.textContent=result.state==='success'?`最近连接成功 · ${result.model||''}`:result.state==='failed'?`最近连接失败：${result.error||''}`:'已配置未测试';el.classList.toggle('connection-error',result.state==='failed');}}).catch(()=>{});
  updateDirty();
}
function organizeSettingsNavigation(grid, sections) {
  const navigation=document.createElement('nav');
  navigation.className='settings-navigation';
  navigation.setAttribute('aria-label','设置分组');
  const groups=[['model','模型连接'],['reading','阅读与画像'],['storage','数据存储'],['pdf','PDF 来源'],['appearance','外观'],['clipboard','剪贴板']];
  for(const [key] of groups) grid.append(sections[key]);
  const page=grid.closest('.page');
  // Keep the page heading and navigation outside the form's scroll viewport.
  const content=document.createElement('div');
  content.className='settings-content';
  content.setAttribute('aria-label','设置内容');
  grid.before(navigation,content);
  content.append(grid,...[page.querySelector('.plugins-card'),page.querySelector('.about')].filter(Boolean));
  const destinations=[...groups.map(([key,label])=>[key,label,sections[key]]),
    ['plugins','插件',page.querySelector('.plugins-card')],['about','关于',page.querySelector('.about')]];
  for(const [key,label,target] of destinations) {
    if(!target)continue;
    target.id=`settings-group-${key}`;
    const button=document.createElement('button');
    button.type='button';button.className='btn small';button.textContent=label;
    button.dataset.settingsTarget=key;button.setAttribute('aria-controls',target.id);
    button.addEventListener('click',()=>{
      navigation.querySelectorAll('button').forEach(node=>node.removeAttribute('aria-current'));
      button.setAttribute('aria-current','location');
      content.scrollTo({top:content.scrollTop+target.getBoundingClientRect().top-content.getBoundingClientRect().top,behavior:'instant'});
      const heading=target.querySelector('h3');if(heading){heading.tabIndex=-1;heading.focus({preventScroll:true});}
    });
    navigation.append(button);
  }
  navigation.querySelector('button')?.setAttribute('aria-current','location');
}
async function loadTrash(){
  const target=byId('maintenance-results');
  try{const result=await api.get('/maintenance/trash');const items=Array.isArray(result)?result:result.items||[];target.replaceChildren();if(!items.length){target.textContent='回收站为空';return;}for(const item of items){const row=document.createElement('div');row.className='trash-row';const title=document.createElement('span');title.textContent=item.label||item.title||item.name||item.id;row.append(title);const restore=document.createElement('button');restore.className='btn tiny';restore.textContent='恢复';restore.addEventListener('click',async()=>{try{const restored=await api.post(`/maintenance/trash/${encodeURIComponent(item.id||item.trash_id)}/restore`);if(restored.conversation){const {getConversation}=await import('./conversation.js');getConversation(restored.conversation.scope).accept(restored.conversation);}toast('已恢复','success');emit('library-changed');emit('recovery-changed');loadTrash();}catch(error){toast(error.message,'error');}});row.append(restore);target.append(row);}}catch(error){target.textContent=`回收站读取失败：${error.message}`;}
}
