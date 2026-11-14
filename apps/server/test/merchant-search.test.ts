import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';import {readFile} from 'node:fs/promises';
import {Database} from '../src/database';
import {buildMerchantSearchIndex,merchantLiteralGrams,merchantQueryGrams} from '../src/merchant-search-index';
import {MerchantSearchService,merchantNameRetrieval} from '../src/merchant-search';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
import {StoreNameIndex} from '../../../packages/domain/src/store-name-index';
import request from '../node_modules/supertest';
import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';

const url=process.env.NEXT_TEST_DATABASE_URL;
test('literal prefilter supports single characters, mixed fields and literal punctuation without inventing matches',()=>{
 const grams=merchantLiteralGrams(['abc商店','新宿1-2','store-1']);
 for(const query of ['店','商店','ＡＢＣ 商店','新宿 1-2'])assert(merchantQueryGrams(query).every(g=>grams.includes(g)));
 assert(merchantQueryGrams('不存在').some(g=>!grams.includes(g)));
});
test('merchant search is global, keyed by source identity and atomically rebuilt; no task or batch is required',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const db=new Database({databaseUrl:url!,sessionSecret:'unused',cookieSecure:false,port:0,host:'127.0.0.1'}),service=new MerchantSearchService(db);
 try{
  await db.query(await readFile('deploy-next/migrations/007-merchant-search-index.sql','utf8'));
  await db.query(await readFile('deploy-next/migrations/008-merchant-search-telemetry.sql','utf8'));
  await db.query(await readFile('deploy-next/migrations/009-merchant-search-geography-version.sql','utf8'));
  await db.query(await readFile('deploy-next/migrations/009-merchant-search-geography-version.sql','utf8'));
  await db.query(await readFile('deploy-next/migrations/007-merchant-search-index.sql','utf8'));
  for(const fields of [['abc商店','新宿1-2','store-1'],['𠮷野家😀','药店',''],['%_','a b','東京都'],['','','']]){
   const actual=(await db.query('SELECT bd_merchant_search_literal_keys($1,$2,$3) AS keys',fields)).rows[0].keys;
   assert.deepEqual([...actual].sort(),merchantLiteralGrams(fields),'SQL expression index keys must equal shared JS code-point keys');
  }
  const actor=(await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES('merchant-search-fixture','not-a-login-hash','fixture','staff','active',true,false,'zh',now()) RETURNING id")).rows[0].id;
  await db.query(`INSERT INTO public.kanto_merchants_v20261029(place_id,name,address,lat,lon,target_en,target_zh,sources,overture_id,osm_id,phone,website,region,prefecture,municipality) SELECT id,name,address,35,139,'','','','','','','','','','' FROM (VALUES('merchant-a','ＡＢＣ　商店','新宿1-2'),('merchant-b','あさひ薬局','中野'),('merchant-c','同名商店','東京'),('merchant-d','同名商店','千葉')) AS fixture(id,name,address)`);
  const publish=()=>db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');return buildMerchantSearchIndex(client);});
  const first=await publish();assert.equal(first.documentCount,4);
  const literal=await service.search(actor,{q:'ABC 商店'});assert.equal(literal.total,1);assert.equal(literal.items[0].merchantId,'merchant-a');assert.equal(literal.unassignedTotal,1);assert.deepEqual(literal.gridGroups,[]);assert(literal.items[0].searchMatch.nameTerms.length);
  assert(literal.searchLogId);const feedback={eventId:crypto.randomUUID(),searchLogId:literal.searchLogId!,merchantId:'merchant-a',kind:'confirmed'};
  assert.equal((await service.feedback(actor,feedback)).duplicate,false);assert.equal((await service.feedback(actor,feedback)).duplicate,true);
  await assert.rejects(()=>service.feedback(actor,{...feedback,merchantId:'merchant-b'}),{code:'FORBIDDEN'});
  await assert.rejects(()=>service.feedback(-1,feedback),{code:'FORBIDDEN'});
  const logged=(await db.query('SELECT search_scope,result_merchant_ids FROM bd_merchant_search_log WHERE id=$1',[literal.searchLogId])).rows[0];assert.deepEqual(logged,{search_scope:'all_merchants',result_merchant_ids:['merchant-a']});

  const folded=await service.search(actor,{q:'アサヒ薬局'});assert.equal(folded.total,1);assert.equal(folded.nameMatchMethod,'folded_name');assert.equal(folded.items[0].merchantId,'merchant-b');
  assert.equal((await service.search(actor,{q:'同名商店'})).total,2,'same name does not merge different merchant identities');
  assert.equal((await service.search(actor,{q:'同名商店',groupGrid:0})).total,2);
  assert.equal((await service.search(actor,{q:'完全不在'})).total,0);
  await assert.rejects(()=>service.search(actor,{q:'商店',scope:'batch'}),{code:'VALIDATION'});
  await assert.rejects(()=>service.search(actor,{q:'\0药店'}),{code:'VALIDATION'});
  await assert.rejects(()=>service.search(actor,{q:'商店',groupGrid:0,groupRegion:0}),{code:'VALIDATION'});
  await assert.rejects(()=>service.search(-1,{q:'商店'}),{code:'FORBIDDEN'});
  await db.query("DELETE FROM public.kanto_merchants_v20261029 WHERE place_id='merchant-c'");
  await assert.rejects(()=>db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');await buildMerchantSearchIndex(client);throw Error('publish rollback');}),/publish rollback/);
  assert.equal((await service.search(actor,{q:'同名商店'})).total,2,'failed rebuild leaves prior published snapshot intact');
  const second=await publish();assert.notEqual(second.generation,first.generation);assert.equal((await service.search(actor,{q:'同名商店'})).total,1);
  assert.equal(Number((await db.query('SELECT count(*) FROM bd_store_task')).rows[0].count),0,'merchant search never creates tasks');
 }finally{await db.onModuleDestroy();}
});

test('geographic groups count unique merchants across all pages and releases, not tasks or teams',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const db=new Database({databaseUrl:url!,sessionSecret:'unused',cookieSecure:false,port:0,host:'127.0.0.1'}),service=new MerchantSearchService(db);let f:any,otherRelease:number|undefined,otherRegion:number|undefined,otherGrid:number|undefined;let app:Awaited<ReturnType<typeof createApp>>|undefined;
 try{
  f=await seedBatchWork(db,'MERCHANT-GROUP-'+Date.now(),await hash('merchant-fixture-pass'));
  await db.query(`INSERT INTO public.kanto_merchants_v20261029(place_id,name,address,lat,lon,target_en,target_zh,sources,overture_id,osm_id,phone,website,region,prefecture,municipality) SELECT 'group-merchant-'||n,'分类样本商户 '||n,'synthetic',35,139,'','','','','','','','','','' FROM generate_series(1,25)n`);
  await db.query(`INSERT INTO bd_grid_store(release_id,grid_id,source_id) SELECT $1,$2,'group-merchant-'||n FROM generate_series(1,22)n`,[f.release,f.baseGrids[0]]);
  await db.query(`INSERT INTO bd_grid_store(release_id,grid_id,source_id) VALUES($1,$2,'group-merchant-23')`,[f.release,f.baseGrids[1]]);
  otherRelease=(await db.query(`INSERT INTO bd_zone_release(fingerprint,source,parameters,audit,reviews,created_by,created_at) VALUES($1,'{}','{}','{}','[]',$2,now()) RETURNING id`,[f.prefix+'-other',f.users[0]])).rows[0].id;
  otherRegion=(await db.query(`INSERT INTO bd_region(release_id,source_id,name,geometry,area_km2,store_count,review,state,revision,updated_at) SELECT $1,$2,'other region',geometry,1,1,'[]','ready',0,now() FROM bd_region WHERE id=$3 RETURNING id`,[otherRelease,f.prefix+'-other',f.region])).rows[0].id;
  otherGrid=(await db.query(`INSERT INTO bd_work_grid(release_id,zone_id,source_id,name,stations,keywords,geometry,area_km2,store_count,state,task_state,progress,revision,updated_at) SELECT $1,$2,$3,'other grid','[]','[]',geometry,1,1,'ready','unassigned',0,0,now() FROM bd_work_grid WHERE id=$4 RETURNING id`,[otherRelease,otherRegion,f.prefix+'-other-grid',f.baseGrids[0]])).rows[0].id;
  await db.query(`INSERT INTO bd_grid_store(release_id,grid_id,source_id) VALUES($1,$2,'group-merchant-1')`,[otherRelease,otherGrid]);
  const generation=await db.transaction(async client=>{await client.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');return buildMerchantSearchIndex(client);});
  const beforeTasks=Number((await db.query('SELECT count(*) FROM bd_store_task')).rows[0].count);
  app=await createApp({databaseUrl:url!,sessionSecret:'merchant-search-http-fixture-secret-at-least-40-characters',cookieSecure:false,port:0,host:'127.0.0.1'});
  assert.equal((await request(app.getHttpServer()).get('/api/batch-work/merchants/search').query({q:'分类样本商户'})).status,401);
  const agent=request.agent(app.getHttpServer());assert.equal((await agent.post('/api/auth/login').send({username:f.codes[2],password:'merchant-fixture-pass'})).status,201);
  const http=await agent.get('/api/batch-work/merchants/search').query({q:'分类样本商户'});assert.equal(http.status,200);assert.equal(http.body.total,25);assert.equal(http.body.items.length,20);
  assert.equal((await agent.post('/api/batch-work/merchants/search-feedback').send({eventId:crypto.randomUUID(),searchLogId:http.body.searchLogId,merchantId:http.body.items[0].merchantId,kind:'confirmed'})).status,403);
  assert.equal((await agent.get('/api/batch-work/merchants/search').query({q:'分类样本商户',scope:'batch'})).status,400);

  const first=await service.search(f.users[2],{q:'分类样本商户'}),second=await service.search(f.users[2],{q:'分类样本商户',page:2});
  assert.equal(first.total,25);assert.equal(first.matchedTotal,25);assert.equal(first.unassignedTotal,2);assert.equal(first.items.length,20);assert.equal(second.items.length,5);
  assert.equal(new Set([...first.items,...second.items].map((x:any)=>x.merchantId)).size,25);
  assert.deepEqual(first.gridGroups,second.gridGroups,'groups reflect all matches, not loaded page');
  assert.equal(first.gridGroups.find((g:any)=>g.id===f.baseGrids[0]).count,22);assert.equal(first.gridGroups.find((g:any)=>g.id===otherGrid).count,1);
  assert.equal(first.regionGroups.find((g:any)=>g.id===f.region).count,23);assert.equal(first.regionGroups.reduce((n:number,g:any)=>n+g.count,0),24,'overlapping associations are not the global unique count');
  const grid=await service.search(f.users[2],{q:'分类样本商户',groupGrid:f.baseGrids[0],page:2});assert.equal(grid.total,22);assert.equal(grid.items.length,2);assert.equal(grid.matchedTotal,25);
  const region=await service.search(f.users[2],{q:'分类样本商户',groupRegion:otherRegion});assert.equal(region.total,1);assert.equal(region.items[0].merchantId,'group-merchant-1');
  assert.equal((await service.search(f.users[2],{q:'分类样本商户',groupGrid:0})).total,2);
  const geoRevision=async()=>String((await db.query('SELECT revision::text FROM bd_merchant_search_geography_version WHERE singleton')).rows[0].revision);
  const originalRevision=await geoRevision();await db.query('UPDATE bd_work_grid SET progress=progress+1 WHERE id=$1',[f.baseGrids[0]]);assert.equal(await geoRevision(),originalRevision,'task progress is not a geographic version change');
  await db.query("UPDATE bd_work_grid SET name='renamed geographic grid' WHERE id=$1",[f.baseGrids[0]]);assert.notEqual(await geoRevision(),originalRevision);
  const renamed=await service.search(f.users[2],{q:'分类样本商户'});assert.equal(renamed.gridGroups.find((g:any)=>g.id===f.baseGrids[0]).name,'renamed geographic grid');assert.notEqual(renamed.geographyVersion,first.geographyVersion);
  const serialized=JSON.stringify(first);for(const field of ['teamId','assigneeId','mainTaskId','storeTaskId','password_hash'])assert(!serialized.includes(field),'no task/team private fields');
  await db.query("UPDATE bd_region SET state='archived' WHERE id=$1",[otherRegion]);assert.equal((await service.search(f.users[2],{q:'分类样本商户'})).gridGroups.some((g:any)=>g.id===otherGrid),false);
  await db.query("UPDATE bd_work_grid SET state='archived' WHERE id=$1",[f.baseGrids[1]]);assert.equal((await service.search(f.users[2],{q:'分类样本商户'})).unassignedTotal,3);
  const documents=(await db.query('SELECT row_number() OVER(ORDER BY store_id)::int AS id,name,address FROM bd_merchant_search_document WHERE generation_id=$1',[generation.generation])).rows as {id:number;name:string;address:string}[];
  const reference=new StoreNameIndex(documents);
  for(const q of ['分類样本商戸','あさひ薬局','アサヒ薬局','完全不在']){const expected=reference.search(q);const actual=await db.transaction(c=>merchantNameRetrieval(c,generation.generation,q,generation.groupCount));if(actual.kind!=='literal'){assert.equal(actual.method,expected.method,q);if(expected.status==='no_candidates'&&actual.status==='too_many_candidates')assert.deepEqual(actual.keys,[],'broad partial-name fallback returns no truncated candidates');else assert.equal(actual.status,expected.status,q);}}
  assert.equal(Number((await db.query('SELECT count(*) FROM bd_store_task')).rows[0].count),beforeTasks);
 }finally{
  await app?.close();
  if(f){await db.query('DELETE FROM bd_grid_store WHERE release_id=ANY($1::int[])',[[f.release,otherRelease].filter(Boolean)]);if(otherGrid)await db.query('DELETE FROM bd_work_grid WHERE id=$1',[otherGrid]);if(otherRegion)await db.query('DELETE FROM bd_region WHERE id=$1',[otherRegion]);if(otherRelease)await db.query('DELETE FROM bd_zone_release WHERE id=$1',[otherRelease]);await clearBatchWork(db,f);}await db.onModuleDestroy();
 }
});

test('persisted weighted/core policy and scores match the shared evaluated index, including broad-name protection',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const db=new Database({databaseUrl:url!,sessionSecret:'unused',cookieSecure:false,port:0,host:'127.0.0.1'});
 try{
  await db.query(`INSERT INTO public.kanto_merchants_v20261029(place_id,name,address,lat,lon,target_en,target_zh,sources,overture_id,osm_id,phone,website,region,prefecture,municipality) SELECT id,name,'synthetic',35,139,'','','','','','','','','','' FROM (VALUES('policy-a','中央苑'),('policy-b','中央苑'),('policy-c','新宿中央薬店'),('policy-d','炭火焼鳥西田屋'),('policy-e','KIMONO RENTAL REN')) f(id,name)`);
  await db.query(`INSERT INTO public.kanto_merchants_v20261029(place_id,name,address,lat,lon,target_en,target_zh,sources,overture_id,osm_id,phone,website,region,prefecture,municipality) SELECT 'policy-broad-'||n,'株式会社CONNECT','synthetic',35,139,'','','','','','','','','','' FROM generate_series(1,21)n`);
  const generation=await db.transaction(async c=>{await c.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');return buildMerchantSearchIndex(c);});
  const documents=(await db.query('SELECT row_number() OVER(ORDER BY store_id)::int AS id,store_id AS "storeId",name,address,name_key FROM bd_merchant_search_document WHERE generation_id=$1',[generation.generation])).rows;
  const reference=new StoreNameIndex(documents as any[]),byId=new Map(documents.map(row=>[row.id,row]));
  const methods=new Set();
  for(const query of ['焼肉 中央苑','新宿中央薬局','炭火焼鳥 西田屋 下北沢店','KIMONO RENTAL MOMO','12345','CONNECT']){
   const actual=await db.transaction(c=>merchantNameRetrieval(c,generation.generation,query,generation.groupCount));
   if(reference.literalSearch(query).length){assert.equal(actual.kind,'literal');continue;}
   const expected=reference.search(query);methods.add(actual.method);assert.equal(actual.method,expected.method,query);assert.equal(actual.status,expected.status,query);
   assert.deepEqual(new Set(actual.keys),new Set(expected.candidates.map(candidate=>byId.get(candidate.id)!.name_key)),query);
   for(const candidate of expected.candidates)if(expected.method==='weighted_name')assert(Math.abs(actual.scoreByKey![byId.get(candidate.id)!.name_key]-candidate.score)<1e-9,'persisted corpus weights preserve score');
  }
  assert(methods.has('exact_core'));assert(methods.has('weighted_name'));
 }finally{await db.onModuleDestroy();}
});

test('partial-word candidates expose real name evidence without claiming a full merchant match',{skip:!url},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');const db=new Database({databaseUrl:url!,sessionSecret:'unused',cookieSecure:false,port:0,host:'127.0.0.1'});
 try{
  await db.query(`INSERT INTO public.kanto_merchants_v20261029(place_id,name,address,lat,lon,target_en,target_zh,sources,overture_id,osm_id,phone,website,region,prefecture,municipality) SELECT 'partial-'||id,name,'synthetic',35,139,'','','','','','','','','','' FROM (VALUES('1','xx药店'),('2','KIMONO RENTAL REN'),('3','大阪王将 渋谷駅前店')) f(id,name)`);
  await db.transaction(async c=>{await c.query('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ');await buildMerchantSearchIndex(c);});
  const actor=(await db.query("SELECT id FROM users WHERE username='merchant-search-fixture'")).rows[0].id;
  const result=await new MerchantSearchService(db).search(actor,{q:'长乐药店'});
  assert.equal(result.searchMode,'related');assert(result.items.some((item:any)=>item.merchantId==='partial-1'));
  assert.deepEqual(result.items.find((item:any)=>item.merchantId==='partial-1').searchMatch.nameTerms,['药店']);
  for(const q of ['KIMONO RENTAL MOMO','大阪王将 糀谷駅前店'])assert.equal((await new MerchantSearchService(db).search(actor,{q})).total,0,'partial overlap must not revive explicit brand/branch conflicts');
 }finally{await db.onModuleDestroy();}
});
