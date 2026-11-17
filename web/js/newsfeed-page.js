/* 资讯页：站点源 + 关注方向 */
import { api, escapeHtml, toast } from "./api.js";

const SORT_KEY = "pw-news-sort";

const state = {
  loadedAt: 0,
  filter: "all",
  sort: "score",
  sourceName: "",
  snapshot: null,
  busy: false,
  loading: null,
  adding: false,
  initialized: false,
  autoRefreshed: false,
  pollTimer: 0,
};

const SOURCE_FALLBACK = {
  "media-people": "人民日报",
  "media-xinhua": "新华网",
  "media-cctv": "新闻联播",
  "media-thepaper": "澎湃新闻",
  "media-qq": "腾讯新闻",
  "media-huanqiu": "环球网",
  hn: "Hacker News",
  github: "GitHub",
  aihot: "AIHOT",
  "rss-openai": "OpenAI",
  "rss-deepmind": "DeepMind",
  "rss-simonw": "Simon Willison",
};

const DEFAULT_TOPICS = [
  { id: "model-release", label: "大模型发布" },
  { id: "cn-politics", label: "国内时政新闻" },
];

function safeUrl(value) {
  try {
    const url = new URL(String(value || "").trim());
    return ["https:", "http:"].includes(url.protocol) && !url.username && !url.password ? url.href : "";
  } catch {
    return "";
  }
}

function when(value) {
  if (!value) return "";
  if (/^\d{4}-\d{2}-\d{2}$/.test(String(value))) return String(value);
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  const diff = Date.now() - date.getTime();
  if (diff < 0) return "刚刚";
  if (diff < 60_000) return "刚刚";
  if (diff < 3_600_000) return `${Math.max(1, Math.floor(diff / 60_000))} 分钟前`;
  if (diff < 86_400_000) return `${Math.floor(diff / 3_600_000)} 小时前`;
  return `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, "0")}-${String(date.getDate()).padStart(2, "0")}`;
}

function sourceLabel(item) {
  return item.source_name || SOURCE_FALLBACK[item.source] || item.source || "来源";
}

function topicLabel(item) {
  return item.topic_label || "";
}

function itemTitle(item) {
  return item.title_zh || item.title || "未命名资讯";
}

function itemSummary(item) {
  return item.summary_zh || item.summary || "";
}

function scoreOf(item) {
  return typeof item.score === "number" && Number.isFinite(item.score) ? Math.max(0, Math.min(100, Math.round(item.score))) : null;
}

function sourceById(id) {
  return (state.snapshot?.sources || []).find((source) => source.id === id) || null;
}

function savedSort() {
  try {
    return typeof localStorage !== "undefined" && localStorage.getItem(SORT_KEY) === "time" ? "time" : "score";
  } catch {
    return "score";
  }
}

function timestampOf(item) {
  const value = item?.published_at || item?.fetched_at || "";
  const ms = Date.parse(value);
  return Number.isFinite(ms) ? ms : 0;
}

function sortedItems(items) {
  return items.slice().sort((a, b) => {
    const scoreA = scoreOf(a);
    const scoreB = scoreOf(b);
    if (state.sort === "time") {
      const timeDiff = timestampOf(b) - timestampOf(a);
      if (timeDiff) return timeDiff;
      if (scoreA === null && scoreB === null) return 0;
      if (scoreA === null) return 1;
      if (scoreB === null) return -1;
      return scoreB - scoreA;
    }
    if (scoreA === null && scoreB === null) return timestampOf(b) - timestampOf(a);
    if (scoreA === null) return 1;
    if (scoreB === null) return -1;
    if (scoreA !== scoreB) return scoreB - scoreA;
    return timestampOf(b) - timestampOf(a);
  });
}

function filteredItems() {
  let items = state.snapshot?.items || [];
  if (state.filter.startsWith("source:")) {
    const id = state.filter.slice(7);
    items = items.filter((item) => item.source === id);
  } else if (state.filter.startsWith("topic:")) {
    const id = state.filter.slice(6);
    items = items.filter((item) => (item.topic_ids || [item.topic_id]).includes(id));
  }
  return sortedItems(items);
}

function renderSort() {
  const group = document.getElementById("news-sort");
  if (group && group.querySelectorAll) {
    for (const button of group.querySelectorAll("[data-news-sort]")) {
      const active = button.dataset.newsSort === state.sort;
      button.classList.toggle("active", active);
      button.setAttribute("aria-pressed", active ? "true" : "false");
    }
  }
  const list = document.getElementById("news-list");
  if (list) {
    list.setAttribute("aria-label", state.sort === "time" ? "按发布时间从新到旧排列的资讯" : "按评分从高到低排列的资讯");
  }
}

