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
  const health=await(await fetch(base+'/api/health')).json();
  assert.equal(health.version,'2.10.1');assert.equal(health.product_name,'Nexus Paper Library');assert.equal(health.app,'PaperWhale','legacy discovery remains compatible');
  browser=await chromium.launch({executablePath:process.env.PAPERWHALE_EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1500,height:1000}}),errors=[],missing=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('response',response=>{if(response.url().includes('/brand/')&&!response.ok())missing.push(response.url());});
  await page.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);await page.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length>0);
  assert.equal(await page.title(),'Nexus Paper Library');
  assert.equal(await page.locator('.brand-text strong').textContent(),'NPL');
  const output=join(root,'pw-debug/npl-2.10.1');mkdirSync(output,{recursive:true});
  const checks=[];
  for(const name of ['library','methods','cards','chat','discover','import','settings']) {
    await page.locator(`.nav-item[data-page="${name}"]`).click();
    const text=await page.locator('body').innerText();assert.doesNotMatch(text,/鲸|🐳|🐋/u,name);
    await page.waitForFunction(()=>[...document.images].filter(image=>image.getClientRects().length&&image.src.includes('/brand/')).every(image=>image.complete&&image.naturalWidth>0));
    checks.push(name);
    if(['library','chat','discover','settings'].includes(name))await page.screenshot({animations:'disabled',path:join(output,name+'.png')});
  }
  await page.evaluate(()=>window.PW.openPaper('a'));await page.waitForFunction(()=>window.PW.readerState?.doc);
  await page.locator('#reader-tabs [data-tab="chat"]').click();
  assert.doesNotMatch(await page.locator('#reader').innerText(),/鲸|🐳|🐋/u);
  await page.screenshot({animations:'disabled',path:join(output,'reader.png')});
  assert.deepEqual(missing,[]);assert.deepEqual(errors,[]);
  const result={health,pages:checks,missingBrandResources:missing,pageErrors:errors};writeFileSync(join(output,'branding-ui.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
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
