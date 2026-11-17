import test from 'node:test';
import assert from 'node:assert/strict';
globalThis.window = {};

test('conversation client exists and scopes delayed replies', async () => {
  const { Conversation } = await import('../web/js/conversation.js');
  const snapshots = new Map();
  let finish;
  const api = {
    async get(path) {
      const scope = decodeURIComponent(path.split('/').at(-1));
      if (!snapshots.has(scope)) snapshots.set(scope, { id:scope, scope, revision:0, messages:[], attachments:[], draft:{} });
      return structuredClone(snapshots.get(scope));
    },
    post(path, payload) {
      return new Promise(resolve => { finish = () => resolve({ conversation:{
        ...snapshots.get(payload.conversation_id), revision:2,
        messages:[{role:'user',content:payload.question},{role:'assistant',content:'answer A'}]
      }}); });
    },
  };
  const a = new Conversation('paper:a', api);
  const b = new Conversation('paper:b', api);
  await Promise.all([a.load(), b.load()]);
  const result = a.post('/ai/chat', { question:'question A' });
  await Promise.resolve();
  assert.equal(a.busy, true);
  assert.equal(b.busy, false);
  assert.deepEqual(b.data.messages, []);
  finish();
  await result;
  assert.equal(a.data.messages[1].content, 'answer A');
  assert.deepEqual(b.data.messages, []);
});

test('clear prevents a late reply from replacing the new state', async () => {
  const { Conversation } = await import('../web/js/conversation.js');
  const empty = { id:'paper:a', scope:'paper:a', revision:0, messages:[], attachments:[], draft:{} };
  let finish;
  const api = {
    async get() { return structuredClone(empty); },
    post() { return new Promise(resolve => { finish = () => resolve({ conversation:{ ...empty, revision:2, messages:[{role:'assistant',content:'OLD'}] } }); }); },
    async del() { return { ...empty, revision:3 }; },
  };
  const session = new Conversation('paper:a', api);
  await session.load();
  const pending = session.post('/ai/chat', { question:'old' });
  await Promise.resolve();
  await session.clear();
  finish();
  await pending;
  assert.deepEqual(session.data.messages, []);
  assert.equal(session.data.revision, 3);
  assert.equal(session.busy, false);
});

test('materials are stored against the captured conversation revision', async () => {
  const { Conversation } = await import('../web/js/conversation.js');
  let received;
  const empty = { id:'paper:a', scope:'paper:a', revision:7, messages:[], attachments:[], draft:{} };
  const session = new Conversation('paper:a', {
    async get() { return empty; },
    async put(path, body) { received={path,body}; return { ...empty, revision:8, attachments:body.attachments }; },
  });
  await session.load();
  await session.materials([{name:'a.txt',text:'ALPHA'}]);
  assert.equal(received.path, '/ai/conversations/paper%3Aa/materials');
  assert.equal(received.body.revision, 7);
  assert.equal(session.data.attachments[0].text, 'ALPHA');
});

test('failed clear releases the busy UI and refreshes the server state', async () => {
  const { Conversation } = await import('../web/js/conversation.js');
  const data = {id:'paper:a',scope:'paper:a',revision:1,messages:[],attachments:[],draft:{}};
  const session = new Conversation('paper:a', {
    async get() { return data; },
    async del() { throw new Error('offline'); },
  });
  await session.load();
  session.busy = true;
  await assert.rejects(session.clear(), /offline/);
  assert.equal(session.busy, false);
});

test('material conflict refreshes the revision so retry is possible', async () => {
  const { Conversation } = await import('../web/js/conversation.js');
  let revision = 1;
  const session = new Conversation('paper:a', {
    async get() { return {id:'a',scope:'paper:a',revision,messages:[],attachments:[],draft:{}}; },
    async put() { revision=3; throw new Error('conflict'); },
  });
  await session.load();
  await assert.rejects(session.materials([]), /conflict/);
  assert.equal(session.data.revision, 3);
});