function setSort(sort) {
  state.sort = sort === "time" ? "time" : "score";
  try {
    if (typeof localStorage !== "undefined") localStorage.setItem(SORT_KEY, state.sort);
  } catch {
    // 本地存储不可用时只在本次打开期间生效。
  }
  renderAll();
}

function renderTopics() {
  const wrap = document.getElementById("news-topics");
  if (!wrap) return;
  const snapshot = state.snapshot || {};
  const topics = snapshot.topics || [];
  const items = snapshot.items || [];
  const topicCount = (id) => items.filter((item) => (item.topic_ids || [item.topic_id]).includes(id)).length;
  const chips = [
    `<button class="news-filter ${state.filter === "all" ? "active" : ""}" data-news-filter="all">全部 <span>${items.length}</span></button>`,
  ];
  if (state.filter.startsWith("source:")) {
    const name = state.sourceName || sourceById(state.filter.slice(7))?.name || "站点";
    chips.push(`<button class="news-filter active" data-news-clear-source="1">来源：${escapeHtml(name)} <span>×</span></button>`);
  }
  for (const topic of topics) {
    const active = state.filter === `topic:${topic.id}`;
    const count = topic.article_count ?? topicCount(topic.id);
    const local = topic.builtin
      ? `${count} 条 · ${topic.source_count || 0} 站点`
      : topic.status === "discovering"
        ? "发现中…"
        : topic.status === "error"
          ? "发现失败"
          : `${count} 条 · ${topic.source_count || 0} 站点`;
    chips.push(`<span class="news-topic ${active ? "active" : ""}">
      <button class="news-filter news-topic-main" data-news-filter="topic:${escapeHtml(topic.id)}" title="${escapeHtml(topic.error || topic.label)}">${escapeHtml(topic.label)} <span>${escapeHtml(local)}</span></button>
    </span>`);
  }
  wrap.innerHTML = chips.join("");
}

function topicMeta(topic) {
  if (topic.status === "discovering") return "正在发现新的站点…";
  if (topic.status === "pending") return "等待发现…";
  if (topic.status === "error") return topic.error ? `发现失败：${topic.error}` : "发现失败";
  return `${topic.article_count || 0} 条 · ${topic.source_count || 0} 站点`;
}

function renderTopicCount() {
  const node = document.getElementById("news-topic-count");
  if (!node) return;
  const count = (state.snapshot?.topics || []).length;
  node.textContent = count ? String(count) : "";
}

function renderTopicsPanel() {
  const list = document.getElementById("news-topics-list");
  if (!list) return;
  const topics = state.snapshot?.topics || [];
  const present = new Set(topics.map((topic) => topic.id));
  const rows = topics.map((topic) => `<div class="news-topic-row">
    <div class="news-topic-row-main">
      <strong>${escapeHtml(topic.label || "未命名方向")}</strong>
      <em>${escapeHtml(topicMeta(topic))}</em>
    </div>
    <div class="news-topic-row-actions">
      <button class="btn tiny ghost" type="button" data-news-delete="${escapeHtml(topic.id)}" title="移除这个方向">移除</button>
    </div>
  </div>`);
  for (const spec of DEFAULT_TOPICS) {
    if (present.has(spec.id)) continue;
    rows.push(`<div class="news-topic-row">
      <div class="news-topic-row-main">
        <strong>${escapeHtml(spec.label)}</strong>
        <em>默认方向已移除</em>
      </div>
      <div class="news-topic-row-actions">
        <button class="news-topic-restore" type="button" data-news-restore="${escapeHtml(spec.label)}">恢复</button>
      </div>
    </div>`);
  }
  list.innerHTML = rows.length
    ? rows.join("")
    : '<div class="news-topics-empty">还没有关注方向。添加一个方向，或者恢复默认方向。</div>';
  const sub = document.getElementById("news-topics-sub");
  if (sub) sub.textContent = `${topics.length} 个方向 · 可添加、移除或恢复默认`;
}

