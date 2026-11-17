/* Real tour interactions in an isolated browser and temporary data directory. */
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
const temp=mkdtempSync(join(tmpdir(),'npl-tour-ui-'));
const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`;
const server=spawn(join(root,'.venv/Scripts/python.exe'),[join(root,'tests/ai_ui_server.py'),String(port)],{cwd:root,windowsHide:true,env:{...process.env,PAPERWHALE_UI_TEST:'1',PAPERWHALE_DATA:temp,PAPERWHALE_LLM_MOCK:'1'},stdio:['ignore','pipe','pipe']});
let logs='',browser;server.stdout.on('data',v=>logs+=v);server.stderr.on('data',v=>logs+=v);
const output=join(root,'pw-debug/onboarding');mkdirSync(output,{recursive:true});
try {
  let ready=false;for(let i=0;i<100;i++){try{ready=(await fetch(base+'/api/health')).ok;}catch{}if(ready)break;await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:900}}),errors=[],mutations=[],checks=[];
  page.on('pageerror',e=>errors.push(e.message));page.on('request',r=>{if(['POST','PUT','DELETE','PATCH'].includes(r.method()) && !/\/api\/settings\/validate/.test(r.url()))mutations.push(r.url());});
  await page.goto(base+'/?startup='+'a'.repeat(32));
  await page.waitForSelector('#npl-startup');assert.equal(await page.locator('#npl-tour').count(),0);
  await page.waitForSelector('#npl-tour');assert.equal(await page.locator('#npl-startup').count(),0);checks.push('first-use tour waits for startup');
  await page.screenshot({animations:'disabled',path:join(output,'welcome.png')});
  async function geometry(p=page) {
    const result=await p.evaluate(()=>{
      const panel=document.querySelector('.tour-panel').getBoundingClientRect(),spot=document.querySelector('.tour-spot').getBoundingClientRect();
      return {panel:{x:panel.x,y:panel.y,right:panel.right,bottom:panel.bottom},spot:{width:spot.width,height:spot.height},width:innerWidth,height:innerHeight};
    });
    assert.ok(result.panel.x>=0 && result.panel.y>=0 && result.panel.right<=result.width+1 && result.panel.bottom<=result.height+1,JSON.stringify(result));
    assert.ok(result.spot.width>0 && result.spot.height>0,JSON.stringify(result));
  }
  const expected=['welcome','model','profile','files','links','library','reading','discover','chat','methods','cards','plugins','finish'];
  for(const [index,id] of expected.entries()) {
    await page.waitForFunction(id=>document.querySelector('#npl-tour')?.dataset.step===id,id);
    await geometry();
    if(id==='model') {
      assert.equal(await page.locator('.page.active').getAttribute('data-page'),'settings');
      await page.getByRole('button',{name:'上一步',exact:true}).click();
      assert.equal(await page.locator('.page.active').getAttribute('data-page'),'library');
      await page.getByRole('button',{name:'下一步 →',exact:true}).click();
      await page.screenshot({animations:'disabled',path:join(output,'model.png')});
    }
    if(id==='profile') {
      await page.setViewportSize({width:1040,height:700});await page.waitForTimeout(80);await geometry();
      assert.ok(await page.locator('.settings-content').evaluate(el=>el.scrollTop)>0,'settings form scrolls to profile');
      await page.keyboard.press('Tab');assert.ok(await page.locator('#npl-tour').evaluate(el=>el.contains(document.activeElement)));
      await page.screenshot({animations:'disabled',path:join(output,'profile-1040.png')});
      await page.setViewportSize({width:1440,height:900});
    }
    if(id==='links')await page.screenshot({animations:'disabled',path:join(output,'import-links.png')});
    assert.match(await page.locator('.tour-count').textContent(),new RegExp(`${index+1} / 13`));
    await page.locator('[data-tour="next"]').click();
  }
  assert.equal(await page.locator('#npl-tour').count(),0);assert.equal(await page.evaluate(()=>localStorage.getItem('npl.onboarding.v1')),'completed');
  assert.deepEqual(mutations.filter(url=>!url.endsWith('/api/discover/sessions')),[],'tour never mutates settings, imports files or starts AI; ordinary discovery initialization may create an empty topic');checks.push('13 real-page steps, back, scroll, geometry, focus, no submissions');
  await page.evaluate(()=>window.PW.openPaper('a'));await page.waitForSelector('#npl-tour[data-step="reader-tools"]');
  for(const step of ['reader-tools','reader-annotation','reader-ai','reader-done']) {
    assert.equal(await page.locator('#npl-tour').getAttribute('data-step'),step);await geometry();
    if(step==='reader-ai')await page.screenshot({animations:'disabled',path:join(output,'reader.png')});
    await page.locator('[data-tour="next"]').click();
  }
  assert.equal(await page.locator('#npl-tour').count(),0);assert.equal(await page.locator('#reader').evaluate(el=>el.classList.contains('hidden')),false);
  await page.locator('#reader-next').click();await page.waitForFunction(()=>window.PW.readerState.currentPage===2);
  await page.locator('#btn-reader-tour').click();await page.keyboard.press('Escape');assert.equal(await page.locator('#npl-tour').count(),0);
  assert.equal(await page.locator('#reader').evaluate(el=>el.classList.contains('hidden')),false,'Esc dismisses tour before reader');
  await page.locator('#reader-close').click();checks.push('first reader tour, replay, Esc isolation, input restored');
  await page.reload();await page.waitForFunction(()=>window.PW?.goto);await page.waitForTimeout(550);assert.equal(await page.locator('#npl-tour').count(),0);
  await page.locator('.nav-item[data-page="settings"]').click();await page.locator('#btn-onboarding').click();await page.locator('[data-tour="skip"]').click();
  await page.locator('.nav-item[data-page="import"]').click();checks.push('completion persists; settings replay and skip release input');
  await page.evaluate(()=>{window.pywebview={api:{window_close(){window.nativeClosed=true}}};window.dispatchEvent(new Event('pywebviewready'));});
  await page.getByRole('button',{name:'帮助',exact:true}).click();await page.getByRole('button',{name:'新手指引',exact:true}).click();await page.waitForSelector('#npl-tour');
  await page.locator('#tb-close').click();assert.equal(await page.evaluate(()=>window.nativeClosed),true);await geometry();
  await page.getByRole('button',{name:'查看',exact:true}).click();await page.locator('.tb-menu-item').filter({hasText:'发现论文'}).click();
  assert.equal(await page.locator('#npl-tour').count(),0);checks.push('help replay, native close and external navigation remain usable');
  const empty=await browser.newPage({viewport:{width:1040,height:700},reducedMotion:'reduce'});
  await empty.route('**/api/papers?*',r=>r.fulfill({json:{items:[],total:0}}));
  await empty.goto(base);await empty.waitForSelector('#npl-tour');
  for(let i=0;i<6;i++)await empty.locator('[data-tour="next"]').click();
  assert.equal(await empty.locator('#npl-tour').getAttribute('data-step'),'reading');await geometry(empty);
  assert.equal(await empty.evaluate(()=>document.querySelector('.tour-panel').getAnimations().filter(a=>a.constructor.name==='Animation').length),0,'no scripted entrance animation with reduced motion');
  await empty.keyboard.press('Escape');await empty.reload();await empty.waitForFunction(()=>window.PW?.goto);await empty.waitForTimeout(550);
  assert.equal(await empty.locator('#npl-tour').count(),0);checks.push('empty library, reduced motion and skip persistence');
  const legacy=await browser.newPage();await legacy.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));await legacy.goto(base);await legacy.waitForFunction(()=>window.PW?.goto);await legacy.waitForTimeout(550);
  assert.equal(await legacy.locator('#npl-tour').count(),0);checks.push('existing setup dismissal respected');
  const noStyle=await browser.newPage();await noStyle.route('**/css/onboarding.css',r=>r.abort());await noStyle.goto(base);await noStyle.waitForFunction(()=>window.PW?.goto);await noStyle.waitForTimeout(650);
  assert.equal(await noStyle.locator('#npl-tour').count(),0);await noStyle.locator('.nav-item[data-page="import"]').click();
  checks.push('missing optional tour stylesheet leaves workspace usable');
  assert.deepEqual(errors,[]);writeFileSync(join(output,'checks.json'),JSON.stringify({checks,errors},null,2));console.log(JSON.stringify({checks,errors},null,2));
} finally {
  await browser?.close();
  if(server.exitCode===null){const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;}
  const absolute=resolve(temp);if(!absolute.startsWith(resolve(tmpdir())+sep)||!absolute.split(sep).at(-1).startsWith('npl-tour-ui-'))throw Error('Unexpected temporary data directory');
  rmSync(absolute,{recursive:true,force:true});
}
