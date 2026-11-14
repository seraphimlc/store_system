import test from 'node:test';
import assert from 'node:assert/strict';
import {storeRelatedNameTerms,storeSearchMatchEvidence,storeNameCandidateEvidence} from '../src/store-search-evidence';

test('related search uses server word boundaries and refuses character guessing and numeric inputs',()=>{
 assert.deepEqual(storeRelatedNameTerms('长乐药店'),['长乐','药店']);
 for(const q of ['','薬局','ABXDEF','12-34','1234','a'.repeat(81)])assert.deepEqual(storeRelatedNameTerms(q),[]);
 assert.deepEqual(storeRelatedNameTerms('NEW YORK'),['new','york']);
});
test('partial evidence highlights only the actually shared word in the name, never the missing brand or address',()=>{
 assert.deepEqual(storeSearchMatchEvidence('长乐药店',{storeId:'长乐',snapshot:{name:'xx药店',address:'长乐路'}},'related'),{mode:'related',nameTerms:['药店'],addressTerms:[],idTerms:[]});
 assert.deepEqual(storeSearchMatchEvidence('長楽薬店',{storeId:'x',snapshot:{name:'xx药店'}},'related').nameTerms,[],'no unsupported simplified/traditional conversion');
 assert.deepEqual(storeSearchMatchEvidence('001',{storeId:'001',snapshot:{}},'literal'),{mode:'literal',nameTerms:[],addressTerms:[],idTerms:['001']});
 const exact=storeSearchMatchEvidence('ABC 商店',{storeId:'001',snapshot:{name:'ＡＢＣ　商店',address:'東京都'}},'literal');
 assert.deepEqual(exact.nameTerms,['abc商店','abc','商店']);assert.deepEqual(exact.addressTerms,[]);
});

test('name-index evidence is expressed as original candidate slices, never invented corrected query text',()=>{
 const folded=storeNameCandidateEvidence('かな薬局',{storeId:'id',snapshot:{name:'カナ・薬局'}},'folded_name');assert.equal(folded.mode,'literal');assert.deepEqual(folded.nameTerms,['カナ・薬局']);
 const fuzzy=storeNameCandidateEvidence('新宿中央薬局',{storeId:'id',snapshot:{name:'新宿中央薬店',address:'新宿中央薬局'}},'weighted_name');assert.equal(fuzzy.mode,'related');assert.deepEqual(fuzzy.nameTerms,['新宿','宿中','中央','央薬']);assert.deepEqual(fuzzy.addressTerms,[]);
});