function renderItems() {
  const list = document.getElementById("news-list");
  if (!list) return;
  const items = filteredItems();
  if (!items.length) {
    let message = "还没有资讯。点「刷新资讯」拉取站点源，或在上方添加一个关注方向。";
    if (state.snapshot?.refreshing) message = "正在抓取资讯、翻译和评分，稍等一下…";
    if (state.filter.startsWith("source:")) message = "这个站点暂时没有匹配的条目。";
    list.innerHTML = `<div class="news-empty"><p>${escapeHtml(message)}</p></div>`;
    return;
  }
  list.innerHTML = items.map((item) => {
    const url = safeUrl(item.url);
    const title = escapeHtml(itemTitle(item));
    const head = url
      ? `<a href="${escapeHtml(url)}" target="_blank" rel="noopener noreferrer">${title}</a>`
      : title;
    const summary = itemSummary(item) ? `<p class="news-summary">${escapeHtml(itemSummary(item))}</p>` : "";
    const score = scoreOf(item);
    const scoreStyle = score === null ? "" : `--score-hue:${215 - score * 1.9};--score-saturation:${10 + score * 0.8}%;--score-lightness:${97 - score * 0.2}%`;
    const scoreHtml = score === null
      ? '<span class="news-score pending">待评分</span>'
      : `<span class="news-score" style="${scoreStyle}" title="${escapeHtml(item.score_reason || "AI 评分")}（满分 100）">${score}<small> / 100</small></span>`;
    const stamp = when(item.published_at);
    const topic = topicLabel(item);
    const topicHtml = topic && topic !== sourceLabel(item)
      ? `<span class="news-topic-badge">${escapeHtml(topic)}</span>`
      : "";
    const originalTitle = item.title_zh && item.title ? ` title="原标题：${escapeHtml(item.title)}"` : "";
    return `<article class="news-item">
      <div class="news-meta">
        <button type="button" class="news-source news-source-btn" data-news-source="${escapeHtml(item.source || "")}" title="只看这个站点">${escapeHtml(sourceLabel(item))}</button>
        ${topicHtml}
        ${stamp ? `<span title="发布时间：${escapeHtml(item.published_at)}">${escapeHtml(stamp)}</span>` : '<span class="news-time-missing" title="来源页未提供发布时间">发布时间未知</span>'}
      </div>
      ${scoreHtml}
      <h3${originalTitle}>${head}</h3>
      ${summary}
    </article>`;
  }).join("");
}

function renderStatus() {
  const refreshButton = document.getElementById("btn-news-refresh");
  if (refreshButton) refreshButton.disabled = state.busy || Boolean(state.snapshot?.refreshing);
  const node = document.getElementById("news-status");
  if (!node) return;
  const snapshot = state.snapshot;
  if (!snapshot) {
    node.textContent = "正在读取本地缓存…";
    return;
  }
  const parts = [];
  if (snapshot.refreshing || (snapshot.topics || []).some((topic) => topic.status === "discovering")) {
    parts.push("正在刷新站点源、翻译和评分…");
  } else if (snapshot.refreshed_at) {
    parts.push(`更新于 ${when(snapshot.refreshed_at)}`);
  }
  if (snapshot.total) parts.push(`共 ${snapshot.total} 条`);
  if (snapshot.source_total) parts.push(`站点 ${snapshot.source_total} 个`);
  if (snapshot.total) parts.push(`已评分 ${snapshot.scored || 0}`);
  if (snapshot.total) parts.push(`保留最近 ${snapshot.retention_days || 30} 天`);
  if (snapshot.enrich_error) parts.push(`翻译/评分：${snapshot.enrich_error}`);
  if (snapshot.last_error) parts.push(`上次刷新：${snapshot.last_error}`);
  node.textContent = parts.join(" · ");
}

function renderSourceCount() {
  const node = document.getElementById("news-source-count");
  if (!node) return;
  const count = state.snapshot?.source_total || 0;
  node.textContent = count ? String(count) : "";
}

