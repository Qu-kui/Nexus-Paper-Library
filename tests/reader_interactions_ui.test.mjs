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
  await page.waitForFunction(() => document.querySelectorAll('.paper-row-item').length>0 && window.PW?.openPaper);
  await page.locator('#library-search').focus();
  await page.evaluate(() => window.PW.openPaper('a'));
  await page.waitForFunction(() => window.PW.readerState.doc);
  assert.ok(await page.evaluate(() => document.querySelector('#reader').contains(document.activeElement)),'opening focuses reader');
  await page.locator('#reader-tabs [data-tab="summary"]').focus();
  await page.keyboard.press('ArrowRight');
  assert.equal(await page.locator('#reader-tabs .active').getAttribute('data-tab'),'chat','one arrow advances exactly one tab');
  await page.keyboard.press('End');
  assert.equal(await page.locator('#reader-tabs .active').getAttribute('data-tab'),'paper-notes');
  await page.keyboard.press('Home');
  assert.equal(await page.locator('#reader-tabs .active').getAttribute('data-tab'),'summary');
  await page.locator('#reader-tabs [data-tab="chat"]').click();
  await page.evaluate(() => {
    const session = window.PW.readerState.chatSession;
    session.data = {...session.data, messages: [
      {role:'user', content:'请解释布朗运动'},
      {role:'assistant', content:'行内 $t_{exp} \\ll \\tau_c$。\n\n$$\n\\Delta s_x = \\frac{1}{2}\\langle s_x\\rangle\n$$'},
    ]};
    session.notify();
  });
  assert.equal(await page.locator('#reader-chat-messages .katex').count(), 2);
  assert.equal(await page.locator('#reader-chat-messages .katex-error').count(), 0);
  await page.evaluate(() => document.fonts.ready);
  assert.ok(await page.evaluate(() => document.fonts.check('16px KaTeX_Main')), 'offline math font loads');
  await page.evaluate(() => {
    const session=window.PW.readerState.chatSession;
    session.data={...session.data,messages:Array.from({length:20},(_,i)=>({role:i%2?'assistant':'user',content:('message '+i+' ').repeat(90),reasoning:i%2?'saved reasoning':'',sources:i%2?[{paper_id:'a',page:1,has_pdf:true,title:'source'}]:[]}))};
    session.pending='stream question';session.busy=true;session.progress={answer:'$x^2$',reasoning:'thinking',stage:'answering'};session.notify();
    if (!document.querySelector('#reader-chat-messages .katex')) throw new Error('Streaming formula failed to render');
    window.oldBubble=document.querySelector('#reader-chat-messages .mini-msg');
    window.oldSource=document.querySelector('#reader-chat-messages .src-item');
    window.oldCopy=document.querySelector('#reader-chat-messages .reader-answer-copy');
    window.oldReasoning=document.querySelector('#reader-chat-messages .reader-reasoning');window.oldReasoning.open=true;
    const wrap=document.querySelector('#reader-chat-messages');wrap.scrollTop=80;window.chatTop=wrap.scrollTop;
    session.progress.answer='updated '.repeat(200);session.notify();
  });
  assert.ok(await page.evaluate(() => window.oldBubble===document.querySelector('#reader-chat-messages .mini-msg')),'history DOM survives streaming');
  assert.ok(await page.evaluate(() => Math.abs(document.querySelector('#reader-chat-messages').scrollTop-window.chatTop)<2),'stream preserves manual scroll');
  assert.ok(await page.evaluate(() => window.oldSource.isConnected && window.oldCopy.isConnected && window.oldReasoning.isConnected && window.oldReasoning.open),'references, actions and expanded reasoning preserved');
  await page.evaluate(() => {window.pendingReasoning=document.querySelector('#reader-chat-messages .mini-msg:last-child .reader-reasoning');window.pendingReasoning.open=false;const s=window.PW.readerState.chatSession;s.progress.reasoning+=' continued';s.notify();});
  assert.ok(await page.evaluate(() => window.pendingReasoning.isConnected && !window.pendingReasoning.open),'streaming reasoning respects user collapse');
  await page.locator('#reader-chat-bottom').click();
  assert.ok(await page.evaluate(() => {const w=document.querySelector('#reader-chat-messages');return w.scrollHeight-w.clientHeight-w.scrollTop<3;}));
  await page.evaluate(() => {const s=window.PW.readerState.chatSession;s.progress.answer+=' more'.repeat(200);s.notify();});
  assert.ok(await page.evaluate(() => {const w=document.querySelector('#reader-chat-messages');return w.scrollHeight-w.clientHeight-w.scrollTop<3;}),'bottom follows stream');
  await page.evaluate(() => {const s=window.PW.readerState.chatSession;s.data.messages.push({role:'user',content:s.pending},{role:'assistant',content:'final answer',reasoning:'final thought'});s.progress=null;s.notify();});
  assert.equal(await page.locator('#reader-chat-messages .mini-msg').count(),22,'final answer replaces pending without duplicates');
  await page.evaluate(() => {const s=window.PW.readerState.chatSession;s.pending=null;s.busy=false;s.notify();window.oldChat=document.querySelector('#reader-chat-messages').innerHTML;s.scope='paper:b';s.progress={answer:'foreign text'};s.notify();});
  assert.ok(await page.evaluate(() => window.oldChat===document.querySelector('#reader-chat-messages').innerHTML),'foreign paper session ignored');
  await page.evaluate(() => {window.PW.readerState.chatSession.scope='paper:a';window.widthWrites=0;const original=Storage.prototype.setItem;Storage.prototype.setItem=function(k,v){if(k==='pw-reader-sidebar-width')window.widthWrites++;return original.call(this,k,v)};window.fitBefore=window.PW.readerState.fitScale;window.canvasBefore=document.querySelector('#pdf-pages canvas');});
  const handle=page.locator('.reader-sidebar-resizer'), bounds=await handle.boundingBox();
  await page.mouse.move(bounds.x+4,bounds.y+100);await page.mouse.down();
  assert.equal(await page.evaluate(() => {
    const style=document.querySelector('#reader').style, original=style.setProperty.bind(style);window.widthApplies=0;
    style.setProperty=(key,value,...rest)=>{if(key==='--reader-panel-width')window.widthApplies++;return original(key,value,...rest);};
    const handle=document.querySelector('.reader-sidebar-resizer');
    for(let i=0;i<30;i++)handle.dispatchEvent(new PointerEvent('pointermove',{clientX:700+i,pointerId:1,bubbles:true}));
    return window.widthApplies;
  }),0,'pointermove defers layout to animation frame');
  await page.evaluate(() => new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve))));
  assert.equal(await page.evaluate(() => window.widthApplies),1,'one frame coalesces pointermove burst');
  await page.mouse.move(bounds.x-120,bounds.y+100,{steps:12});
  assert.equal(await page.evaluate(() => window.widthWrites),0,'no storage writes during drag');
  assert.ok(await page.evaluate(() => window.canvasBefore.isConnected),'no PDF invalidation during drag');
  assert.equal(await page.evaluate(() => window.PW.readerState.fitScale),await page.evaluate(() => window.fitBefore),'no PDF layout during drag');
  await page.mouse.up();
  assert.equal(await page.evaluate(() => window.widthWrites),1,'persist final width once');
  assert.ok(await page.evaluate(() => window.PW.readerState.fitScale!==window.fitBefore),'PDF relayout after drag');
  assert.ok(await page.evaluate(() => !window.canvasBefore.isConnected),'PDF canvas refreshed after drag');
  await handle.focus();await page.keyboard.press('ArrowLeft');
  assert.equal(await page.evaluate(() => window.widthWrites),2,'keyboard resize persists');
  assert.ok(await page.evaluate(() => document.querySelector('.main').closest('[inert]')!==null),'reader background inert');
  // Modal dialogs keep their own trap and restore focus within the reader.
  await page.locator('#reader-chat-input').focus();
  await page.evaluate(() => {
    const modal=document.createElement('div');modal.id='reader-test-modal';modal.className='modal';
    modal.innerHTML='<div class="modal-card"><button class="modal-close">close</button><input id="reader-modal-input"></div>';
    modal.querySelector('button').onclick=()=>modal.classList.add('hidden');document.querySelector('#reader').append(modal);
  });
  await page.waitForFunction(() => document.activeElement.closest('#reader-test-modal'));
  await page.keyboard.press('Escape');
  assert.equal(await page.locator('#reader').evaluate(n=>n.classList.contains('hidden')),false,'Escape closes inner modal first');
  await page.waitForFunction(() => document.activeElement.id==='reader-chat-input');
  await page.evaluate(() => {
    const modal=document.createElement('div');modal.id='reader-external-modal';modal.className='modal';
    modal.innerHTML='<div class="modal-card"><button class="modal-close">close</button><input id="reader-external-input"></div>';
    modal.querySelector('button').onclick=()=>modal.classList.add('hidden');document.body.append(modal);
  });
  await page.waitForFunction(() => document.activeElement.closest('#reader-external-modal'));
  assert.equal(await page.locator('#reader-external-modal').evaluate(n=>n.inert),false);
  await page.keyboard.press('Escape');
  await page.waitForFunction(() => document.activeElement.id==='reader-chat-input');
  await page.evaluate(() => {
    const focusable='button:not(:disabled),a[href],input:not(:disabled):not([type="hidden"]),select:not(:disabled),textarea:not(:disabled),[tabindex]:not([tabindex="-1"])';
    const nodes=[...document.querySelector('#reader').querySelectorAll(focusable)].filter(n=>n.getClientRects().length);nodes.at(-1).focus();
  });
  await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(() => document.querySelector('#reader').contains(document.activeElement)),'Tab remains inside reader');
  let clipboardCandidate = {id:'reader-clipboard-1', kind:'title', value:'A synthetic clipboard paper title'};
  const clipboardActions = [];
  await page.route('**/api/clipboard/**', async route => {
    const path = new URL(route.request().url()).pathname;
    if (path.endsWith('/candidates')) {
      await route.fulfill({json:{items:clipboardCandidate ? [clipboardCandidate] : []}});
    } else {
      clipboardActions.push(path);
      if (path.endsWith('/accept')) {
        clipboardCandidate = null;
        await route.fulfill({json:{paper:{id:'clipboard-test', title:'Clipboard paper'}}});
      } else {
        // A stale/in-flight candidate or a failed dismiss must not reopen a closed prompt.
        await route.fulfill({status:503,json:{detail:'simulated dismiss failure'}});
      }
    }
  });
  const clipboardPrompt = page.locator('#clipboard-prompt');
  await clipboardPrompt.waitFor({timeout:8000});
  assert.equal(await clipboardPrompt.evaluate(node=>!!node.closest('[inert]')),false,'visible clipboard prompt stays interactive over the reader');
  await page.locator('#cp-close').click();
  await clipboardPrompt.waitFor({state:'hidden'});
  await page.waitForTimeout(4300);
  assert.equal(await clipboardPrompt.isVisible(),false,'dismissed candidate stays hidden even if the server offers it again');
  clipboardCandidate = {id:'reader-clipboard-2',kind:'doi',value:'10.0000/synthetic'};
  await clipboardPrompt.waitFor({timeout:8000});
  await page.locator('#cp-import').focus();
  await page.keyboard.press('Tab');
  assert.equal(await page.evaluate(()=>document.activeElement.id),'cp-dismiss','clipboard keyboard navigation stays usable');
  await page.locator('#cp-dismiss').click();
  await clipboardPrompt.waitFor({state:'hidden'});
  clipboardCandidate = {id:'reader-clipboard-3',kind:'title',value:'Keyboard dismissal'};
  await clipboardPrompt.waitFor({timeout:8000});
  await page.locator('#cp-close').focus();
  await page.keyboard.press('Escape');
  await clipboardPrompt.waitFor({state:'hidden'});
  assert.ok(await page.locator('#reader').isVisible(),'Escape dismisses the focused prompt and keeps the reader open');
  clipboardCandidate = {id:'reader-clipboard-4',kind:'title',value:'Import from reader'};
  await clipboardPrompt.waitFor({timeout:8000});
  await page.locator('#cp-import').click();
  await clipboardPrompt.waitFor({state:'hidden'});
  assert.ok(clipboardActions.some(path=>path.endsWith('/reader-clipboard-1/dismiss')));
  assert.ok(clipboardActions.some(path=>path.endsWith('/reader-clipboard-2/dismiss')));
  assert.ok(clipboardActions.some(path=>path.endsWith('/reader-clipboard-3/dismiss')));
  assert.ok(clipboardActions.some(path=>path.endsWith('/reader-clipboard-4/accept')));
  console.log('PASS clipboard close, ignore, import, keyboard and stale-poll suppression inside reader');
  await page.locator('#reader-close').click();
  assert.equal(await page.evaluate(() => document.activeElement.id),'library-search','close restores opener');
  assert.equal(await page.locator('.main').evaluate(n=>!!n.closest('[inert]')),false);
  assert.deepEqual(errors,[]);
  console.log('PASS reader tabs, incremental stream, scroll ownership, scope guard, drag batching, keyboard resize and focus/inert');
} finally {
  await browser?.close();
  if(server.exitCode===null){const exited=new Promise(resolve=>server.once('exit',resolve));server.kill();await exited;}
  const absolute=resolve(temp);
  if(!absolute.startsWith(resolve(tmpdir())+sep)||!absolute.split(sep).at(-1).startsWith('pw-ai-ui-'))throw new Error('Unexpected test data path');
  rmSync(absolute,{recursive:true,force:true});
}
