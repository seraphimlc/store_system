import {storeMatchTier} from '../src/store-search';
import test from 'node:test';import assert from 'node:assert/strict';import {StoreNameIndex} from '../src/store-name-index';
test('index preserves exact duplicates and applies name, branch and written-address rejection without guessing missing evidence',()=>{
 const index=new StoreNameIndex([{id:1,name:'カナ・薬局',address:'銀座3-6-1'},{id:2,name:'カナ薬局',address:'北沢2-12-2'},{id:3,name:'VMGカフェ',address:'香取市佐原イ1720'},{id:4,name:'KIMONO RENTAL REN'},{id:5,name:'炭火焼鳥西田屋',address:'北沢2丁目12-2'}]);
 assert.deepEqual(index.search('かな薬局').candidates.map(x=>x.id),[1,2],'folded exact layer retains baseline ambiguities');
 assert.equal(index.search('MGカフェ','東京都中央区銀座3-6-1').candidates.length,0);
 assert.equal(index.search('KIMONO RENTAL MOMO').candidates.length,0);
 assert.deepEqual(index.search('炭火焼鳥 西田屋 下北沢店','東京都世田谷区北沢2-12-2').candidates.map(x=>x.id),[5]);
 assert.equal(index.search('12345').status,'unsupported_query');assert.equal(index.search('ab').status,'unsupported_query');
});
test('index rejects entire broad fallback, allows exact core, is permutation invariant and never merges source IDs',()=>{
 const rows=Array.from({length:21},(_,i)=>({id:i+1,name:'株式会社CONNECT'}));const index=new StoreNameIndex(rows);
 assert.equal(index.search('CONNECT').status,'too_many_candidates');assert.equal(index.search('株式会社CONNECT').candidates.length,21);
 const small=[{id:5,name:'中央苑'},{id:2,name:'中央苑'},{id:8,name:'他の店'}];const a=new StoreNameIndex(small).search('焼肉 中央苑'),b=new StoreNameIndex([...small].reverse()).search('焼肉 中央苑');
 assert.equal(a.method,'exact_core');assert.deepEqual(a.candidates,[{id:2,score:1},{id:5,score:1}]);assert.deepEqual(a,b);
 assert.throws(()=>new StoreNameIndex([{id:1,name:'A'},{id:1,name:'B'}]),/unique/);
});

test('word-only fallback retains the requested partial category behavior but never revives explicit brand or branch contradictions',()=>{
 const index=new StoreNameIndex([{id:1,name:'xx药店'},{id:2,name:'KIMONO RENTAL REN'},{id:3,name:'大阪王将 渋谷駅前店'},{id:4,name:'セブンイレブン 大久保店'}]);
 assert.deepEqual(index.relatedWords('长乐药店'),[{id:1,score:0}]);
 assert.deepEqual(index.relatedWords('KIMONO RENTAL MOMO'),[]);
 assert.deepEqual(index.relatedWords('大阪王将 糀谷駅前店'),[]);
 assert.deepEqual(index.relatedWords('chocoZA大久保'),[],'location-only overlap cannot discard explicit Latin brand');
});

test('deferred core construction never alters subsequent exact, fuzzy or word retrieval and source documents are not mutated',()=>{
 const rows=[{id:1,name:'カナ・薬局'},{id:2,name:'中央苑'},{id:3,name:'新宿中央薬店'}],copy=JSON.stringify(rows),index=new StoreNameIndex(rows);
 const before=index.search('かな薬局');assert.equal(index.search('焼肉 中央苑').method,'exact_core');assert.deepEqual(index.search('かな薬局'),before);assert.deepEqual(index.search('新宿中央薬局'),new StoreNameIndex(rows).search('新宿中央薬局'));assert.equal(JSON.stringify(rows),copy);
});

test('prepared literal matching preserves every legacy tier, original field boundaries and repeated query terms',()=>{
 const documents=[{id:1,storeId:'ABC',name:'別名',address:''},{id:2,storeId:'001',name:'ＡＢＣ',address:''},{id:3,storeId:'002',name:'ABC store',address:''},{id:4,storeId:'003',name:'The ABC store',address:''},{id:5,storeId:'004',name:'Other',address:'ABC'},{id:6,storeId:'005',name:'さくら薬局',address:'東京都新宿区'},{id:7,storeId:'006',name:'新宿さくら薬局',address:'東京都'},{id:8,storeId:'007',name:'100%_shop',address:''}];
 const index=new StoreNameIndex(documents);for(const query of ['abc','a b c','新宿 薬局','薬局 新宿 薬局','%_','不存在','００１','ABC　store'])assert.deepEqual(index.literalSearch(query),documents.flatMap(row=>{const tier=storeMatchTier(query,row);return tier===null?[]:[{id:row.id,score:0,tier}];}));
});
