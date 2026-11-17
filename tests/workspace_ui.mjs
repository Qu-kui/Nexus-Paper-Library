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
  const errors=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.addInitScript(()=>{localStorage.setItem('pw-setup-dismissed','1');localStorage.setItem('pw-title-lines','1');localStorage.setItem('pw-list-density','comfortable');localStorage.setItem('pw-visible-cols','[true,false,false,false,false]');});
  await page.goto(base);
  await page.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length>0 && window.PW?.openPaper);
  assert.equal(await page.locator('#title-lines,#list-density,.column-options').count(),0,'Display settings are removed');
  assert.equal(await page.locator('.pr-venue').first().evaluate(node=>getComputedStyle(node).visibility),'visible','Legacy hidden-column choices no longer hide columns');
  const rowSize=await page.locator('.paper-row-item').first().evaluate(row=>({height:row.getBoundingClientRect().height,line:parseFloat(getComputedStyle(row.querySelector('.pr-title-text')).lineHeight)}));
  assert.ok(rowSize.height>=rowSize.line*2&&rowSize.height<=rowSize.line*2+12,'Default row fits two text lines with minimal padding');
  assert.equal(await page.locator('.pr-title-text').first().evaluate(node=>getComputedStyle(node).webkitLineClamp),'2');
  const initialTitleWidth=await page.locator('.plh-cell').first().evaluate(node=>node.getBoundingClientRect().width);
  async function dragTitleTo(width){
    const current=await page.locator('.plh-cell').first().evaluate(node=>node.getBoundingClientRect().width);
    const handle=await page.locator('.col-resizer[data-col="0"]').boundingBox();
    await page.mouse.move(handle.x+handle.width/2,handle.y+handle.height/2);await page.mouse.down();
    await page.mouse.move(handle.x+handle.width/2+width-current,handle.y+handle.height/2);await page.mouse.up();
  }
  await dragTitleTo(100);
  assert.ok(Math.abs(await page.locator('.pr-title').first().evaluate(node=>node.getBoundingClientRect().width)-100)<2,'Title can shrink without a flexible-column width floor');
  await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length>0);
  assert.ok(Math.abs(await page.locator('.pr-title').first().evaluate(node=>node.getBoundingClientRect().width)-100)<2,'Manual width survives reload');
  await page.setViewportSize({width:1800,height:1000});await dragTitleTo(1300);
  await page.setViewportSize({width:1200,height:1000});
  assert.ok(Math.abs(await page.locator('.pr-title').first().evaluate(node=>node.getBoundingClientRect().width)-1300)<2,'Manual width is not capped by the window');
  assert.ok(await page.locator('.library-main').evaluate(node=>node.scrollWidth>node.clientWidth),'Wide columns scroll horizontally');
  await page.setViewportSize({width:1500,height:1000});
  await page.evaluate(()=>localStorage.removeItem('pw.compactCols'));await page.reload();await page.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length>0);
  assert.ok(Math.abs(await page.locator('.pr-title').first().evaluate(node=>node.getBoundingClientRect().width)-initialTitleWidth)<2,'Reset restores automatic remaining width');
  assert.equal(await page.locator('.col-resizer').count(),4,'Title, venue, author and year columns remain freely resizable');
  assert.equal(await page.locator('.plh-cell').last().evaluate(node=>node.getBoundingClientRect().width),80,'The status indicator column retains its existing fixed width');
  const titleLayout=await page.locator('.pr-title-text').first().evaluate(node=>{
    const original=node.textContent;const results=[];
    for(const text of ['长标题自然换行测试'.repeat(80),'UnbrokenLongEnglishTitle'.repeat(120)]){
      node.textContent=text;const style=getComputedStyle(node),box=node.getBoundingClientRect(),range=document.createRange();range.selectNodeContents(node);
      const first=[...range.getClientRects()].find(rect=>rect.width>0);
      results.push({height:box.height,line:parseFloat(style.lineHeight),overflow:node.scrollHeight>node.clientHeight,firstLineFills:first.width>=box.width-16});
    }
    node.textContent=original;return results;
  });
  for(const result of titleLayout){assert.ok(Math.abs(result.height-result.line*2)<1);assert.ok(result.overflow,'Long title truncates only after two lines');assert.ok(result.firstLineFills,'The first line fills before wrapping');}
  const rowHeights=await page.locator('.paper-row-item').evaluateAll(nodes=>nodes.map(node=>node.getBoundingClientRect().height));
  assert.ok(Math.max(...rowHeights)-Math.min(...rowHeights)<1,'Short and long titles share the same two-line row height');
  await page.locator('#library-pagination').waitFor();
  const seen = new Set();
  do {
    for (const id of await page.locator('.paper-row-item').evaluateAll(nodes => nodes.map(n => n.dataset.id))) seen.add(id);
    if (await page.locator('#library-next').isDisabled()) break;
    const first = await page.locator('.paper-row-item').first().getAttribute('data-id');
    await page.locator('#library-next').click();
    await page.waitForFunction(id => document.querySelector('.paper-row-item')?.dataset.id !== id, first);
  } while (seen.size < 300);
  assert.equal(seen.size, 233, 'All synthetic papers can be browsed');
  const requests=[];
  page.on('request',r => { if(r.url().includes('/api/papers?')) requests.push(r.url()); });
  await page.locator('#filter-sort').selectOption('title');
  await page.waitForFunction(() => document.querySelector('.paper-row-item')?.dataset.id==='broken');
  assert.ok(requests.some(url => url.includes('sort=title') && url.includes('offset=0')));
  await page.locator('#library-search').fill('NO_MATCH');
  await page.locator('#library-search').press('Enter');
  await page.locator('#library-empty').waitFor({state:'visible'});
  await page.locator('#library-empty button').click();
  await page.waitForFunction(() => document.querySelectorAll('.paper-row-item').length>0);
  assert.equal(await page.locator('#library-search').inputValue(),'');
  await page.locator('.nav-item[data-page="settings"]').click();
  await page.locator('#set-model').fill('changed-unsaved');
  assert.match(await page.locator('[data-settings-group="model"] .save-state').textContent(),/未保存/);
  await page.evaluate(() => window.PW.refreshLlmPill());
  assert.equal(await page.locator('#set-model').inputValue(),'changed-unsaved');
  await page.locator('#btn-profile-ai').click();
  await page.locator('#profile-chat-modal').waitFor({state:'visible'});
  assert.equal(await page.locator('#profile-chat-modal').getAttribute('role'),'dialog');
  for(let i=0;i<20;i++) await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(() => !!document.activeElement.closest('#profile-chat-modal')));
  await page.keyboard.press('Escape');
  await page.locator('#profile-chat-modal').waitFor({state:'hidden'});
  assert.equal(await page.evaluate(() => document.activeElement.id),'btn-profile-ai');
  assert.equal(await page.locator('input:not([aria-label]),textarea:not([aria-label]),select:not([aria-label])').evaluateAll(nodes => nodes.filter(n=>!n.labels?.length && n.type!=='hidden').length),0);
  await page.locator('.nav-item[data-page="library"]').click();
  await page.locator('#library-search').fill('ALPHA');
  await page.locator('#library-search').press('Enter');
  await page.waitForFunction(()=>document.querySelector('.paper-row-item')?.dataset.id==='a');
  await page.locator('.paper-row-item[data-id="a"]').click();
  let releaseRetag;
  const heldRetag=new Promise(resolve=>releaseRetag=resolve);
  await page.route('**/api/ai/retag/a',async route=>{await heldRetag;const paper=await (await fetch(base+'/api/papers/a')).json();await route.fulfill({json:{paper,tags:['late result']}});});
  await page.locator('#detail-retag').click();
  await page.locator('#library-search').fill('BETA');
  await page.locator('#library-search').press('Enter');
  await page.waitForFunction(()=>document.querySelector('.paper-row-item')?.dataset.id==='b');
  await page.locator('.paper-row-item[data-id="b"]').click();
  releaseRetag();
  await page.waitForResponse('**/api/ai/retag/a');
  await page.waitForTimeout(100);
  assert.match(await page.locator('.detail-head h3').textContent(),/BETA/,'Late result cannot replace another paper');
  await page.locator('#btn-detail-toggle').click();
  await page.locator('#btn-clear-filters').click();
  await page.waitForFunction(()=>document.querySelectorAll('.paper-row-item').length===50);
  const smallTitleWidth=await page.locator('.pr-title').first().evaluate(n=>n.getBoundingClientRect().width);
  await page.setViewportSize({width:1800,height:1000});
  const largeTitleWidth=await page.locator('.pr-title').first().evaluate(n=>n.getBoundingClientRect().width);
  assert.ok(largeTitleWidth>smallTitleWidth+200,'Title uses added window space');
  assert.equal(await page.locator('.pr-title-text').first().evaluate(n=>getComputedStyle(n).webkitLineClamp),'2','Fixed two-line layout ignores old single-line preferences');
  assert.equal(await page.locator('.pr-venue').first().evaluate(n=>getComputedStyle(n).visibility),'visible');
  const compareEvent=page.evaluate(()=>new Promise(resolve=>window.addEventListener('pw:compare-papers',event=>resolve(event.detail),{once:true})));
  await page.locator('.compare-paper').nth(0).check();await page.locator('.compare-paper').nth(1).check();
  await page.locator('#btn-compare-papers').click();
  assert.equal((await compareEvent).paper_ids.length,2);
  await page.route('**/api/tasks/batches',route=>route.fulfill({json:{items:[{batch_id:'ui-test-batch',kind:'pdf',total:3,done:3,succeeded:2,failed:1,pending:0,active:0,errors:[{paper_id:'b',error:'test failure'}]}]}}));
  let retried=false;
  await page.route('**/api/tasks/batches/ui-test-batch/retry',route=>{retried=true;return route.fulfill({json:{queued:1,status:{batch_id:'ui-retry',kind:'pdf',total:1,pending:1,active:0,done:0,failed:0,succeeded:0}}});});
  await page.locator('#btn-task-center').click();
  await page.locator('[data-retry-batch="ui-test-batch"]').click();
  assert.ok(retried,'Retry targets only the selected batch');
  assert.match(await page.locator('#task-center-content').textContent(),/3 \/ 3/,'Finished batch remains visible');
  await page.locator('#task-center-close').click();
  await page.setViewportSize({width:1100,height:720});
  await page.locator('.nav-item[data-page="settings"]').click();
  let connection={state:'untested',model:'mock-model',checked_at:null,error:''};
  const modelPuts=[];
  await page.route('**/api/settings/connection-status',route=>route.fulfill({json:connection}));
  await page.route('**/api/settings/test-connection',route=>{connection={state:'success',model:'ui-model',checked_at:'2026-10-27',error:''};return route.fulfill({json:connection});});
  page.on('request',request=>{if(request.url()===base+'/api/settings'&&request.method()==='PUT')modelPuts.push(request.postDataJSON());});
  await page.locator('#set-profile').fill('unsaved reading profile');
  await page.locator('#set-model').fill('ui-model');
  await page.locator('#btn-test-llm').click();
  await page.waitForFunction(()=>document.getElementById('model-connection-status').textContent.includes('最近连接成功'));
  await page.waitForFunction(()=>document.getElementById('llm-pill-text').textContent.includes('最近连接成功'));
  assert.equal((await (await fetch(base+'/api/settings')).json()).model,'ui-model');
  assert.ok(modelPuts.length>0 && !('profile' in modelPuts.at(-1)) && !('pdf_dir' in modelPuts.at(-1)),'Connection test saves only model group');
  assert.equal(await page.locator('#set-profile').inputValue(),'unsaved reading profile');
  assert.match(await page.locator('[data-settings-group="reading"] .save-state').textContent(),/未保存/);
  await page.route('**/api/settings/test-connection',route=>{connection={state:'failed',model:'ui-model',checked_at:'2026-10-27',error:'simulated authentication failure'};return route.fulfill({json:connection});});
  await page.locator('#btn-test-llm').click();
  await page.waitForFunction(()=>document.getElementById('model-connection-status').textContent.includes('simulated authentication failure'));
  await page.waitForFunction(()=>document.getElementById('llm-pill-text').textContent.includes('最近连接失败'));
  await page.locator('[data-settings-group="reading"] .group-save').click();
  await page.waitForFunction(()=>document.querySelector('[data-settings-group="reading"] .save-state').textContent==='已保存');
  assert.equal((await (await fetch(base+'/api/settings')).json()).profile,'unsaved reading profile');
  assert.ok(await page.evaluate(()=>document.body.scrollWidth<=innerWidth),'No page width overflow at 1100px');
  let releaseSave, signalSaved;
  const saveGate=new Promise(resolve=>{releaseSave=resolve;});
  const savedOnServer=new Promise(resolve=>{signalSaved=resolve;});
  await page.route(base+'/api/settings',async route=>{
    if(route.request().method()!=='PUT'){await route.continue();return;}
    const response=await route.fetch();signalSaved();await saveGate;await route.fulfill({response});
  });
  await page.locator('#set-model').fill('submitted-model');
  await page.locator('[data-settings-group="model"] .group-save').click();
  await savedOnServer;
  await page.locator('#set-model').fill('edited-during-save');
  releaseSave();
  await page.waitForFunction(()=>!document.querySelector('[data-settings-group="model"] .group-save').disabled);
  assert.equal((await (await fetch(base+'/api/settings')).json()).model,'submitted-model');
  assert.equal(await page.locator('#set-model').inputValue(),'edited-during-save');
  assert.match(await page.locator('[data-settings-group="model"] .save-state').textContent(),/未保存/,'In-flight edits stay unsaved');
  await page.evaluate(async()=>{const settings=await import('/js/settings.js');await settings.refreshSettings();});
  assert.equal(await page.locator('#set-model').inputValue(),'edited-during-save','Refreshing cannot erase in-flight edits');
  await page.unroute(base+'/api/settings');
  await page.locator('[data-settings-group="model"] .group-save').click();
  await page.waitForFunction(()=>document.querySelector('[data-settings-group="model"] .save-state').textContent==='已保存');
  await page.evaluate(async()=>{const {getConversation}=await import('/js/conversation.js');await getConversation('library').post('/ai/chat',{question:'BEFORE_BACKUP',deep_thinking:false,search_web:false});});
  // Maintenance acts only on the newly created synthetic data directory.
  const downloadEvent=page.waitForEvent('download');
  await page.locator('#btn-backup-data').click();
  const backup=await downloadEvent;const backupPath=join(temp,'ui-backup.zip');await backup.saveAs(backupPath);
  await page.evaluate(async()=>{const {getConversation}=await import('/js/conversation.js');await getConversation('library').post('/ai/chat',{question:'AFTER_BACKUP',deep_thinking:false,search_web:false});});
  const removed=await fetch(base+'/api/papers/broken',{method:'DELETE'});assert.ok(removed.ok);
  await page.locator('#btn-load-trash').click();
  await page.locator('.trash-row').filter({hasText:'previously stored PDF'}).getByRole('button',{name:'恢复',exact:true}).click();
  await page.waitForFunction(async()=> (await fetch('/api/papers/broken')).ok);
  assert.equal((await (await fetch(base+'/api/papers?limit=1')).json()).total,233);
  await fetch(base+'/api/papers/broken',{method:'DELETE'});
  await Promise.all([page.waitForEvent('load'),page.locator('#restore-data-file').setInputFiles(backupPath)]);
  await page.waitForFunction(()=>Boolean(window.PW?.goto));
  assert.equal((await (await fetch(base+'/api/papers?limit=1')).json()).total,233,'Restoring metadata backup returns removed paper');
  const restoredHistory=await page.evaluate(async()=>{const {getConversation}=await import('/js/conversation.js');return (await getConversation('library').load()).messages;});
  assert.ok(restoredHistory.some(message=>message.content==='BEFORE_BACKUP'));
  assert.ok(!restoredHistory.some(message=>message.content==='AFTER_BACKUP'),'Restoring reloads cached conversation revisions and history');
  await page.evaluate(async()=>{const {getConversation}=await import('/js/conversation.js');await getConversation('library').post('/ai/chat',{question:'AFTER_RESTORE',deep_thinking:false,search_web:false});});
  assert.equal((await (await fetch(base+'/api/ai/conversations/library')).json()).messages.at(-2).content,'AFTER_RESTORE','Chat remains usable after restoring an older revision');
  await page.locator('.nav-item[data-page="settings"]').click();
  const output=join(root,'docs/ui-review-2026-10-27/implementation');mkdirSync(output,{recursive:true});
  await page.screenshot({path:join(output,'settings-1100.png')});
  await page.locator('.nav-item[data-page="library"]').click();
  await page.setViewportSize({width:1500,height:1000});
  await page.screenshot({path:join(output,'library-1500.png')});
  assert.deepEqual(errors,[]);
  console.log('Workspace UI: 233-paper pagination, global sort, clear filters, dirty and in-flight settings edits, dialog focus, late detail isolation, responsive columns, compare selection, batch retry, connection status, group saves, trash, backup/restore and restored chat revisions passed');

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
