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
  let ready=false;for(let i=0;i<100;i++){try{ready=(await fetch(base+'/api/health')).ok;}catch{}if(ready)break;await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[];
  const counts={tags:0,categories:0};
  page.on('request',request=>{const path=new URL(request.url()).pathname;if(path==='/api/tags')counts.tags++;if(path==='/api/categories/tree')counts.categories++;});
  page.on('pageerror',e=>errors.push(e.message));await page.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);await page.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length>0);
  const initial={...counts};
  await page.evaluate(async()=>{await(await import('/js/library.js')).refreshLibrary();});
  assert.deepEqual(counts,initial,'plain refresh reuses metadata');
  await page.locator('#library-next').click();await page.waitForFunction(()=>document.getElementById('library-page-label')?.textContent.startsWith('第 2 /'));
  assert.deepEqual(counts,initial,'pagination reuses metadata');
  await page.evaluate(async()=>{document.dispatchEvent(new CustomEvent('pw:library-changed'));await(await import('/js/library.js')).refreshLibrary();});
  assert.equal(counts.tags,initial.tags+1);assert.equal(counts.categories,initial.categories+1);
  await page.locator('#btn-refresh-library').click();await page.waitForFunction(()=>!document.getElementById('btn-refresh-library').disabled);
  assert.equal(counts.tags,initial.tags+2);assert.equal(counts.categories,initial.categories+2);
  const output=join(root,'docs/ui-review-2026-11-01/after');mkdirSync(output,{recursive:true});
  const capture=async name=>{await page.screenshot({path:join(output,name+'.png')});};
  await page.evaluate(async()=>{await window.PW.openPaperDetail('broken');});await page.locator('#detail-thumbs.is-empty').waitFor();
  assert.ok((await page.locator('#detail-thumbs').boundingBox()).height<150,'missing PDF does not reserve a page-height preview');await capture('detail');
  await page.locator('.nav-item[data-page="methods"]').click();await page.locator('.method-pending').first().waitFor();
  assert.ok((await page.locator('.method-pending').first().boundingBox()).height<110,'pending methods are compact');
  await page.locator('#methods-state').selectOption('pending');await capture('methods');
  await page.locator('.nav-item[data-page="discover"]').click();await page.locator('#scholar-sub').waitFor();
  await page.waitForFunction(()=>document.getElementById('scholar-sub').textContent.includes('连接邮箱'));
  assert.ok(await page.locator('#scholar-config').isHidden());
  await page.locator('#btn-scholar-config').click();assert.ok(await page.locator('#scholar-config').isVisible());
  await page.locator('#btn-scholar-config').click();assert.equal(await page.locator('#btn-scholar-config').getAttribute('aria-expanded'),'false');await capture('discover');
  await page.locator('.nav-item[data-page="settings"]').click();assert.equal(await page.locator('.settings-navigation button').count(),8);
  await page.locator('[data-settings-target="reading"]').click();await page.locator('#set-profile-name').fill('审查回归');
  assert.ok(await page.locator('[data-settings-target="reading"]').evaluate(el=>el.classList.contains('is-dirty')));
  await page.locator('[data-settings-target="storage"]').click();assert.equal(await page.locator('#set-profile-name').inputValue(),'审查回归');
  await page.locator('[data-settings-target="model"]').click();await capture('settings');
  const contrastResults=await page.evaluate(async()=>{
    const {THEME_PRESETS,applyAppearance}=await import('/js/theme.js');
    const L=css=>css.match(/[\d.]+/g).slice(0,3).map(Number).map(v=>v/255).map(v=>v<=.04045?v/12.92:((v+.055)/1.055)**2.4).reduce((s,v,i)=>s+v*[.2126,.7152,.0722][i],0);
    return THEME_PRESETS.map(t=>{applyAppearance({theme:t.key},{persist:false});const box=document.getElementById('clipboard-prompt');const a=L(getComputedStyle(box.querySelector('#cp-value')).color),b=L(getComputedStyle(box).backgroundColor);return {theme:t.key,contrast:(Math.max(a,b)+.05)/(Math.min(a,b)+.05)};});
  });
  contrastResults.forEach(result=>assert.ok(result.contrast>=4.5,JSON.stringify(result)));
  await page.evaluate(async()=>{(await import('/js/theme.js')).applyAppearance({theme:'midnight'},{persist:false});});
  assert.deepEqual(errors,[]);
  const result={version:(await(await fetch(base+'/api/health')).json()).version,checks:['metadata reuse / invalidation / manual refresh','compact missing PDF preview','compact pending methods and state filter','collapsed scholar setup','settings group navigation and unsaved changes','clipboard contrast in all 8 themes'],metadataRequests:counts,contrastResults,errors};
  writeFileSync(join(output,'verification.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
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
