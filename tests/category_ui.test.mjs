import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const read = (rel) => readFileSync(path.join(ROOT, rel), "utf8");

const HTML = read("web/index.html");
const ASSISTANT = read("web/js/category-assistant.js");
const APP = read("web/js/app.js");
const TASKS = read("web/js/task-center.js");

test("分类助手有「对所有论文重新分类」入口和 PDF 移动开关", () => {
  for (const id of ['id="btn-apply-categories-all"', 'id="category-move-files"', 'id="category-move-wrap"']) {
    assert.ok(HTML.includes(id), `index.html 缺少 ${id}`);
  }
  assert.match(HTML, /对所有论文重新分类/, "按钮文案要对用户说清楚这是全量重分类");
});

test("全量分类会带 scope=all 和 move_files，并在覆盖前确认", () => {
  assert.match(ASSISTANT, /apply\("all"\)/, "全量按钮必须调用 apply('all')");
  assert.match(ASSISTANT, /scope: allScope \? "all" : "unclassified"/, "payload 必须带 scope");
  assert.match(ASSISTANT, /move_files: moveFiles/, "payload 必须带 move_files");
  assert.match(ASSISTANT, /window\.confirm\(/, "全量重分类覆盖已有分类，必须先确认");
  assert.match(ASSISTANT, /preview\?scope=all/, "确认框要先问后端有多少篇");
});

test("分类进度写进任务中心，且刷新/重开弹窗还能接上", () => {
  assert.match(ASSISTANT, /updateTaskBatch\("category", status\)/, "分类进度要写进任务中心批次");
  assert.match(ASSISTANT, /resumeStatus\(\)/, "重开弹窗要能接着显示在跑的那批");
  // 原先前端最多轮询 120 次（4 分钟）就停，长批次会「假死」；现在不能有这个上限
  assert.ok(!/index < 120/.test(ASSISTANT), "轮询不能再用 120 次（4 分钟）上限");
  assert.match(ASSISTANT, /任务中心/, "提示要告诉用户进度在任务中心看");
});

test("全局轮询与任务中心都认识 category 批次", () => {
  assert.match(APP, /pollCategories\(\)/, "app.js 要有全局分类轮询，这样不进弹窗也能看到进度");
  assert.match(APP, /updateTaskBatch\("category", status\)/, "全局轮询要把状态喂给任务中心");
  assert.match(APP, /正在批量分类/, "右下角进度要有分类这一行");
  assert.match(TASKS, /'分类整理'/, "任务中心要把 category 批次显示成「分类整理」");
});

test("前端脚本语法可解析", () => {
  for (const rel of ["web/js/app.js", "web/js/category-assistant.js", "web/js/task-center.js"]) {
    execFileSync(process.execPath, ["--check", path.join(ROOT, rel)], { stdio: "pipe" });
  }
});
