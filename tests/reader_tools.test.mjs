import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const path = new URL('../web/js/reader-tools.js', import.meta.url);
let mod;
try { mod = await import('data:text/javascript;base64,' + Buffer.from(await readFile(path)).toString('base64')); }
catch { mod = {}; }
assert.equal(typeof mod.findTextMatches, 'function', 'search must expose occurrence matching');
const matches = mod.findTextMatches([{str:'Alpha alpha'}, {str:'beta ALPHA'}], 'alpha');
assert.equal(matches.length, 3);
assert.deepEqual(matches[1].parts, [{itemIndex:0,start:6,end:11}]);
assert.deepEqual(mod.findTextMatches([{str:'Across'}, {str:'items'}], 'across items')[0].parts,
 [{itemIndex:0,start:0,end:6},{itemIndex:1,start:0,end:5}]);
assert.equal(mod.findTextMatches([{str:'a.b aXb'}], 'a.b').length,1,'query must be literal');
assert.equal(mod.findTextMatches([{str:'anything'}], '').length,0);
assert.deepEqual(mod.findTextMatches([{str:'İ alpha'}], 'alpha')[0].parts,[{itemIndex:0,start:2,end:7}], 'case folding must retain original PDF offsets');
const store = new Map();
const storage = {getItem:k=>store.get(k),setItem:(k,v)=>store.set(k,v)};
mod.saveReadingState(storage, 'a', {page:3,offset:.42,zoom:1.5,tab:'chat',fitMode:'page'});
assert.deepEqual(mod.loadReadingState(storage,'a'), {page:3,offset:.42,zoom:1.5,tab:'chat',fitMode:'page'});
assert.equal(mod.loadReadingState(storage,'b'),null);
store.set('pw-reader:a','bad');
assert.equal(mod.loadReadingState(storage,'a'),null);
assert.equal(mod.loadReadingState({getItem(){throw Error('blocked');}},'a'),null);
assert.equal(mod.appendNote('Existing','Answer'), 'Existing\n\nAnswer');
assert.deepEqual(mod.findSourceMatch([{str:'Polarization-'},{str:'resolved imaging in ﬁbres.'}],
  'Polarization-resolved imaging in fibres').parts,
  [{itemIndex:0,start:0,end:12},{itemIndex:1,start:0,end:25}]);
assert.equal(mod.findSourceMatch([{str:'A different sentence'}], 'unmatched original source fragment'), null);
assert.equal(mod.findSourceMatch([{str:'simple'}], 'simple'), null);
console.log('PASS occurrence ranges, cross-item matches, literal search, paper-specific state, broken storage and note append');
