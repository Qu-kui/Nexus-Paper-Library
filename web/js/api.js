/* NPL 前端公共层：API、Toast、Markdown、工具 */

const BASE = "/api";

async function request(method, path, { json, form, signal } = {}) {
  const opts = { method, signal, headers: {} };
  if (json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  }
  if (form) opts.body = form;
  let res;
  try {
    res = await fetch(BASE + path, opts);
  } catch (err) {
    throw new Error("连不上本地服务，确认 start.cmd 还开着");
  }
  const text = await res.text();
  let data = null;
  try { data = text ? JSON.parse(text) : null; } catch { data = { message: text }; }
  if (!res.ok) {
    const message = (data && (data.message || data.detail)) || `请求失败 (${res.status})`;
    throw new Error(typeof message === "string" ? message : JSON.stringify(message));
  }
  return data;
}

export const api = {
  get: (path, opts) => request("GET", path, opts),
  post: (path, json, opts) => request("POST", path, { ...opts, json }),
  put: (path, json, opts) => request("PUT", path, { ...opts, json }),
  patch: (path, json, opts) => request("PATCH", path, { ...opts, json }),
  del: (path, opts) => request("DELETE", path, opts),
  upload: (path, files, opts) => {
    const form = new FormData();
    for (const file of files) form.append("files", file);
    return request("POST", path, { ...opts, form });
  },
  uploadFile: (path, file, field = "file", opts) => {
    const form = new FormData();
    form.append(field, file);
    return request("POST", path, { ...opts, form });
  },
};

/* ---------- Toast ---------- */
export function toast(message, type = "info", ms = 3200, action = null) {
  const wrap = document.getElementById("toasts");
  if (!wrap) return;
  const node = document.createElement("div");
  node.className = `toast ${type === "error" ? "error" : type === "success" ? "success" : ""}`;
  node.textContent = message;
  if (action && action.label) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "toast-action";
    button.textContent = action.label;
    button.addEventListener("click", () => {
      try { action.onClick?.(); } finally { node.remove(); }
    });
    node.append(button);
  }
  wrap.appendChild(node);
  setTimeout(() => {
    node.style.opacity = "0";
    node.style.transition = "opacity .25s";
    setTimeout(() => node.remove(), 260);
  }, ms);
}
api.toast = toast;

/* ---------- 工具 ---------- */
export function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

export function debounce(fn, wait = 300) {
  let timer = null;
  return (...args) => {
    clearTimeout(timer);
    timer = setTimeout(() => fn(...args), wait);
  };
}

export function formatDate(value) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return String(value).slice(0, 10);
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

export function shortAuthors(authors, max = 3) {
  if (!authors) return "未知作者";
  const list = String(authors).split(/[;；,，]/).map((s) => s.trim()).filter(Boolean);
  if (list.length <= max) return list.join(", ");
  return `${list.slice(0, max).join(", ")} 等`;
}

/* ---------- 极简 Markdown ---------- */
function protectMath(text) {
  // Protect math before Markdown handles underscores, lists, HTML and line breaks.
  // A token prefix absent from the source prevents source text impersonating a token.
  let prefix = '\uE000NPLMATH';
  while (text.includes(prefix)) prefix += 'X';
  const rendered = [];
  const pattern = /```[^\n]*\n[\s\S]*?(?:```|$)|`[^`\n]+`|\[[^\]\n]*\]\(https?:\/\/[^\s)]+\)|https?:\/\/[^\s]+|(?<!\\)\$\$[\s\S]+?(?<!\\)\$\$|\\\[[\s\S]+?\\\]|\\\([\s\S]+?\\\)|(?<![\\$])\$(?!\$)[^\n$]+?(?<!\\)\$(?!\$)/g;
  const source = text.replace(pattern, match => {
    let formula, display = false;
    if (match.startsWith('```')) {
      const fenced = match.match(/^```(?:math|latex)\s*\n([\s\S]*?)\n```$/i);
      if (!fenced) return match;
      formula = fenced[1];
      display = true;
    } else if (match.startsWith('`') || match.startsWith('[') || /^https?:/.test(match)) return match;
    else if (match.startsWith('$$') || match.startsWith('\\[')) {
      formula = match.slice(2, -2);
      display = true;
    } else if (match.startsWith('\\(')) formula = match.slice(2, -2);
    else {
      formula = match.slice(1, -1);
      // Currency amounts with prose between them are ordinary text.
      if (/^\d[\d.,]*\s+\p{L}/u.test(formula)) return match;
    }
    if (!window.katex || formula.length > 12000) return match;
    let html;
    try {
      html = window.katex.renderToString(formula, {
        displayMode: display, throwOnError: false, trust: false,
        strict: 'ignore', maxExpand: 500, maxSize: 20,
      });
    } catch {
      html = escapeHtml(match);
    }
    const token = prefix + rendered.length + '\uE001';
    rendered.push(html);
    return token;
  });
  return {source, restore: html => html.replace(new RegExp(prefix + '(\\d+)\uE001', 'g'),
    (_, index) => rendered[Number(index)])};
}

