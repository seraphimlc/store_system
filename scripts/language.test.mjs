import test from 'node:test';import assert from 'node:assert/strict';
import {resolveLanguage} from '../packages/client/src/language.ts';
test('language priority preserves query, cookie, saved choice, account and browser fallback',()=>{
 assert.equal(resolveLanguage({query:'ja',cookie:'zh',account:'zh'}),'ja');
 assert.equal(resolveLanguage({query:'invalid',cookie:'ja',account:'zh'}),'ja');
 assert.equal(resolveLanguage({account:'ja',browser:'zh-CN'}),'ja');
 assert.equal(resolveLanguage({account:'',browser:'ja-JP'}),'ja');
 assert.equal(resolveLanguage({saved:'zh',account:'ja'}),'zh');
 assert.equal(resolveLanguage({browser:'en-US'}),'zh');
});
