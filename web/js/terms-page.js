/* 术语复习：双方向选择题 + 间隔排期 + 抽取/手动添加 */
import { api, toast, escapeHtml } from "./api.js";

let items = [];
let papers = [];
let paperTitles = new Map();
let quizItems = [];
let quizIndex = 0;
let quizCorrect = 0;
let answered = false;
let loaded = false;
let extractTimer = null;
let quizSession = 0;
let advanceTimer = null;
let quizController = null;
let pendingAnswer = null;
let savingAnswer = false;
let listOffset = 0;
let listTotal = 0;
let listRequest = 0;
let searchTimer = null;
const PAGE_SIZE = 100;

const el = (id) => document.getElementById(id);

function errorText(err) {
  return String((err && err.message) || err || "未知错误");
}

function selectedPaper() {
  return String(el("terms-paper")?.value || "");
}

function currentMode() {
  return String(el("terms-mode")?.value || "mixed");
}

function renderStats(stats = {}) {
  const box = el("terms-stats");
  if (!box) return;
  const total = Number(stats.total || 0);
  if (!total) {
    box.textContent = "还没有术语：先在右上角选一篇论文抽取，或在下方手动添加。";
    return;
  }
  const buckets = [
    ["未学习", stats.unlearned],
    ["不认识", stats.unknown],
    ["不熟悉", stats.unfamiliar],
    ["认识", stats.familiar],
    ["熟识", stats.mastered],
    ["不再复习", stats.retired],
  ];
  box.innerHTML = `共 <b>${total}</b> 个词` + buckets.map(([label, value]) =>
    `<span class="terms-bucket">${label} <b>${Number(value || 0)}</b></span>`).join("") +
    `<span class="terms-dict">累计答题 ${Number(stats.asked || 0)} · 对 ${Number(stats.correct || 0)} · 错 ${Number(stats.wrong || 0)} · 模糊 ${Number(stats.fuzzy || 0)}${stats.unclassified ? ` · 旧记录结果未知 ${Number(stats.unclassified)}` : ""}</span>` +
    (stats.dictionary_error ? `<span class="terms-dict">词典导出暂未完成，后台正在重试。</span>` : "") +
    (stats.dictionary ? `<span class="terms-dict">词典：${escapeHtml(stats.dictionary)}</span>` : "");
}

function renderList() {
  const box = el("terms-list");
  if (!box) return;
  if (!items.length) {
    box.innerHTML = '<p class="muted small">这里会列出收集到的术语和缩写。</p>';
    return;
  }
  box.innerHTML = items.map((item) => {
    const title = paperTitles.get(item.paper_id) || "";
    const meta = [title ? `《${title}》` : "", item.page ? `第 ${item.page} 页` : "", item.source === "ai" ? "AI 抽取" : item.source === "regex" ? "自动提取" : "手动添加"]
      .filter(Boolean).join(" · ");
    const mastery = Math.max(0, Math.min(100, Math.round(Number(item.mastery || 0))));
    const asked = Number(item.asked || 0);
    const correct = Number(item.correct || 0);
    const kindLabel = item.kind === "abbr" ? "缩写" : "单词";
    const detail = item.kind === "abbr" ? item.expansion : [item.phonetic ? `/${item.phonetic.replace(/^[\/\s]+|[\/\s]+$/g, "")}/` : "", item.pos || ""].filter(Boolean).join(" ");
    const mastered = mastery >= 100;
    const state = item.retired ? "不再复习" : mastered ? `熟识 · 维护 ${escapeHtml(item.due_at || "待安排")}` : (item.due_at ? `下次 ${escapeHtml(String(item.due_at).slice(0, 16))}` : "待复习");
    return `<div class="term-row${mastered ? " mastered" : ""}" data-id="${escapeHtml(item.id)}">
      <span class="tr-term">${escapeHtml(item.term)}<i class="tr-kind">${kindLabel}</i></span>
      <span class="tr-expansion">${escapeHtml(detail || "")}</span>
      <span class="tr-meaning">${escapeHtml(item.meaning || "")}</span>
      <span class="tr-mastery" title="总记忆度 ${mastery}% · 英→中 ${Number(item.mastery_en2zh || 0)}% · 中→英 ${Number(item.mastery_zh2en || 0)}% · 模糊 ${Number(item.fuzzy || 0)} 次"><span class="tr-mastery-bar"><i style="width:${mastery}%"></i></span><b>${mastery}%</b></span>
      <span class="tr-meta">${escapeHtml(meta)} · 答题 ${asked} 次（对 ${correct}） · 模糊 ${Number(item.fuzzy || 0)} 次 · 英→中 ${Number(item.mastery_en2zh || 0)}% / 中→英 ${Number(item.mastery_zh2en || 0)}% · ${state}</span>
      <button type="button" class="btn ghost tiny" data-term-edit="${escapeHtml(item.id)}">编辑纠错</button>
      ${item.retired ? "" : `<button type="button" class="btn ghost tiny" data-term-retire="${escapeHtml(item.id)}" title="留在词典里，只是不再出题">不再复习</button>`}
    </div>`;
  }).join("");
  box.querySelectorAll("[data-term-edit]").forEach(button => button.addEventListener("click", () => editTerm(button.dataset.termEdit)));
  box.querySelectorAll("[data-term-retire]").forEach((button) => {
    button.addEventListener("click", () => retireTerm(button.dataset.termRetire));
  });
}

