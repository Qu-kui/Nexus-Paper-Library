import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../extension/popup.js',import.meta.url),'utf8');
const start=source.indexOf('async function fetchPdfInPage(');
const end=source.indexOf('\nasync function cookieHeader',start);
const extractorStart=source.indexOf('async function extractPageMetadata(');
const extractorEnd=source.indexOf('\nasync function probeBase',extractorStart);
const extractorSource=extractorStart>=0&&extractorEnd>extractorStart?source.slice(extractorStart,extractorEnd):'';

/* 真跑 popup.js 里的 extractPageMetadata（页面 meta 标签解析），只搭一个够用的假 DOM。
   这样 citation_volume / prism.volume 这类兜底映射是被真实代码验证的，而不是 mock 出来的 meta。 */
function extractMetaFromTags(pairs){
  const nodes=pairs.map(([name,value])=>({
    getAttribute(attr){
      if(attr==='name')return name;
      if(attr==='content')return value;
      return null;
    },
    textContent:'',
  }));
  const document={
    documentElement:{outerHTML:'<html><head></head><body></body></html>'},
    title:'Fake Paper',
    body:null,
    querySelectorAll:(selector)=>(selector==='meta'?nodes:[]),
    querySelector:()=>null,
    addEventListener(){},
  };
  const context=vm.createContext({URL,document,location:new URL('https://example.test/science/article/pii/S1234567890'),window:{},navigator:{userAgent:'Synthetic Browser/1.0'},console,setTimeout,clearTimeout});
  vm.runInContext(extractorSource+'\nglobalThis.__extract=extractPageMetadata;',context);
  return context.__extract();
}

/* 追踪被 popup.js 代码创建的 setTimeout：用来断言没有“输家”定时器漏挂在事件循环里。 */
function trackedTimers(){
  const pending=new Set();
  return {
    pending,
    setTimeout:(fn,ms)=>{const id=setTimeout(()=>{pending.delete(id);fn();},ms);pending.add(id);return id;},
    clearTimeout:id=>{pending.delete(id);clearTimeout(id);},
  };
}

/* 只用于「CDP 一直没拿到真 PDF、要等兜底超时」的用例：把长延时压到 80ms，
   否则 bodyPromise 要等 20s，套件会被拖垮。 */
function cappedTimers(limit=80){
  const pending=new Set();
  return {
    pending,
    setTimeout:(fn,ms)=>{const id=setTimeout(()=>{pending.delete(id);fn();},Math.min(ms,limit));pending.add(id);return id;},
    clearTimeout:id=>{pending.delete(id);clearTimeout(id);},
  };
}

test('extension rejects an oversized PDF before reading its body',async () => {
  let read=false;
  const context=vm.createContext({AbortController,setTimeout,clearTimeout,Uint8Array,TextDecoder,btoa,
    fetch:async () => ({ok:true,headers:new Headers({'content-length':String(100*1024*1024)}),
      arrayBuffer:async () => {read=true;return new ArrayBuffer(100);}})});
  vm.runInContext(source.slice(start,end)+'\nglobalThis.grab=fetchPdfInPage;',context);
  const result=await context.grab('https://example.org/file.pdf');
  assert.equal(result,'');
  assert.equal(read,false);
});


const evidenceStart=source.indexOf('function pdfEvidenceUrl(');
const evidenceEnd=source.indexOf('\nfunction waitForPdfResponse',evidenceStart);

test('a publisher "preparing your download" page is never mistaken for a PDF',async () => {
  const context=vm.createContext({URL});
  vm.runInContext(source.slice(evidenceStart,evidenceEnd)+'\nglobalThis.evidence=pdfEvidenceUrl;',context);
  const evidence=context.evidence;
  const signedPdf='https://www.sciencedirect.com/science/article/pii/S0143816619313120/pdfft?md5=358ccc&pid=1-s2.0-S0143816619313120-main.pdf';
  assert.equal(evidence({url:signedPdf,responseHeaders:[{name:'Content-Type',value:'text/html'}]}),'');
  assert.equal(evidence({url:'https://example.org/download?file=paper.pdf',responseHeaders:[]}),'');
  assert.equal(evidence({url:'https://pdf.sciencedirectassets.com/signed/paper.pdf?token=1',responseHeaders:[{name:'Content-Type',value:'application/pdf'}]}),'https://pdf.sciencedirectassets.com/signed/paper.pdf?token=1');
  assert.equal(evidence({url:'https://example.org/files/paper.pdf',responseHeaders:[]}),'https://example.org/files/paper.pdf');
  assert.equal(evidence({url:'https://example.org/get',responseHeaders:[{name:'Content-Disposition',value:'attachment; filename="paper.pdf"'}]}),'https://example.org/get');
});


const helperStart=source.indexOf('function isArticleLandingUrl(');
const helperEnd=source.indexOf('\n  // ScienceDirect 的 pdfDownload',helperStart);

function helperContext(){
  const context=vm.createContext({URL});
  vm.runInContext(source.slice(helperStart,helperEnd)+'\nglobalThis.isArticleLandingUrl=isArticleLandingUrl;globalThis.authorsFromPreloadedState=authorsFromPreloadedState;',context);
  return context;
}

test('an article landing page is never offered as the PDF address',async()=>{
  const {isArticleLandingUrl}=helperContext();
  const page='https://www.sciencedirect.com/science/article/abs/pii/S0030399223009453';
  assert.equal(isArticleLandingUrl('https://www.sciencedirect.com/science/article/abs/pii/S0030399223009453',page),true);
  assert.equal(isArticleLandingUrl('https://www.sciencedirect.com/science/article/pii/S0030399223009453',page),true);
  assert.equal(isArticleLandingUrl('https://www.sciencedirect.com/science/article/pii/S0030399223009453/pdfft?md5=a&pid=1-s2.0-S0030399223009453-main.pdf',page),false);
  assert.equal(isArticleLandingUrl('https://pdf.sciencedirectassets.com/signed/paper.pdf',page),false);
  assert.equal(isArticleLandingUrl('javascript:void(0)',page),true);
});

test('ScienceDirect authors are read from the preloaded page state',async()=>{
  const {authorsFromPreloadedState}=helperContext();
  const state={abstracts:{authors:{content:[
    {'#name':'author','$$':[{'#name':'given-name','_':'Qifeng'},{'#name':'surname','_':'Yan'}]},
    {'#name':'author','$$':[{'#name':'given-name','_':'Shuai'},{'#name':'surname','_':'Zhao'}]},
    {'#name':'author','$$':[{'#name':'given-name','_':'Qifeng'},{'#name':'surname','_':'Yan'}]},
  ]}}};
  assert.deepEqual(Array.from(authorsFromPreloadedState(state)),['Qifeng Yan','Shuai Zhao']);
  assert.deepEqual(Array.from(authorsFromPreloadedState(undefined)),[]);
});


const formatStart=source.indexOf('function formatError(');
const formatSource=(source.slice(formatStart).match(/^function formatError[\s\S]*?\r?\n}\r?\n/)||[''])[0];

test('errors are shown as readable text instead of [object Object]',async()=>{
  const context=vm.createContext({});
  vm.runInContext(formatSource+'\nglobalThis.formatError=formatError;',context);
  const formatError=context.formatError;
  assert.equal(formatError(new Error('boom')),'boom');
  assert.equal(formatError('直接字符串'),'直接字符串');
  assert.equal(formatError({status:422,detail:[{msg:'x'}]}),'{"status":422,"detail":[{"msg":"x"}]}');
  assert.equal(formatError({}),'未知错误（浏览器返回了无法解析的错误对象）');
  assert.equal(formatError(null),'未知错误');
});


test('page metadata extraction stays self-contained for executeScript injection',async()=>{
  const start=source.indexOf('async function extractPageMetadata(');
  const end=source.indexOf('\nasync function probeBase',start);
  const body=source.slice(start,end);
  for(const helper of ['isArticleLandingUrl','authorsFromPreloadedState']){
    assert.ok(body.includes(`function ${helper}(`),`${helper} must live inside extractPageMetadata: injected functions only carry their own body`);
  }
});


