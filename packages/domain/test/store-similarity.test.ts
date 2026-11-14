import test from 'node:test';import assert from 'node:assert/strict';
import {canSuggestStoreName,oneNameEdit,groupStoreNameSuggestions} from '../src/store-similarity';
test('explicit suggestions require a sufficiently long literal single name and protect code-like queries',()=>{
 for(const q of ['abc','新宿薬局','123456','shop1','新宿 薬局','name%','a'.repeat(65)])assert.equal(canSuggestStoreName(q),false,q);
 assert.equal(canSuggestStoreName('新宿中央薬局'),true);assert.equal(canSuggestStoreName('ＡＢＣＤＥ'),true);
});
test('one-name edit handles Unicode insertion, deletion and replacement but not transposition or two edits',()=>{
 for(const [a,b]of [['abcde','abcde'],['新宿中央薬局','新宿中央薬局']])assert.equal(oneNameEdit(a,b),0);
 for(const [a,b]of [['abcde','abde'],['abcde','abXcde'],['abcde','abXde'],['新宿中央薬局','新橋中央薬局'],['abcd😀','abcd🦊']]){assert.equal(oneNameEdit(a,b),1);assert.equal(oneNameEdit(b,a),1);}
 assert.equal(oneNameEdit('abcde','abXYe'),null);assert.equal(oneNameEdit('abcde','abdce'),null);assert.equal(oneNameEdit('abcde','abc'),null);
});
test('suggestions group width/space-equivalent names but never identify same-name tasks as one merchant',()=>{
 const rows=[{id:3,name:'ＡＢＣＤＥＦ'},{id:1,name:'ABCDEF'},{id:4,name:'ABCDEG'},{id:6,name:'ABC1EH'},{id:7,name:null}];
 assert.deepEqual(groupStoreNameSuggestions('ABCDEH',rows),[{name:'ABCDEF',candidateCount:2,firstTaskId:1},{name:'ABCDEG',candidateCount:1,firstTaskId:4}]);
 assert.deepEqual(groupStoreNameSuggestions('ABCDEH',[...rows].reverse()),groupStoreNameSuggestions('ABCDEH',rows));
 assert.deepEqual(groupStoreNameSuggestions('ABCDEF',[{id:1,name:'ABCDEF'}]),[]);
});
