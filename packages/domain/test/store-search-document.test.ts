import test from 'node:test';import assert from 'node:assert/strict';
import {storeSearchDocument} from '../src/store-search-document';
test('task search documents preserve original values, separate repeated merchant tasks and carry rebuildable provenance',()=>{
 const source={id:1,grid_task_id:2,store_id:'００１',revision:3,snapshot:{name:'ＡＢＣ　商店',address:'新宿 １－２',photos:['private-not-searchable']}};
 const before=JSON.stringify(source),doc=storeSearchDocument(source);assert.equal(JSON.stringify(source),before);assert.equal(doc.documentId,'store-task:1');assert.equal(doc.normalized.name,'abc商店');assert.equal(doc.normalized.storeId,'001');assert.equal(doc.original.name,'ＡＢＣ　商店');assert.equal('photos' in doc.original,false);
 assert.notEqual(storeSearchDocument({...source,id:2}).documentId,doc.documentId);assert.notEqual(storeSearchDocument({...source,revision:4}).sourceHash,doc.sourceHash);
 assert.notEqual(storeSearchDocument({...source,snapshot:{name:'Different',address:'新宿 １－２'}}).sourceHash,doc.sourceHash);
 assert.equal(storeSearchDocument({...source,snapshot:{address:'新宿 １－２',name:'ＡＢＣ　商店'}}).sourceHash,doc.sourceHash);
 assert.equal(storeSearchDocument({...source,snapshot:{}}).missing.name,true);assert.equal(storeSearchDocument({...source,snapshot:{name:null}}).original.name,'');
 for(const bad of [{...source,id:0},{...source,grid_task_id:-1},{...source,revision:-1},{...source,store_id:''},{...source,snapshot:[]},{...source,snapshot:{name:123}}])assert.throws(()=>storeSearchDocument(bad));
});
