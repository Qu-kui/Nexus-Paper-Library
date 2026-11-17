/* End-to-end: isolated server + headless Edge, no user browser profile or remote LLM. */
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, mkdirSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { resolve, dirname, join, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';
import net from 'node:net';

const root = 'D:/Harness APP/PaperWhale';
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
  let ready=false;for(let i=0;i<100;i++){try{ready=(await fetch(base+'/api/health')).ok;}catch{}if(ready)break;await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);await page.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length>0);
  const output=join(root,'docs/ui-review-2026-11-01'),observations={fixture:true,version:(await(await fetch(base+'/api/health')).json()).version};
  const capture=async name=>{await page.waitForTimeout(350);await page.screenshot({path:join(output,name+'.png')});};
  await capture('01-library');await page.locator('.paper-row-item').first().click();await capture('02-detail');
  for(const name of ['methods','cards','chat','discover','import','settings']){await page.locator(`.nav-item[data-page="${name}"]`).click();await capture('page-'+name);}
  observations.settings=await page.evaluate(()=>({cards:document.querySelectorAll('[data-page="settings"] .card').length,mainHeight:document.querySelector('.main').clientHeight,mainScrollHeight:document.querySelector('.main').scrollHeight}));
  await page.evaluate(()=>window.PW.openPaper('a'));await page.waitForFunction(()=>window.PW.readerState?.doc);await capture('03-reader');
  await page.setViewportSize({width:1100,height:720});await capture('04-reader-small');
  observations.reader=await page.evaluate(()=>({bodyWidth:document.body.scrollWidth,viewport:innerWidth,pdfWidth:document.getElementById('pdf-scroll')?.clientWidth,sideWidth:document.getElementById('reader-side')?.clientWidth}));
  await page.locator('#reader-tabs .tab').first().focus();
  const beforeTab=await page.locator('#reader-tabs .tab.active').getAttribute('data-tab');
  await page.keyboard.press('ArrowRight');
  observations.readerKeyboard={before:beforeTab,after:await page.locator('#reader-tabs .tab.active').getAttribute('data-tab'),labels:await page.locator('#reader-tabs .tab').allTextContents()};
  observations.clipboard=await page.locator('#clipboard-prompt').evaluate(el=>({background:getComputedStyle(el).backgroundColor,title:getComputedStyle(el.querySelector('#cp-value')).color,hint:getComputedStyle(el.querySelector('#cp-hint')).color}));
  observations.errors=errors;writeFileSync(join(output,'observations.json'),JSON.stringify(observations,null,2));console.log(JSON.stringify(observations));
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