async function saveFixture({manual=false,navigated=false,diagnostics=false,injectionFailed=false,blocked=false,meta=null,authors='',timers=null,dnr=true,debuggerApi=false,debugResponses=null,debugResponsesNext=null,postPdf='attached',saveMode=undefined,archivePath=''}={}) {
  const nodes=new Map();
  const listeners=new Map();
  const domListeners=[];
  const tabReads=[];
  const node=id=>{if(!nodes.has(id)){
    const classes=new Set();
    nodes.set(id,{value:'',checked:false,textContent:'',innerHTML:'',dataset:{},
      classList:{
        add(...names){names.forEach(name=>classes.add(name));},
        remove(...names){names.forEach(name=>classes.delete(name));},
        toggle(name,force){const want=force===undefined?!classes.has(name):Boolean(force);if(want)classes.add(name);else classes.delete(name);return want;},
        contains(name){return classes.has(name);},
      },
      addEventListener(type,fn){const list=listeners.get(id)||[];list.push({type,fn});listeners.set(id,list);},
      focus(){nodes.get(id).focused=true;}});
  }return nodes.get(id);};
  // 二选一 radio：init 会给它们挂 change 监听，测试里可主动切换。
  const saveRadios=saveMode!==undefined?['archive','analyze'].map(value=>({
    value,checked:value===saveMode,listeners:[],
    addEventListener(type,fn){this.listeners.push({type,fn});},
  })):null;
  const fireSaveModeChange=(value)=>{
    const radio=(saveRadios||[]).find(item=>item.value===value);
    if(!radio)throw new Error('no save-mode radio for '+value);
    if(!radio.listeners.length)throw new Error('save-mode radio has no change listener; did init run?');
    radio.listeners.forEach(item=>item.fn());
  };
  const original='https://proxy.test/science/article/pii/S1234567890';
  const pageUrl=(meta&&meta.url)||original;
  const pageMeta=meta||{url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',original+'/valid']};
  const calls=[],posts=[],messages=[],rules=[],debugCalls=[],tabsCreated=[];
  // chrome.debugger 的 CDP 通道：Page.navigate 之后异步派发一次 Fetch.requestPaused，
  // Fetch.getResponseBody 回真实的 %PDF base64，等价于实测里拿到的 1236 字节正文。
  const DEBUG_PDF_BASE64='JVBERi0xLjcg'+ 'A'.repeat(1400);
  const debugListeners=[];
  const debugBodies=new Map();
  let debugNavCount=0;
  const debugRuntime={
    attach:async(target,version)=>{debugCalls.push(['attach',target.tabId,version]);},
    detach:async target=>{debugCalls.push(['detach',target.tabId]);},
    onEvent:{
      addListener:f=>{debugListeners.push(f);},
      removeListener:f=>{const i=debugListeners.indexOf(f);if(i>=0)debugListeners.splice(i,1);},
    },
    sendCommand:(target,method,params,cb)=>{
      debugCalls.push(['send',method,params&&params.url,params&&params.referrer]);
      if(method==='Page.navigate'){
        // 真实出版社可能先回一条“伪装成 .pdf 的 HTML”，再回真正的 PDF，所以允许排一队响应。
        // 允许按「第几次导航」给不同响应：先跳转页、再真 PDF 的场景要能模拟。
        const queue = debugNavCount===0 ? debugResponses : (debugResponsesNext||debugResponses);
        debugNavCount+=1;
        const responses=queue||[{url:params.url,contentType:'application/pdf',body:DEBUG_PDF_BASE64,status:200}];
        responses.forEach((entry,index)=>{
          const requestId='req-'+(index+1);
          debugBodies.set(requestId,entry.body);
          setTimeout(()=>{for(const f of [...debugListeners])f({tabId:target.tabId},'Fetch.requestPaused',{
            request:{url:entry.url},
            responseStatusCode:entry.status??200,
            responseHeaders:[{name:'Content-Type',value:entry.contentType}],
            requestId,
          });},index*2);
        });
        if(cb)cb({frameId:'F',loaderId:'L'});
        return;
      }
      if(method==='Fetch.getResponseBody'){if(cb)cb({base64Encoded:true,body:debugBodies.get(params&&params.requestId)||''});return;}
      if(cb)cb({});
    },
  };
  const context=vm.createContext({AbortController,setTimeout:timers?timers.setTimeout:setTimeout,clearTimeout:timers?timers.clearTimeout:clearTimeout,Uint8Array,TextDecoder,btoa,atob,URL,Date,
    document:{getElementById:node,addEventListener:(type,fn)=>{domListeners.push({type,fn});},
      ...(saveRadios?{
        querySelectorAll:selector=>String(selector).includes('save-mode')?saveRadios:[],
      }:{}),
    },
    chrome:{runtime:{sendMessage:async message=>{messages.push(message);},openOptionsPage(){}},
      storage:{local:{get:async()=>({}),set:async()=>{}}},
      tabs:{get:async()=>{tabReads.push(true);return {url:navigated?pageUrl+'/other':pageUrl};},
        query:async()=>[{id:7,url:pageUrl}],
        ...(debuggerApi?{
          create:async opts=>{tabsCreated.push(opts&&opts.url);return {id:70+tabsCreated.length};},
          remove:async id=>{tabsCreated.push('removed:'+id);},
        }:{}),
      },scripting:{executeScript:async options=>{
      if(options.func.name==='extractPageMetadata')return [{result:{...pageMeta}}];
      calls.push({url:options.args?.[0],deadline:options.args?.[1],func:options.func?.name||''});
      if(injectionFailed)throw new Error('private browser error token=secret');
      if(blocked)return [{result:{base64:'',attempts:[{url:options.args[0],code:'not_pdf'}],blocked:true}}];
      if(diagnostics)return [{result:{base64:'',attempts:[{url:options.args[0]+'?token=secret#private',code:'access_denied',status:403}]}}];
      return [{result:(options.args?.[0]||'').endsWith('/valid')?'JVBERi0xLjcg'+ 'A'.repeat(1400):''}];
    }},cookies:{getAll:async()=>[]},
    ...(dnr?{declarativeNetRequest:{updateSessionRules:async payload=>{rules.push(payload);}}}:{}),
    ...(debuggerApi?{debugger:debugRuntime}:{})},
    fetch:async(url,options)=>{if(url.includes('/api/import/web')){posts.push(JSON.parse(options.body));return {ok:true,json:async()=>diagnostics?({pdf:'queued',paper:{title:'<img src=x onerror=alert(1)>'},pdf_warning:'identity_mismatch: <script>unsafe</script>',identity_warning:'Wrong article'}):({pdf:postPdf,paper:{id:'p1',title:'Paper',has_pdf:postPdf==='attached'}})};}if(diagnostics||injectionFailed)throw new TypeError('CORS token=secret');return {ok:false};}});
  vm.runInContext(source,context);
  vm.runInContext(`state.base='http://127.0.0.1:8765';state.tab={id:7,url:${JSON.stringify(pageUrl)}};state.meta=${JSON.stringify(pageMeta)};`,context);
  node('f-title').value='Paper';node('f-authors').value=authors;node('f-pdf').value=original+(manual?'/edited':'/bad');node('opt-pdf').checked=true;
  node('archive-path').value=archivePath;
  await context.save();
  return {calls,posts,messages,rules,debugCalls,tabsCreated,debugBase64:DEBUG_PDF_BASE64,tabReads,node,original,saveRadios,fireSaveModeChange,
    hasClass:(id,name)=>Boolean(node(id).classList&&node(id).classList.contains(name)),
    domReady:async()=>{for(const item of domListeners)if(item.type==='DOMContentLoaded')await item.fn();},
    fire:(id,type)=>{for(const item of (listeners.get(id)||[]))if(item.type===type)item.fn();},
    listenerCount:(id,type)=>(listeners.get(id)||[]).filter(item=>item.type===type).length,
    run:()=>context.save(),
    error:node('error').textContent,html:node('result').innerHTML};
}

/* 卷期页：页面 meta 抓取 + 保存 payload。citation_* 优先，prism.* 兜底，页码用 en dash 拼。 */
async function saveWithExtractedMeta(pairs){
  const extracted=await extractMetaFromTags(pairs);
  const fixture=await saveFixture({meta:extracted});
  assert.equal(fixture.error,'','saving must not surface a browser error');
  assert.equal(fixture.posts.length,1);
  return fixture.posts[0];
}

test('saving a page with citation_volume/issue/firstpage/lastpage sends volume, issue and an en-dash page range',async()=>{
  const body=await saveWithExtractedMeta([
    ['citation_title','Paper From Tags'],
    ['citation_journal_title','Journal X'],
    ['citation_volume','12'],
    ['citation_issue','3'],
    ['citation_firstpage','45'],
    ['citation_lastpage','67'],
  ]);
  assert.equal(body.volume,'12');
  assert.equal(body.issue,'3');
  assert.equal(body.pages,'45–67');
});

test('only citation_firstpage still yields a single page number',async()=>{
  const body=await saveWithExtractedMeta([
    ['citation_title','Paper With One Page'],
    ['citation_volume','12'],
    ['citation_firstpage','45'],
  ]);
  assert.equal(body.volume,'12');
  assert.equal(body.pages,'45');
});

test('prism metadata is used when the citation_* fields are missing',async()=>{
  const body=await saveWithExtractedMeta([
    ['citation_title','Prism Paper'],
    ['prism.volume','7'],
    ['prism.number','2'],
    ['prism.startingPage','101'],
    ['prism.endingPage','110'],
  ]);
  assert.equal(body.volume,'7');
  assert.equal(body.issue,'2');
  assert.equal(body.pages,'101–110');
});

test('a page without bibliographic metadata sends empty volume/issue/pages',async()=>{
  const body=await saveWithExtractedMeta([
    ['citation_title','No Bibliographic Data'],
  ]);
  assert.equal(body.volume,'');
  assert.equal(body.issue,'');
  assert.equal(body.pages,'');
});

test('save retries candidates, keeps page URL and shares bounded deadlines',async()=>{
  const {calls,posts,original}=await saveFixture();
  assert.deepEqual(calls.filter(c=>c.func==='fetchPdfInPage').map(c=>c.url),[original+'/bad',original+'/valid']);
  assert.equal(posts.length,1);assert.equal(posts[0].url,original);assert.equal(posts[0].pdf_referer,original);
  assert.equal(posts[0].pdf_user_agent,'Synthetic Browser/1.0');
  assert.equal(posts[0].pdf_url,original+'/valid');assert.ok(posts[0].pdf_base64);
  assert.ok(calls.filter(c=>c.func==='fetchPdfInPage').every(c=>c.deadline<=Date.now()+90000));
});

test('manual PDF override wins and changed tab cannot import old article',async()=>{
  const manual=await saveFixture({manual:true});
  assert.deepEqual(manual.calls.filter(c=>c.func==='fetchPdfInPage').map(c=>c.url),[manual.original+'/edited']);
  assert.deepEqual(manual.posts[0].pdf_candidates,[manual.original+'/edited']);
  const changed=await saveFixture({navigated:true});assert.equal(changed.posts.length,0);assert.equal(changed.calls.length,0);assert.match(changed.error,/changed/);
});

test('malformed page metadata cannot leak non-strings into the import payload',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  // 页面元数据来自任意网页：模拟 authors/tags/pdf_candidates 里混进对象、数字、null。
  const hostile={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',
    pdf_candidates:[{href:original+'/object'},17,null,original+'/valid'],
    tags:[{name:'object-tag'},42,'real-tag',null],year:2020};
  const {posts,error}=await saveFixture({meta:hostile,authors:'Alice; Bob'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  const payload=posts[0];
  for(const key of ['title','year','venue','doi','arxiv_id','url','abstract','item_type','pdf_url','pdf_referer','pdf_user_agent']){
    assert.equal(typeof payload[key],'string',`${key} must be a string or the server rejects it with 422`);
  }
  for(const key of ['pdf_base64','pdf_name','pdf_cookies','pdf_download_path','page_html']){
    if(key in payload)assert.equal(typeof payload[key],'string',`${key} must be a string when sent`);
  }
  for(const key of ['authors','tags','pdf_candidates']){
    assert.ok(Array.isArray(payload[key]),`${key} must be an array`);
    assert.ok(payload[key].every(item=>typeof item==='string'),`${key} must only contain strings`);
  }
  assert.deepEqual(payload.authors,['Alice','Bob'],'authors are re-read from the form');
  assert.deepEqual(payload.tags,['real-tag'],'non-string page keywords are dropped');
  assert.deepEqual(payload.pdf_candidates,[original+'/valid',original+'/bad']);
  assert.equal(payload.pdf_url,original+'/valid');
  assert.equal(typeof payload.download_pdf,'boolean');assert.equal(typeof payload.analyze,'boolean');
  assert.ok(Array.isArray(payload.pdf_attempts)&&payload.pdf_attempts.every(item=>item&&typeof item==='object'&&!Array.isArray(item)),'pdf_attempts entries must be plain objects');
  assert.deepEqual(JSON.parse(JSON.stringify(payload)),payload,'payload must survive a JSON round-trip unchanged');
});


test('a failed PDF grab arms the download watch with a light payload',async()=>{
  for(const options of [{blocked:true},{manual:true,injectionFailed:true}]){
    const label=JSON.stringify(options);
    const {messages,posts,html,original}=await saveFixture(options);
    assert.equal(messages.length,1,`${label}: exactly one watch message`);
    const message=messages[0];
    assert.equal(message.type,'pw-watch-download',`${label}: watch message type`);
    assert.equal(message.watch.base,'http://127.0.0.1:8765',`${label}: watch must target the same base as the save`);
    assert.equal(message.watch.pageUrl,original,`${label}: pageUrl must be the article URL`);
    assert.equal(typeof message.watch.at,'number',`${label}: watch needs a timestamp for the 15-minute TTL`);
    assert.equal(typeof message.watch.meta.title,'string',`${label}: metadata is carried over`);
    assert.equal(message.watch.meta.page_html,undefined,`${label}: page_html must not enter chrome.storage (2MB quota)`);
    assert.equal(message.watch.meta.pdf_base64,undefined,`${label}: pdf_base64 must not enter chrome.storage`);
    assert.equal(message.watch.meta.pdf_cookies,undefined,`${label}: cookies must not enter chrome.storage`);
    assert.equal(posts.length,1,`${label}: the save itself still goes through`);
    assert.match(html,/已自动开始监听/,`${label}: the popup must tell the user to click Download PDF once`);
  }
});

test('a successful grab does not arm the download watch',async()=>{
  const {messages,html}=await saveFixture();
  assert.equal(messages.length,0,'there is nothing left to catch, so no watch may be armed');
  assert.doesNotMatch(html,/已自动开始监听/,'the auto-watch hint must not appear when a PDF was captured');
});

/* 弹窗精简后只留「论文名」+ 二选一模式，且默认不预选。 */
test('choosing "AI 精读" sets analyze=true on the payload',async()=>{
  const {posts,error}=await saveFixture({saveMode:'analyze'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].analyze,true);
});

test('choosing "直接归档" keeps analyze=false on the payload',async()=>{
  const {posts,error}=await saveFixture({saveMode:'archive'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].analyze,false);
});

test('no save mode selected blocks the save before the tab is touched',async()=>{
  const {posts,error,calls,tabReads}=await saveFixture({saveMode:null});
  assert.equal(posts.length,0,'nothing may be submitted until the user picks a mode');
  assert.equal(calls.length,0,'no PDF probe may run before the mode is chosen');
  assert.equal(tabReads.length,0,'the tab must not be read before the mode is chosen');
  assert.match(error,/先选一个/);
});

test('the visible title box stays in sync with the hidden form and the payload',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  const meta={title:'Meta Title',url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',original+'/valid']};
  const fixture=await saveFixture({saveMode:'archive',meta});
  await fixture.domReady();
  for(let i=0;i<50&&fixture.listenerCount('paper-title','input')===0;i+=1)await new Promise((resolve)=>setTimeout(resolve,10));
  assert.ok(fixture.listenerCount('paper-title','input')>0,'init must bind the title box input handler');
  assert.equal(fixture.node('paper-title').textContent,'Meta Title','fillForm must prefill the visible title box');
  fixture.node('paper-title').textContent='Renamed Paper';
  fixture.fire('paper-title','input');
  assert.equal(fixture.node('f-title').value,'Renamed Paper','editing the title box must sync into the hidden form field');
  await fixture.run();
  assert.equal(fixture.posts.length,2,'the second save must reach the server');
  assert.equal(fixture.posts.at(-1).title,'Renamed Paper','the payload must use the edited title');
});

/* 「直接归档」= 用户自己选归档路径：下拉框的值按 " / " 拆成 category_path。 */
test('archiving with a chosen path sends category_path as a string array',async()=>{
  const {posts,error}=await saveFixture({saveMode:'archive',archivePath:'光学工程 / 偏振成像'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].analyze,false);
  assert.ok('category_path' in posts[0],'a chosen path must be sent as category_path');
  assert.deepEqual(JSON.parse(JSON.stringify(posts[0].category_path)),['光学工程','偏振成像']);
});

test('archiving without a chosen path omits category_path',async()=>{
  const {posts,error}=await saveFixture({saveMode:'archive'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal('category_path' in posts[0],false,'the default directory must not send a path');
  assert.equal(posts[0].analyze,false);
});

test('analyze mode never sends category_path even if a path is chosen',async()=>{
  const {posts,error}=await saveFixture({saveMode:'analyze',archivePath:'光学工程 / 偏振成像'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].analyze,true);
  assert.equal('category_path' in posts[0],false,'AI 精读后归档 has no archive path');
});

test('the archive path picker is hidden for AI 精读 and revealed by 直接归档',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  const meta={title:'Meta Title',url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',original+'/valid']};
  const fixture=await saveFixture({saveMode:'analyze',archivePath:'光学工程 / 偏振成像',meta});
  await fixture.domReady();
  for(let i=0;i<50&&fixture.listenerCount('paper-title','input')===0;i+=1)await new Promise((resolve)=>setTimeout(resolve,10));
  assert.ok(fixture.hasClass('archive-path-wrap','hidden'),'the path picker must start hidden while AI 精读 is selected');
  fixture.saveRadios.find(radio=>radio.value==='analyze').checked=false;
  fixture.saveRadios.find(radio=>radio.value==='archive').checked=true;
  fixture.fireSaveModeChange('archive');
  assert.equal(fixture.hasClass('archive-path-wrap','hidden'),false,'choosing 直接归档 must reveal the path picker');
  await fixture.run();
  assert.equal(fixture.posts.length,2);
  assert.equal(fixture.posts.at(-1).analyze,false);
  assert.ok('category_path' in fixture.posts.at(-1),'the revealed picker value must be sent');
  assert.deepEqual(JSON.parse(JSON.stringify(fixture.posts.at(-1).category_path)),['光学工程','偏振成像'],
    'the path chosen in the revealed picker must reach the payload');
});

/* DNR 会话规则：扩展自己的 fetch 才能带 Referer（downloads.download 会抛
   Unsafe request header name，且 DNR 不作用于它）。规则必须「装一次、撤一次」。 */
function assertRefererRuleLifecycle(rules, pageUrl){
  assert.equal(rules.length,2,`expected install+remove, got ${JSON.stringify(rules)}`);
  const install=rules[0];
  assert.ok(Array.isArray(install.removeRuleIds)&&install.removeRuleIds.includes(9001),'install must clear stale id 9001 first');
  assert.ok(Array.isArray(install.addRules)&&install.addRules.length===1,'install must add exactly one rule');
  const rule=install.addRules[0];
  assert.equal(rule.id,9001,'rule id must stay 9001 so removal can find it');
  assert.equal(rule.action&&rule.action.type,'modifyHeaders');
  const headers=(rule.action&&rule.action.requestHeaders)||[];
  assert.equal(headers.length,1,'only the Referer header may be touched');
  assert.equal(headers[0].header,'Referer');
  assert.equal(headers[0].operation,'set');
  assert.equal(headers[0].value,pageUrl,'Referer must be the article page, not the PDF URL');
  const domains=(rule.condition&&rule.condition.requestDomains)||[];
  assert.ok(domains.includes('sciencedirect.com'),'rule must cover sciencedirect.com');
  assert.ok(domains.includes('sciencedirectassets.com'),'rule must cover the PDF CDN');
  for(const type of ['main_frame','sub_frame','xmlhttprequest','other']){
    assert.ok((rule.condition.resourceTypes||[]).includes(type),`rule must cover ${type} (fetch is xmlhttprequest)`);
  }
  const removal=rules.at(-1);
  assert.deepEqual(JSON.parse(JSON.stringify(removal.removeRuleIds)),[9001],'the last call must remove the rule');
  assert.equal(removal.addRules,undefined,'removal must not add anything');
}

test('the referer rule is installed for the PDF stage and removed afterwards',async()=>{
  for(const options of [{},{blocked:true}]){
    const label=JSON.stringify(options);
    const {rules,original,posts}=await saveFixture(options);
    assertRefererRuleLifecycle(rules,original);
    assert.equal(posts.length,1,`${label}: the save still goes through`);
  }
});

test('the referer rule is removed even when the PDF stage throws',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  // pdf_candidates 是数字：捕获取值时的展开会在 try 里抛 TypeError，finally 必须撤规则。
  const hostile={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:42};
  const {rules,error,posts}=await saveFixture({meta:hostile});
  assertRefererRuleLifecycle(rules,original);
  assert.ok(error&&!error.includes('[object Object]'),`the throw must surface as readable text, got: ${error}`);
  assert.equal(posts.length,0,'the broken PDF stage aborts before POST');
});

test('an old browser without declarativeNetRequest still saves without errors',async()=>{
  const {rules,posts,error}=await saveFixture({dnr:false});
  assert.equal(rules.length,0,'no rule may be attempted when the API is missing');
  assert.equal(posts.length,1);
  assert.equal(error,'');
});

/* CDP 调试通道：挂后台标签页，在 Fetch.requestPaused 的 Response 阶段读正文。
   实测 Network.getResponseBody / PDFViewerApplication 都不行，所以固定 Fetch 域。
   Page.navigate 必须带 referrer（文章页 URL）——真机成败点，缺了 SD 只回 init.htm。 */
test('CDP Fetch captures the PDF bytes, detaches and closes the probe tab',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  const pdfft=original+'/pdfft?md5=abc';
  const meta={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',pdfft]};
  const timers=trackedTimers();
  const {posts,debugCalls,tabsCreated,debugBase64,error}=await saveFixture({injectionFailed:true,meta,debuggerApi:true,timers});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(timers.pending.size,0,'the body timeout timer must be cleared once the PDF body is captured');
  const payload=posts[0];
  assert.equal(payload.pdf_base64,debugBase64,'the CDP response body must become payload.pdf_base64');
  assert.equal(payload.pdf_name,'Paper.pdf');
  assert.ok(payload.pdf_candidates.includes(pdfft),'the signed URL must stay in the candidate list');
  const methods=debugCalls.map(c=>c[1]);
  for(const method of ['Fetch.enable','Fetch.getResponseBody','Fetch.continueRequest']){
    assert.ok(methods.includes(method),`${method} must be called on the debugger session`);
  }
  const navigations=debugCalls.filter(c=>c[1]==='Page.navigate');
  assert.ok(navigations.length>=1,'the probe tab must be navigated');
  assert.equal(navigations[0][2],pdfft,'the /pdfft candidate must be probed before the landing page');
  assert.equal(navigations[0][3],original,'Page.navigate must carry the article page as referrer (the real-world make-or-break)');
  assert.ok(methods.indexOf('Fetch.enable')<methods.indexOf('Page.navigate'),'Fetch must be enabled before navigating');
  const attach=debugCalls.find(c=>c[0]==='attach');
  assert.ok(attach,'the debugger must attach before probing');
  assert.equal(attach[2],'1.3','attach to the stable CDP version');
  const detach=debugCalls.find(c=>c[0]==='detach');
  assert.ok(detach,'the debugger must detach after probing');
  assert.equal(detach[1],attach[1],'detach must target the same tab as attach');
  assert.ok(tabsCreated.includes('about:blank'),'the probe tab starts from about:blank');
  assert.ok(tabsCreated.includes('removed:'+attach[1]),'the probe tab must be closed afterwards');
});

test('without chrome.debugger the save still completes without errors',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  const meta={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad']};
  const {posts,debugCalls,error,messages}=await saveFixture({injectionFailed:true,meta,debuggerApi:false});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].pdf_base64,undefined,'nothing can be captured without the debugger API');
  assert.equal(debugCalls.length,0,'no debugger call may be attempted');
  assert.equal(messages.length,1,'the download watch still arms when no PDF could be captured');
});

/* 真机教训：出版社常用 200 + text/html（或把 HTML 挂在 .pdf 地址上）冒充 PDF。
   CDP 捕获必须校验正文是 %PDF，否则服务端会回 invalid_pdf。 */
test('CDP keeps looking when the captured body is HTML instead of a PDF',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  const pdfft=original+'/pdfft?md5=abc';
  const meta={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',pdfft]};
  const htmlUrl=original+'/download/paper.pdf?token=x';
  const signedUrl=original+'/signed/paper.pdf';
  const htmlBase64=Buffer.from('<html><body>Preparing your download…</body></html>','utf8').toString('base64');
  const timers=trackedTimers();
  const {posts,debugCalls,debugBase64,error}=await saveFixture({injectionFailed:true,meta,debuggerApi:true,timers,
    debugResponses:[
      {url:htmlUrl,contentType:'text/html',body:htmlBase64,status:200},
      {url:signedUrl,contentType:'application/pdf',body:'JVBERi0xLjcg'+ 'A'.repeat(1400),status:200},
    ]});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].pdf_base64,debugBase64,'only the real %PDF body may become payload.pdf_base64');
  const attempts=posts[0].pdf_attempts||[];
  const htmlAttempt=attempts.find(a=>a.route==='browser-extension'&&a.code==='not_pdf'&&a.url===original+'/download/paper.pdf');
  assert.ok(htmlAttempt,`the HTML response must be reported as not_pdf, got ${JSON.stringify(attempts)}`);
  const bodyCalls=debugCalls.filter(c=>c[1]==='Fetch.getResponseBody');
  assert.equal(bodyCalls.length,2,'both paused responses must be read (HTML first, PDF second)');
  assert.equal(debugCalls.filter(c=>c[1]==='Fetch.continueRequest').length,2,'both paused requests must be continued');
  assert.equal(timers.pending.size,0,'successful capture must clear the body timeout');
  assert.equal(debugCalls.filter(c=>c[1]==='Page.navigate')[0][3],original,'the navigation must still carry the article referrer');
});

