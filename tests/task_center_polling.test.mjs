// Isolated browser, mock HTTP only: no application server, library, or user profile.
import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {createRequire} from 'node:module';
import {join} from 'node:path';

const require = createRequire(join(process.env.PAPERWHALE_NODE_MODULES || 'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules', 'package.json'));
const {chromium} = require('playwright');
const source = name => readFileSync(new URL(`../web/js/${name}`, import.meta.url), 'utf8');
const batch = (id, kind, extra = {}) => ({batch_id:id, kind, created_at:'2026-11-01', total:2, pending:1, active:0, done:1, succeeded:0, failed:1, errors:[{title:'Test', error:'<unsafe>'}], ...extra});

test('all batches poll while hidden; unchanged and progressing rows preserve interaction state', async () => {
  const browser = await chromium.launch({headless:true, executablePath:process.env.PAPERWHALE_EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  try {
    const page = await browser.newPage();
    const errors = [];
    page.on('pageerror', error => errors.push(error.message));
    let items = [batch('old-analysis', 'analyze'), batch('old-pdf', 'pdf'), batch('latest', 'cards')];
    const requests = [];
    let failNext = false;
    await page.route('http://task.test/**', async route => {
      const url = new URL(route.request().url());
      if (url.pathname.startsWith('/api/tasks/batches')) {
        requests.push(url);
        if (failNext) { failNext = false; return route.fulfill({status:503, body:'unavailable'}); }
        return route.fulfill({contentType:'application/json', body:JSON.stringify({items})});
      }
      if (url.pathname.endsWith('.js')) return route.fulfill({contentType:'text/javascript', body:source(url.pathname.slice(4))});
      await route.fulfill({contentType:'text/html', body:`<style>#task-center-content{height:120px;overflow:auto}.task-batch{height:250px}</style><button id="btn-task-center">Tasks</button><section id="task-center"><button id="task-center-close">Close</button><div id="task-center-content"></div></section><script type="module">import * as tasks from '/js/task-center.js'; window.tasks=tasks;tasks.initTaskCenter();</script>`});
    });
    await page.goto('http://task.test/');
    await page.waitForFunction(() => document.querySelectorAll('.task-batch').length === 3);
    await page.evaluate(() => {
      const article = document.querySelector('.task-batch');
      window.saved = {article, details:article.querySelector('details'), button:article.querySelector('button')};
      saved.details.open = true;
      saved.button.focus();
      document.getElementById('task-center-content').scrollTop = 80;
      window.mutations = 0;
      new MutationObserver(entries => window.mutations += entries.length).observe(document.getElementById('task-center-content'), {subtree:true,childList:true,attributes:true,characterData:true});
    });
    await page.evaluate(b => tasks.updateTaskBatch('analyze', b), items[0]);
    assert.equal(await page.evaluate(() => mutations), 0, 'identical status must not mutate rows');
    await page.evaluate(b => tasks.updateTaskBatch('analyze', b), {...items[0], pending:0, active:1});
    assert.deepEqual(await page.evaluate(() => ({article:saved.article === document.querySelector('.task-batch'), details:saved.details === document.querySelector('details'), open:saved.details.open, focus:document.activeElement === saved.button, scroll:document.getElementById('task-center-content').scrollTop})), {article:true,details:true,open:true,focus:true,scroll:80});
    assert.equal(await page.locator('[data-errors] script').count(), 0);
    assert.match(await page.locator('[data-errors]').first().innerText(), /<unsafe>/);
    await page.locator('#task-center-close').click();
    failNext = true;
    // The first failed poll leaves the previous snapshot intact; the next retries.
    items = items.map(item => ({...item, pending:0, active:0, done:2, succeeded:1}));
    await page.waitForFunction(() => [...document.querySelectorAll('progress')].every(el => el.value === 2), {timeout:10000});
    assert.ok(requests.length >= 3);
    assert.ok(requests.slice(1).some(url => ['old-analysis','old-pdf','latest'].every(id => url.searchParams.getAll('batch_id').includes(id))), 'poll must request each unfinished batch by ID');
    assert.equal(await page.evaluate(() => saved.details.open), true);
    assert.equal(await page.locator('#task-center').evaluate(el => el.classList.contains('hidden')), true);
    assert.deepEqual(errors, []);
  } finally { await browser.close(); }
});