async function refresh() {
  try {
    const request = ++listRequest;
    const query = encodeURIComponent(el("terms-search")?.value || "");
    const data = await api.get(`/terms?limit=${PAGE_SIZE}&offset=${listOffset}&q=${query}`);
    if (request !== listRequest) return;
    listTotal = data.total || 0;
    el("terms-list-count").textContent = `显示 ${listTotal ? listOffset + 1 : 0}–${Math.min(listOffset + PAGE_SIZE, listTotal)} / ${listTotal} 条`;
    el("terms-prev").disabled = listOffset === 0;
    el("terms-more").disabled = listOffset + PAGE_SIZE >= listTotal;
    items = data.items || [];
    renderStats(data.stats || {});
    renderList();
  } catch (err) {
    toast(`读取术语失败：${errorText(err)}`, "error");
  }
}

async function loadPapers() {
  try {
    const data = await api.get("/papers?limit=300&sort=updated");
    papers = data.items || [];
    paperTitles = new Map(papers.map((paper) => [paper.id, paper.title || "未命名论文"]));
    const select = el("terms-paper");
    if (!select) return;
    select.replaceChildren();
    const allOption = document.createElement("option");
    allOption.value = "";
    allOption.textContent = "全部英文文献（跨论文去重）";
    select.append(allOption);
    for (const paper of papers) {
      const option = document.createElement("option");
      option.value = paper.id;
      option.textContent = (paper.title || "未命名论文").slice(0, 40);
      select.append(option);
    }
  } catch { /* 论文列表失败不影响手动添加 */ }
}

async function addTerm(term, expansion, meaning) {
  try {
    await api.post("/terms", { term, expansion, meaning, paper_id: selectedPaper(), page: 0 });
    toast("已添加", "success", 1600);
    await refresh();
  } catch (err) {
    toast(`添加失败：${errorText(err)}`, "error");
  }
}

async function retireTerm(id) {
  if (!id) return;
  try {
    await api.post(`/terms/${encodeURIComponent(id)}/retire`, {});
    toast("已标记为不再复习，词仍留在词典里", "success", 2200);
    await refresh();
  } catch (err) { toast(`操作失败：${errorText(err)}`, "error"); }
}

async function openDictionary() {
  try {
    const result = await api.post("/terms/dictionary/open", {});
    toast(`已打开单词本：${result.path}`, "success", 3600);
  } catch (err) { toast(`打开单词本失败：${errorText(err)}`, "error"); }
}

/* ---------- 选择题复习 ---------- */

function hintFor(item) {
  const ask = item.direction === "meaning2term"
    ? (item.kind === "abbr" ? "选出正确的缩写 + 全称" : "选出正确的英文单词")
    : "选出正确的中文释义";
  if (item.note) return item.note.includes("选出") ? item.note : `${item.note} · ${ask}`;
  return ask;
}

function cancelQuizSession() {
  quizSession += 1;
  clearTimeout(advanceTimer);
  advanceTimer = null;
  quizController?.abort();
  quizController = null;
  pendingAnswer = null;
  savingAnswer = false;
}