test('CDP reports what it actually captured and never passes HTML off as a PDF',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  const pdfft=original+'/pdfft?md5=abc';
  const meta={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',pdfft]};
  const htmlUrl=original+'/download/paper.pdf?token=x';
  const htmlBase64=Buffer.from('<!doctype html><title>Preparing your download</title>','utf8').toString('base64');
  const timers=cappedTimers();
  const {posts,html,error,messages}=await saveFixture({injectionFailed:true,meta,debuggerApi:true,timers,
    debugResponses:[{url:htmlUrl,contentType:'text/html',body:htmlBase64,status:200}]});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].pdf_base64,undefined,'an HTML body must never be uploaded as the PDF');
  assert.match(html,/调试通道/,'the popup must explain what the debugger channel captured');
  assert.match(html,/捕获到的不是 PDF/);
  assert.match(html,/\d+ 字节/,'the note must include the captured byte count');
  assert.equal(messages.length,1,'a failed auto-capture still arms the download watch');
  assert.equal(messages[0].type,'pw-watch-download');
});

/* 真机现象（ScienceDirect PII S0030401819300987）：/pdfft 和 assets/main.pdf 都回 200 + HTML
   （5 万多字节，正文以 <!doct 开头 = 出版社的安全验证页）。这时必须停手并让用户先去浏览器过验证，
   而不是继续换候选、空转整个超时时间。 */
