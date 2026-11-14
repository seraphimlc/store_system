import {test} from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {execFileSync} from 'node:child_process';

test('offline coverage preserves denominator, multiplicity and matching thresholds; rejects altered snapshots',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'store-coverage-check-'));
 try{
  const files=[];
  for(const [file,rows] of [
   ['entities.jsonl',[{id:1,name_local:'ＡＢＣ　商店'},{id:2,name_local:'かな店'},{id:3,name_local:'東京・薬店'},{id:4,name_local:'長楽薬店'},{id:5,name_local:''}]],
   ['merchants.jsonl',[{name:'ABC商店'},{name:'カナ店'},{name:'東京薬店'},{name:'東京薬店'},{name:'長楽薬店本店'}]],
  ]){
   const text=rows.map(r=>JSON.stringify(r)).join('\n')+'\n';
   await writeFile(join(dir,file),text);
   files.push({file,rows:rows.length,sha256:createHash('sha256').update(text).digest('hex')});
  }
  await writeFile(join(dir,'manifest.json'),JSON.stringify({version:'fixture',files}));
  const run=()=>JSON.parse(execFileSync('node',['--import','tsx','scripts/search-coverage-evaluation.mjs',dir],{encoding:'utf8',stdio:['ignore','pipe','pipe']}));
  const result=run(),stats=Object.fromEntries(result.strategies.map(s=>[s.name,s]));
  assert.equal(result.denominator,5);assert.equal(result.databaseAccessed,false);assert.equal(result.runtimeChanged,false);
  assert.equal(stats.normalized.covered,1);assert.equal(stats.punctuation.covered,2);
  assert.equal(stats.kana_punctuation.covered,3);assert.equal(stats.kana_punctuation.ambiguous,1);
  assert.equal(stats['query_substring_ratio_0.65'].covered,4);
  assert.equal(stats['name_core_address_idf_0.6_plus_exact_core'].covered,4);
  assert.equal(stats['bigram_dice_0.8'].covered,3);assert.equal(stats['bigram_dice_0.7'].covered,4);
  for(const stat of result.strategies){
   assert.equal(stat.covered+stat.missing,5);
   assert.equal(stat.unique+stat.ambiguous,stat.covered);
   if(stat.cappedCovered!==undefined)assert.ok(stat.cappedCovered<=stat.covered);
  }
  await writeFile(join(dir,'entities.jsonl'),'{}\n');
  assert.throws(run,/Command failed/);
 }finally{await rm(dir,{recursive:true,force:true});}
});

test('optional address context filters contradictory fallback records without changing the frozen denominator',async()=>{
 const dir=await mkdtemp(join(tmpdir(),'store-coverage-address-check-'));
 try{
  const contents={
   'entities.jsonl':JSON.stringify({id:1,name_local:'青空薬局'})+'\n',
   'merchants.jsonl':[{name:'青空薬局本館',address:'北新宿1丁目2-3'},{name:'青空薬局本館',address:'東京都豊島区南大塚3-50-3'}].map(x=>JSON.stringify(x)).join('\n')+'\n',
  };
  const files=[];for(const [file,content] of Object.entries(contents)){await writeFile(join(dir,file),content);files.push({file,rows:content.trim().split('\n').length,sha256:createHash('sha256').update(content).digest('hex')});}
  await writeFile(join(dir,'manifest.json'),JSON.stringify({version:'address-fixture',files}));
  const context=join(dir,'context');await (await import('node:fs/promises')).mkdir(context);
  const text=JSON.stringify({id:1,address:'東京都新宿区北新宿1-2-3'})+'\n';await writeFile(join(context,'addresses.jsonl'),text);
  const manifest={baseSnapshot:'address-fixture',baseEntitiesSha256:files[0].sha256,rows:1,file:'addresses.jsonl',sha256:createHash('sha256').update(text).digest('hex')};
  await writeFile(join(context,'manifest.json'),JSON.stringify(manifest));
  const run=()=>JSON.parse(execFileSync('node',['--import','tsx','scripts/search-coverage-evaluation.mjs',dir,context],{encoding:'utf8',stdio:['ignore','pipe','pipe']}));
  const result=run(),stats=Object.fromEntries(result.strategies.map(s=>[s.name,s]));
  assert.equal(result.denominator,1);assert.equal(stats['name_core_guard_idf_0.6'].covered,1);assert.equal(stats['name_core_guard_idf_0.6'].ambiguous,1);
  assert.equal(stats['name_core_address_guard_idf_0.6'].covered,1);assert.equal(stats['name_core_address_guard_idf_0.6'].unique,1);
  assert.equal(stats['name_core_address_idf_0.6_plus_exact_core'].unique,1);
  assert.equal(result.addressEvidencePairCounts.same_written_location,1);assert.equal(result.addressEvidencePairCounts.town_conflict,1);
  await writeFile(join(context,'manifest.json'),JSON.stringify({...manifest,baseSnapshot:'wrong-snapshot'}));assert.throws(run,/Command failed/);
 }finally{await rm(dir,{recursive:true,force:true});}
});
