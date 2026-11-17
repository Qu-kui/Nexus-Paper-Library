import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,rmSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve,dirname,join,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import net from 'node:net';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const dependencies=process.env.PAPERWHALE_NODE_MODULES||'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules';
const require=createRequire(join(dependencies,'package.json'));
const {chromium}=require('playwright');
const temp=mkdtempSync(join(tmpdir(),'pw-ai-feedback-'));
const listener=net.createServer();
await new Promise(r=>listener.listen(0,'127.0.0.1',r));
const port=listener.address().port;
await new Promise(r=>listener.close(r));
const base=`http://127.0.0.1:${port}`;
let logs='',browser;
const server=spawn(join(root,'.venv/Scripts/python.exe'),[join(root,'tests/ai_feedback_ui_server.py'),String(port)],{cwd:root,windowsHide:true,env:{...process.env,PAPERWHALE_UI_TEST:'1',PAPERWHALE_DATA:temp,PAPERWHALE_LLM_MOCK:'1'},stdio:['ignore','pipe','pipe']});
server.stdout.on('data',b=>logs+=b);server.stderr.on('data',b=>logs+=b);
try{
  let ready=false;
  for(let i=0;i<100;i++){
    try{ready=(await fetch(base+'/api/health')).ok;}catch{}
    if(ready)break;
    if(server.exitCode!==null)throw Error(logs);
    await new Promise(r=>setTimeout(r,100));
  }
  assert.ok(ready,logs);
  browser=await chromium.launch({executablePath:process.env.PAPERWHALE_EDGE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1500,height:1000}});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  await page.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);
  await page.waitForFunction(()=>Boolean(window.PW?.goto));
  await page.evaluate(()=>window.PW.goto('chat'));
  await page.waitForFunction(()=>document.querySelectorAll('#kb-file-list .kb-file').length>=2);
  assert.doesNotMatch(await page.locator('#kb-file-list').innerText(),/加载失败/);
  await page.waitForFunction(()=>!document.getElementById('btn-chat-send').disabled);
  await page.locator('#chat-input').fill('PROGRESS_TEST');
  await page.locator('#btn-chat-send').click();
  await page.locator('#chat-messages .ai-analysis').waitFor();
  assert.match(await page.locator('#chat-messages .ai-analysis').innerText(),/模型分析摘要/);
  await page.locator('#chat-messages [data-ai-action="stop"]').click();
  await page.waitForFunction(async()=>{const {getConversation}=await import('/js/conversation.js');return !getConversation('library').busy;});
  await page.locator('#chat-messages [data-ai-action="retry"]').waitFor();
  await new Promise(r=>setTimeout(r,1500));
  let snapshot=await (await fetch(base+'/api/ai/conversations/library')).json();
  assert.equal(snapshot.messages.length,0,'stopped answer cannot be saved');
  await page.locator('#chat-messages [data-ai-action="retry"]').click();
  await page.waitForFunction(async()=>{const {getConversation}=await import('/js/conversation.js');const s=getConversation('library');return !s.busy&&s.data.messages.length===2;});
  await page.locator('#chat-messages .ai-evidence-title').waitFor();
  assert.match(await page.locator('#chat-messages').innerText(),/本轮实际引用/);
  assert.match(await page.locator('#chat-messages .ai-candidates summary').innerText(),/未被本轮编号引用/);
  const target=await page.locator('#chat-messages [data-answer-index] select').inputValue();
  await page.locator('#chat-messages [data-answer-save]').click();
  await page.waitForFunction(()=>document.querySelector('#chat-messages .ai-action-result')?.textContent==='已存入笔记');
  const paper=await(await fetch(base+'/api/papers/'+target)).json();
  assert.match(paper.notes,/基于本轮资料作答/);
  await page.locator('#btn-clear-chat').click();
  await page.locator('#chat-messages [data-ai-action="undo"]').click();
  await page.waitForFunction(async()=>{const {getConversation}=await import('/js/conversation.js');return getConversation('library').data.messages.length===2;});
  await page.evaluate(()=>window.dispatchEvent(new CustomEvent('pw:compare-papers',{detail:{paper_ids:['a','b'],titles:['ALPHA','BETA']}})));
  await page.locator('#chat-messages .ai-compare-scope').waitFor();
  const request=page.waitForRequest(r=>r.url().endsWith('/api/ai/chat')&&r.method()==='POST');
  await page.locator('#btn-chat-send').click();
  assert.deepEqual((await request).postDataJSON().paper_ids,['a','b']);
  await page.waitForFunction(async()=>{const {getConversation}=await import('/js/conversation.js');return !getConversation('library').busy;});
  // Exercise each real conversation renderer with successive model snapshots.
  // No model/network call is needed for these deterministic scroll positions.
  async function progressLayout(scope,selector) {
    await page.evaluate(async scope=>{
      const {getConversation}=await import('/js/conversation.js');const session=getConversation(scope);await session.load();
      ++session.epoch;++session.requestSerial;clearTimeout(session.pollTimer);
      session.pending='连续内容布局测试';session.busy=true;
      session.progress={stage:'核对来源',reasoning:'模型正在核对证据。\n'.repeat(100),answer:'回答增量。\n'.repeat(60)};session.notify();
    },scope);
    const progress=page.locator(`${selector} .ai-progress`);
    await progress.waitFor({state:'visible'});
    assert.ok(await progress.evaluate(p=>p.getBoundingClientRect().height<=240.5),`${scope}: combined reasoning and answer have a compact height`);
    assert.ok(await progress.evaluate(p=>p.scrollHeight-p.clientHeight-p.scrollTop<25),`${scope}: initial content follows bottom`);
    await progress.evaluate(p=>p.scrollTop=20);
    await page.evaluate(async scope=>{const {getConversation}=await import('/js/conversation.js');const s=getConversation(scope);s.progress.reasoning+='新增证据。\n'.repeat(50);s.notify();},scope);
    assert.ok(await progress.evaluate(p=>Math.abs(p.scrollTop-20)<3),`${scope}: manual reading position survives render`);
    await progress.evaluate(p=>p.scrollTop=0);
    await page.locator(`${selector} .ai-analysis summary`).click();
    await page.evaluate(async scope=>{const {getConversation}=await import('/js/conversation.js');const s=getConversation(scope);s.progress.reasoning+='下一段分析';s.notify();},scope);
    assert.equal(await page.locator(`${selector} .ai-analysis`).evaluate(p=>p.open),false,`${scope}: collapsed analysis stays collapsed`);
    await progress.evaluate(p=>{p.querySelector('details').open=true;p.scrollTop=p.scrollHeight;});
    await page.evaluate(async scope=>{const {getConversation}=await import('/js/conversation.js');const s=getConversation(scope);s.progress.answer+='回答新增内容。\n'.repeat(30);s.notify();},scope);
    assert.ok(await progress.evaluate(p=>p.scrollHeight-p.clientHeight-p.scrollTop<25),`${scope}: follow resumes at bottom`);
    await page.evaluate(async scope=>{const {getConversation}=await import('/js/conversation.js');const s=getConversation(scope);s.pending=null;s.busy=false;s.progress=null;s.notify();},scope);
  }
  await progressLayout('library','#chat-messages');
  await page.evaluate(()=>window.PW.goto('library'));
  await page.locator('#btn-category-assistant').click();
  await progressLayout('categories','#category-chat-messages');
  await page.locator('#category-assistant-close').click();
  await page.evaluate(()=>window.PW.goto('settings'));
  await page.locator('#btn-profile-ai').click();
  await progressLayout('profile','#profile-chat-messages');
  await page.locator('#profile-chat-close').click();
  assert.deepEqual(errors,[]);
  console.log('PASS model progress, stop/retry, evidence/candidates, save note, undo and explicit comparison; compact streaming analysis across library/category/profile, preserved scroll/collapse; no browser errors');
}finally{
  if(browser)await browser.close();
  if(server.exitCode===null){server.kill();await new Promise(r=>server.once('exit',r));}
  const safe=resolve(temp);assert.ok(safe.startsWith(resolve(tmpdir())+sep));rmSync(safe,{recursive:true,force:true});
}