test('a publisher verification page stops the debugger channel and asks the user to verify first',async()=>{
  const original='https://proxy.test/science/article/pii/S0030401819300987';
  const pdfft=original+'/pdfft';
  const assets='https://cdn.proxy.test/271557/1-s2.0-S0030401819X00048/1-s2.0-S0030401819300987/main.pdf';
  const meta={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[pdfft,assets]};
  const verificationHtml='<!doctype html><html><head><title>Security verification</title></head><body>Please complete the security check. Just a moment…</body></html>';
  const body=Buffer.from(' '.repeat(2000)+verificationHtml,'utf8').toString('base64');
  const timers=trackedTimers();
  const {posts,html,error,debugCalls}=await saveFixture({injectionFailed:true,meta,debuggerApi:true,timers,
    debugResponses:[{url:assets,contentType:'text/html',body,status:200}]});
  assert.equal(error,'');
  assert.equal(posts[0].pdf_base64,undefined,'a verification page must never be uploaded as the PDF');
  const attempts=posts[0].pdf_attempts||[];
  assert.ok(attempts.some(a=>a.code==='needs_verification'&&a.route==='browser-extension'),
    `the verification page must be reported as needs_verification, got ${JSON.stringify(attempts)}`);
  assert.match(html,/安全验证/, 'the popup must tell the user the publisher asked for verification');
  const navigations=debugCalls.filter(c=>c[1]==='Page.navigate');
  assert.equal(navigations.length,1,'once a verification page is seen the plugin must stop trying other candidates');
  assert.equal(timers.pending.size,0,'the body timeout must be cleared when we stop early');
});

