/* Real local engines, isolated library, no remote AI or user browser profile. */
import assert from 'node:assert/strict';
import {spawn} from 'node:child_process';
import {mkdtempSync,rmSync,mkdirSync,readFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
import {resolve,dirname,join,sep} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createRequire} from 'node:module';
import net from 'node:net';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const require=createRequire(join(process.env.PAPERWHALE_NODE_MODULES||'C:/Users/10498/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules','package.json'));
const {chromium}=require('playwright');
const temp=mkdtempSync(join(tmpdir(),'npl-rich-answer-'));
const listener=net.createServer();await new Promise(r=>listener.listen(0,'127.0.0.1',r));
const port=listener.address().port;await new Promise(r=>listener.close(r));
const base=`http://127.0.0.1:${port}`;
const server=spawn(join(root,'.venv/Scripts/python.exe'),[join(root,'tests/ai_ui_server.py'),String(port)],{
  cwd:root,windowsHide:true,env:{...process.env,PAPERWHALE_UI_TEST:'1',PAPERWHALE_DATA:temp,PAPERWHALE_LLM_MOCK:'1'},
  stdio:['ignore','pipe','pipe'],
});
let logs='',browser;
server.stdout.on('data',chunk=>logs+=chunk);server.stderr.on('data',chunk=>logs+=chunk);
try {
  let ready=false;
  for(let index=0;index<100;index++){
    try{ready=(await fetch(base+'/api/health')).ok;}catch{}
    if(ready)break;if(server.exitCode!==null)throw new Error(logs);
    await new Promise(r=>setTimeout(r,100));
  }
  assert.ok(ready,logs);
  browser=await chromium.launch({executablePath:process.env.PAPERWHALE_EDGE||'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe',headless:true});
  const page=await browser.newPage({viewport:{width:1450,height:980},acceptDownloads:true});
  const errors=[],remote=[];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('request',request=>{if(request.url().startsWith('http')&&!request.url().startsWith(base))remote.push(request.url());});
  await page.addInitScript(()=>localStorage.setItem('pw-setup-dismissed','1'));
  await page.goto(base);await page.waitForFunction(()=>window.PW?.openPaper);
  await page.evaluate(()=>window.PW.openPaper('a'));
  if (await page.locator('#reader').evaluate(node=>node.classList.contains('reader-sidebar-collapsed'))) await page.locator('#reader-sidebar-toggle').click();
  await page.locator('#reader-tabs [data-tab="chat"]').click();
  await page.waitForFunction(()=>window.PW.readerState.chatSession?.data);
  const chart={type:'bar',title:'不同条件下的响应',xLabel:'实验条件',yLabel:'响应 / a.u.',
    labels:['条件 A','条件 B','条件 C'],series:[{name:'响应',data:[12,24,18]}],source:'示意数据，用于界面验证'};
  const answer=[
    '## 原文与解释对照',
    '| 原文 | 含义 | 位置 |','|---|---|---|',
    '| $s_x = \\frac{I_R-I_L}{I_R+I_L}$ | 左右旋分量差异的归一化表示 | 第 4 页 [1] |',
    '| $t_{exp} \\gg \\tau_c$ | 相对较长时间内的平均测量 | 第 4 页 [1] |',
    '',
    '### 过程示意',
    '```mermaid','flowchart LR','A["偏振测量"] --> B["获取左右旋分量"]','B --> C["计算归一化差异"]','```',
    '根据原文整理 [1]。',
    '```chart',JSON.stringify(chart),'```',
  ].join('\n');
  await page.evaluate(answer=>{
    const session=window.PW.readerState.chatSession;
    session.data={...session.data,messages:[{role:'user',content:'请用表格和图形讲清楚'},{role:'assistant',content:answer}]};
    session.notify();
  },answer);
  await page.waitForFunction(()=>document.querySelectorAll('.ai-visual[data-visual-state="ready"]').length===2,null,{timeout:20000});
  assert.equal(await page.locator('#reader-chat-messages table tbody tr').count(),2);
  assert.equal(await page.locator('#reader-chat-messages table .katex').count(),2);
  assert.equal(await page.locator('.ai-visual-image').count(),2);
  await page.waitForFunction(()=>[...document.querySelectorAll('.ai-visual-image')].every(image=>image.complete&&image.naturalWidth>0));
  assert.ok(await page.locator('#reader-chat-messages').evaluate(node=>node.scrollWidth<=node.clientWidth+2),'wide table stays inside horizontal scrolling wrapper');
  const diagram=page.locator('.ai-visual').first();
  await diagram.getByRole('button',{name:'放大',exact:true}).click();
  await page.locator('dialog[open]').waitFor();
  assert.ok(await page.locator('dialog').evaluate(node=>!node.inert),'graphic dialog stays interactive over the reader');
  await page.keyboard.press('Tab');
  assert.ok(await page.evaluate(()=>document.querySelector('dialog').contains(document.activeElement)),'native dialog owns focus');
  await page.keyboard.press('Escape');
  await page.locator('dialog').waitFor({state:'detached'});
  assert.ok(await page.locator('#reader').isVisible(),'Escape closes only graphic preview');
  let downloaded=page.waitForEvent('download');
  await diagram.getByRole('button',{name:'导出 SVG',exact:true}).click();
  let download=await downloaded;
  const exportedSVG = readFileSync(await download.path(),'utf8');
  assert.match(exportedSVG,/<svg/);
  assert.match(exportedSVG,/偏振测量/,'exported flowchart retains its labels');
  downloaded=page.waitForEvent('download');
  await diagram.getByRole('button',{name:'导出 PNG',exact:true}).click();
  download=await downloaded;
  assert.equal(readFileSync(await download.path()).subarray(0,8).toString('hex'),'89504e470d0a1a0a');
  downloaded=page.waitForEvent('download');
  await page.locator('.ai-visual').nth(1).getByRole('button',{name:'导出 SVG',exact:true}).click();
  download=await downloaded;
  const chartSVG=readFileSync(await download.path(),'utf8');
  assert.match(chartSVG,/条件 A/);assert.match(chartSVG,/示意数据/);
  const output=join(root,'pw-debug/answer-content');mkdirSync(output,{recursive:true});
  await page.locator('#reader-chat-messages').evaluate(node=>node.scrollTop=0);
  await page.screenshot({path:join(output,'reader-table-and-diagram.png')});
  await page.locator('.ai-visual').nth(1).scrollIntoViewIfNeeded();
  await page.screenshot({path:join(output,'reader-chart.png')});
  await page.evaluate(()=>{
    const session=window.PW.readerState.chatSession;
    session.pending='流式图形';session.busy=true;
    session.progress={answer:'```mermaid\nflowchart LR\nA-->',state:'running'};session.notify();
  });
  assert.equal(await page.locator('#reader-chat-messages .mini-msg:last-child .ai-visual').count(),0,'incomplete diagram stays as text');
  await page.evaluate(()=>{
    const session=window.PW.readerState.chatSession;
    session.progress.answer='```mermaid\nflowchart LR\nA-->B\n```';session.notify();
  });
  await page.locator('#reader-chat-messages .mini-msg:last-child .ai-visual[data-visual-state="ready"]').waitFor();
  await page.evaluate(()=>{
    const session=window.PW.readerState.chatSession;
    session.pending=null;session.busy=false;session.progress=null;
    session.data={...session.data,messages:[{role:'assistant',content:'```chart\n{"type":"bar"}\n```'}]};session.notify();
  });
  await page.locator('.ai-visual[data-visual-state="error"]').waitFor();
  assert.ok(await page.getByText('图形代码',{exact:true}).isVisible());
  assert.deepEqual(remote,[],'rendering works with only local resources');
  assert.deepEqual(errors,[]);
  console.log('PASS real tables, formulas, local Mermaid/ECharts, focus, SVG/PNG export, streaming completion and failure fallback');
} catch(error) {
  if(browser){
    const page=browser.contexts()[0]?.pages()[0];
    if(page)console.log(await page.locator('.ai-visual').allTextContents());
  }
  throw error;
} finally {
  await browser?.close();
  if(server.exitCode===null){const exited=new Promise(r=>server.once('exit',r));server.kill();await exited;}
  const safe=resolve(temp);assert.ok(safe.startsWith(resolve(tmpdir())+sep));rmSync(safe,{recursive:true,force:true});
}
