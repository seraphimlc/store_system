import test from 'node:test';import assert from 'node:assert/strict';import {storeAddressProfile,storeAddressEvidence} from '../src/store-address-evidence';
const compare=(a:string,b:string)=>storeAddressEvidence(storeAddressProfile(a),storeAddressProfile(b));
test('address evidence recognizes written town and street numbers without equating different floors or entities',()=>{
 assert.equal(compare('東京都世田谷区北沢２－１２－２','北沢2丁目12-2'),'same_written_location');
 assert.equal(compare('東京都中央区銀座３丁目６−１ 8階','銀座3-6-1 1F'),'same_written_location','same building is not same merchant');
 assert.equal(compare('〒160-0023 東京都新宿区北新宿1-2-17','北新宿1丁目2番17号'),'same_written_location');
 assert.equal(compare('東京都新宿区北新宿1-2-17','北新宿3-2-17'),'street_number_conflict');
});
test('contradictory written addresses are rejected, missing and untranslated addresses stay unknown',()=>{
 assert.equal(compare('京都府京都市中京区伊勢屋町354-1','231-0023 神奈川県横浜市山下町97'),'prefecture_conflict');
 assert.equal(compare('東京都豊島区南大塚3-50-3','吾妻橋1丁目16'),'town_conflict');
 assert.equal(compare('東京都中央区銀座3-6-1','287-0003 香取市佐原イ1720'),'town_conflict');
 assert.equal(compare('東京都中央区銀座3-6-1','3 Chome-6-1 Ginza'),'unknown');
 assert.equal(compare('','北沢2-12-2'),'unknown');assert.equal(compare('東京都','東京都'),'unknown');
});
