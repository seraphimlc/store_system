import test from 'node:test';
import assert from 'node:assert/strict';
import {storeNavigationHref} from '../apps/staff/src/work/store-navigation.ts';
const destination=store=>{const href=storeNavigationHref(store);return href?new URL(href).searchParams.get('destination'):null;};
test('store navigation uses saved coordinates, including zero, ahead of snapshot and text',()=>{
 assert.equal(destination({lat:35.1,lon:139.2,snapshot:{lat:36,lon:140,address:'地址'}}),'35.1,139.2');
 assert.equal(destination({lat:0,lon:0,snapshot:{address:'地址'}}),'0,0');
});
test('store navigation falls back safely without turning absent coordinates into zero',()=>{
 assert.equal(destination({lat:null,lon:null,snapshot:{lat:35,lon:139}}),'35,139');
 assert.equal(destination({lat:NaN,lon:139,snapshot:{address:' 日本 東京都 & 店铺 '}}),'日本 東京都 & 店铺');
 assert.equal(destination({lat:91,lon:181,snapshot:{name:'门店 A'}}),'门店 A');
 assert.equal(destination({lat:null,lon:null,snapshot:{address:' ',name:' '}}),null);
});