function renderSources() {
  const list = document.getElementById("news-sources-list");
  if (!list) return;
  const snapshot = state.snapshot || {};
  const sources = snapshot.sources || [];
  const topics = snapshot.topics || [];
  const groups = [];
  const groupsToShow = [...topics, { id: "__other", label: "其他站点" }];
  for (const topic of groupsToShow) {
    const rows = sources.filter((source) => topic.id === "__other"
      ? !(source.topic_ids || []).some((id) => topics.some((row) => row.id === id))
      : (source.topic_ids || []).includes(topic.id));
    if (!rows.length) continue;
    groups.push(`<section class="news-source-group">
      <h3>${escapeHtml(topic.label)} <span>${rows.length} 个站点</span></h3>
      ${rows.map((source) => {
        const count = source.article_count || 0;
        const quality = source.quality_label ? ` · ${source.quality_label}` : "";
        const kind = `${source.kind || "站点"}${quality}`;
        const meta = source.type === "search"
          ? "检索入口"
          : `${count} 条${source.last_error ? ` · ${source.last_error}` : ""}`;
        return `<button type="button" class="news-source-row" data-news-source="${escapeHtml(source.id)}">
          <span class="news-source-main"><strong>${escapeHtml(source.name)}</strong><em>${escapeHtml(kind)}</em></span>
          <span class="news-source-meta">${escapeHtml(meta)}</span>
        </button>`;
      }).join("")}
    </section>`);
  }
  if (!groups.length) {
    groups.push('<div class="news-empty"><p>还没有站点源，先刷新一次资讯。</p></div>');
  }
  list.innerHTML = groups.join("");
  const sub = document.getElementById("news-sources-sub");
  if (sub) sub.textContent = `${sources.length} 个站点 · 点站点筛选条目`;
}

function renderAll() {
  renderTopics();
  renderTopicCount();
  renderTopicsPanel();
  renderSort();
  renderItems();
  renderStatus();
  renderSourceCount();
  renderSources();
}

function openSources() {
  const panel = document.getElementById("news-sources-panel");
  const backdrop = document.getElementById("news-sources-backdrop");
  if (!panel || !backdrop) return;
  closeTopics();
  renderSources();
  panel.classList.remove("hidden");
  backdrop.classList.remove("hidden");
  panel.setAttribute("aria-hidden", "false");
}

function closeSources() {
  const panel = document.getElementById("news-sources-panel");
  const backdrop = document.getElementById("news-sources-backdrop");
  if (!panel || !backdrop) return;
  panel.classList.add("hidden");
  backdrop.classList.add("hidden");
  panel.setAttribute("aria-hidden", "true");
}

function openTopics() {
  const panel = document.getElementById("news-topics-panel");
  const backdrop = document.getElementById("news-topics-backdrop");
  if (!panel || !backdrop) return;
  closeSources();
  renderTopicsPanel();
  panel.classList.remove("hidden");
  backdrop.classList.remove("hidden");
  panel.setAttribute("aria-hidden", "false");
}

function closeTopics() {
  const panel = document.getElementById("news-topics-panel");
  const backdrop = document.getElementById("news-topics-backdrop");
  if (!panel || !backdrop) return;
  panel.classList.add("hidden");
  backdrop.classList.add("hidden");
  panel.setAttribute("aria-hidden", "true");
}

async function restoreTopic(label) {
  try {
    const result = await api.post("/newsfeed/topics", { label });
    toast(`已恢复「${result.topic.label}」，正在刷新站点源`, "success", 3000);
    await load();
  } catch (error) {
    toast(error.message, "error");
  }
}

function showSource(id, name) {
  if (!id) return;
  state.filter = `source:${id}`;
  state.sourceName = name || sourceById(id)?.name || "";
  closeSources();
  renderAll();
  document.getElementById("news-list")?.scrollIntoView({ behavior: "smooth", block: "start" });
}

function schedulePoll() {
  clearTimeout(state.pollTimer);
  const snapshot = state.snapshot;
  const active = Boolean(snapshot?.refreshing || (snapshot?.topics || []).some((topic) => topic.status === "discovering"));
  if (!active) return;
  state.pollTimer = setTimeout(() => load(), 1600);
}

function load() {
  if (state.loading) return state.loading;
  state.loading = loadSnapshot().finally(() => { state.loading = null; });
  return state.loading;
}

async function loadSnapshot() {
  try {
    const data = await api.get("/newsfeed");
    state.snapshot = data;
    state.loadedAt = Date.now();
    if (state.filter.startsWith("source:") && !sourceById(state.filter.slice(7))) {
      state.filter = "all";
      state.sourceName = "";
    }
    if (state.filter.startsWith("topic:") && !(data.topics || []).some((topic) => topic.id === state.filter.slice(6))) {
      state.filter = "all";
      state.sourceName = "";
    }
    renderAll();
    if (!state.autoRefreshed && !data.refreshed_at && !data.refreshing && !(data.items || []).length) {
      state.autoRefreshed = true;
      setTimeout(() => refresh(), 0);
      return;
    }
    schedulePoll();
  } catch (error) {
    const status = document.getElementById("news-status");
    if (status) status.textContent = `读取资讯失败：${error.message}`;
    clearTimeout(state.pollTimer);
    state.pollTimer = setTimeout(() => load(), 5000);
  }
}

