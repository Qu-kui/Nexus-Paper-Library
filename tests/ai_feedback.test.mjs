import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const source = (await readFile(new URL('../web/js/conversation.js', import.meta.url),'utf8')).replace("import { api } from './api.js';",'const api = {};');
const {Conversation} = await import('data:text/javascript;base64,'+Buffer.from(source).toString('base64'));
assert.equal(typeof Conversation.prototype.stop,'function','generation can be stopped');
assert.equal(typeof Conversation.prototype.undo,'function','cleared history can be restored');
const base = {id:'c',revision:0,messages:[],attachments:[],draft:{}};
let resolvePost, resolveProgress;
const calls=[];
const api={get:async path=>{
  calls.push(path);
  if (path.includes('/progress/')) return await new Promise(r=>resolveProgress=r);
  return base;
},post:async (path,body)=>{
  calls.push([path,body]);
  if (path.endsWith('/cancel')) return {...base,revision:2};
  if (path.endsWith('/undo')) return {...base,revision:4,messages:[{role:'user',content:'restored'}]};
  return await new Promise(r=>resolvePost=r);
},del:async()=>({...base,revision:3,undo_available:true})};
const session = new Conversation('library',api);
const pending=session.post('/ai/chat',{question:'old'});
await new Promise(r=>setTimeout(r,20));
assert.ok(calls.some(p=>typeof p==='string'&&p.includes('revision=1')),'poll exact server generation revision');
await session.stop();
resolveProgress({stage:'old',reasoning:'stale reasoning',state:'running'});
resolvePost({conversation:{...base,revision:1,messages:[{role:'assistant',content:'stale answer'}]}});
await pending;
assert.equal(session.data.revision,2);
assert.equal(session.progress?.reasoning||'','');
assert.equal(session.busy,false);
await session.clear();
assert.equal(session.data.undo_available,true);
await session.undo();
assert.equal(session.data.messages[0].content,'restored');
console.log('PASS exact generation progress, stop isolation, undo');
// A late poll from a completed request must not overwrite a newer round.
let polls=[],requests=[];
const racing = new Conversation('library',{
 get:async path=>path.includes('/progress/')?await new Promise(r=>polls.push(r)):base,
 post:async()=>await new Promise(r=>requests.push(r))
});
const first=racing.post('/ai/chat',{question:'first'});
await new Promise(r=>setTimeout(r,0));
requests[0]({conversation:{...base,revision:2}});await first;
const second=racing.post('/ai/chat',{question:'second'});
await new Promise(r=>setTimeout(r,0));
polls[0]({state:'running',reasoning:'OLD POLL'});
await new Promise(r=>setTimeout(r,0));
assert.notEqual(racing.progress.reasoning,'OLD POLL');
requests[1]({conversation:{...base,revision:4}});await second;
polls[1]({state:'completed',reasoning:'SECOND'});

// Source labels and targets stay truthful even without a PDF.
globalThis.window={};
const apiCode=await readFile(new URL('../web/js/api.js',import.meta.url),'utf8');
const apiUrl='data:text/javascript;base64,'+Buffer.from(apiCode).toString('base64');
const feedbackCode=(await readFile(new URL('../web/js/ai-feedback.js',import.meta.url),'utf8')).replace("'./api.js'",JSON.stringify(apiUrl));
const feedback=await import('data:text/javascript;base64,'+Buffer.from(feedbackCode).toString('base64'));
const cards=feedback.renderSources([{paper_id:'a',title:'<unsafe>',source_kind:'abstract',has_pdf:false,citation_index:1}], [{paper_id:'b',title:'Candidate',source_kind:'generated_summary'}]);
assert.match(cards,/论文摘要/);assert.match(cards,/AI 概述/);assert.match(cards,/查看摘要 \/ 补齐 PDF/);
assert.match(cards,/\[1\]/);assert.match(cards,/&lt;unsafe&gt;/);assert.doesNotMatch(cards,/打开原文/);
assert.match(cards,/未被本轮编号引用/);
console.log('PASS late previous poll is ignored; source kinds, citation numbers, missing PDF actions and escaping');
