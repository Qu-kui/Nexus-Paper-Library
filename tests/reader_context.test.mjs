import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import test from 'node:test';

let context = {};
try {
  const source = await readFile(new URL('../web/js/reader-context.js', import.meta.url));
  context = await import('data:text/javascript;base64,' + source.toString('base64'));
} catch {}

test('snapshot excludes another paper selection and freezes owned material before later mutations', () => {
  assert.equal(typeof context.createMaterialSnapshot, 'function', 'material snapshot helper exists');
  const state = {paperId:'a', paper:{title:'Paper A'}, currentPage:4,
    annotations:[{id:1,page:3,text:'quoted source',note:'my note'}],
    chatSession:{data:{attachments:[{name:'notes.md',text:'saved attachment'}]}}};
  const snapshot = context.createMaterialSnapshot(state, {
    selection:{paperId:'b',pageNum:2,text:'wrong paper'},
    chapters:[{title:'Methods',page:3,level:1}],
  });
  assert.equal(snapshot.selection, null);
  assert.equal(snapshot.chapter.title, 'Methods');
  assert.ok(Object.isFrozen(snapshot.annotations[0]));
  state.paper.title = 'changed'; state.annotations[0].note = 'changed';
  state.chatSession.data.attachments[0].name = 'changed';
  assert.equal(snapshot.title, 'Paper A');
  assert.equal(snapshot.annotations[0].note, 'my note');
  assert.equal(snapshot.attachments[0].name, 'notes.md');
  assert.match(context.materialScopeText(snapshot), /Paper A.*4.*1.*notes\.md/);
});

test('chapter material stops at next known chapter and explicitly caps long ranges', () => {
  assert.equal(typeof context.chapterPageRange, 'function');
  assert.deepEqual(context.chapterPageRange([{title:'A',page:3},{title:'B',page:8}],5,40),
    {title:'A',start:3,end:7,truncated:false});
  assert.deepEqual(context.chapterPageRange([{title:'A',page:3}],5,40),
    {title:'A',start:3,end:14,truncated:true});
  assert.equal(context.chapterPageRange([],5,40), null);
  assert.equal(context.chapterPageRange([{title:'Later',page:8}],5,40), null);
});

test('captured wire quotes frozen source data, respects limits, and keeps ordinary questions exact', () => {
  assert.equal(typeof context.captureMaterialQuestion, 'function');
  assert.deepEqual(context.captureMaterialQuestion('  ordinary question  ',
    context.createMaterialSnapshot({paperId:'a',currentPage:1})),
    {question:'  ordinary question  ',selection:'',displayQuestion:'  ordinary question  ',page:1});
  const prepared = {paperId:'a',text:'UNTRUSTED '.repeat(2000),source:'本节，第 3–14 页',truncated:true};
  const snapshot = context.createMaterialSnapshot({paperId:'a',paper:{title:'A'},currentPage:3}, {
    selection:{paperId:'a',pageNum:3,text:'selected '.repeat(1000)},prepared,
  });
  prepared.text = 'mutated after capture';
  const result = context.captureMaterialQuestion('Please explain. '.repeat(1500),snapshot);
  assert.ok(result.question.length <= 16000);
  assert.ok(result.selection.length <= 4000);
  assert.match(result.question, /不可信引用资料/);
  assert.match(result.question, /UNTRUSTED/);
  assert.doesNotMatch(result.question, /mutated after capture/);
  assert.match(result.displayQuestion, /第 3–14 页/);
  assert.match(result.displayQuestion, /截断/);
});

test('selection length is bounded and material is rejected when it belongs to a different paper', () => {
  assert.equal(typeof context.createMaterialSnapshot, 'function');
  const snapshot = context.createMaterialSnapshot({paperId:'a',currentPage:2}, {
    selection:{paperId:'a',pageNum:2,text:'x'.repeat(5000)},
    prepared:{paperId:'b',source:'wrong',text:'must not appear'},
  });
  assert.equal(snapshot.selection.text.length,4000);
  assert.equal(snapshot.selection.truncated,true);
  assert.equal(snapshot.prepared,null);
  const result = context.captureMaterialQuestion('Explain',snapshot);
  assert.doesNotMatch(result.question,/must not appear/);
  assert.match(result.displayQuestion,/4000/);
});

test('escaped source text stays valid quoted JSON within the request limit', () => {
  const snapshot = context.createMaterialSnapshot({paperId:'a',currentPage:1}, {
    selection:{paperId:'a',pageNum:1,text:'\u0000'.repeat(4000)},
    prepared:{paperId:'a',source:'本页，第 1 页',text:'\u0001'.repeat(10000)},
  });
  const result = context.captureMaterialQuestion('explain',snapshot);
  const quoted = result.question.split('引用资料（JSON 字符串值）：\n')[1].split('\n\n用户问题：\n')[0];
  assert.doesNotThrow(() => JSON.parse(quoted));
  assert.ok(result.question.length <= 16000);
  assert.ok(result.question.endsWith('explain'));
  assert.match(result.displayQuestion,/截断/);
});

test('annotation snapshots use the persisted quote field as source text', () => {
  const snapshot = context.createMaterialSnapshot({paperId:'a',currentPage:1,
    annotations:[{id:1,page:7,quote:'Evidence stored as an annotation quote',note:'Check this'},
      {id:2,page:8,quote:'x'.repeat(5000)}]});
  assert.equal(snapshot.annotations[0].text,'Evidence stored as an annotation quote');
  assert.equal(snapshot.annotations[1].truncated,true,'per-annotation bounds must be visible');
});

test('prepared prompts preserve user writing and only replace an unchanged automatic template', () => {
  assert.equal(typeof context.preparedQuestionValue,'function');
  assert.equal(context.preparedQuestionValue('My existing question',null,'Explain this page'),'My existing question');
  assert.equal(context.preparedQuestionValue('New writing while extracting','Earlier template','New template'),'New writing while extracting');
  assert.equal(context.preparedQuestionValue('Earlier template','Earlier template','New template'),'New template');
  assert.equal(context.preparedQuestionValue('',null,'New template'),'New template');
});

test('wire page follows prepared material, then selected text, then the current page', () => {
  const state = {paperId:'a',currentPage:9};
  const selected = {paperId:'a',pageNum:5,text:'selected evidence'};
  const prepared = {paperId:'a',page:3,source:'本页，第 3 页',text:'prepared evidence'};
  const snapshot = context.createMaterialSnapshot(state,{selection:selected,prepared});
  prepared.page = 8;
  assert.equal(snapshot.prepared.page,3);
  assert.equal(context.captureMaterialQuestion('Explain',snapshot).page,3);
  assert.equal(context.captureMaterialQuestion('Explain',context.createMaterialSnapshot(state,{selection:selected})).page,5);
  assert.equal(context.captureMaterialQuestion('Explain',context.createMaterialSnapshot(state)).page,9);
});
