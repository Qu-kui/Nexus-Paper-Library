/* Settings chrome must stay outside the form's scroll viewport. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,rmSync,mkdirSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve,dirname,join,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import net from 'node:net';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const dependencies=process.env.PAPERWHALE_NODE_MODULES || 'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const {chromium}=createRequire(join(dependencies,'package.json'))('playwright');
const temp=mkdtempSync(join(tmpdir(),'npl-settings-nav-'));
const socket=net.createServer();await new Promise(r=>socket.listen(0,'127.0.0.1',r));const port=socket.address().port;await new Promise(r=>socket.close(r));
const base=`http://127.0.0.1:${port}`;
const server=spawn(join(root,'.venv/Scripts/python.exe'),[join(root,'tests/ai_ui_server.py'),String(port)],{cwd:root,windowsHide:true,env:{...process.env,PAPERWHALE_UI_TEST:'1',PAPERWHALE_DATA:temp,PAPERWHALE_LLM_MOCK:'1'},stdio:['ignore','pipe','pipe']});
let browser,logs='';server.stdout.on('data',v=>logs+=v);server.stderr.on('data',v=>logs+=v);
const output=join(root,'pw-debug/settings-navigation');mkdirSync(output,{recursive:true});
try {
  let ready=false;for(let i=0;i<100;i++){try{ready=(await fetch(base+'/api/health')).ok;}catch{}if(ready)break;await new Promise(r=>setTimeout(r,100));}assert.ok(ready,logs);
  browser=await chromium.launch({executablePath:'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1440,height:900},reducedMotion:'reduce'}),errors=[],checks=[];
  page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);await page.waitForFunction(()=>window.PW?.goto);
  await page.evaluate(()=>{window.pywebview={api:{}};window.dispatchEvent(new Event('pywebviewready'));});
  await page.locator('.nav-item[data-page="settings"]').click();
  const form=page.locator('.settings-content');
  await form.waitFor({timeout:2500});
  for(const width of [1440,1040,800]) {
    await page.setViewportSize({width,height:700});
    await page.locator('[data-settings-target="model"]').click();
    await page.locator('#set-model').fill('kept-unsaved');
    for(const key of ['reading','pdf','appearance','clipboard','plugins','about','model']) {
      await page.locator(`[data-settings-target="${key}"]`).click();
      const geometry=await page.evaluate(key=>{
        const nav=document.querySelector('.settings-navigation').getBoundingClientRect(),content=document.querySelector('.settings-content').getBoundingClientRect(),target=document.querySelector(`#settings-group-${key} h3`).getBoundingClientRect(),header=document.querySelector('.page[data-page="settings"] .page-head').getBoundingClientRect();
        return {nav:nav.toJSON(),content:content.toJSON(),target:target.toJSON(),header:header.toJSON(),bodyWidth:document.body.scrollWidth,width:innerWidth};
      },key);
      assert.ok(geometry.nav.top>=36 && geometry.nav.top>=geometry.header.bottom-1,JSON.stringify(geometry));
      assert.ok(geometry.content.top>=geometry.nav.bottom && geometry.target.top>=geometry.content.top-1 && geometry.target.bottom<=geometry.content.bottom+1,JSON.stringify(geometry));
      assert.ok(geometry.bodyWidth<=geometry.width,JSON.stringify(geometry));
      assert.equal(await page.locator('#set-model').inputValue(),'kept-unsaved');
      assert.equal(await page.locator(`[data-settings-target="${key}"]`).getAttribute('aria-current'),'location');
    }
    await form.evaluate(el=>{el.scrollTop=el.scrollHeight/2;});
    const before=await page.locator('.settings-navigation').boundingBox();
    await page.mouse.move(700,500);await page.mouse.wheel(0,350);await page.waitForTimeout(100);
    const after=await page.locator('.settings-navigation').boundingBox();
    assert.ok(Math.abs(after.y-before.y)<1,'navigation stays in its own region while forms scroll');
    assert.equal(await page.locator('.page[data-page="settings"]').evaluate(el=>el.scrollTop),0,'outer page never scrolls the header away');
    await page.screenshot({animations:'disabled',path:join(output,`settings-${width}.png`)});
    checks.push(`${width}px: section jumps, no header overlap, scrolling, unsaved input preserved`);
  }
  await page.setViewportSize({width:1440,height:900});
  await page.waitForTimeout(80);
  const navStart=await page.locator('.settings-navigation').boundingBox();
  const headingStart=await page.locator('.page[data-page="settings"] .page-head').boundingBox();
  await form.evaluate(el=>{el.scrollTop=el.scrollHeight;});
  assert.ok(Math.abs((await page.locator('.settings-navigation').boundingBox()).y-navStart.y)<1);
  assert.ok(Math.abs((await page.locator('.page[data-page="settings"] .page-head').boundingBox()).y-headingStart.y)<1);
  assert.deepEqual(errors,[]);console.log(JSON.stringify({checks,errors},null,2));writeFileSync(join(output,'checks.json'),JSON.stringify({checks,errors},null,2));
} finally {
  await browser?.close();if(server.exitCode===null){const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;}
  const absolute=resolve(temp);if(!absolute.startsWith(resolve(tmpdir())+sep)||!absolute.split(sep).at(-1).startsWith('npl-settings-nav-'))throw Error('Unexpected temporary data directory');
  rmSync(absolute,{recursive:true,force:true});
}
