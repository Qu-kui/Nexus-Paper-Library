/* End-to-end: isolated server + headless Edge, no user browser profile or remote LLM. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync } from 'node:fs';
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
  cwd:root, windowsHide:true, env:{...process.env, PAPERWHALE_UI_TEST:'1', PAPERWHALE_DATA:temp, PAPERWHALE_LLM_MOCK:'1'},
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
  const errors = [];
  page.on('pageerror', err => errors.push(err.message));
  await page.addInitScript(() => localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);
  await page.waitForFunction(() => Boolean(window.PW?.openPaper));
  const open = async id => {
    await page.evaluate(id => window.PW.openPaper(id), id);
    await page.locator('#reader-tabs [data-tab="chat"]').click();
    await page.waitForFunction(() => !document.getElementById('reader-chat-send').disabled);
  };
  await open('a');
  await page.locator('#reader-chat-input').fill('ALPHA_ONLY_QUESTION');
  await page.locator('#reader-chat-send').click();
  await page.waitForFunction(() => !window.PW.readerState.chatSession.busy && window.PW.readerState.chatSession.data.messages.length===2);
  await open('b');
  assert.ok(!(await page.locator('#reader-chat-messages').innerText()).includes('ALPHA_ONLY_QUESTION'));
  await open('a');
  assert.ok((await page.locator('#reader-chat-messages').innerText()).includes('ALPHA_ONLY_QUESTION'));
  console.log('PASS per-paper history restores without leaking to B');

  // Hold A's request until B is visible, then allow the actual backend response through.
  let release;
  const gate = new Promise(resolve => { release=resolve; });
  let started;
  const incoming = new Promise(resolve => { started=resolve; });
  await page.route('**/api/ai/chat', async route => { started(); await gate; await route.continue(); });
  await page.locator('#reader-chat-input').fill('DELAYED_ALPHA_QUESTION');
  await page.locator('#reader-chat-send').click();
  await incoming;
  await open('b');
  release();
  await page.waitForFunction(async () => {
    const {getConversation} = await import('/js/conversation.js');
    return !getConversation('paper:a').busy;
  });
  assert.ok(!(await page.locator('#reader-chat-messages').innerText()).includes('DELAYED_ALPHA_QUESTION'));
  await open('a');
  assert.ok((await page.locator('#reader-chat-messages').innerText()).includes('DELAYED_ALPHA_QUESTION'));
  await page.unroute('**/api/ai/chat');
  console.log('PASS delayed response remains attached to the original paper');

  // Read an attachment while another paper is open.
  let finishUpload;
  const uploadGate = new Promise(resolve => { finishUpload=resolve; });
  let uploading;
  const uploadStarted = new Promise(resolve => { uploading=resolve; });
  await page.route('**/api/ai/profile/upload', async route => { uploading(); await uploadGate; await route.continue(); });
  await page.locator('#reader-chat-file').setInputFiles({name:'alpha.txt', mimeType:'text/plain', buffer:Buffer.from('ALPHA_ATTACHMENT')});
  await uploadStarted;
  await open('b');
  finishUpload();
  await page.waitForFunction(async () => {
    const {getConversation} = await import('/js/conversation.js');
    return getConversation('paper:a').data.attachments.length===1;
  });
  assert.equal(await page.locator('#reader-chat-attachments').innerText(), '');
  await open('a');
  assert.ok((await page.locator('#reader-chat-attachments').innerText()).includes('alpha.txt'));
  await page.unroute('**/api/ai/profile/upload');
  console.log('PASS in-flight attachment is saved only to its original paper');

  // Selected text explanation must become a normal turn in A's history.
  await page.evaluate(() => {
    window.PW.readerState.selection={text:'SELECTION_ALPHA_TERM',pageNum:1};
    document.querySelector('#selection-toolbar button[data-action="explain"]').click();
  });
  await page.waitForFunction(() => !window.PW.readerState.chatSession.busy && window.PW.readerState.chatSession.data.messages.length===6);
  assert.ok((await page.locator('#reader-chat-messages').innerText()).includes('SELECTION_ALPHA_TERM'));
  await page.reload();
  await page.waitForFunction(() => Boolean(window.PW?.openPaper));
  await open('a');
  assert.ok((await page.locator('#reader-chat-messages').innerText()).includes('SELECTION_ALPHA_TERM'));
  assert.ok((await page.locator('#reader-chat-attachments').innerText()).includes('alpha.txt'));
  console.log('PASS selection history and materials survive a page reload');

  let releaseCleared;
  const clearGate = new Promise(resolve => { releaseCleared=resolve; });
  let clearStarted;
  const clearIncoming = new Promise(resolve => { clearStarted=resolve; });
  await page.route('**/api/ai/chat', async route => { clearStarted(); await clearGate; await route.continue(); });
  await page.locator('#reader-chat-input').fill('SHOULD_NOT_RETURN_AFTER_CLEAR');
  await page.locator('#reader-chat-send').click();
  await clearIncoming;
  await page.locator('#reader-chat-clear').click();
  await page.waitForFunction(() => window.PW.readerState.chatSession.data.messages.length===0);
  releaseCleared();
  await page.waitForResponse(response => response.url().endsWith('/api/ai/chat') && response.status()===409);
  await page.unroute('**/api/ai/chat');
  assert.ok(!(await page.locator('#reader-chat-messages').innerText()).includes('SHOULD_NOT_RETURN_AFTER_CLEAR'));
  assert.equal(await page.locator('#reader-chat-input').inputValue(), '');
  assert.equal(await page.locator('#reader-chat-attachments').innerText(), '');
  console.log('PASS clear rejects an in-flight response and does not restore the old input');
  await page.locator('#reader-close').click();
  await page.locator('.nav-item[data-page="chat"]').click();
  await page.locator('#chat-input').fill('LIBRARY_ONLY_QUESTION');
  await page.locator('#btn-chat-send').click();
  await page.waitForFunction(() => !document.getElementById('btn-chat-send').disabled);
  assert.ok((await page.locator('#chat-messages').innerText()).includes('LIBRARY_ONLY_QUESTION'));
  await page.reload();
  await page.locator('.nav-item[data-page="chat"]').click();
  await page.waitForFunction(() => document.getElementById('chat-messages').textContent.includes('LIBRARY_ONLY_QUESTION'));
  console.log('PASS independent clear and library history persistence');

  await page.locator('.nav-item[data-page="settings"]').click();
  let migrationRelease;
  const migrationGate=new Promise(resolve => {migrationRelease=resolve;});
  let migrationStarted;
  const migrationIncoming=new Promise(resolve => {migrationStarted=resolve;});
  await page.route('**/api/ai/conversations/profile/migrate',async route => {migrationStarted();await migrationGate;await route.continue();});
  await page.locator('#btn-profile-ai').click();
  await migrationIncoming;
  try { assert.equal(await page.locator('#profile-chat-send').isDisabled(),true); }
  finally { migrationRelease(); }
  await page.waitForFunction(() => !document.getElementById('profile-chat-send').disabled);
  await page.unroute('**/api/ai/conversations/profile/migrate');
  await page.locator('#profile-chat-input').fill('帮我整理成研究画像');
  await page.locator('#profile-chat-send').click();
  await page.locator('#profile-chat-apply').waitFor({state:'visible'});
  await page.locator('#profile-chat-apply').click();
  await page.locator('#profile-chat-modal').waitFor({state:'hidden'});
  assert.ok((await page.locator('#set-profile').inputValue()).includes('光学成像'));
  await page.locator('.nav-item[data-page="library"]').click();
  await page.locator('#btn-category-assistant').click();
  await page.waitForFunction(() => !document.getElementById('category-chat-send').disabled);
  await page.locator('#category-chat-input').fill('帮我给一个分类方案');
  await page.locator('#category-chat-send').click();
  await page.locator('#btn-apply-categories').waitFor({state:'visible'});
  await page.locator('#category-chat-clear').click();
  await page.locator('#btn-apply-categories').waitFor({state:'hidden'});
  await page.locator('#category-assistant-close').click();
  await page.locator('.nav-item[data-page="settings"]').click();
  await page.locator('#set-openalex-key').fill('UI_TEST_KEY');
  await page.locator('#set-openalex-content').check();
  await page.locator('#set-pdf-browser').selectOption('Chrome');
  await page.locator('#set-pdf-browser-profile').fill('Profile 2');
  await page.locator('#set-pdf-browser-session').uncheck();
  await page.locator('#btn-save-settings').click();
  await page.waitForFunction(() => document.getElementById('set-openalex-key').placeholder.includes('已保存'));
  assert.equal(await page.locator('#set-openalex-key').inputValue(),'');
  const settings=await (await page.request.get(base+'/api/settings')).json();
  assert.equal(settings.openalex_content_enabled,true);
  assert.equal(settings.pdf_browser_profile,'Profile 2');
  assert.equal(settings.openalex_api_key,'');
  await page.locator('.nav-item[data-page="library"]').click();
  await page.locator('[data-id="a"]').first().click();
  // 2.7.51：「PDF 获取记录与来源」折叠块已移除，详情面板里不应再出现它
  assert.equal(await page.locator('#detail-fetch-report').count(), 0);
  console.log('PASS PDF source settings and secret masking; acquisition report removed in 2.7.51');
  assert.deepEqual(errors, []);
  console.log('PASS profile save, category drafts and clear; no browser runtime errors');
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
