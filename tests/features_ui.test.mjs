/* 剪贴板监听 + 参考文献外扩 + 两个新栏目的接线回归（静态断言，不需要浏览器）。 */
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
import { test } from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "..", "web");
const read = (path) => readFileSync(join(web, path), "utf8");

const indexHtml = read("index.html");
const appJs = read("js/app.js");
const clipboardJs = read("js/clipboard.js");
const referencesJs = read("js/references.js");
const settingsJs = read("js/settings.js");
const taskCenterJs = read("js/task-center.js");
const libraryJs = read("js/library.js");
const featuresCss = read("css/features.css");

test("sidebar exposes the two new pages and both get sections", () => {
  assert.match(indexHtml, /data-page="methods"[^>]*>[\s\S]*?方法 \/ 参数/);
  assert.match(indexHtml, /data-page="cards"[^>]*>[\s\S]*?复习卡片/);
  assert.match(indexHtml, /<section class="page" data-page="methods">/);
  assert.match(indexHtml, /<section class="page" data-page="cards">/);
});

test("methods page shell keeps the ids the page module binds", () => {
  for (const id of [
    "btn-methods-refresh",
    "btn-methods-extract",
    "methods-search",
    "methods-state",
    "methods-status",
    "methods-list",
  ]) {
    assert.ok(indexHtml.includes(`id="${id}"`), `missing #${id}`);
  }
});

test("cards page shell keeps review elements", () => {
  for (const id of [
    "btn-cards-generate",
    "btn-cards-start",
    "cards-summary",
    "cards-review",
    "cards-front",
    "btn-cards-reveal",
    "cards-back",
    "cards-grades",
  ]) {
    assert.ok(indexHtml.includes(`id="${id}"`), `missing #${id}`);
  }
  for (const grade of ["again", "hard", "good", "easy"]) {
    assert.ok(indexHtml.includes(`data-grade="${grade}"`), `missing grade ${grade}`);
  }
});

test("app.js wires navigation, init and batch polling for the new features", () => {
  assert.match(appJs, /import \{ initMethods \} from "\.\/methods-page\.js"/);
  assert.match(appJs, /import \{ initCards \} from "\.\/cards-page\.js"/);
  assert.match(appJs, /import \{ initClipboard \} from "\.\/clipboard\.js"/);
  assert.match(appJs, /import \{ initReferenceExpand \} from "\.\/references\.js"/);
  assert.ok(appJs.includes("pw:methods-shown"));
  assert.ok(appJs.includes("pw:cards-shown"));
  assert.ok(appJs.includes('"/methods/status"'));
  assert.ok(appJs.includes('"/cards/status"'));
  assert.match(appJs, /initMethods\(\)/);
  assert.match(appJs, /initCards\(\)/);
  assert.match(appJs, /initClipboard\(\)/);
  assert.match(appJs, /initReferenceExpand\(\)/);
});

test("task center labels the new batch kinds", () => {
  assert.ok(taskCenterJs.includes("方法参数"));
  assert.ok(taskCenterJs.includes("复习卡片"));
});

test("clipboard prompt polls candidates and posts accept/dismiss", () => {
  assert.ok(indexHtml.includes('id="clipboard-prompt"'));
  assert.ok(indexHtml.includes('id="cp-import"'));
  assert.ok(indexHtml.includes('id="cp-dismiss"'));
  assert.ok(clipboardJs.includes("/clipboard/candidates"));
  assert.ok(clipboardJs.includes("/accept"));
  assert.ok(clipboardJs.includes("/dismiss"));
  assert.match(clipboardJs, /encodeURIComponent/);
  assert.match(clipboardJs, /setTimeout\(poll, POLL_MS\)/);
  assert.ok(!clipboardJs.includes("setInterval"), "should not poll with setInterval");
});

test("reference expand modal calls the expand/import API and escapes titles", () => {
  assert.ok(indexHtml.includes('id="reference-modal"'));
  assert.ok(indexHtml.includes('id="reference-list"'));
  assert.ok(indexHtml.includes('id="btn-reference-import"'));
  assert.ok(referencesJs.includes("/references/expand"));
  assert.ok(referencesJs.includes("/references/import"));
  assert.match(referencesJs, /escapeHtml\(item\.title/);
  assert.ok(referencesJs.includes("existing_paper_id"));
  assert.ok(referencesJs.includes("window.PW.openReferenceExpand"));
});

test("library context menu exposes the reference expand action", () => {
  assert.ok(indexHtml.includes('data-action="references"'));
  assert.match(libraryJs, /action === "references"/);
  assert.match(libraryJs, /openReferenceExpand/);
});

test("settings persist the clipboard switches", () => {
  assert.ok(indexHtml.includes('id="set-clipboard-watch"'));
  assert.ok(indexHtml.includes('id="set-clipboard-title"'));
  assert.match(settingsJs, /clipboard_watch:/);
  assert.match(settingsJs, /clipboard_title_guess:/);
  assert.match(settingsJs, /clipboard:\["clipboard_watch","clipboard_title_guess"\]/);
});

test("clipboard prompt styles exist", () => {
  assert.match(featuresCss, /\.clipboard-prompt/);
  assert.match(featuresCss, /\.reference-item/);
});
