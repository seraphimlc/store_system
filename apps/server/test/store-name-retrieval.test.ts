import test from 'node:test';import assert from 'node:assert/strict';
import {retrieveStoreNames,stopStoreNameRetrieval} from '../src/store-name-retrieval';
test('name worker has bounded admission, deterministic results, cancellation and recovery without any database or provider',async()=>{
 const documents=[{id:1,name:'中央苑'},{id:2,name:'カナ・薬局'}];
 try{
  const first=Array.from({length:8},()=>retrieveStoreNames(documents,'焼肉 中央苑'));
  await assert.rejects(()=>retrieveStoreNames(documents,'焼肉 中央苑'),{code:'SEARCH_BUSY'});
  const values=await Promise.all(first);assert.ok(values.every(value=>value.method==='exact_core'&&value.candidates[0].id===1));
  const cancelled=retrieveStoreNames(documents,'かな薬局');stopStoreNameRetrieval();await assert.rejects(()=>cancelled,{code:'SEARCH_UNAVAILABLE'});
  const recovered=await retrieveStoreNames(documents,'かな薬局');assert.equal(recovered.method,'folded_name');assert.deepEqual(recovered.candidates,[{id:2,score:1}]);
  const invalid=retrieveStoreNames([{id:1,name:'A'},{id:1,name:'B'}],'C');await assert.rejects(()=>invalid,{code:'SEARCH_UNAVAILABLE'});
  assert.equal((await retrieveStoreNames(documents,'かな薬局')).candidates[0].id,2,'a failed construction cannot poison the cache or worker');
 }finally{stopStoreNameRetrieval();}
});
