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
  const errors=[];
  page.on('pageerror',error => errors.push(error.message));
  await page.addInitScript(() => localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);
  await page.waitForFunction(()=>window.PW?.openPaperDetail && document.querySelectorAll('.paper-row-item').length);
  await page.evaluate(()=>{window.pywebview={api:{window_minimize(){window.nativeMinimized=true},window_toggle_maximize(){window.nativeMaximized=true},window_close(){window.nativeClosed=true},get_version(){return 'test'}}};window.dispatchEvent(new Event('pywebviewready'));});
  await page.evaluate(()=>window.PW.openPaperDetail('a'));
  await page.waitForFunction(()=>document.querySelector('.library-split').classList.contains('detail-open'));
  await page.evaluate(()=>window.PW.openPaper('a'));
  await page.waitForFunction(()=>window.PW.readerState?.doc);
  assert.equal(await page.locator('#app-titlebar').evaluate(el=>el.inert),false,'native titlebar stays interactive while reader is open');
  assert.equal(await page.locator('.library-split').evaluate(el=>el.classList.contains('detail-open')),false,'library detail drawer closes on reader entry');
  assert.equal(await page.locator('#paper-detail').evaluate(el=>getComputedStyle(el).visibility),'hidden');
  await page.evaluate(()=>window.PW.openPaperDetail('a'));
  assert.equal(await page.locator('.library-split').evaluate(el=>el.classList.contains('detail-open')),false,'late detail requests cannot reopen above the reader');
  await page.locator('#reader-next').click();
  await page.waitForFunction(()=>window.PW.readerState.currentPage===2);
  await page.locator('#reader-sidebar-toggle').click();await page.locator('#reader-sidebar-toggle').click();
  for (const button of await page.locator('.tb-menu-btn').all()) {
    await button.click();assert.equal(await button.evaluate(el=>el.parentElement.classList.contains('open')),true);
    await button.click();
  }
  await page.locator('.tb-menu-btn').filter({hasText:'帮助'}).click();
  await page.locator('.tb-menu-item').filter({hasText:'使用说明'}).click();
  await page.locator('[data-guide-act=close]').click();
  assert.equal(await page.locator('.tb-resize[inert]').count(),0);
  await page.locator('#tb-min').click();assert.equal(await page.evaluate(()=>window.nativeMinimized),true);
  await page.locator('#tb-max').click();assert.equal(await page.evaluate(()=>window.nativeMaximized),true);
  await page.locator('#tb-close').click();assert.equal(await page.evaluate(()=>window.nativeClosed),true);
  await page.locator('#reader-close').click();await page.waitForFunction(()=>document.getElementById('reader').classList.contains('hidden'));
  assert.equal(await page.locator('.main').evaluate(el=>el.inert),false);
  await page.locator('#btn-detail-toggle').click();
  assert.equal(await page.locator('.library-split').evaluate(el=>el.classList.contains('detail-open')),true,'details can reopen after closing reader');
  await page.locator('#btn-detail-toggle').click();
  await page.evaluate(()=>window.PW.openPaper('a'));await page.waitForFunction(()=>window.PW.readerState?.doc);
  await page.locator('.tb-menu-btn').filter({hasText:'查看'}).click();
  await page.locator('.tb-menu-item').filter({hasText:/^设置$/}).click();
  await page.waitForFunction(()=>document.getElementById('reader').classList.contains('hidden'));
  assert.equal(await page.locator('.page[data-page=settings]').evaluate(el=>el.classList.contains('active')),true,'native menu navigation leaves the reader');
  assert.deepEqual(errors,[]);
  console.log('Reader overlay interaction regression passed');
} finally {
  await browser?.close();
  if(server.exitCode===null){const exited=new Promise(resolve=>server.once('exit',resolve));server.kill();await exited;}
  const absolute=resolve(temp);
  if(!absolute.startsWith(resolve(tmpdir())+sep)||!absolute.split(sep).at(-1).startsWith('pw-ai-ui-'))throw new Error('Unexpected test data path');
  rmSync(absolute,{recursive:true,force:true});
}
