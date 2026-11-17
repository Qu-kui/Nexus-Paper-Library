/* Real launch/reload/reduced-motion tests with a temporary library and isolated browser. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,rmSync,mkdirSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve,dirname,join,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import net from 'node:net';

const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const deps=process.env.PAPERWHALE_NODE_MODULES || 'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const {chromium}=createRequire(join(deps,'package.json'))('playwright');
const temp=mkdtempSync(join(tmpdir(),'npl-startup-ui-'));
const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`;
const output=join(root,'pw-debug/npl-startup-orbit');mkdirSync(output,{recursive:true});
const server=spawn(join(root,'.venv/Scripts/python.exe'),[join(root,'tests/ai_ui_server.py'),String(port)],{
  cwd:root,windowsHide:true,stdio:['ignore','pipe','pipe'],env:{...process.env,PAPERWHALE_UI_TEST:'1',PAPERWHALE_UI_AUDIT:'1',PAPERWHALE_DATA:temp,PAPERWHALE_LLM_MOCK:'1'},
});
let logs='',browser;server.stdout.on('data',x=>logs+=x);server.stderr.on('data',x=>logs+=x);
const nonce=n=>String(n).padStart(32,'0');
const checks=[],errors=[];
try {
  let ready=false;for(let i=0;i<100;i++){try{ready=(await fetch(base+'/api/health')).ok;}catch{}if(ready)break;await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({headless:true,executablePath:process.env.PAPERWHALE_EDGE || 'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe'});
  const page=await browser.newPage({viewport:{width:1440,height:900}});
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);assert.equal(await page.locator('#npl-startup').count(),0);checks.push('direct URL skips');
  await page.goto(base+'/?startup='+nonce(1)+'&view=library#library');
  await page.waitForSelector('#npl-startup');
  assert.equal(new URL(page.url()).searchParams.has('startup'),false);
  assert.equal(new URL(page.url()).searchParams.get('view'),'library');
  assert.equal(new URL(page.url()).hash,'#library');
  assert.equal(await page.locator('.sidebar').evaluate(el=>getComputedStyle(el).visibility),'hidden');
  await page.waitForFunction(()=>!document.getElementById('npl-startup'));
  assert.equal(await page.locator('[inert]').count(),0);
  assert.equal(await page.locator('.sidebar').evaluate(el=>getComputedStyle(el).visibility),'visible');
  await page.locator('.nav-item[data-page="discover"]').click();
  assert.equal(await page.locator('#npl-startup').count(),0);checks.push('fresh launch completes and navigation works');
  await page.reload();assert.equal(await page.locator('#npl-startup').count(),0);checks.push('refresh skips');
  await page.goto(base+'/?startup='+nonce(1));assert.equal(await page.locator('#npl-startup').count(),0);checks.push('same launch token skips');
  await page.goto(base+'/?startup='+nonce(2));await page.waitForSelector('#npl-startup');
  await page.keyboard.press('Escape');assert.equal(await page.locator('#npl-startup').count(),0);assert.equal(await page.locator('[inert]').count(),0);checks.push('new token replays; Esc releases input');
  await page.goto(base+'/?startup='+nonce(3));await page.getByRole('button',{name:'跳过开屏动画'}).click();assert.equal(await page.locator('#npl-startup').count(),0);checks.push('skip button works');
  await page.emulateMedia({reducedMotion:'reduce'});await page.goto(base+'/?startup='+nonce(4));assert.equal(await page.locator('#npl-startup').count(),0);checks.push('reduced motion skips');
  await page.emulateMedia({reducedMotion:'no-preference'});await page.goto(base+'/?startup='+nonce(5));await page.waitForSelector('#npl-startup');
  await page.emulateMedia({reducedMotion:'reduce'});await page.waitForFunction(()=>!document.getElementById('npl-startup'));assert.equal(await page.locator('[inert]').count(),0);checks.push('motion preference change cleans up');
  await page.emulateMedia({reducedMotion:'no-preference'});
  // Reader may take focus while the initial shell is still assembling.
  await page.goto(base+'/?startup='+nonce(6));await page.waitForSelector('#npl-startup');
  await page.waitForFunction(()=>window.PW?.openPaper);await page.evaluate(()=>window.PW.openPaper('a'));await page.waitForFunction(()=>window.PW.readerState?.doc);
  assert.equal(await page.locator('#npl-startup').count(),0);await page.locator('#reader-close').click();assert.equal(await page.locator('.main').evaluate(el=>el.inert),false);checks.push('early reader open releases intro ownership');

  // Authoring preview: a seekable pure timeline captures the same actual app, including reveal.
  const preview=await browser.newPage({viewport:{width:1440,height:900}});
  preview.on('pageerror',e=>errors.push(e.message));
  await preview.addInitScript(()=>{window.__NPL_STARTUP_PREVIEW__=true;localStorage.setItem('pw-setup-dismissed','1');});
  await preview.goto(base+'/?startup='+nonce(7));await preview.waitForFunction(()=>window.__NPL_STARTUP__?.seek && document.querySelectorAll('.paper-row-item').length>0);
  for (const t of [.15,.45,.70,.95,1.25,1.65,2.05,2.40,2.60,2.85,3.00]) {
    await preview.evaluate(t=>window.__NPL_STARTUP__.seek(t),t);
    await preview.screenshot({path:join(output,`frame-${t.toFixed(2)}.png`)});
  }
  await preview.evaluate(()=>window.__NPL_STARTUP__.seek(.45));
  assert.equal(await preview.locator('[data-orbit]').evaluateAll(els=>els.filter(el=>Number(getComputedStyle(el).opacity)>.1).length),3,'three visible orbit strands');
  assert.equal(await preview.locator('[data-part]').evaluateAll(els=>els.every(el=>Number(getComputedStyle(el).opacity)===0)),true,'light strands precede the solid mark');
  await preview.evaluate(()=>window.__NPL_STARTUP__.seek(2.05));
  assert.equal(await preview.locator('[data-orbit],[data-orbit-head],[data-outline]').evaluateAll(els=>els.every(el=>Number(getComputedStyle(el).opacity)===0)),true,'all light trails resolve before the logo hold');
  assert.equal(await preview.locator('[data-part]').evaluateAll(els=>els.every(el=>Number(getComputedStyle(el).opacity)===1)),true,'five approved solid logo parts remain');
  checks.push('three orbit strands draw and resolve into the unchanged logo');
  const settled=await preview.screenshot();
  await preview.evaluate(()=>{window.__NPL_STARTUP__.seek(.25);window.__NPL_STARTUP__.seek(2.05);});
  assert.deepEqual(await preview.screenshot(),settled,'same seek produces identical pixels');
  for(const width of [980,1920]) {
    await preview.setViewportSize({width,height:width===980?680:1080});
    await preview.evaluate(()=>window.__NPL_STARTUP__.seek(2.05));
    assert.equal(await preview.locator('.startup-fullname').evaluate(el=>{const b=el.getBoundingClientRect();return b.right<=innerWidth && b.left>=0;}),true);
    await preview.screenshot({path:join(output,`settled-${width}.png`)});
  }
  checks.push('deterministic timeline; 980–1920px framing');
  // Real-time playback recording, not a replacement for the native runtime animation.
  if(process.env.NPL_RECORD_STARTUP) {
    const recorded=await browser.newContext({viewport:{width:1440,height:900},recordVideo:{dir:output,size:{width:1440,height:900}}});
    const p=await recorded.newPage();await p.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
    await p.goto(base+'/?startup='+nonce(8));await p.waitForSelector('#npl-startup');await p.waitForFunction(()=>!document.getElementById('npl-startup'));await p.waitForTimeout(350);
    const video=p.video();await recorded.close();await video.saveAs(join(output,'NPL-startup-preview.webm'));
  }
  // Missing animation module must fail open rather than leave a blank window.
  for(const [i,resource] of ['js/startup.js','js/startup-motion.js','css/startup.css'].entries()) {
    const broken=await browser.newPage();await broken.route('**/'+resource,r=>r.abort());
    await broken.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
    await broken.goto(base+'/?startup='+nonce(9+i));await broken.waitForFunction(()=>!document.documentElement.dataset.nplStartup,{},{timeout:8000});
    assert.equal(await broken.locator('.sidebar').evaluate(el=>getComputedStyle(el).visibility),'visible');
    await broken.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length>0 && window.PW?.goto);
    await broken.locator('.nav-item[data-page="discover"]').click();
    assert.equal(await broken.locator('.page[data-page="discover"]').evaluate(el=>el.classList.contains('active')),true);
    await broken.close();
  }
  checks.push('missing module, motion data or CSS still boots a usable app');
  assert.deepEqual(errors,[]);
  const result={checks,pageErrors:errors};writeFileSync(join(output,'checks.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result,null,2));
} finally {
  await browser?.close();
  if(server.exitCode===null){const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;}
  const absolute=resolve(temp);
  if(!absolute.startsWith(resolve(tmpdir())+sep)||!absolute.split(sep).at(-1).startsWith('npl-startup-ui-'))throw Error('Unexpected temporary data directory');
  rmSync(absolute,{recursive:true,force:true});
}