function inline(text) {
  let out = escapeHtml(text);
  out = out.replace(/`([^`]+)`/g, "<code>$1</code>");
  out = out.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  out = out.replace(/(^|[\s(])\*([^*\n]+)\*/g, "$1<em>$2</em>");
  out = out.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
  return out;
}

function tableCells(line) {
  const cells = [];
  let cell = '', code = false;
  const text = line.trim();
  for (let index = 0; index < text.length; index++) {
    const character = text[index];
    if (character === '\\' && text[index + 1] === '|') { cell += '|';index++;continue; }
    if (character === '`') code = !code;
    if (character === '|' && !code) { cells.push(cell.trim());cell = ''; }
    else cell += character;
  }
  cells.push(cell.trim());
  if (text.startsWith('|') && !cells[0]) cells.shift();
  if (text.endsWith('|') && !cells.at(-1)) cells.pop();
  return cells;
}

function fencedBlock(language, source) {
  if (['mermaid', 'chart'].includes(language)) {
    return `<figure class="ai-visual" data-visual-kind="${language}"><div class="ai-visual-status" role="status">正在生成图形…</div><details class="ai-visual-source"><summary>图形代码</summary><pre><code>${escapeHtml(source)}</code></pre></details></figure>`;
  }
  return `<pre><code>${escapeHtml(source)}</code></pre>`;
}

export function md(text) {
  if (!text) return "";
  const math = protectMath(String(text).replace(/\r/g, ""));
  const lines = math.source.split("\n");
  const html = [];
  let listType = null;
  let inCode = false;
  let codeBuf = [];
  let codeLanguage = '';
  let paragraph = [];

  const flushParagraph = () => {
    if (paragraph.length) {
      html.push(`<p>${paragraph.map(inline).join("<br>")}</p>`);
      paragraph = [];
    }
  };
  const closeList = () => {
    if (listType) { html.push(`</${listType}>`); listType = null; }
  };

  for (let lineIndex = 0; lineIndex < lines.length; lineIndex++) {
    const line = lines[lineIndex];
    if (line.trim().startsWith("```")) {
      if (inCode) {
        html.push(fencedBlock(codeLanguage, codeBuf.join("\n")));
        codeBuf = []; inCode = false;
      } else {
        flushParagraph(); closeList(); inCode = true;
        codeLanguage = line.trim().slice(3).trim().toLowerCase();
      }
      continue;
    }
    if (inCode) { codeBuf.push(line); continue; }

    const separator = lines[lineIndex + 1];
    if (line.includes('|') && separator?.includes('|')) {
      const headers = tableCells(line);
      const rules = tableCells(separator);
      if (headers.length && headers.length === rules.length && rules.every(rule => /^:?-{3,}:?$/.test(rule))) {
        flushParagraph(); closeList();
        const alignments = rules.map(rule => rule.startsWith(':') && rule.endsWith(':') ? 'center' : rule.endsWith(':') ? 'right' : 'left');
        const row = (cells, tag) => '<tr>' + headers.map((_, index) =>
          `<${tag} class="md-align-${alignments[index]}">${inline(cells[index] || '')}</${tag}>`).join('') + '</tr>';
        const rows = [];
        lineIndex += 2;
        while (lineIndex < lines.length && lines[lineIndex].trim() && lines[lineIndex].includes('|')) {
          rows.push(row(tableCells(lines[lineIndex]), 'td'));
          lineIndex++;
        }
        lineIndex--;
        html.push(`<div class="md-table-wrap" role="region" aria-label="回答表格" tabindex="0"><table><thead>${row(headers, 'th')}</thead><tbody>${rows.join('')}</tbody></table></div>`);
        continue;
      }
    }
    const heading = line.match(/^(#{1,4})\s+(.*)$/);
    if (heading) {
      flushParagraph(); closeList();
      html.push(`<h${heading[1].length}>${inline(heading[2])}</h${heading[1].length}>`);
      continue;
    }
    if (/^\s*[-*+]\s+/.test(line)) {
      flushParagraph();
      if (listType !== "ul") { closeList(); html.push("<ul>"); listType = "ul"; }
      html.push(`<li>${inline(line.replace(/^\s*[-*+]\s+/, ""))}</li>`);
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(line)) {
      flushParagraph();
      if (listType !== "ol") { closeList(); html.push("<ol>"); listType = "ol"; }
      html.push(`<li>${inline(line.replace(/^\s*\d+[.)]\s+/, ""))}</li>`);
      continue;
    }
    if (/^\s*>\s?/.test(line)) {
      flushParagraph(); closeList();
      html.push(`<blockquote>${inline(line.replace(/^\s*>\s?/, ""))}</blockquote>`);
      continue;
    }
    if (!line.trim()) { flushParagraph(); closeList(); continue; }
    paragraph.push(line);
  }
  if (inCode && codeBuf.length) html.push(`<pre><code>${escapeHtml(codeBuf.join("\n"))}</code></pre>`);
  flushParagraph(); closeList();
  return math.restore(html.join(""));
}

/* ---------- 全局事件 ---------- */
export function emit(name, detail = {}) {
  document.dispatchEvent(new CustomEvent(`pw:${name}`, { detail }));
}

export function on(name, handler) {
  document.addEventListener(`pw:${name}`, (event) => handler(event.detail));
}

export const state = {
  papers: [],
  tags: [],
  categories: [],
  settings: null,
  currentPaperId: null,
};

window.PW = window.PW || {};
window.PW.api = api;
window.PW.toast = toast;
window.PW.md = md;
window.PW.escapeHtml = escapeHtml;
window.PW.state = state;
window.PW.emit = emit;
window.PW.on = on;
