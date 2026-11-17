/* 深度思考折叠面板：流式时展开显示步骤和联网记录，回答一开始就缩回一行（可再点开） */
import { escapeHtml } from "./api.js";

export function formatDuration(seconds) {
  const value = Math.max(0, Number(seconds) || 0);
  if (value <= 0) return "";
  if (value < 1) return "不到 1 秒";
  if (value < 60) return `${Math.round(value)} 秒`;
  const minutes = Math.floor(value / 60);
  const rest = Math.round(value % 60);
  return rest ? `${minutes} 分 ${rest} 秒` : `${minutes} 分钟`;
}

function paragraphs(text) {
  const raw = String(text || "").trim();
  if (!raw) return "";
  return raw
    .split(/\n{2,}/)
    .map((block) => block.trim())
    .filter(Boolean)
    .map((block) => `<p class="tp-para">${escapeHtml(block)}</p>`)
    .join("");
}

function link(url, title) {
  return `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${escapeHtml(title || url)}</a>`;
}

/** 把服务端回传的联网轨迹渲染成「搜索到 N 个网页 / 浏览 N 个页面」两行 */
function traceHtml(trace) {
  const items = Array.isArray(trace) ? trace : [];
  if (!items.length) return "";
  const searches = items.filter((row) => row && row.type === "search");
  const opens = items.filter((row) => row && row.type === "open");
  const searchLinks = [];
  const seen = new Set();
  for (const search of searches) {
    for (const source of search.sources || []) {
      const url = String((source && source.url) || "").trim();
      if (!url || seen.has(url)) continue;
      seen.add(url);
      searchLinks.push({ url, title: source.title || url });
    }
  }
  const openLinks = [];
  const seenOpen = new Set();
  for (const open of opens) {
    const url = String((open && open.url) || "").trim();
    if (!url || seenOpen.has(url)) continue;
    seenOpen.add(url);
    openLinks.push({ url, title: open.title || url });
  }
  const queries = [...new Set(searches.flatMap((row) => row.queries || []))].slice(0, 4);
  const rows = [];
  if (searchLinks.length) {
    const queryLine = queries.length ? `<span class="tp-queries">${escapeHtml(queries.join(" / "))}</span>` : "";
    rows.push(`<details class="tp-trace"><summary><span class="tp-trace-ico">🔍</span>搜索到 ${searchLinks.length} 个网页${queryLine}</summary><div class="tp-links">${searchLinks.slice(0, 12).map((row) => link(row.url, row.title)).join("")}</div></details>`);
  }
  if (openLinks.length) {
    rows.push(`<p class="tp-trace-line"><span class="tp-trace-ico">↗</span>浏览 ${openLinks.length} 个页面：${openLinks.slice(0, 5).map((row) => link(row.url, row.title)).join(" · ")}</p>`);
  }
  return rows.join("");
}

export function thinkingPanelHtml(opts = {}) {
  const { text = "", state = "done", stage = "", thinkingSeconds = 0, elapsedSeconds = 0, trace = [] } = opts;
  const running = state === "running";
  const seconds = running ? elapsedSeconds || thinkingSeconds || 0 : thinkingSeconds || 0;
  const time = formatDuration(seconds);
  const label = running ? "思考中…" : "已思考";
  const traceBlock = traceHtml(trace);
  const stageLine = running && stage ? `<p class="tp-stage">${escapeHtml(stage)}</p>` : "";
  const body = paragraphs(text) || (running ? '<p class="tp-para tp-empty">正在整理思路…</p>' : "");
  if (!running && !body && !traceBlock) return "";
  return `<details class="thinking-panel"${running ? " open" : ""}>
  <summary><span class="tp-chevron" aria-hidden="true"></span><span class="tp-label">${label}</span>${time ? `<span class="tp-time">（用时 ${time}）</span>` : ""}</summary>
  <div class="tp-body">${traceBlock}${stageLine}${body}</div>
</details>`;
}

/** 原地更新（找不到就插到最前面），保留用户手动展开的状态 */
export function upsertThinkingPanel(container, opts = {}) {
  const hasTrace = Array.isArray(opts.trace) && opts.trace.length > 0;
  if (!container || (!opts.text && !hasTrace)) return null;
  const existing = container.querySelector(".thinking-panel");
  const wasOpen = existing ? existing.open : false;
  const html = thinkingPanelHtml(opts);
  if (!html) {
    existing?.remove();
    return null;
  }
  if (existing) existing.outerHTML = html;
  else container.insertAdjacentHTML("afterbegin", html);
  const panel = container.querySelector(".thinking-panel");
  if (panel && (opts.state === "running" || wasOpen)) panel.open = true;
  const body = panel?.querySelector(".tp-body");
  if (body && opts.state === "running") body.scrollTop = body.scrollHeight;
  return panel;
}