function renderQuiz() {
  const item = quizItems[quizIndex];
  if (!item) { finishQuiz(); return; }
  answered = false;
  pendingAnswer = null;
  const box = el("terms-quiz");
  box.classList.remove("hidden");
  box.classList.remove("question-enter");
  void box.offsetWidth;
  box.classList.add("question-enter");
  el("terms-quiz-progress").textContent = `第 ${quizIndex + 1} / ${quizItems.length} 题 · 已保存答对 ${quizCorrect}`;
  el("terms-progress-bar").value = quizIndex;
  el("terms-progress-bar").max = quizItems.length;
  const prompt = el("terms-quiz-prompt");
  prompt.textContent = item.prompt;
  prompt.tabIndex = -1;
  prompt.focus({preventScroll: true});
  el("terms-quiz-hint").textContent = hintFor(item);
  const feedback = el("terms-quiz-feedback");
  feedback.textContent = "先回想，再选择；不确定时点对应选项旁的「模糊」。";
  feedback.className = "terms-quiz-feedback";
  el("btn-terms-unknown").hidden = false;
  el("btn-terms-next").hidden = true;
  el("btn-terms-retry").hidden = true;
  const options = el("terms-quiz-options");
  options.replaceChildren();
  item.options.forEach((option, index) => {
    const row = document.createElement("div");
    row.className = "quiz-option";
    row.style.setProperty("--option-index", index);
    const choice = document.createElement("button");
    choice.type = "button";
    choice.className = "quiz-option-text";
    choice.textContent = option;
    choice.addEventListener("click", () => submitAnswer(item, option, false));
    const fuzzy = document.createElement("button");
    fuzzy.type = "button";
    fuzzy.className = "quiz-option-fuzzy";
    fuzzy.textContent = "模糊";
    fuzzy.setAttribute("aria-label", `${option}，模糊回答`);
    fuzzy.title = "不太确定，答对只加少量分，不能单独达到熟识";
    fuzzy.addEventListener("click", () => submitAnswer(item, option, true));
    row.append(choice, fuzzy);
    options.append(row);
  });
}

function nextQuestion() {
  if (!answered || el("btn-terms-next").hidden) return;
  clearTimeout(advanceTimer);
  quizIndex += 1;
  renderQuiz();
}

function markUnknown() {
  const item = quizItems[quizIndex];
  if (item) submitAnswer(item, null, false, true);
}

async function submitAnswer(item, option, fuzzy = false, unknown = false) {
  if (answered) return;
  answered = true;
  pendingAnswer = {item, option, fuzzy, unknown};
  el("btn-terms-unknown").hidden = true;
  el("terms-quiz-options").querySelectorAll(".quiz-option").forEach(row => {
    row.querySelectorAll("button").forEach(button => { button.disabled = true; });
    const text = row.querySelector(".quiz-option-text").textContent;
    if (text === item.answer) row.classList.add("correct", ...(unknown ? ["reveal"] : []));
    if (text === option && option !== item.answer) row.classList.add("wrong");
    if (text === option && fuzzy) row.classList.add("fuzzy-selected");
  });
  await saveAnswer();
}

async function saveAnswer() {
  if (!pendingAnswer || savingAnswer) return;
  savingAnswer = true;
  const {item, option, fuzzy, unknown} = pendingAnswer;
  const session = quizSession;
  el("btn-terms-retry").hidden = true;
  el("btn-terms-next").hidden = true;
  const feedback = el("terms-quiz-feedback");
  feedback.textContent = "正在保存答题记录…";
  try {
    const data = await api.post(`/terms/${encodeURIComponent(item.id)}/answer`, {
      question_id: item.question_id, chosen_option: option, fuzzy, unknown,
    });
    if (session !== quizSession || item !== quizItems[quizIndex]) return;
    const correct = !unknown && option === item.answer;
    if (correct) quizCorrect += 1;
    pendingAnswer = null;
    renderStats(data.stats || {});
    feedback.textContent = correct
      ? (fuzzy ? "✓ 已保存 · 模糊答对，少量积分；继续巩固" : "✓ 已保存 · 答对了")
      : `已保存 · 正确答案：${item.answer}`;
    feedback.className = `terms-quiz-feedback ${correct ? "ok" : "bad"}`;
    el("btn-terms-next").textContent = "下一题";
    el("btn-terms-next").hidden = false;
    el("btn-terms-next").focus({preventScroll: true});
    if (el("terms-auto-advance").checked) {
      advanceTimer = setTimeout(() => { if (session === quizSession) nextQuestion(); }, 4000);
    }
  } catch (err) {
    if (session !== quizSession) return;
    feedback.textContent = `保存失败：${errorText(err)}。本题未计入本轮成绩，请重试。`;
    feedback.className = "terms-quiz-feedback bad";
    el("btn-terms-retry").hidden = false;
    el("btn-terms-next").hidden = false;
    el("btn-terms-next").textContent = "跳过本题（保存状态未确认）";
    el("btn-terms-retry").focus({preventScroll: true});
  } finally {
    if (session === quizSession) savingAnswer = false;
  }
}

