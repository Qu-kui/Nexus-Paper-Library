# 阅读窗口第一批增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use subagent-driven-development or executing-plans. Tasks below are independently testable; root integrates the reader. User approved the four-feature first batch on 2026-10-27. No further design approval is needed.

**Goal:** Add PDF native link navigation/history, annotation filtering/export, fallback chapter navigation, and visible per-turn AI materials to the current local reader.

**Architecture:** Small ES modules provide pure helpers and owned UI controllers. Existing `reader.js` supplies document identity, page navigation, annotations and conversation state; all async work captures the document/open token. No PDF file or installed-app changes. The project has no Git repository, so no commits/worktrees.

**Tech Stack:** Vanilla JS modules, PDF.js, FastAPI/SQLite, Node assert and isolated headless Edge fixtures.

## 1. Annotation tools

Files: create `web/js/reader-annotations.js`, `tests/reader_annotations.test.mjs`; root modifies `reader.js` and reader CSS.

- [x] Write and run failing Node tests for `filterAnnotations(items,{color,note,query,chapter},chapters)` and `annotationsMarkdown(paper,items,chapters)`.
- [x] Implement pure filtering by color/note-presence/text/chapter, sorted page/id; escape Markdown metadata and provide title/authors/DOI, page, source quote, note and chapter in UTF-8 export. Never treat rendered HTML as export data.
- [x] Root adds accessible controls in the annotation tab, shows shown/total count and exports the current filtered set as `.md`. All annotations remain on the page regardless of list filter; zero results provides reset. Edit/deletion refreshes the filtered list.
- [x] Run `node --test tests/reader_annotations.test.mjs` and browser filtering/download assertions against two papers with different annotations. Check selected filters reset on paper switch.

## 2. Fallback outline

Files: create `web/js/reader-outline.js`, `tests/reader_outline.test.mjs`; root integrates `loadOutline`.

- [x] Write/run failing tests for `extractHeadings(pages)` where pages are `{page,items}` PDF.js text content; cover English numbered and Chinese headings, font prominence, repeated headers, ordinary sentence rejection and empty/scanned pages.
- [x] Implement deterministic local heuristics, no LLM/network calls. Return `{title,page,level}`. Limit scan to a bounded page count, yield between pages and check ownership after each await. Label result as automatically recognized and show progress/cancel/retry if no embedded outline.
- [x] Native outline remains preferred. Clicking any outline entry keeps the outline tab open and records the origin for navigation back. Do not fabricate a confident section when recognition is uncertain; provide page navigation when none found.
- [x] Run `node --test tests/reader_outline.test.mjs` and Edge fallback outline/late-old-document assertions.

## 3. Native PDF links and navigation history

Files: create `web/js/reader-navigation.js`, `tests/reader_navigation.test.mjs`; modify `reader.js`, `reader-enhancements.css`.

- [x] Write/run failing tests for per-document bounded back/forward navigation and safe link classification (`https/http/mailto` allowed, script/file rejected).
- [x] Overlay native PDF link rectangles from `getAnnotations`; named/explicit destinations resolve through the captured document. Support page/vertical target offset where the destination provides coordinates; external links open via safe anchors. No inferred figure/reference links in this batch.
- [x] Add back/forward buttons with accessible names and disabled states. Record real current reading position before link/outline/annotation/search/page jumps; history traversal restores page/offset without adding history. Clear on document switch and prevent old async links affecting the new document.
- [x] Run navigation Node tests and isolated Edge test with an actual native-link PDF; verify forward-history truncation after a new jump, cross-paper isolation and selection unaffected outside links.

## 4. AI materials visibility and integration

Files: create `web/js/reader-context.js`, `tests/reader_context.test.mjs`; modify `reader.js`, reader CSS and, if necessary, AI request handling.

- [x] Write/run failing tests for a snapshot of `{paperId,title,page,selection,annotations,chapter,attachments}` and visible scope text; old-paper selections must be excluded.
- [x] Show current paper/page/selected text/attachments in the chat area. Provide “解释本页”, “梳理本节” (only with known section), “结合批注回答” (only with annotations), and a removable selected-material preview. Capture request material before await; no stale DOM selection silently changes an in-flight request. Keep web/deep defaults true.
- [x] Explicit chapter/annotation requests send their selected materials with page references, not as trusted system instructions; keep each paper's history isolated and show what was included on the user message. Bound material size and state truncation. Existing ordinary questions continue to use current page and selection.
- [x] Run `node --test tests/reader_context.test.mjs`, all new Node helpers, `node tests/reader_first_batch_ui.mjs`, existing `reader_ui.mjs` and AI conversation browser tests; run backend regressions if API changes.

## Final review

- [x] Spec coverage first, then code quality/ownership/error handling review, followed by relevant fresh tests. Test data uses new temporary folders only.
- [x] Update `HANDOFF.md` and user-facing reader instructions with scope and limitations. Do not package/install without a separate request.
