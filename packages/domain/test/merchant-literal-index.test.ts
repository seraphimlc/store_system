import test from 'node:test';import assert from 'node:assert/strict';
import {MerchantLiteralIndex} from '../src/merchant-literal-index';import {storeMatchTier} from '../src/store-search';
const documents=[{id:9,storeId:'ABC',name:'別名',address:''},{id:2,storeId:'002',name:'ＡＢＣ　商店',address:'新宿 1-2'},{id:3,storeId:'003',name:'さくら薬局',address:'東京都新宿区'},{id:4,storeId:'004',name:'100%_shop',address:''},{id:5,storeId:'005',name:'𠮷野家😀',address:'北沢'}];
test('merchant literal slab preserves all shared match tiers, field boundaries, Unicode and input order',()=>{
 const index=new MerchantLiteralIndex(documents);
 for(const q of ['abc','ABC 商店','商店 新宿','新宿 薬局','薬局 新宿 薬局','%_','𠮷野','😀','不存在','１-２','002','商店新宿','c別']){
  const expected=documents.flatMap(d=>{const tier=storeMatchTier(q,d);return tier===null?[]:[{id:d.id,score:0,tier}];});assert.deepEqual(index.search(q),expected,q);
 }
 assert.deepEqual(index.search(''),[]);assert.deepEqual(index.search('\0'),[]);assert.deepEqual(new MerchantLiteralIndex([]).search('a'),[]);
 assert.throws(()=>new MerchantLiteralIndex([...documents,documents[0]]),/unique/);assert.throws(()=>new MerchantLiteralIndex([{id:1,storeId:'x',name:'a\0b'}]),/NUL/);
});
test('same merchant fields in different identities stay separate and multi-field query does not require adjacent words',()=>{
 const rows=Array.from({length:1000},(_,i)=>({id:i+1,storeId:'id-'+i,name:i%2?'药店':'其他店',address:i%3?'新宿':'中野'})),index=new MerchantLiteralIndex(rows);
 for(const q of ['店','药店 新宿','id-1','店 店','中野'])assert.deepEqual(index.search(q),rows.flatMap(d=>{const tier=storeMatchTier(q,d);return tier===null?[]:[{id:d.id,score:0,tier}];}));
});

test('packed literal results retain corpus order, shared ranking and stable source identity mapping',()=>{
 const rows=Array.from({length:20000},(_,i)=>({id:100000-i,storeId:'id-'+i,name:i%2?'药店':'其他店',address:i%3?'新宿':'中野'})),index=new MerchantLiteralIndex(rows);
 for(const q of ['店','药店 新宿','新宿 药店 新宿','００１','店中野','id-19999','不存在']){
  const actual=index.searchPacked(q),expected=rows.flatMap(d=>{const tier=storeMatchTier(q,d);return tier===null?[]:[{id:d.id,tier}];});
  assert.equal(actual.rows.length,expected.length);assert.deepEqual(Array.from(actual.rows,(row,i)=>({id:index.identity(row),tier:actual.tiers[i]})),expected,q);
 }
 assert.throws(()=>index.identity(-1),/Unknown/);assert.throws(()=>index.identity(rows.length),/Unknown/);
});

test('packed implementation is equivalent for deterministic mixed Unicode and multi-field queries',()=>{
 let seed=17;const next=()=>{seed=(seed*1664525+1013904223)>>>0;return seed;};const words=['药店','新宿','𠮷野','😀','ＡＢＣ','%_','あさひ','カフェ','東京','１-２',''];
 const rows=Array.from({length:250},(_,i)=>({id:i*3+1,storeId:'store-'+i,name:words[next()%words.length]+words[next()%words.length],address:words[next()%words.length]+' '+words[next()%words.length]})),index=new MerchantLiteralIndex(rows);
 for(let i=0;i<150;i++){const query=words[next()%words.length]+(i%3?' ':'')+words[next()%words.length],actual=index.searchPacked(query);const expected=rows.flatMap(row=>{const tier=storeMatchTier(query,row);return query.trim()&&tier!==null?[{id:row.id,tier}]:[];});
  assert.deepEqual(Array.from(actual.rows,(row,n)=>({id:index.identity(row),tier:actual.tiers[n]})),expected,JSON.stringify(query));
 }
});