function finishQuiz() {
  cancelQuizSession();
  el("terms-quiz")?.classList.add("hidden");
  if (quizItems.length) toast(`本轮已保存答对 ${quizCorrect} 题 / 共 ${quizItems.length} 题`, "info", 3200);
  quizItems = [];
  quizIndex = 0;
  el("btn-terms-start")?.focus({preventScroll: true});
  refresh();
}

async function startReview() {
  cancelQuizSession();
  const session = quizSession;
  quizController = new AbortController();
  el("terms-quiz")?.classList.add("hidden");
  quizItems = [];
  try {
    const data = await api.get(`/terms/quiz?limit=30&mode=${encodeURIComponent(currentMode())}`, {signal: quizController.signal});
    if (session !== quizSession) return;
    quizItems = data.items || [];
    quizIndex = 0;
    quizCorrect = 0;
    if (!quizItems.length) {
      toast("暂无可复习题目：可能尚未到期，或词条需要补充释义／全称及干扰选项。可在列表中编辑纠错。", "info", 4200);
      return;
    }
    renderStats(data.stats || {});
    renderQuiz();
  } catch (err) {
    if (session === quizSession) toast(`读取复习队列失败：${errorText(err)}`, "error");
  }
}

function editTerm(id) {
  const item = items.find(row => row.id === id);
  if (!item) return;
  const dialog = document.createElement("dialog");
  dialog.className = "terms-edit-dialog";
  const fields = [["term", "术语"], ["meaning", "中文释义"], ["expansion", "全称"], ["kind", "类型（word / abbr）"], ["phonetic", "音标"], ["pos", "词性"], ["aliases", "可接受别名（用英文分号分隔）"]];
  dialog.innerHTML = `<form><h3>编辑词条</h3>${fields.map(([key, label]) => `<label>${label}<input name="${key}" value="${escapeHtml(key === "aliases" ? (item.aliases || []).join(";") : item[key] || "")}" maxlength="${["meaning","expansion"].includes(key) ? 400 : key === "aliases" ? 2000 : 120}" ${key === "term" ? "required" : ""}></label>`).join("")}<p class="muted small">来源：${escapeHtml(item.source || "")} · ${escapeHtml(paperTitles.get(item.paper_id) || item.paper_id || "手动词条")} · 第 ${Number(item.page || 0)} 页。AI 抽取内容请核对原文。</p><p role="alert"></p><div class="quiz-actions"><button type="button" class="btn ghost">取消</button><button class="btn primary" type="submit">保存修正</button></div></form>`;
  dialog.querySelector('button[type="button"]').onclick = () => dialog.close();
  dialog.addEventListener("close", () => { dialog.remove(); el("terms-search")?.focus(); });
  dialog.querySelector("form").onsubmit = async event => {
    event.preventDefault();
    const body = Object.fromEntries(new FormData(event.target));
    body.aliases = body.aliases.split(";").map(a => a.trim()).filter(Boolean);
    const button = dialog.querySelector('[type="submit"]');
    button.disabled = true;
    try {
      await api.patch(`/terms/${encodeURIComponent(id)}`, body);
      dialog.close();
      await refresh();
    } catch (err) { dialog.querySelector('[role="alert"]').textContent = errorText(err); }
    finally { button.disabled = false; }
  };
  document.body.append(dialog);
  dialog.showModal();
}

function setExtractStatus(text, { showStop = false } = {}) {
  const box = el("terms-extract-status");
  const row = el("terms-extract-row");
  if (!box) return;
  if (row) row.hidden = !text;
  box.textContent = text || "";
  const stop = el("btn-terms-extract-stop");
  if (stop) stop.hidden = !showStop;
}

function stopExtractPolling() {
  if (extractTimer) {
    clearInterval(extractTimer);
    extractTimer = null;
  }
  const button = el("btn-terms-ai");
  if (button) button.disabled = false;
}

async function pollExtractJob() {
  try {
    const data = await api.get("/terms/extract/status");
    const job = data.job || {};
    if (job.running) {
      const aiButton = el("btn-terms-ai");
      if (aiButton) aiButton.disabled = true;
      setExtractStatus(
        `${job.cancel ? "正在停止" : "AI 抽取中"}：${job.done || 0}/${job.total || 0} 篇 · 新增 ${job.added || 0} 条` +
        (job.current ? ` · 当前《${job.current}》` : ""),
        { showStop: true },
      );
      if (data.stats) renderStats(data.stats);
      return;
    }
    stopExtractPolling();
    setExtractStatus(
      `AI 抽取完成：处理 ${job.done || 0}/${job.total || 0} 篇，新增 ${job.added || 0} 条` +
      (job.errors ? `，失败 ${job.errors} 篇` : ""),
      { showStop: false },
    );
    await refresh();
  } catch (err) {
    stopExtractPolling();
    setExtractStatus(`抽取状态读取失败：${errorText(err)}`);
  }
}

