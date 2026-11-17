/* End-to-end: isolated server + headless Edge, no user browser profile or remote LLM. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname, join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import net from 'node:net';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const dependencies = process.env.PAPERWHALE_NODE_MODULES || 'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const require = createRequire(join(dependencies, 'package.json'));
const { chromium } = require('playwright');
const temp = mkdtempSync(join(tmpdir(), 'pw-ai-ui-'));
const listener = net.createServer();
await new Promise(resolve => listener.listen(0, '127.0.0.1', resolve));
const port = listener.address().port;
await new Promise(resolve => listener.close(resolve));
const base = `http://127.0.0.1:${port}`;
let logs = '';
const server = spawn(join(root,'.venv/Scripts/python.exe'), [join(root,'tests/ai_ui_server.py'), String(port)], {
  cwd:root, windowsHide:true, env:{...process.env, PAPERWHALE_UI_TEST:'1', PAPERWHALE_UI_AUDIT:'1', PAPERWHALE_DATA:temp, PAPERWHALE_LLM_MOCK:'1'},
  stdio:['ignore','pipe','pipe'],
});
server.stdout.on('data', chunk => { logs += chunk; });
server.stderr.on('data', chunk => { logs += chunk; });
let browser;
try {
  let ready = false;
  for (let i=0; i<100; i++) {
    try { ready = (await fetch(base+'/api/health')).ok; } catch {}
    if (ready) break;
    if (server.exitCode !== null) throw new Error(logs);
    await new Promise(resolve => setTimeout(resolve,100));
  }
  assert.ok(ready, logs);
  browser = await chromium.launch({ executablePath:process.env.PAPERWHALE_EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe', headless:true });
  const page = await browser.newPage({ viewport:{width:1500,height:1000} });
  const output=join(root,'docs/ui-review-2026-10-27');
  mkdirSync(output,{recursive:true});
  const errors=[];
  page.on('pageerror',error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);
  await page.waitForFunction(() => document.querySelectorAll('.paper-row-item').length>0 && window.PW?.openPaper);
  const capture=async name => page.screenshot({path:join(output,name+'.png')});
  const observations={syntheticFixture:true,viewport:{width:1500,height:1000}};
  observations.library=await page.evaluate(async () => ({
    total:(await (await fetch('/api/papers?limit=1')).json()).total,
    displayed:document.querySelectorAll('.paper-row-item').length,
    heading:document.getElementById('library-sub').textContent,
    bodyScrollWidth:document.body.scrollWidth,viewport:innerWidth,
    unlabeledInputs:[...document.querySelectorAll('input,textarea,select')].filter(el => !el.labels?.length && !el.getAttribute('aria-label')).length,
  }));
  await capture('01-library');
  await page.locator('[data-id="audit229"]').first().click();
  await capture('02-detail');
  await page.locator('#library-search').fill('NO_MATCH_FOR_UI_AUDIT');
  await page.locator('#library-search').press('Enter');
  await page.locator('#library-empty').waitFor({state:'visible'});
  observations.filteredEmpty=await page.locator('#library-empty').innerText();
  await capture('03-filtered-empty');
  await page.locator('.nav-item[data-page="settings"]').click();
  observations.settings=await page.evaluate(() => ({cards:document.querySelectorAll('[data-page="settings"] .card').length,
    topScroll:document.querySelector('.main').scrollTop,scrollHeight:document.querySelector('.main').scrollHeight}));
  await capture('04-settings-top');
  await page.locator('#set-openalex-key').scrollIntoViewIfNeeded();
  await capture('05-settings-pdf');
  await page.evaluate(() => window.PW.openPaper('a'));
  await page.locator('#reader-tabs [data-tab="chat"]').click();
  await page.locator('#reader-chat-input').fill('这篇论文的核心方法是什么？');
  await page.locator('#reader-chat-send').click();
  await page.waitForFunction(() => !window.PW.readerState.chatSession.busy && window.PW.readerState.chatSession.data.messages.length>0);
  await capture('06-reader-chat');
  await page.locator('#reader-page-input').fill('3');
  await page.locator('#reader-page-input').press('Enter');
  await page.waitForFunction(() => window.PW.readerState.currentPage===3);
  await page.locator('#reader-close').click();
  await page.evaluate(() => window.PW.openPaper('a'));
  observations.reopenedPage=await page.evaluate(() => window.PW.readerState.currentPage);
  await page.setViewportSize({width:1100,height:720});
  await page.waitForTimeout(600);
  await page.locator('#reader-tabs [data-tab="chat"]').click();
  await capture('07-reader-small');
  observations.smallReader=await page.evaluate(() => ({viewport:innerWidth,scrollWidth:document.body.scrollWidth,
    pdfWidth:document.getElementById('pdf-scroll').getBoundingClientRect().width,
    chatWidth:document.getElementById('reader-chat-messages').getBoundingClientRect().width}));
  await page.locator('#reader-close').click();
  await page.setViewportSize({width:1500,height:1000});
  await page.locator('.nav-item[data-page="settings"]').click();
  await page.locator('#btn-profile-ai').click();
  await page.waitForFunction(() => !document.getElementById('profile-chat-send').disabled);
  observations.modal=await page.locator('#profile-chat-modal').evaluate(el => ({role:el.getAttribute('role'),ariaModal:el.getAttribute('aria-modal')}));
  const focus=[];
  for (let n=0;n<18;n++) {
    await page.keyboard.press('Tab');
    focus.push(await page.evaluate(() => ({id:document.activeElement.id,inside:!!document.activeElement.closest('#profile-chat-modal')})));
  }
  observations.modal.focusEscapes=focus.some(item => !item.inside);
  observations.modal.focusSequence=focus;
  await capture('08-profile-modal');
  await page.locator('#profile-chat-close').click();
  observations.brokenFile=await (await page.request.get(base+'/api/papers/broken')).json();
  observations.brokenFile={has_pdf:observations.brokenFile.has_pdf,status:observations.brokenFile.status};
  observations.errors=errors;
  writeFileSync(join(output,'observations.json'),JSON.stringify(observations,null,2));
  console.log(JSON.stringify(observations,null,2));

} finally {
  await browser?.close();
  if (server.exitCode === null) {
    const exited = new Promise(resolve => server.once('exit', resolve));
    server.kill();
    await exited;
  }
  // Only remove the exact newly-created test directory under the system temp root.
  const absolute = resolve(temp);
  if (!absolute.startsWith(resolve(tmpdir())+sep) || !dirname(absolute).startsWith(resolve(tmpdir())) || !absolute.split(sep).at(-1).startsWith('pw-ai-ui-')) throw new Error('Unexpected test data path');
  rmSync(absolute, {recursive:true,force:true});
}
