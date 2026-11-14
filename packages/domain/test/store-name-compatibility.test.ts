import test from 'node:test';import assert from 'node:assert/strict';
import {storeNameKey,storeNameProfile,createStoreNameProfiler,storeNameCompatibility} from '../src/store-name-compatibility';
const compare=(q:string,c:string)=>storeNameCompatibility(storeNameProfile(q),storeNameProfile(c));
test('name gate rejects the observed category, Latin brand and explicit branch contradictions',()=>{
 assert.notEqual(compare('創作居酒屋　まんまる','創作居酒屋マルク'),'compatible');
 assert.notEqual(compare('KIMONO RENTAL MOMO','KIMONO RENTAL REN'),'compatible');
 assert.notEqual(compare('KIMONO RENTAL MOMO','KIMONO RENTAL ANN'),'compatible');
 assert.equal(compare('ファッションヘルス　クリスタル','ファッションヘルス'),'category_only');
 assert.equal(compare('大阪王将　糀谷駅前店','大阪王将 渋谷駅前店'),'branch_conflict');
 assert.equal(compare('大阪王将糀谷駅前店','大阪王将渋谷駅前店'),'branch_conflict');
 assert.notEqual(compare('Baluko Laundry Place 文京動坂 コインランドリー','Baluko Laundry Place 矢口 コインランドリー'),'compatible');
 assert.equal(compare('ランランランドリー','カフェ ランラン'),'business_family_conflict');
 assert.notEqual(compare('605','焼肉食堂605'),'compatible');
 assert.equal(compare('バンビーノCafe','バンビーノ薬局'),'business_family_conflict');
 assert.equal(compare('XYZ HAIR SALON','Bar XYZ'),'business_family_conflict');
 assert.equal(compare('ＦＵＺＥ ｈａｉｒ','Cafe Fuze'),'business_family_conflict');
 assert.notEqual(compare('スーパー麻辣湯','麻辣湯'),'compatible');
 assert.notEqual(compare('MGカフェ','RGカフェ'),'compatible');
 assert.equal(compare('カフェ クリスタル','カフェ'),'category_only');
 assert.equal(compare('炭火焼鳥 西田屋 下北沢店','炭火焼鳥西田屋渋谷店'),'branch_conflict');
 assert.equal(compare('まいばすけっと一之江3丁目','まいばすけっと一之江7丁目店'),'name_number_conflict');
 assert.equal(compare('Studio54','Studio55'),'name_number_conflict');
 assert.notEqual(compare('ベトナムキッチン大塚','ベトナムキッチン浅草店'),'compatible');
 assert.equal(compare('KFC新宿店','KFC渋谷店'),'branch_conflict');
});
test('name gate preserves compatible written names without inventing aliases or branch identity',()=>{
 assert.equal(storeNameKey('ＡＢＣ・かな　店'),'abcカナ店');
 assert.equal(compare('伊勢屋 錦糸町店','伊勢屋 錦糸町'),'compatible');
 assert.equal(compare('富士そば 秋葉原電気街店','名代富士そば 秋葉原電気街店'),'compatible');
 assert.equal(compare('NIWAKA 俄 新宿店（ブライダルサテライトストア）','NIWAKA新宿店（ブライダルサテライトストア）'),'compatible');
 assert.equal(compare('ラーメン 一蘭 新宿店','一蘭 新宿店'),'compatible');
 assert.equal(compare('株式会社CONNECT','CONNECT'),'compatible');
 assert.equal(compare('ABCDXFG','ABCDEFG'),'compatible');
 assert.notEqual(compare('长乐药店','長楽薬店'),'compatible');
 assert.equal(compare('カフェ',''),'empty_name');
});

test('build-local token memoization is semantically identical to uncached profiles across repeated and varied written forms',()=>{
 const profile=createStoreNameProfiler();for(const name of ['NIWAKA 俄 新宿店（ブライダルサテライトストア）','カナ・薬局','KIMONO RENTAL MOMO','大久保3丁目店','', '中文 药店','ＡＢＣ カフェ','Cafe foo','株式会社CONNECT']){assert.deepEqual(profile(name),storeNameProfile(name));assert.deepEqual(profile(name),storeNameProfile(name));}
});
