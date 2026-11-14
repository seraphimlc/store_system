import test from 'node:test';import assert from 'node:assert/strict';
import {storeSearchTextParts} from '../src/store-search-highlight';
const hits=(text:string,query:string)=>storeSearchTextParts(text,query).filter(p=>p.matched).map(p=>p.text);
test('highlight maps normalized width, whitespace and decomposed graphemes to original text',()=>{
 assert.deepEqual(hits('ＡＢＣ　商店','abc商店'),['ＡＢＣ　商店']);assert.deepEqual(hits('店名ﬃ末尾','ffi'),['ﬃ']);assert.deepEqual(hits('ガレージ','ガ'),['ガ']);
 assert.deepEqual(hits('住所東京都新宿区','新宿 薬局'),['新宿']);assert.deepEqual(hits('新宿さくら薬局','薬局 新宿'),['新宿','薬局']);
 assert.deepEqual(hits('ΟΣ','ος'),['ΟΣ']);assert.deepEqual(hits('İstanbul','i\u0307stan'),['İstan']);assert.deepEqual(hits('abc😀def','😀'),['😀']);
});
test('highlight treats markup, regex and punctuation as literal strings and merges overlapping ranges',()=>{
 assert.deepEqual(hits('<img src=x onerror=alert(1)>','img'),['img']);assert.deepEqual(hits('100%_ shop','%_'),['%_']);assert.deepEqual(hits('a.b[xyz]','a.b'),['a.b']);assert.deepEqual(hits('banana','ana'),['anana']);
 assert.deepEqual(hits('高島屋','高岛屋'),[]);assert.deepEqual(hits('ABCDEF','ABXDEF'),[]);
});
test('parts reconstruct the exact original string and fail safely on excessive inputs or empty terms',()=>{
 for(const text of ['ＡＢＣ　商店','ガレージ','<script>bad()</script>','a😀b','ΟΣ','İstanbul','\u0085',''])for(const q of ['ABC','ガ','bad','😀','ος','istan',' ', '%',''])assert.equal(storeSearchTextParts(text,q).map(p=>p.text).join(''),text);
 assert.deepEqual(storeSearchTextParts('x'.repeat(4097),'x'),[{text:'x'.repeat(4097),matched:false}]);assert.deepEqual(hits('abc','a'.repeat(201)),[]);
});

test('server match evidence controls partial highlighting without frontend query splitting',()=>{
 assert.deepEqual(storeSearchTextParts('xx药店','长乐药店',['药店']).filter(p=>p.matched).map(p=>p.text),['药店']);
 assert.deepEqual(storeSearchTextParts('xx药店','长乐药店',[]).filter(p=>p.matched),[]);
 assert.deepEqual(storeSearchTextParts('xx药店','长乐药店').filter(p=>p.matched),[]);
 assert.deepEqual(storeSearchTextParts('长乐路','长乐药店',[]).filter(p=>p.matched),[]);
});