async function refresh() {
  if (state.busy || state.snapshot?.refreshing) return;
  state.busy = true;
  const button = document.getElementById("btn-news-refresh");
  if (button) button.disabled = true;
  try {
    clearTimeout(state.pollTimer);
    if (state.loading) await state.loading;
    const result = await api.post("/newsfeed/refresh", {});
    toast(result.accepted ? "开始刷新站点源，翻译和评分会在后台完成" : "资讯正在刷新中", "info", 2600);
    await load();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.busy = false;
    renderStatus();
  }
}

async function addTopic() {
  if (state.adding) return;
  const input = document.getElementById("news-topic-input");
  const label = String(input?.value || "").trim();
  if (!label) {
    input?.focus();
    return;
  }
  state.adding = true;
  const button = document.getElementById("btn-news-add");
  if (button) button.disabled = true;
  try {
    const result = await api.post("/newsfeed/topics", { label });
    if (input) input.value = "";
    state.filter = `topic:${result.topic.id}`;
    state.sourceName = "";
    toast(result.topic.restored
      ? `已恢复默认方向「${result.topic.label}」，正在刷新站点源`
      : `已添加「${result.topic.label}」，AI 正在查可收录的站点`, "success", 3200);
    await load();
    openTopics();
  } catch (error) {
    toast(error.message, "error");
  } finally {
    state.adding = false;
    if (button) button.disabled = false;
  }
}

async function deleteTopic(id) {
  if (!window.confirm("删除这个关注方向？已抓到的该方向资讯和专属站点也会一起移除。")) return;
  try {
    await api.del(`/newsfeed/topics/${encodeURIComponent(id)}`);
    if (state.filter === `topic:${id}`) state.filter = "all";
    toast("已删除方向", "info", 2200);
    await load();
  } catch (error) {
    toast(error.message, "error");
  }
}

export function initNewsfeed() {
  if (state.initialized) return;
  state.initialized = true;
  state.sort = savedSort();
  renderSort();
  document.getElementById("btn-news-refresh")?.addEventListener("click", refresh);
  document.getElementById("btn-news-add")?.addEventListener("click", addTopic);
  document.getElementById("btn-news-topics")?.addEventListener("click", openTopics);
  document.getElementById("btn-news-topics-close")?.addEventListener("click", closeTopics);
  document.getElementById("news-topics-backdrop")?.addEventListener("click", closeTopics);
  document.getElementById("btn-news-sources")?.addEventListener("click", openSources);
  document.getElementById("btn-news-sources-close")?.addEventListener("click", closeSources);
  document.getElementById("news-sources-backdrop")?.addEventListener("click", closeSources);
  document.getElementById("news-sort")?.addEventListener("click", (event) => {
    const button = event.target.closest("[data-news-sort]");
    if (button) setSort(button.dataset.newsSort);
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      closeSources();
      closeTopics();
    }
  });
  const input = document.getElementById("news-topic-input");
  input?.addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      event.preventDefault();
      addTopic();
    }
  });
  document.getElementById("news-topics")?.addEventListener("click", (event) => {
    if (event.target.closest("[data-news-clear-source]")) {
      state.filter = "all";
      state.sourceName = "";
      renderAll();
      return;
    }
    const filter = event.target.closest("[data-news-filter]");
    if (!filter) return;
    state.filter = filter.dataset.newsFilter || "all";
    state.sourceName = "";
    renderAll();
  });
  document.getElementById("news-topics-list")?.addEventListener("click", (event) => {
    const deleteButton = event.target.closest("[data-news-delete]");
    if (deleteButton) {
      deleteTopic(deleteButton.dataset.newsDelete);
      return;
    }
    const restoreButton = event.target.closest("[data-news-restore]");
    if (restoreButton) restoreTopic(restoreButton.dataset.newsRestore);
  });
  document.getElementById("news-list")?.addEventListener("click", (event) => {
    const source = event.target.closest("[data-news-source]");
    if (source) showSource(source.dataset.newsSource, source.textContent.trim());
  });
  document.getElementById("news-sources-list")?.addEventListener("click", (event) => {
    const source = event.target.closest("[data-news-source]");
    if (source) {
      const name = source.querySelector("strong")?.textContent || "";
      showSource(source.dataset.newsSource, name);
    }
  });
  window.addEventListener("pw:news-shown", () => {
    if (Date.now() - state.loadedAt > 60_000) load();
  });
  load();
}
