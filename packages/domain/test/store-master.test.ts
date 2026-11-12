import {spawnLegacyPython} from '../../../scripts/legacy-python.mjs';
import test from 'node:test';import assert from 'node:assert/strict';import {spawnSync} from 'node:child_process';import {candidatePairs,masterName,sequenceRatio} from '../src/store-master';
test('master normalization and fuzzy similarity match Unicode casefold and Python SequenceMatcher',()=>{
 const names=['Ｗｉｎｅ Shop　Sommelier','Straße','Σςσ','Ａ\u0085Ｂ','日本 店舗','ﬃ','İIı'];const pairs=[['tide','diet'],['diet','tide'],['日本店舗東京','日本店舗東亰'],['a'.repeat(200)+'b','a'.repeat(200)+'c'],['x',''],['',''],['abcdab','badcab']];
 const code="import json,sys,unicodedata,difflib,re\nx=json.load(sys.stdin)\nprint(json.dumps({'names':[re.sub(r'[\\s\\u3000]+','',unicodedata.normalize('NFKC',s).casefold()) for s in x['names']],'ratio':[difflib.SequenceMatcher(None,a,b).ratio() for a,b in x['pairs']]}))";
 const oracle=spawnLegacyPython(['-c',code],{input:JSON.stringify({names,pairs}),encoding:'utf8'});assert.equal(oracle.status,0);const expected=JSON.parse(oracle.stdout);assert.deepEqual(names.map(masterName),expected.names);assert.deepEqual(pairs.map(([a,b])=>sequenceRatio(a,b)),expected.ratio);
});
test('incremental candidates only include target roots and never recreate previously handled pairs',()=>{
 const entities=[{id:1,nameNorm:'same',city:null,masterId:1},{id:2,nameNorm:'same',city:'Tokyo',masterId:2},{id:3,nameNorm:'same',city:null,masterId:3},{id:4,nameNorm:'samf',city:null,masterId:4},{id:5,nameNorm:'same',city:null,masterId:1}];
 const pairs=candidatePairs(entities,new Set([2]),new Set(['1:2']));assert.deepEqual(pairs,[{a:2,b:3,kind:'exact',sim:null}]);assert.equal(candidatePairs(entities,new Set(),new Set()).length,0);
});