test('an html redirect served by the download endpoint is followed to the real pdf',async()=>{
  const original='https://proxy.test/science/article/pii/S0030401819300987';
  const pdfft=original+'/pdfft';
  const signed='https://cdn.proxy.test/signed/paper.pdf';
  const meta={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',pdfft]};
  const redirectHtml='<html><head><script>location.replace("'+signed+'")</script></head><body>Redirecting…</body></html>';
  const redirectBase64=Buffer.from(redirectHtml,'utf8').toString('base64');
  const pdfBase64='JVBERi0xLjcg'+ 'A'.repeat(1400);
  const timers=trackedTimers();
  const {posts,error,debugCalls}=await saveFixture({injectionFailed:true,meta,debuggerApi:true,timers,
    debugResponses:[{url:pdfft,contentType:'text/html',body:redirectBase64,status:200}],
    debugResponsesNext:[{url:signed,contentType:'application/pdf',body:pdfBase64,status:200}]});
  assert.equal(error,'');
  assert.ok(posts[0].pdf_base64,'the followed redirect must yield the real PDF');
  assert.equal(posts[0].pdf_base64,pdfBase64);
  const navigated=debugCalls.filter(c=>c[1]==='Page.navigate').map(c=>c[2]);
  assert.ok(navigated.includes(signed),`must navigate to the redirect target, got ${JSON.stringify(navigated)}`);
  assert.equal(timers.pending.size,0,'no timer may leak after following a redirect');
});

test('a queued server response arms the watch after POST when the capture did not land',async()=>{
  const original='https://proxy.test/science/article/pii/S1234567890';
  const pdfft=original+'/pdfft?md5=abc';
  const meta={url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/bad',pdf_candidates:[original+'/bad',pdfft]};
  const timers=trackedTimers();
  const {posts,messages,html,error}=await saveFixture({injectionFailed:true,meta,debuggerApi:true,timers,postPdf:'queued'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.ok(posts[0].pdf_base64,'the CDP capture did land, so the pre-send watch was skipped');
  assert.equal(messages.length,1,'the post-POST fallback must arm the watch exactly once');
  assert.equal(messages[0].type,'pw-watch-download');
  assert.equal(messages[0].watch.base,'http://127.0.0.1:8765');
  assert.equal(messages[0].watch.pageUrl,original);
  assert.equal(messages[0].watch.meta.pdf_base64,undefined,'the heavy base64 must not be stored in chrome.storage');
  assert.match(html,/已自动开始监听/);
  for(const id of [...timers.pending])clearTimeout(id);
});

test('manifest keeps the declarativeNetRequest permission',()=>{
  const manifest=JSON.parse(readFileSync(new URL('../extension/manifest.json',import.meta.url),'utf8'));
  assert.equal(manifest.manifest_version,3);
  assert.ok(Array.isArray(manifest.permissions),'manifest.permissions must be an array');
  assert.ok(manifest.permissions.includes('declarativeNetRequest'),
    'the referer rule silently stops working if this permission is dropped');
  assert.ok(manifest.permissions.includes('debugger'),
    'the CDP capture path silently stops working if this permission is dropped');
});

test('a publisher verification page stops the probes instead of hammering the site',async()=>{
  const {calls,posts,html}=await saveFixture({blocked:true});
  assert.equal(posts.length,1);
  assert.equal(posts[0].pdf_base64,undefined);
  assert.equal(posts[0].pdf_download_path,undefined);
  assert.equal(posts[0].pdf_cookies,undefined);
  assert.equal(calls.filter(c=>c.func==='fetchPdfInPage').length,1,'remaining candidates and tab probes are skipped after a challenge page');
  assert.match(html,/安全验证/);
});


test('extension cancels huge HTML streams before parsing',async()=>{
  let cancelled=false;
  const context=vm.createContext({AbortController,setTimeout,clearTimeout,Uint8Array,TextDecoder,btoa,
    fetch:async()=>({ok:true,headers:new Headers(),body:{getReader:()=>({
      read:async()=>({done:false,value:new Uint8Array(1024*1024)}),cancel:async()=>{cancelled=true;}
    })}})});
  vm.runInContext(source.slice(start,end)+'\nglobalThis.grab=fetchPdfInPage;',context);
  assert.equal(await context.grab('https://example.org/html'),'');assert.equal(cancelled,true);
});

function diagnosticFixture(fetch, extra={}) {
  const context=vm.createContext({AbortController,setTimeout,clearTimeout,Uint8Array,TextDecoder,btoa,URL,Date,fetch,...extra});
  vm.runInContext(source.slice(start,end)+'\nglobalThis.grab=fetchPdfInPage;',context);
  return context.grab;
}

test('PDF diagnostics report HTTP failures and redact URL credentials and tokens',async()=>{
  for(const [status,code] of [[403,'access_denied'],[429,'rate_limited'],[503,'server_error'],[404,'http_error']]) {
    const grab=diagnosticFixture(async()=>new Response('',{status}));
    const result=await grab('https://user:password@example.org/file.pdf?token=secret#private',Date.now()+1000,'',true);
    assert.equal(result.base64,'');
    assert.equal(result.attempts[0].code,code);assert.equal(result.attempts[0].status,status);
    assert.equal(result.attempts[0].url,'https://example.org/file.pdf');
    assert.doesNotMatch(JSON.stringify(result),/secret|password|private/);
  }
});

test('PDF diagnostics preserve ambiguity for browser network errors and classify bounds',async()=>{
  const failed=await diagnosticFixture(async()=>{throw new TypeError('CORS token=secret');})('https://example.org/a',Date.now()+1000,'',true);
  assert.equal(failed.attempts[0].code,'network_error');assert.doesNotMatch(JSON.stringify(failed),/CORS|secret/);
  const expired=await diagnosticFixture(async()=>{throw new Error('must not fetch');})('https://example.org/a',1,'',true);
  assert.equal(expired.attempts[0].code,'timeout');
  const huge=await diagnosticFixture(async()=>new Response('',{headers:{'content-length':100*1024*1024}}))('https://example.org/a',Date.now()+1000,'',true);
  assert.equal(huge.attempts[0].code,'too_large');
  const short=await diagnosticFixture(async()=>new Response('%PDF-1.7'))('https://example.org/a',Date.now()+1000,'',true);
  assert.equal(short.attempts[0].code,'not_pdf');
});

test('save sends bounded sanitized browser traces and displays backend warnings safely',async()=>{
  const {posts,html}=await saveFixture({diagnostics:true});
  assert.equal(posts.length,1);
  const attempts=posts[0].pdf_attempts;
  assert.ok(attempts.length<=24);
  assert.ok(attempts.some(a=>a.route==='browser-page'&&a.code==='access_denied'&&a.status===403));
  assert.ok(attempts.some(a=>a.route==='browser-extension'&&a.code==='network_error'));
  assert.doesNotMatch(JSON.stringify(attempts),/token|secret|private|CORS/);
  assert.match(html,/HTTP 403/);assert.match(html,/identity_mismatch/);assert.match(html,/Wrong article/);
  assert.doesNotMatch(html,/<script>|<img/);assert.match(html,/&lt;script&gt;/);assert.match(html,/&lt;img/);
});

test('executeScript failure produces a page attempt and still tries extension fallback',async()=>{
  const {posts}=await saveFixture({manual:true,injectionFailed:true});
  assert.equal(posts.length,1);
  assert.deepEqual(posts[0].pdf_attempts.map(a=>[a.route,a.code]),[['browser-page','network_error'],['browser-extension','network_error']]);
});

async function captureFixture({mode='iframe',page='sciencedirect',timers=null}={}) {
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',checked:false,textContent:'',innerHTML:'',classList:{add(){},remove(){}},addEventListener(){}});return nodes.get(id);};
  const original=page==='sciencedirect'
    ?'https://www.sciencedirect.com/science/article/pii/S0143816619313120'
    :'https://onlinelibrary.wiley.com/doi/10.1000/test';
  const resolved='https://pdf.sciencedirectassets.com/signed/paper.pdf';
  const events=[],posts=[],downloads=[],discards=[];
  const headers=[],updated=[];
  // Content-Disposition: attachment 的 HTML（init.htm/pdfft.htm）是 SD 那条链路的典型产物。
  const attachmentUrl=original+'/pdfft?md5=abc&pid=1-s2.0-S0143816619313120-main.pdf';
  const timing={createdAt:0,removedAt:0};
  const chromeApi={
    runtime:{},
    tabs:{
      get:async()=>({url:original}),
      update:async(id,opts)=>{events.push(['tabs.update',opts.url]);},
      create:async opts=>{events.push(['tabs.create',opts.url]);timing.createdAt=Date.now();
        if(mode==='tab'||mode==='found-html')setTimeout(()=>{for(const f of [...updated])f(9,{url:resolved});},0);
        if(mode==='html-attachment')setTimeout(()=>{for(const f of [...headers])f({tabId:9,type:'sub_frame',url:attachmentUrl,responseHeaders:[
          {name:'Content-Type',value:'text/html'},
          {name:'Content-Disposition',value:'attachment; filename="init.htm"'},
        ]});},0);
        return {id:9};},
      remove:async id=>{events.push(['tabs.remove',id]);},
      onUpdated:{addListener:f=>updated.push(f),removeListener:f=>{const i=updated.indexOf(f);if(i>=0)updated.splice(i,1);}},
    },
    scripting:{executeScript:async options=>{
      const name=options.func?.name||'';
      if(name==='extractPageMetadata')return [{result:{url:original,user_agent:'Synthetic Browser/1.0',doi:'10.1000/test',pdf_url:original+'/pdf',pdf_candidates:[original+'/pdf']}}];
      if(name==='fetchPdfInPage')return [{result:{base64:'',attempts:[{url:options.args[0],code:'not_pdf'}]}}];
      events.push(['script',options.args?.length||0]);
      if(mode==='iframe'&&options.args?.length===2){
        setTimeout(()=>{for(const f of [...headers])f({tabId:7,type:'sub_frame',url:resolved,responseHeaders:[{name:'Content-Type',value:'application/pdf'}]});},0);
        return [{result:null}];
      }
      if(['tab','html-attachment','found-html'].includes(mode)&&options.args?.length===2)throw new Error('inline frame is blocked by the site');
      return [{result:null}];
    }},
    cookies:{getAll:async()=>[]},
    downloads:{
      onCreated:{addListener:f=>{if(mode!=='html-attachment')setTimeout(()=>f({id:99,filename:'C:\\Users\\tester\\Downloads\\paper.pdf'}),0);
        else setTimeout(()=>f({id:99,filename:'C:\\Users\\tester\\Downloads\\init.htm'}),0);},removeListener(){}},
      download:async url=>{downloads.push(url);return 42;},
      search:async query=>{const id=(query&&typeof query==='object')?query.id:query;return [{id,state:'complete',filename:mode==='found-html'?'C:\\Users\\tester\\Downloads\\pdfft.htm':'C:\\Users\\tester\\Downloads\\paper.pdf'}];},
      cancel:async id=>{discards.push(['cancel',id]);},
      removeFile:async id=>{discards.push(['removeFile',id]);},
      erase:async query=>{discards.push(['erase',query&&query.id]);},
    },
    webRequest:{onHeadersReceived:{addListener:f=>headers.push(f),removeListener:f=>{const i=headers.indexOf(f);if(i>=0)headers.splice(i,1);if(!timing.removedAt)timing.removedAt=Date.now();}}},
  };
  const context=vm.createContext({AbortController,setTimeout:timers?timers.setTimeout:setTimeout,clearTimeout:timers?timers.clearTimeout:clearTimeout,Uint8Array,TextDecoder,btoa,atob,URL,Date,
    document:{getElementById:node,addEventListener(){},createElement:()=>({style:{}}),body:{appendChild(){}}},
    chrome:chromeApi,
    fetch:async(url,options)=>{if(url.includes('/api/import/web')){posts.push(JSON.parse(options.body));return {ok:true,json:async()=>({pdf:'attached',paper:{id:'p1',title:'Paper',has_pdf:true}})};}return {ok:false};}});
  vm.runInContext(source,context);
  vm.runInContext(`state.base='http://127.0.0.1:8765';state.tab={id:7,url:${JSON.stringify(original)}};state.meta={doi:'10.1000/test',pdf_url:${JSON.stringify(original+'/pdf')}};`,context);
  node('f-title').value='Paper';node('f-pdf').value=original+'/pdf';node('opt-pdf').checked=true;
  await context.save();
  return {events,posts,downloads,discards,error:node('error').textContent,original,resolved,attachmentUrl,timing};
}

test('hidden iframe capture sniffs a signed PDF and imports the downloaded file',async()=>{
  const {events,posts,downloads,error,original}=await captureFixture({mode:'iframe',page:'wiley'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].pdf_download_path,'C:\\Users\\tester\\Downloads\\paper.pdf');
  assert.equal(posts[0].pdf_base64,undefined);
  assert.equal(posts[0].pdf_cookies,undefined);
  assert.deepEqual(posts[0].pdf_candidates,[original+'/pdf']);
  assert.equal(downloads.length,1);
  assert.ok(events.some(e=>e[0]==='script'&&e[1]===2),'inline frame is injected');
  assert.deepEqual(events.at(-1),['script',1],'inline frame is removed after the attempt');
});

test('tab capture probes in a background tab and never hijacks the article tab',async()=>{
  const {events,posts,error,original}=await captureFixture({mode:'tab'});
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.equal(posts[0].pdf_download_path,'C:\\Users\\tester\\Downloads\\paper.pdf');
  assert.equal(posts[0].pdf_url,original+'/pdf');
  assert.ok(events.some(e=>e[0]==='tabs.create'&&e[1]===original+'/pdf'),'the probe runs in a new background tab');
  assert.ok(!events.some(e=>e[0]==='tabs.update'),'the article tab must stay where the user left it');
  assert.ok(events.some(e=>e[0]==='tabs.remove'),'the probe tab is closed afterwards');
  assert.ok(!events.some(e=>e[0]==='script'&&e[1]===2),'ScienceDirect never gets the hidden-frame attempt that only yields pdfft.htm');
});

test('a sniffed "PDF" that lands as HTML is reported, discarded and stops immediately',async()=>{
  const started=Date.now();
  const {events,posts,discards,downloads,error,resolved}=await captureFixture({mode:'found-html'});
  const elapsed=Date.now()-started;
  assert.equal(error,'');
  assert.equal(posts.length,1);
  assert.deepEqual(downloads.map(d=>d.url||d),[resolved],'the sniffed signed URL is the one the tab probe requests');
  const entry=(posts[0].pdf_attempts||[]).find(a=>a.route==='browser-extension'&&a.code==='not_pdf'&&a.url===resolved);
  assert.ok(entry,`expected not_pdf for ${resolved}, got ${JSON.stringify(posts[0].pdf_attempts)}`);
  assert.equal(posts[0].pdf_download_path,undefined,'pdfft.htm must never be imported as the PDF');
  for(const op of ['cancel','removeFile','erase']){
    assert.ok(discards.some(d=>d[0]===op&&d[1]===42),`${op} must be called for download 42: ${JSON.stringify(discards)}`);
  }
  assert.equal(discards.length,3,'found-path must discard once and continue before the created-download race');
  assert.ok(events.some(e=>e[0]==='tabs.remove'),'the probe tab must still be closed');
  assert.ok(elapsed<1500,`found-path must continue instead of waiting the created-download race (took ${elapsed}ms)`);
});

test('an HTML attachment (init.htm) stops the tab probe and is reported as not_pdf',async()=>{
  const started=Date.now();
  const {events,posts,discards,error,original,attachmentUrl,timing}=await captureFixture({mode:'html-attachment'});
  const elapsed=Date.now()-started;
  assert.equal(error,'');
  assert.equal(posts.length,1);
  const expectedUrl=original+'/pdfft';
  const attempts=posts[0].pdf_attempts||[];
  const entry=attempts.find(a=>a.route==='browser-extension'&&a.code==='not_pdf'&&a.url===expectedUrl);
  assert.ok(entry,`expected a browser-extension not_pdf attempt for ${expectedUrl}, got ${JSON.stringify(attempts)}`);
  assert.equal(attempts.filter(a=>a.url===expectedUrl).length,1,'the same attachment must not be reported twice');
  assert.equal(posts[0].pdf_download_path,undefined,'init.htm must never be imported as the PDF');
  assert.equal(discards.length,0,'the HTML attachment must stop before the created-download race (no download to discard)');
  assert.ok(events.some(e=>e[0]==='tabs.remove'),'the probe tab must still be closed after the HTML attachment');
  assert.ok(elapsed<1500,`the probe must stop as soon as the attachment is seen instead of racing the timeout (took ${elapsed}ms)`);
  assert.ok(timing.removedAt>0&&timing.removedAt-timing.createdAt<3000,
    `the webRequest listener must be cleaned up as soon as the attachment arrives (create=${timing.createdAt} remove=${timing.removedAt})`);
});

/* 无信号那次（waitForPdfResponse 一直等满）在生产里才会走到 createdPromise 分支，
   集成测试要等 12s 会毁掉「套件 < 2s」这条护栏，所以这里单独给 capturePdfViaTab 注入
   秒回的 waitForPdfResponse，用真实的 raceTimeout/waitForCreatedDownload/discardDownload 跑。 */
const captureStart=source.indexOf('async function capturePdfViaTab(');
const captureEnd=source.indexOf('\nasync function grabPdfBase64',captureStart);
const raceStart=source.indexOf('function raceTimeout(');
const raceEnd=source.indexOf('\nfunction waitForCreatedDownload',raceStart);
const createdStart=source.indexOf('function waitForCreatedDownload(');
const createdEnd=source.indexOf('\nfunction waitForDownload',createdStart);
const discardStart=source.indexOf('async function discardDownload(');
const discardEnd=source.indexOf('\nasync function capturePdfViaTab',discardStart);

function createdDownloadFixture(){
  const discards=[],removed=[];
  const timers=trackedTimers();
  const attachmentUrl='https://www.sciencedirect.com/science/article/pii/S0143816619313120/pdfft?md5=abc&pid=1';
  const context=vm.createContext({URL,Date,setTimeout:timers.setTimeout,clearTimeout:timers.clearTimeout,
    chrome:{tabs:{create:async()=>({id:9}),remove:async id=>{removed.push(id);}},
      downloads:{
        onCreated:{addListener:f=>{setTimeout(()=>f({id:98,filename:'C:\\Users\\tester\\Downloads\\init.htm',url:attachmentUrl}),0);},removeListener(){}},
        cancel:async id=>{discards.push(['cancel',id]);},
        removeFile:async id=>{discards.push(['removeFile',id]);},
        erase:async query=>{discards.push(['erase',query&&query.id]);},
      }}});
  const code=[
    source.slice(safeStart,safeEnd),
    source.slice(raceStart,raceEnd),
    source.slice(createdStart,createdEnd),
    source.slice(discardStart,discardEnd),
    'function waitForPdfResponse(){return Promise.resolve("");}',
    'function waitForDownload(){return Promise.resolve(null);}',
    source.slice(captureStart,captureEnd),
    'globalThis.capture=capturePdfViaTab;',
  ].join('\n');
  vm.runInContext(code,context);
  return {capture:context.capture,attachmentUrl,discards,removed,timers};
}

test('a browser download that lands as HTML is reported once and discarded',async()=>{
  const started=Date.now();
  const {capture,attachmentUrl,discards,removed,timers}=await createdDownloadFixture();
  const attempts=[];
  const result=await capture(['https://www.sciencedirect.com/science/article/pii/S0143816619313120/pdfft'],Date.now()+90000,attempts);
  const elapsed=Date.now()-started;
  assert.equal(result,'','an HTML download must never be handed back as the PDF path');
  assert.equal(attempts.length,1,'exactly one not_pdf record for the created download');
  assert.deepEqual(JSON.parse(JSON.stringify(attempts[0])),{url:'https://www.sciencedirect.com/science/article/pii/S0143816619313120/pdfft',code:'not_pdf',route:'browser-extension'});
  for(const op of ['cancel','removeFile','erase']){
    assert.ok(discards.some(d=>d[0]===op&&d[1]===98),`${op} must be called for download 98: ${JSON.stringify(discards)}`);
  }
  assert.ok(removed.includes(9),'the probe tab must be closed');
  assert.equal(timers.pending.size,0,'raceTimeout must clear the losing timer instead of leaking it');
  assert.ok(elapsed<2000,`the created-download branch must not wait on leftover timers (took ${elapsed}ms)`);
});

test('capture probes do not leak timers between save attempts',async()=>{
  const started=Date.now();
  const cases=[
    ['iframe',captureFixture,{mode:'iframe',page:'wiley'}],
    ['tab',captureFixture,{mode:'tab'}],
    ['html-attachment',captureFixture,{mode:'html-attachment'}],
    ['found-html',captureFixture,{mode:'found-html'}],
    ['failed diagnostics',saveFixture,{diagnostics:true}],
    ['both probes failed',saveFixture,{manual:true,injectionFailed:true}],
  ];
  for(const [label,fixture,options] of cases){
    const timers=trackedTimers();
    await fixture({...options,timers});
    assert.equal(timers.pending.size,0,`${label}: save must leave no pending timer behind, or the popup process hangs after closing`);
  }
  const second=Date.now()-started;
  assert.ok(second<2000,`five save flows must finish fast; leaked timers used to keep the suite ~12s (took ${second}ms)`);
});


/* 回归：2026-10-28 用户保存报 422，服务端 loc=["body","pdf_base64"]。
   根因是诊断对象（{base64,attempts,blocked}）被当成 base64 文本塞进 payload。
   下面两组测试分别锁住「grabPdfBase64 只返回字符串」和「POST 出去的 pdf_base64 只能是字符串或不在」。 */

const grabStart=source.indexOf('async function grabPdfBase64(');
const grabEnd=source.indexOf('\nfunction escapeHtml',grabStart);
const safeStart=source.indexOf('function safeAttemptUrl(');
const safeEnd=source.indexOf('\nfunction pdfEvidenceUrl',safeStart);
const pageFetchStart=source.indexOf('async function fetchPdfBase64(');
const pageFetchEnd=source.indexOf('\nasync function cookieHeader',pageFetchStart);

function grabGrab(scriptResult){
  const context=vm.createContext({AbortController,setTimeout,clearTimeout,Uint8Array,TextDecoder,btoa,URL,Date,
    fetch:async()=>({ok:false,status:403,headers:{get:()=>null},body:null}),
    chrome:{tabs:{get:async()=>({url:'https://example.org/a'})},scripting:{executeScript:async()=>[{result:scriptResult}]}}});
  context.state={tab:{id:7,url:'https://example.org/a'},pdfBlocked:false};
  vm.runInContext(source.slice(safeStart,safeEnd)+source.slice(pageFetchStart,pageFetchEnd)+source.slice(grabStart,grabEnd)+'\nglobalThis.grab=grabPdfBase64;',context);
  return {context,grab:context.grab};
}

test('grabPdfBase64 never hands a diagnostics object back as base64 text',async()=>{
  const deadline=Date.now()+60000;
  const base64='JVBERi0xLjcg'+'A'.repeat(1400);
  // 页面探测返回诊断对象（blocked）：必须降级成空字符串，而不是 {attempts,base64,blocked}
  const blocked=grabGrab({base64:'',attempts:[{url:'https://example.org/a',code:'access_denied',status:403}],blocked:true});
  assert.equal(await blocked.grab('https://example.org/a',deadline),'');
  assert.equal(blocked.context.state.pdfBlocked,true,'blocked 标记仍要保留');
  // 有 base64 的诊断对象：只取 base64 字段
  const withBase64=grabGrab({base64,attempts:[]});
  assert.equal(await withBase64.grab('https://example.org/a',deadline),base64);
  // executeScript 直接返回裸字符串
  const plain=grabGrab(base64);
  assert.equal(await plain.grab('https://example.org/a',deadline),base64);
  // 没有 base64 的诊断对象：返回空字符串，绝不返回对象
  const noBase64=grabGrab({attempts:[{url:'https://example.org/a',code:'not_pdf'}]});
  const value=await noBase64.grab('https://example.org/a',deadline);
  assert.equal(typeof value,'string');
  assert.equal(value,'');
  for(const [label,result] of [['blocked',blocked],['withBase64',withBase64],['plain',plain],['noBase64',noBase64]]){
    const returned=await result.grab('https://example.org/a',deadline);
    assert.equal(typeof returned,'string',`${label}: grabPdfBase64 must return a string`);
  }
});

test('the posted payload.pdf_base64 is always absent or a string',async()=>{
  const cases=[
    ['普通成功',{}],
    ['blocked 诊断对象',{blocked:true}],
    ['失败诊断对象',{diagnostics:true}],
    ['两条探测都失败',{manual:true,injectionFailed:true}],
  ];
  for(const [label,options] of cases){
    const {posts,error}=await saveFixture(options);
    assert.equal(posts.length,1,`${label}: must still POST once`);
    const payload=posts[0];
    const value=payload.pdf_base64;
    assert.ok(value===undefined||typeof value==='string',
      `${label}: pdf_base64 must be undefined or string, got ${Object.prototype.toString.call(value)}`);
    assert.equal(JSON.stringify(value),JSON.stringify(value),`${label}: pdf_base64 must survive a JSON round-trip`);
    if(payload.pdf_name!==undefined)assert.equal(typeof payload.pdf_name,'string',`${label}: pdf_name must be a string when sent`);
    assert.equal(typeof payload.title,'string',`${label}: title must stay a string`);
    if(label==='普通成功')assert.ok(value&&value.startsWith('JVBERi0xLjcg'),'a real PDF still uploads its base64');
    if(label!=='普通成功')assert.equal(value,undefined,`${label}: no base64 text was captured, so the field must be omitted`);
    assert.equal(error,'',`${label}: a diagnostics result must not surface as an error`);
  }
});

test('popup footer shows which Connector build is actually loaded',async()=>{
  const {createHash}=await import('node:crypto');
  const nodes=new Map();
  const node=id=>{if(!nodes.has(id))nodes.set(id,{value:'',checked:false,textContent:'',innerHTML:'',classList:{add(){},remove(){}},addEventListener(){}});return nodes.get(id);};
  let opened='';
  const context=vm.createContext({AbortController,setTimeout,clearTimeout,Uint8Array,TextDecoder,TextEncoder,btoa,URL,Date,crypto,
    document:{getElementById:node,addEventListener(){}},
    chrome:{runtime:{getManifest:()=>({version:'9.9.9'}),getURL:path=>'chrome-extension://pw/'+path}},
    fetch:async url=>{opened=url;return {text:async()=>source};}});
  vm.runInContext(source,context);
  await context.showBuildStamp();
  assert.equal(opened,'chrome-extension://pw/popup.js','the stamp hashes the popup file that Chrome actually loaded');
  const expected=createHash('sha256').update(source,'utf8').digest('hex').slice(0,8);
  assert.equal(node('build-stamp').textContent,`Connector v9.9.9 · popup.js ${expected}`);
  assert.ok(node('build-stamp').textContent.length<80,'the stamp stream is short enough for a phone-sized hint line');
});
