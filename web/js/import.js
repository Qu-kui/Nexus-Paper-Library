/* NPL · 导入 / 添加页
 * 拖拽 / 多选 PDF 上传、Zotero 导出文件、Zotero 文件夹、Obsidian 库、DOI / arXiv / URL
 * 依赖：api.js（api、toast、emit）
 */
import { api, toast, emit } from "./api.js";

let initialized = false;
let uploading = false;

const byId = (id) => document.getElementById(id);

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

function showResult(box, text) {
  if (!box) return;
  box.classList.remove("hidden");
  box.textContent = text;
}

function showProgress(text) {
  const progress = byId("upload-progress");
  if (!progress) return;
  progress.classList.remove("hidden");
  progress.textContent = text;
}

function finishProgress(text, isError = false) {
  const progress = byId("upload-progress");
  if (!progress) return;
  progress.classList.remove("hidden");
  progress.textContent = `${isError ? "⚠ " : "✓ "}${text}`;
}

/* 不经过 api.js 的表单上传：Zotero 文件端点要求字段名固定为 file */
async function postForm(path, form) {
  let response;
  try {
    response = await fetch(`/api${path}`, { method: "POST", body: form });
  } catch {
    throw new Error("连不上本地服务，确认 start.cmd 还开着");
  }
  const text = await response.text();
  let data = null;
  try {
    data = text ? JSON.parse(text) : null;
  } catch {
    data = { message: text };
  }
  if (!response.ok) {
    const message = (data && (data.message || data.detail)) || `请求失败 (${response.status})`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return data;
}

/* ---------- 导入结果摘要 ---------- */
function summarizeImport(result) {
  const lines = [];
  let count = 0;

  const raw = result && (result.imported !== undefined ? result.imported : result.papers !== undefined ? result.papers : result.paper);
  if (Array.isArray(raw)) {
    count = raw.length;
    lines.push(`已导入 ${count} 篇`);
    const titles = raw
      .slice(0, 8)
      .map((item) => (typeof item === "string" ? item : item && item.title))
      .filter(Boolean);
    if (titles.length) lines.push(...titles.map((title) => `· ${title}`));
    if (raw.length > 8) lines.push(`…还有 ${raw.length - 8} 条`);
  } else if (raw && typeof raw === "object") {
    count = 1;
    lines.push(`已导入：${raw.title || "1 篇论文"}`);
  } else if (typeof raw === "number") {
    count = raw;
    lines.push(`已导入 ${count} 篇`);
  } else if (result && typeof result.message === "string" && result.message) {
    lines.push(result.message);
  } else {
    lines.push("导入完成，没有新条目");
  }

  const skipped = Number((result && result.skipped) || 0);
  if (skipped) lines.push(`跳过重复 / 无效条目 ${skipped} 条`);
  const notesCount=Number(result?.notes_imported||0);
  if(notesCount)lines.push(`已保留笔记 ${notesCount} 条，其中为已有论文补入 ${Number(result?.notes_updated_papers||0)} 篇`);
  if(Number(result?.notes_duplicates||0))lines.push(`已存在的相同笔记 ${result.notes_duplicates} 条，未重复追加`);
  for(const warning of result?.warnings||[])lines.push(`提示：${warning}`);

  const skippedNoPdf = Number((result && result.skipped_no_pdf) || 0);
  if (skippedNoPdf) {
    lines.push(`其中 ${skippedNoPdf} 条没有定位到 PDF 文件，按规矩不入库（不会变成空壳条目）`);
    const examples = Array.isArray(result && result.skipped_no_pdf_examples) ? result.skipped_no_pdf_examples : [];
    if (examples.length) lines.push(...examples.slice(0, 6).map((title) => `· ${title}`));
  }

  const errors = result && Array.isArray(result.errors) ? result.errors : [];
  if (errors.length) {
    lines.push(`失败 ${errors.length} 条：`);
    lines.push(...errors.slice(0, 12).map((error) => `· ${String(error)}`));
    if (errors.length > 12) lines.push(`…还有 ${errors.length - 12} 条错误`);
  }
  return { count, notesCount, errors, text: lines.join("\n") };
}

function afterImport(summary, label) {
  if (summary.notesCount > 0) {
    toast(`${label}完成：新增 ${summary.count} 篇，保留 ${summary.notesCount} 条笔记`,"success",4500);
    emit('library-changed');emit('notes-changed');return;
  }
  if (summary.count > 0) {
    toast(`${label}导入完成：${summary.count} 篇`, "success", 4000);
    emit("library-changed");
    return;
  }
  if (summary.errors.length) toast(`${label}没有导入成功的条目`, "error", 4200);
  else toast(`${label}没有新条目（可能都已经在库里）`, "info", 3600);
}

/* ---------- PDF 上传 ---------- */
async function uploadFiles(fileList) {
  if (uploading) {
    toast("上一批论文还在导入，请稍等", "info", 2200);
    return;
  }
  const files = Array.from(fileList || []);
  const pdfs = files.filter((file) => /\.pdf$/i.test(file.name || ""));
  const ignored = files.filter((file) => !/\.pdf$/i.test(file.name || ""));
  if (!pdfs.length) {
    toast("只支持 PDF 文件", "error");
    return;
  }

  uploading = true;
  const button = byId("btn-pick-files");
  if (button) button.disabled = true;
  showProgress(`正在导入 ${pdfs.length} 个 PDF：解析元数据、抽文本建索引…`);

  try {
    const result = await api.upload("/papers/upload", pdfs);
    const papers = result && Array.isArray(result.papers) ? result.papers : [];
    const errors = result && Array.isArray(result.errors) ? result.errors : [];
    const parts = [`成功 ${papers.length} 篇`];
    if (errors.length) parts.push(`失败 ${errors.length} 个（${errors.slice(0, 3).join("；")}${errors.length > 3 ? "…" : ""}）`);
    if (ignored.length) parts.push(`跳过非 PDF ${ignored.length} 个`);
    finishProgress(parts.join(" · "), papers.length === 0 && errors.length > 0);

    if (papers.length) {
      toast(`已导入 ${papers.length} 篇论文`, "success", 3600);
      emit("library-changed");
    }
    if (errors.length) toast(`有 ${errors.length} 个文件导入失败`, "error", 4200);
  } catch (err) {
    finishProgress(`导入失败：${err.message || "未知错误"}`, true);
    toast(`导入失败：${err.message || "未知错误"}`, "error", 5000);
  } finally {
    uploading = false;
    if (button && button.isConnected) button.disabled = false;
  }
}

/* ---------- Zotero 导出文件 ---------- */
async function importZoteroFile(file) {
  const button = byId("btn-zotero-file");
  const box = byId("zotero-file-result");
  showResult(box, `正在解析 ${file.name}…`);
  await runWithBusy(button, "导入中…", async () => {
    try {
      const form = new FormData();
      form.append("file", file);
      const result = await postForm("/import/zotero", form);
      const summary = summarizeImport(result);
      showResult(box, summary.text);
      afterImport(summary, "Zotero 文件");
    } catch (err) {
      showResult(box, `导入失败：${err.message || "未知错误"}`);
      toast(`导入失败：${err.message || "未知错误"}`, "error", 5000);
    }
  });
}

/* ---------- 文件夹导入 ---------- */
async function importFolder({ buttonId, inputId, boxId, path: apiPath, label }) {
  const button = byId(buttonId);
  const input = byId(inputId);
  const box = byId(boxId);
  const folder = String((input && input.value) || "").trim();
  if (!folder) {
    toast(`先填${label}文件夹路径`, "error");
    input?.focus();
    return;
  }
  showResult(box, `正在导入 ${folder} …`);
  await runWithBusy(button, "导入中…", async () => {
    try {
      const result = await api.post(apiPath, { path: folder });
      const summary = summarizeImport(result);
      showResult(box, summary.text);
      afterImport(summary, label);
    } catch (err) {
      showResult(box, `导入失败：${err.message || "未知错误"}`);
      toast(`导入失败：${err.message || "未知错误"}`, "error", 5600);
    }
  });
}

/* ---------- EndNote 论文库迁移 ---------- */
async function pickEndnoteFile() {
  const input = byId("endnote-path");
  try {
    const picked = await (window.pywebview && window.pywebview.api && window.pywebview.api.pick_file
      ? window.pywebview.api.pick_file()
      : "");
    if (picked) {
      if (input) input.value = picked;
      return;
    }
  } catch (err) {
    /* 走下面的提示 */
  }
  toast("浏览器模式没法弹文件框，把 EndNote 导出文件的路径粘进来即可", "info", 4200);
  input?.focus();
}

async function importEndnote() {
  const button = byId("btn-import-endnote");
  const input = byId("endnote-path");
  const box = byId("endnote-result");
  const includeMissing = byId("endnote-include-missing")?.checked !== false;
  const target = String((input && input.value) || "").trim();
  if (!target) {
    toast("先选或填 EndNote 的 XML 导出文件（或库所在文件夹）", "error");
    input?.focus();
    return;
  }
  showResult(box, `正在读取 ${target} …\n条目多的时候会慢一些。`);
  await runWithBusy(button, "迁移中…", async () => {
    try {
      const result = await api.post("/import/endnote", { path: target, include_without_pdf: includeMissing });
      const imported = Array.isArray(result.imported) ? result.imported : [];
      const errors = Array.isArray(result.errors) ? result.errors : [];
      const withPdf = imported.filter((paper) => paper && (paper.has_pdf || paper.file_path)).length;
      const lines = [
        `迁移完成：新增 ${imported.length} 篇`,
        `其中带 PDF ${withPdf} 篇 · 库里已有 PDF 的会跳过`,
      ];
      if (Number(result.skipped || 0)) lines.push(`跳过 ${result.skipped} 条（重复或没定位到 PDF）`);
      if(Number(result.notes_imported||0))lines.push(`已保留 ${result.notes_imported} 条笔记，为已有论文补入 ${result.notes_updated_papers||0} 篇`);
      if(Number(result.notes_duplicates||0))lines.push(`相同笔记 ${result.notes_duplicates} 条，未重复追加`);
      for(const warning of result.warnings||[])lines.push(`提示：${warning}`);
      if (Number(result.pdf_files || 0)) lines.push(`可匹配的附件 PDF ${result.pdf_files} 个`);
      if (Number(result.pdf_fetch_queued || 0)) lines.push(`已把 ${result.pdf_fetch_queued} 篇没有全文的加入后台补 PDF 队列`);
      if (errors.length) lines.push(`失败 ${errors.length} 条：`, ...errors.slice(0, 6).map((item) => `· ${item}`));
      showResult(box, lines.join("\n"));
      if (imported.length || result.notes_imported) {
        toast(`EndNote 迁移完成：${imported.length} 篇，保留 ${result.notes_imported||0} 条笔记`, "success", 4600);
        emit("library-changed");
        emit('notes-changed');
      } else {
        toast("没有新增条目（可能都已存在，或文件里没有可识别的记录）", "info", 4600);
      }
    } catch (err) {
      showResult(box, `迁移失败：${err.message || "未知错误"}`);
      toast(`迁移失败：${err.message || "未知错误"}`, "error", 5600);
    }
  });
}

/* ---------- 文件夹里的一堆 PDF：按内容配对已有论文，对不上就新建 ---------- */
async function pickPdfFolder() {
  const input = byId("pdf-folder");
  try {
    const picked = await (window.pywebview && window.pywebview.api && window.pywebview.api.pick_folder
      ? window.pywebview.api.pick_folder()
      : "");
    if (picked) {
      if (input) input.value = picked;
      return;
    }
  } catch (err) {
    /* 走下面的提示 */
  }
  toast("浏览器模式没法弹文件夹选择框，直接把文件夹路径粘进来即可", "info", 4200);
  input?.focus();
}

async function importPdfFolder() {
  const button = byId("btn-import-pdf-folder");
  const input = byId("pdf-folder");
  const box = byId("pdf-folder-result");
  const folder = String((input && input.value) || "").trim();
  if (!folder) {
    toast("先选或填一个 PDF 文件夹", "error");
    input?.focus();
    return;
  }
  showResult(box, `正在扫描 ${folder} …\nPDF 多的时候会慢一些，请等它跑完。`);
  await runWithBusy(button, "扫描中…", async () => {
    try {
      const result = await api.post("/import/pdf-folder", { path: folder });
      const created = Array.isArray(result.created) ? result.created : [];
      const matched = Array.isArray(result.matched) ? result.matched : [];
      const already = Array.isArray(result.already_has_pdf) ? result.already_has_pdf : [];
      const errors = Array.isArray(result.errors) ? result.errors : [];
      const notPdf = Array.isArray(result.skipped_not_pdf) ? result.skipped_not_pdf : [];
      const lines = [
        `扫描 ${result.scanned || 0} 个有效 PDF`,
        `新建 ${created.length} 篇`,
        `匹配已有论文并补 PDF ${matched.length} 篇`,
      ];
      if (already.length) lines.push(`库里已有全文、跳过 ${already.length} 篇`);
      if (notPdf.length) lines.push(`跳过 ${notPdf.length} 个假 PDF（后缀是 .pdf 但内容不是）：${notPdf.slice(0, 3).join("、")}`);
      if (errors.length) lines.push(`失败 ${errors.length} 个：${errors.slice(0, 3).join("；")}${errors.length > 3 ? "…" : ""}`);
      showResult(box, lines.join("\n"));
      if (created.length || matched.length) {
        toast(`导入完成：新建 ${created.length} 篇、补全 ${matched.length} 篇`, "success", 4600);
        emit("library-changed");
      } else {
        toast("没有新变化（这些 PDF 应该都已经在库里了）", "info", 3600);
      }
      if (errors.length) toast(`有 ${errors.length} 个 PDF 没能导入`, "error", 4200);
    } catch (err) {
      showResult(box, `导入失败：${err.message || "未知错误"}`);
      toast(`导入失败：${err.message || "未知错误"}`, "error", 5600);
    }
  });
}

/* ---------- DOI / arXiv / 网址 ---------- */
async function importIdentifier() {
  const input = byId("identifier-input");
  const box = byId("identifier-result");
  const value = String((input && input.value) || "").trim();
  if (!value) {
    toast("先粘贴 DOI / arXiv id / 论文链接", "error");
    input?.focus();
    return;
  }
  showResult(box, `正在解析 ${value} …`);
  await runWithBusy(byId("btn-identifier"), "添加中…", async () => {
    try {
      const result = await api.post("/import/identifier", { identifier: value });
      const summary = summarizeImport(result);
      showResult(box, summary.text);
      afterImport(summary, "标识符");
      if (summary.count > 0 && input) input.value = "";
    } catch (err) {
      showResult(box, `添加失败：${err.message || "未知错误"}`);
      toast(`添加失败：${err.message || "未知错误"}`, "error", 5600);
    }
  });
}

/* ---------- 拖拽 ---------- */
function bindDropzone() {
  const dropzone = byId("dropzone");
  const fileInput = byId("file-input");

  byId("btn-pick-files")?.addEventListener("click", (event) => {
    event.stopPropagation();
    fileInput?.click();
  });
  fileInput?.addEventListener("change", () => {
    const files = Array.from(fileInput.files || []);
    fileInput.value = "";
    if (files.length) uploadFiles(files);
  });

  if (!dropzone) return;
  let depth = 0;
  dropzone.addEventListener("click", (event) => {
    if (event.target.closest("button, input, .dz-progress")) return;
    fileInput?.click();
  });
  dropzone.addEventListener("dragenter", (event) => {
    event.preventDefault();
    depth += 1;
    dropzone.classList.add("drag");
  });
  dropzone.addEventListener("dragover", (event) => {
    event.preventDefault();
    if (event.dataTransfer) event.dataTransfer.dropEffect = "copy";
  });
  dropzone.addEventListener("dragleave", (event) => {
    event.preventDefault();
    depth = Math.max(0, depth - 1);
    if (depth === 0) dropzone.classList.remove("drag");
  });
  dropzone.addEventListener("drop", (event) => {
    event.preventDefault();
    depth = 0;
    dropzone.classList.remove("drag");
    const files = Array.from((event.dataTransfer && event.dataTransfer.files) || []);
    if (files.length) uploadFiles(files);
  });

  // 拖到页面其它地方时不要被浏览器直接打开
  window.addEventListener("dragover", (event) => event.preventDefault());
  window.addEventListener("drop", (event) => {
    if (!dropzone.contains(event.target)) event.preventDefault();
  });
}

/* ---------- 初始化 ---------- */
export function initImport() {
  if (initialized) return;
  initialized = true;

  bindDropzone();

  const zoteroInput = byId("zotero-file");
  byId("btn-zotero-file")?.addEventListener("click", () => zoteroInput?.click());
  zoteroInput?.addEventListener("change", () => {
    const file = (zoteroInput.files && zoteroInput.files[0]) || null;
    zoteroInput.value = "";
    if (file) importZoteroFile(file);
  });

  byId("btn-zotero-folder")?.addEventListener("click", () => {
    importFolder({
      buttonId: "btn-zotero-folder",
      inputId: "zotero-folder",
      boxId: "zotero-folder-result",
      path: "/import/zotero-folder",
      label: "Zotero 导出",
    });
  });

  byId("btn-pick-pdf-folder")?.addEventListener("click", pickPdfFolder);
  byId("btn-import-pdf-folder")?.addEventListener("click", importPdfFolder);
  byId("btn-pick-endnote")?.addEventListener("click", pickEndnoteFile);
  byId("btn-import-endnote")?.addEventListener("click", importEndnote);

  byId("btn-obsidian")?.addEventListener("click", () => {
    importFolder({
      buttonId: "btn-obsidian",
      inputId: "obsidian-folder",
      boxId: "obsidian-result",
      path: "/import/obsidian",
      label: "Obsidian 库",
    });
  });

  // 文件夹路径输入框回车即导入
  for (const [inputId, buttonId] of [["zotero-folder", "btn-zotero-folder"], ["obsidian-folder", "btn-obsidian"], ["pdf-folder", "btn-import-pdf-folder"]]) {
    byId(inputId)?.addEventListener("keydown", (event) => {
      if (event.key !== "Enter") return;
      event.preventDefault();
      byId(buttonId)?.click();
    });
  }

  const identifierInput = byId("identifier-input");
  byId("btn-identifier")?.addEventListener("click", importIdentifier);
  identifierInput?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      importIdentifier();
    }
  });
}
