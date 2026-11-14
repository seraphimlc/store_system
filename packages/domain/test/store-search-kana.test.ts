import test from 'node:test';import assert from 'node:assert/strict';
import {existingKanaKey,alternateKanaWriting} from '../src/store-search-kana';
test('existing kana candidates share a glyph key without generating kanji readings or identity links',()=>{
 for(const name of ['たかしまや','タカシマヤ','ﾀｶｼﾏﾔ'])assert.equal(existingKanaKey(name),'タカシマヤ');
 assert.equal(existingKanaKey('ガレージ'),existingKanaKey('がれーじ'));assert.notEqual(existingKanaKey('高島屋'),existingKanaKey('たかしまや'));
 assert.equal(existingKanaKey('００１ＡＢＣ'),'001abc');assert.equal(existingKanaKey('新宿 さくら薬局'),'新宿サクラ薬局');
 assert.notEqual(existingKanaKey('ヷ'),existingKanaKey('わ'));assert.notEqual(existingKanaKey('かゝ'),existingKanaKey('かか'));assert.notEqual(existingKanaKey('カ・キ'),existingKanaKey('カキ'));
});
test('alternate kana variants are derived written-form tests, not reading transliterations',()=>{
 assert.equal(alternateKanaWriting('さくら薬局'),'サクラ薬局');assert.equal(alternateKanaWriting('サクラ薬局'),'さくら薬局');assert.equal(alternateKanaWriting('高島屋 ABC 001'),'高島屋 ABC 001');
 assert.equal(existingKanaKey(alternateKanaWriting('さくらカフェ')),existingKanaKey('さくらカフェ'));
 for(const name of ['がれーじ','ぁぃゔゕゖ','さくら薬局','ｶﾞﾚｰｼﾞ','😀テスト'])assert.equal(existingKanaKey(alternateKanaWriting(name)),existingKanaKey(name));
});