async function startAiExtractAll() {
  const button = el("btn-terms-ai");
  const ok = window.confirm(
    "AI 抽取全部英文文献：逐篇调用模型，每篇最多 25 个词，可能要十几分钟（可随时停止）。继续？",
  );
  if (!ok) return;
  if (button) button.disabled = true;
  try {
    await api.post("/terms/extract", { paper_id: "", mode: "ai", all: true });
    setExtractStatus("AI 抽取启动中…", { showStop: true });
    stopExtractPolling();
    extractTimer = setInterval(pollExtractJob, 2500);
    pollExtractJob();
  } catch (err) {
    if (button) button.disabled = false;
    setExtractStatus(`启动失败：${errorText(err)}`);
  }
}

async function extract(mode) {
  const paperId = selectedPaper();
  const all = !paperId;
  if (mode === "ai" && all) {
    await startAiExtractAll();
    return;
  }
  const button = mode === "ai" ? el("btn-terms-ai") : el("btn-terms-extract");
  const label = button?.textContent;
  if (button) {
    button.disabled = true;
    button.textContent = mode === "ai" ? "抽取中…" : (all ? "提取全部…" : "提取中…");
  }
  try {
    const result = await api.post("/terms/extract", { paper_id: paperId, mode, all });
    toast(result.added ? `新增 ${result.added} 条术语` : "没有发现新的术语", result.added ? "success" : "info", 2600);
    await refresh();
  } catch (err) {
    toast(`抽取失败：${errorText(err)}`, "error", 4200);
  } finally {
    if (button) { button.disabled = false; button.textContent = label || "抽取"; }
  }
}

export function initTerms() {
  el("btn-terms-next")?.addEventListener("click", nextQuestion);
  el("btn-terms-retry")?.addEventListener("click", saveAnswer);
  el("terms-auto-advance")?.addEventListener("change", () => clearTimeout(advanceTimer));
  el("terms-search")?.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { listOffset = 0; refresh(); }, 250);
  });
  el("terms-prev")?.addEventListener("click", () => { listOffset = Math.max(0, listOffset - PAGE_SIZE); refresh(); });
  el("terms-more")?.addEventListener("click", () => { listOffset += PAGE_SIZE; refresh(); });
  const page = el("terms-quiz")?.closest(".page");
  if (page) new MutationObserver(() => {
    if (!page.classList.contains("active")) {
      cancelQuizSession();
      quizItems = [];
      el("terms-quiz").classList.add("hidden");
    }
  }).observe(page, {attributes: true, attributeFilter: ["class"]});
  el("terms-add-form")?.addEventListener("submit", (event) => {
    event.preventDefault();
    const term = String(el("terms-new-term")?.value || "").trim();
    if (!term) return;
    const expansion = String(el("terms-new-expansion")?.value || "").trim();
    const meaning = String(el("terms-new-meaning")?.value || "").trim();
    addTerm(term, expansion, meaning);
    for (const field of ["terms-new-term", "terms-new-expansion", "terms-new-meaning"]) {
      const input = el(field);
      if (input) input.value = "";
    }
  });
  el("btn-terms-start")?.addEventListener("click", startReview);
  el("btn-terms-quiz-stop")?.addEventListener("click", finishQuiz);
  el("btn-terms-unknown")?.addEventListener("click", markUnknown);
  el("btn-terms-extract")?.addEventListener("click", () => extract("regex"));
  el("btn-terms-ai")?.addEventListener("click", () => extract("ai"));
  el("btn-terms-dict")?.addEventListener("click", openDictionary);
  el("btn-terms-extract-stop")?.addEventListener("click", async () => {
    try { await api.post("/terms/extract/cancel", {}); setExtractStatus("正在停止…", { showStop: false }); }
    catch (err) { setExtractStatus(`停止失败：${errorText(err)}`); }
  });
  const show = async () => {
    await loadPapers();
    await refresh();
  };
  window.addEventListener("pw:terms-shown", show);
  if (!loaded) { show().catch(() => {}); loaded = true; }
}
