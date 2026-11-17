/* 卷期页 UI 静态回归（2.7.27）：编辑弹窗、详情侧栏与设置页补齐入口。
 *
 * 用户要求「几乎所有参考文献格式都要卷期页」，所以这里钉住三条链路都不掉字段：
 *   编辑弹窗读/写 volume·issue·pages → 详情侧栏安全渲染 → 设置页批量补齐。
 */
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {fileURLToPath} from "node:url";
import {dirname, join} from "node:path";
import {test} from "node:test";

const here = dirname(fileURLToPath(import.meta.url));
const web = join(here, "..", "web");
const read = (path) => readFileSync(join(web, path), "utf8");

const indexHtml = read("index.html");
const styleCss = read("css/style.css");
const libraryJs = read("js/library.js");
const settingsJs = read("js/settings.js");

test("the edit modal exposes volume/issue/pages inside one grid-3 container", () => {
  for (const id of ["edit-volume", "edit-issue", "edit-pages"]) {
    assert.ok(indexHtml.includes(`id="${id}"`), `index.html must keep #${id}`);
  }
  const grid = indexHtml.match(/<div class="grid-3">([\s\S]*?)<\/div>/);
  assert.ok(grid, 'the three fields must live in a <div class="grid-3"> container');
  for (const id of ["edit-volume", "edit-issue", "edit-pages"]) {
    assert.ok(grid[1].includes(`id="${id}"`), `#${id} must be inside the grid-3 container`);
  }
  assert.match(indexHtml, /<p class="muted small">卷\/期\/页码[^<]*<\/p>/,
    'a muted note must explain that volume/issue/pages feed the bibliography');
});

test("style.css lays the three fields out with auto-fit/minmax", () => {
  assert.match(styleCss, /\.grid-3\s*\{[^}]*display:\s*grid/,
    '.grid-3 must be a grid');
  assert.match(styleCss, /\.grid-3\s*\{[^}]*grid-template-columns:\s*repeat\(auto-fit,\s*minmax\(190px,\s*1fr\)\)/,
    '.grid-3 must use repeat(auto-fit, minmax(190px, 1fr))');
});

test("openEditModal fills the three fields from the paper", () => {
  assert.match(libraryJs, /byId\("edit-volume"\)\.value\s*=\s*paper\.volume\s*\|\|\s*""/);
  assert.match(libraryJs, /byId\("edit-issue"\)\.value\s*=\s*paper\.issue\s*\|\|\s*""/);
  assert.match(libraryJs, /byId\("edit-pages"\)\.value\s*=\s*paper\.pages\s*\|\|\s*""/);
});

test("savePaperEdit sends all three trimmed fields in the PATCH body", () => {
  const start = libraryJs.indexOf("async function savePaperEdit");
  assert.ok(start >= 0, "library.js must keep savePaperEdit()");
  const patchStart = libraryJs.indexOf("api.patch(", start);
  assert.ok(patchStart > start, "savePaperEdit must call api.patch");
  const patchEnd = libraryJs.indexOf("});", patchStart);
  assert.ok(patchEnd > patchStart, "the PATCH body must be a closed object literal");
  const body = libraryJs.slice(patchStart, patchEnd);
  for (const field of ["volume", "issue", "pages"]) {
    assert.ok(body.includes(`${field}: byId("edit-${field}").value.trim()`),
      `the PATCH body must carry trimmed ${field} (got: ${body.slice(0, 120)}…)`);
  }
});

test("the detail sidebar renders the volume(issue): pages line through escapeHtml", () => {
  const metaStart = libraryJs.indexOf('class="detail-meta"');
  assert.ok(metaStart >= 0, 'library.js must keep the .detail-meta block');
  const volumeAt = libraryJs.indexOf("paper.volume", metaStart);
  assert.ok(volumeAt > metaStart, 'the detail meta must render paper.volume');
  const line = libraryJs.slice(metaStart, volumeAt + 400);
  assert.match(line, /escapeHtml\(\[paper\.volume/,
    'the volume/issue/pages line must be escaped with escapeHtml(');
  assert.match(line, /\]\s*\.filter\(Boolean\)\s*\.join\(": "\)\)/,
    'the whole volume/issue/pages expression must be the argument of escapeHtml(');
  assert.match(line, /\(\\?\$\{paper\.issue\}\)/, 'issue must render as volume(issue)');
  assert.match(line, /join\(": "\)/, 'pages must join as "volume(issue): pages"');
  assert.equal(/>\$\{paper\.volume\}/.test(line), false,
    'paper.volume must never be spliced straight into the HTML template (XSS)');
});

test("settings.js exposes the backfill button and polls the batch status", () => {
  assert.ok(settingsJs.includes("btn-backfill-bibliographic"), 'must keep the backfill button id');
  assert.ok(settingsJs.includes("/papers/backfill-bibliographic"),
    'must POST /api/papers/backfill-bibliographic');
  assert.ok(settingsJs.includes("/papers/backfill-bibliographic/status"),
    'must poll GET /api/papers/backfill-bibliographic/status');
  assert.match(settingsJs, /api\.post\('\/papers\/backfill-bibliographic',\s*\{\s*limit:\s*300\s*\}\)/,
    'the backfill request must ask for limit=300');
  // 从点击处理器开始截，而不是从模板里的按钮 id（模板在前，会把窗口截短）
  const handlerAt = settingsJs.indexOf("byId('btn-backfill-bibliographic')?.addEventListener");
  assert.ok(handlerAt >= 0, 'must keep the backfill click handler');
  const handler = settingsJs.slice(handlerAt, handlerAt + 3000);
  assert.ok(handler.includes("maintenance-results"), 'progress must go to #maintenance-results');
  assert.match(handler, /setTimeout\(\s*poll\s*,\s*1500\s*\)/,
    'the status poll must repeat every 1.5s');
  assert.match(handler, /if\s*\(\s*status\.running\s*\)/, 'polling must continue while status.running');
  assert.ok(handler.includes("emit('library-changed')"),
    'finishing the backfill must refresh the library');
});
