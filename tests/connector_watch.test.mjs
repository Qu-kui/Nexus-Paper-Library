import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

const source=readFileSync(new URL('../extension/background.js',import.meta.url),'utf8');
const PAGE='https://www.sciencedirect.com/science/article/pii/S0143816619313120';

function fixture({watchAt=Date.now(),downloadState='complete',downloadFilename='C:\\Users\\tester\\Downloads\\paper.pdf',fetchImpl}={}) {
  const stored={pwWatch:{base:'http://127.0.0.1:8765',meta:{title:'Real Paper',doi:'10.1000/test'},pageUrl:PAGE,at:watchAt}};
  const posts=[];
  let createdListener=null;
  const context=vm.createContext({
    setTimeout,clearTimeout,Date,
    fetch:fetchImpl||(async(url,options)=>{
      posts.push({url,body:JSON.parse(options.body)});
      return {ok:true,json:async()=>({pdf:'attached',paper:{id:'p1',title:'Real Paper',has_pdf:true}})};
    }),
    chrome:{
      storage:{local:{
        get:async keys=>{const out={};for(const key of (Array.isArray(keys)?keys:[keys]))if(key in stored)out[key]=stored[key];return out;},
        set:async obj=>Object.assign(stored,obj),
        remove:async key=>{delete stored[key];},
      }},
      downloads:{
        onCreated:{addListener:listener=>{createdListener=listener;}},
        search:async id=>[{id,state:downloadState,filename:downloadFilename}],
      },
      runtime:{onMessage:{addListener(){}}},
    },
  });
  vm.runInContext(source,context);
  return {
    stored,
    posts,
    trigger:async item=>{assert.ok(createdListener,'background.js must listen for browser downloads');return createdListener(item);},
  };
}

test('a manually downloaded PDF is imported into the matching paper',async()=>{
  const {stored,posts,trigger}=fixture();
  await trigger({id:7,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://pdf.sciencedirectassets.com/signed/paper.pdf'});
  assert.equal(posts.length,1);
  assert.equal(posts[0].body.pdf_download_path,'C:\\Users\\tester\\Downloads\\paper.pdf');
  assert.equal(posts[0].body.url,PAGE);
  assert.equal(posts[0].body.title,'Real Paper');
  assert.equal(posts[0].body.download_pdf,false);
  assert.equal(posts[0].body.analyze,false);
  assert.equal(stored.pwWatchResult.status,'ok');
  assert.equal(stored.pwWatchResult.paperId,'p1');
  assert.equal('pwWatch' in stored,false);
});

test('downloads that are not PDFs are ignored and the watch stays armed',async()=>{
  const {stored,posts,trigger}=fixture();
  await trigger({id:8,filename:'C:\\Users\\tester\\Downloads\\report.html',url:'https://example.org/report.html'});
  assert.equal(posts.length,0);
  assert.equal(stored.pwWatchResult,undefined);
  assert.ok(stored.pwWatch);
});

test('an interrupted download reports a browser-side failure',async()=>{
  const {stored,posts,trigger}=fixture({downloadState:'interrupted'});
  await trigger({id:9,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://example.org/paper.pdf'});
  assert.equal(posts.length,0);
  assert.equal(stored.pwWatchResult.status,'failed');
  assert.match(stored.pwWatchResult.message,/浏览器|下载/);
});

test('a stale watch from another save is not reused',async()=>{
  const {stored,posts,trigger}=fixture({watchAt:Date.now()-16*60*1000});
  await trigger({id:10,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://example.org/paper.pdf'});
  assert.equal(posts.length,0);
  assert.equal(stored.pwWatchResult,undefined);
});

test('an unreachable local server surfaces the import error to the popup',async()=>{
  const {stored,trigger}=fixture({fetchImpl:async()=>({ok:false,json:async()=>({detail:'本机服务未启动'})})});
  await trigger({id:11,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://example.org/paper.pdf'});
  assert.equal(stored.pwWatchResult.status,'failed');
  assert.match(stored.pwWatchResult.message,/本机服务未启动/);
});

/* 422 的 detail 是数组，旧代码 new Error(data.detail) 会把它变成 [object Object]，
   用户只看到“接住失败：[object Object]”，完全不知道是哪个字段。 */
test('a FastAPI 422 detail array becomes readable text, never [object Object]',async()=>{
  const detail=[{type:'string_type',loc:['body','pdf_base64'],msg:'Input should be a valid string',
    input:{attempts:[{code:'not_pdf'}],base64:'',blocked:true}}];
  const {stored,trigger}=fixture({fetchImpl:async()=>({ok:false,status:422,json:async()=>({detail})})});
  await trigger({id:12,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://example.org/paper.pdf'});
  assert.equal(stored.pwWatchResult.status,'failed');
  const message=stored.pwWatchResult.message;
  assert.match(message,/HTTP 422/);
  assert.match(message,/pdf_base64/);
  assert.match(message,/string_type/);
  assert.doesNotMatch(message,/\[object Object\]/);
});

test('object detail is JSON text and a missing detail still reads in Chinese',async()=>{
  const objectDetail=fixture({fetchImpl:async()=>({ok:false,status:422,json:async()=>({detail:{loc:['body','tags'],msg:'bad'}})})});
  await objectDetail.trigger({id:13,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://example.org/paper.pdf'});
  assert.match(objectDetail.stored.pwWatchResult.message,/HTTP 422/);
  assert.match(objectDetail.stored.pwWatchResult.message,/pdf_base64|tags/);
  assert.match(objectDetail.stored.pwWatchResult.message,/\{.*loc.*\}/);

  const noDetail=fixture({fetchImpl:async()=>({ok:false,status:500,json:async()=>({})})});
  await noDetail.trigger({id:14,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://example.org/paper.pdf'});
  assert.match(noDetail.stored.pwWatchResult.message,/HTTP 500/);
  assert.match(noDetail.stored.pwWatchResult.message,/未知错误/);
  assert.doesNotMatch(noDetail.stored.pwWatchResult.message,/\[object Object\]/);
});

test('a non-Error rejection is still reported as text',async()=>{
  const {stored,trigger}=fixture({fetchImpl:async()=>{throw {code:'E_NET',detail:'本机连接被拒绝'};}});
  await trigger({id:15,filename:'C:\\Users\\tester\\Downloads\\paper.pdf',url:'https://example.org/paper.pdf'});
  assert.equal(stored.pwWatchResult.status,'failed');
  assert.match(stored.pwWatchResult.message,/E_NET|本机连接被拒绝/);
  assert.doesNotMatch(stored.pwWatchResult.message,/\[object Object\]/);
});
