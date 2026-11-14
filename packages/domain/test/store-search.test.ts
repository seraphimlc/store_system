import test from 'node:test';import assert from 'node:assert/strict';
import {normalizeStoreQuery,literalLike,storeMatchTier,storeQueryTerms} from '../src/store-search';
test('search normalization preserves identity punctuation and leading zeroes while accepting width/case/space variants',()=>{
 for(const q of ['ＡＢＣ　商店','ABC商店','a b c 商 店','abc\u00a0商店','abc\u0085商店'])assert.equal(normalizeStoreQuery(q),'abc商店');
 assert.equal(normalizeStoreQuery('ﾀｶｼﾏﾔ'),'タカシマヤ');assert.equal(normalizeStoreQuery('００１２'),'0012');
 assert.equal(normalizeStoreQuery('A%_/-B'),'a%_/-b');assert.notEqual(normalizeStoreQuery('高岛屋'),normalizeStoreQuery('高島屋'));
 assert.equal(literalLike('a%_/b'),'a/%/_//b');
});
test('exact identifiers outrank name matches and names outrank address-only matches',()=>{
 const q='ABC';assert.equal(storeMatchTier(q,{storeId:'ＡＢＣ',name:'Other',address:''}),0);
 assert.equal(storeMatchTier(q,{storeId:'1',name:'ＡＢＣ',address:''}),1);
 assert.equal(storeMatchTier(q,{storeId:'1',name:'ABC shop',address:''}),2);
 assert.equal(storeMatchTier(q,{storeId:'1',name:'The ABC shop',address:''}),3);
 assert.equal(storeMatchTier(q,{storeId:'xABCx',name:'Else',address:''}),5);
 assert.equal(storeMatchTier(q,{storeId:'1',name:'Else',address:'ABC street'}),6);
 assert.equal(storeMatchTier('%',{storeId:'1',name:'Else',address:''}),null);
 assert.equal(storeMatchTier('',{storeId:'1',name:'Else',address:''}),0);
});

test('multi-term search keeps every distinct literal word, permits word-order changes and name/address composition',()=>{
 assert.deepEqual(storeQueryTerms(' 新宿　薬局 新宿 '),['新宿','薬局']);
 const store={storeId:'001',name:'さくら薬局',address:'東京都新宿区'};
 assert.equal(storeMatchTier('新宿 薬局',store),7);assert.equal(storeMatchTier('薬局 新宿',store),7);
 assert.equal(storeMatchTier('薬局 薬局',store),4);
 assert.equal(storeMatchTier('新宿 ない店',store),null);assert.equal(storeMatchTier('薬局 002',store),null);
 assert.equal(storeMatchTier('薬局 新宿',{...store,name:'新宿さくら薬局',address:''}),4);
 assert.equal(storeMatchTier('001',{...store,name:'001参考店'}),0);
 assert.equal(storeMatchTier('新宿 %',store),null);assert.equal(storeMatchTier('new york',{storeId:'2',name:'NewYork',address:''}),1);
});
