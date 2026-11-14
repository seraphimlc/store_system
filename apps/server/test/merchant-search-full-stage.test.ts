/** Opt-in full merchant-corpus acceptance. Writes only to the existing runner's disposable database. */
import 'reflect-metadata';
import test from 'node:test';import assert from 'node:assert/strict';
import {createReadStream} from 'node:fs';import {readFile,mkdir,writeFile} from 'node:fs/promises';import {createInterface} from 'node:readline';import {createHash} from 'node:crypto';import {parseEnv} from 'node:util';import {join,resolve} from 'node:path';
import {chromium} from '@playwright/test';import {hash} from '@node-rs/argon2';import {createApp} from '../src/main';import {seedBatchWork} from '../../../scripts/batch-work-fixture.mjs';
import {Client} from 'pg';import {Database} from '../src/database';import {buildMerchantSearchIndex} from '../src/merchant-search-index';import {MerchantSearchService} from '../src/merchant-search';

const enabled=process.env.NEXT_MERCHANT_FULL_STAGE==='1',url=process.env.NEXT_TEST_DATABASE_URL;
test('full frozen merchant corpus: build, real geography and timed global search',{skip:!enabled,timeout:1800000},async()=>{
 assert.equal(process.env.NEXT_TEST_ISOLATED,'1');assert(url);const target=new URL(url!);assert(/^store_test_[0-9]{13}_[a-f0-9]{12}_refactor_p1$/.test(target.pathname.slice(1)));assert(['localhost','127.0.0.1','[::1]'].includes(target.hostname));
 const root=resolve(process.env.NEXT_MERCHANT_SNAPSHOT??'data/refactor/search-evaluation/2026-11-13T12-24-40-840Z-dd3974b9');
 const manifest=JSON.parse(await readFile(join(root,'manifest.json'),'utf8')),entry=manifest.files.find((f:any)=>f.file==='merchants.jsonl');assert.equal(entry.rows,620280);
 const out=resolve('data/refactor/search-evaluation/merchant-global-stage-'+Date.now());await mkdir(out,{recursive:true,mode:0o700});
 const report:any={createdAt:new Date().toISOString(),snapshot:manifest.version,temporaryDatabase:target.pathname.slice(1),sourceDatabaseWrites:false,productionAccessed:false,phases:[],samples:[]};
 const db=new Database({databaseUrl:url!,sessionSecret:'unused',cookieSecure:false,port:0,host:'127.0.0.1'});let source:Client|undefined;
 const traced={transaction:<T>(fn:(client:any)=>Promise<T>)=>db.transaction(client=>fn({query:async(...args:any[])=>{const t=performance.now();try{return await (client.query as any)(...args);}finally{if(performance.now()-t>500){report.slowStatements??=[];report.slowStatements.push({ms:Math.round(performance.now()-t),sql:String(args[0]).slice(0,180)});if(process.env.NEXT_MERCHANT_EXPLAIN==='1'&&String(args[0]).startsWith('WITH parameters')){try{const plan=await client.query('EXPLAIN (FORMAT JSON) '+args[0],args[1]);await writeFile(join(out,'plan-'+report.slowStatements.length+'.json'),JSON.stringify(plan.rows,null,2),{mode:0o600});}catch{}}}}}}))};
 const service=new MerchantSearchService(traced as Database);
 const phase=async(name:string,fn:()=>Promise<any>)=>{const t=performance.now();const result=await fn();const value={phase:name,ms:Math.round(performance.now()-t),...result};report.phases.push(value);console.log(JSON.stringify(value));await writeFile(join(out,'report.json'),JSON.stringify(report,null,2),{mode:0o600});return result;};
 try{
  await db.query(await readFile('deploy-next/migrations/007-merchant-search-index.sql','utf8'));
  await db.query(await readFile('deploy-next/migrations/008-merchant-search-telemetry.sql','utf8'));
  const actor=(await db.query("INSERT INTO users(username,password_hash,display_name,role,status,is_active,must_change_password,lang,created_at) VALUES('merchant-full-stage','not-a-login-hash','fixture','staff','active',true,false,'zh',now()) RETURNING id")).rows[0].id;
  await phase('load-frozen-merchants',async()=>{
   const stream=createReadStream(join(root,'merchants.jsonl')),hash=createHash('sha256');stream.on('data',chunk=>hash.update(chunk));let batch:any[]=[],count=0;
   const flush=async()=>{if(!batch.length)return;await db.query(`INSERT INTO public.kanto_merchants_v20261029(place_id,name,address,lat,lon,target_en,target_zh,sources,overture_id,osm_id,phone,website,region,prefecture,municipality) SELECT d.place_id,coalesce(d.name,''),coalesce(d.address,''),d.lat,d.lon,'','','',coalesce(d.overture_id,''),coalesce(d.osm_id,''),'','','','','' FROM jsonb_to_recordset($1::jsonb) d(place_id text,name text,address text,lat double precision,lon double precision,overture_id text,osm_id text)`,[JSON.stringify(batch)]);count+=batch.length;batch=[];};
   for await(const line of createInterface({input:stream,crlfDelay:Infinity})){if(line)batch.push(JSON.parse(line));if(batch.length===1000)await flush();}await flush();assert.equal(count,entry.rows);assert.equal(hash.digest('hex'),entry.sha256);return {documents:count};
  });
  // Copy only geographic source membership/labels, not task/team data, under one explicitly read-only snapshot.
  await phase('load-readonly-geography',async()=>{
   const env=parseEnv(await readFile('.env.next.local','utf8')),sourceUrl=new URL(env.NEXT_TEST_DATABASE_URL??env.NEXT_DATABASE_URL);assert.equal(sourceUrl.pathname,'/store_settle');assert(['localhost','127.0.0.1','[::1]'].includes(sourceUrl.hostname));sourceUrl.searchParams.set('options','-c default_transaction_read_only=on');source=new Client({connectionString:sourceUrl.href});await source.connect();await source.query('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY');
   assert.equal((await source.query('SHOW transaction_read_only')).rows[0].transaction_read_only,'on');
   const releases=(await source.query("SELECT id,source FROM bd_zone_release WHERE id IN(SELECT DISTINCT release_id FROM bd_grid_store) ORDER BY id")).rows;
   for(const r of releases)await db.query(`INSERT INTO bd_zone_release(id,fingerprint,source,parameters,audit,reviews,created_by,created_at) VALUES($1,$2,$3,'{}','{}','[]',$4,now())`,[r.id,'merchant-stage-release-'+r.id,JSON.stringify(r.source),actor]);
   const regions=(await source.query("SELECT id,release_id,source_id,name,state FROM bd_region WHERE release_id=ANY($1::int[]) ORDER BY id",[releases.map(r=>r.id)])).rows;
   await db.query(`INSERT INTO bd_region(id,release_id,source_id,name,geometry,area_km2,store_count,review,state,revision,updated_at) SELECT id,release_id,source_id,name,'{}',0,0,'[]',state,0,now() FROM jsonb_to_recordset($1::jsonb) d(id int,release_id int,source_id text,name text,state text)`,[JSON.stringify(regions)]);
   const grids=(await source.query('SELECT id,release_id,zone_id,source_id,name,state FROM bd_work_grid WHERE release_id=ANY($1::int[]) ORDER BY id',[releases.map(r=>r.id)])).rows;
   await db.query(`INSERT INTO bd_work_grid(id,release_id,zone_id,source_id,name,geometry,area_km2,store_count,stations,keywords,state,task_state,progress,revision,updated_at) SELECT id,release_id,zone_id,source_id,name,'{}',0,0,'[]','[]',state,'unassigned',0,0,now() FROM jsonb_to_recordset($1::jsonb) d(id int,release_id int,zone_id int,source_id text,name text,state text)`,[JSON.stringify(grids)]);
   let last=0,links=0;for(;;){const rows=(await source.query('SELECT id,release_id,grid_id,source_id FROM bd_grid_store WHERE id>$1 ORDER BY id LIMIT 5000',[last])).rows;if(!rows.length)break;await db.query(`INSERT INTO bd_grid_store(id,release_id,grid_id,source_id) SELECT * FROM jsonb_to_recordset($1::jsonb) d(id int,release_id int,grid_id int,source_id text)`,[JSON.stringify(rows)]);last=rows.at(-1)!.id;links+=rows.length;}
   await source.query('ROLLBACK');await source.end();source=undefined;return {regions:regions.length,grids:grids.length,links,geographyCapturedAt:new Date().toISOString(),geometryNotUnderTest:true};
  });
  const generation=await phase('build-persisted-index',()=>db.transaction(async client=>{await client.query("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ; SET LOCAL statement_timeout=0");return buildMerchantSearchIndex(client);}));assert.equal(generation.documentCount,620280);
  await phase('analyze-disposable-only',async()=>{for(const table of ['bd_merchant_search_document','bd_merchant_search_name','bd_merchant_search_gram_weight','bd_grid_store','bd_work_grid','bd_region'])await db.query('ANALYZE '+table);return {};});
  const queries=['東京都','新宿','東京 新宿','焼肉 中央苑','新宿中央薬局','炭火焼鳥 西田屋 下北沢店','长乐药店','chocoZAP','ｃｈｏｃｏＺＡＰ','長楽薬店','薬局','店','zzzz完全不存在987654321','セブンイレブン','コーヒー','カフェ 新宿'];
  await phase('cold-searches',async()=>{for(const q of queries){const t=performance.now();try{const r=await service.search(actor,{q});report.samples.push({q,ms:Math.round(performance.now()-t),total:r.total,unassigned:r.unassignedTotal,grids:r.gridGroups.length,method:r.nameMatchMethod,mode:r.searchMode,ok:true});}catch(e){report.samples.push({q,ms:Math.round(performance.now()-t),ok:false,message:(e as Error).message,code:(e as any).code});}}return {samples:report.samples};});
  assert(report.samples.every((s:any)=>s.ok),'Complete corpus search must not timeout; inspect private stage report');
  const timings:number[]=[],errors:any[]=[];let next=0;
  await phase('five-concurrent-200-requests',async()=>{await Promise.all(Array.from({length:5},async()=>{for(;;){const index=next++;if(index>=200)break;const t=performance.now();try{const response=await service.search(actor,{q:queries[index%queries.length]});assert(response.searchLogId,'Merchant telemetry must persist under the measured load');timings.push(performance.now()-t);}catch(e){errors.push({index,code:(e as any).code,message:(e as Error).message});}}}));timings.sort((a,b)=>a-b);return {requests:200,errors,p95:Math.round(timings[Math.ceil(timings.length*.95)-1]??0),p99:Math.round(timings[Math.ceil(timings.length*.99)-1]??0)};});
  const perf=report.phases.at(-1);assert.equal(errors.length,0);assert(perf.p95<=1000&&perf.p99<=2000,'Latency target not achieved');
  await phase('actual-nest-vite-browser',async()=>{
   const app=await createApp({databaseUrl:url!,sessionSecret:'merchant-browser-isolated-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'});let browser:Awaited<ReturnType<typeof chromium.launch>>|undefined;
   try{
    const fixture=await seedBatchWork(db,'MERCHANT-LIVE-'+Date.now(),await hash('merchant-live-pass'));const taskCount=Number((await db.query('SELECT count(*) FROM bd_store_task')).rows[0].count);
    await app.listen(0,'127.0.0.1');const apiBase=await app.getUrl(),frontend=process.env.NEXT_BROWSER_BASE_URL??'http://127.0.0.1:8002',parsed=new URL(frontend);assert(parsed.protocol==='http:'&&['127.0.0.1','localhost'].includes(parsed.hostname));
    browser=await chromium.launch({headless:true,channel:'chrome'});const context=await browser.newContext({viewport:{width:393,height:852}}),page=await context.newPage(),pageErrors:string[]=[];page.on('pageerror',e=>pageErrors.push(e.message));
    const login=await context.request.post(apiBase+'/api/auth/login',{data:{username:fixture.codes[2],password:'merchant-live-pass'}});assert.equal(login.status(),201);
    await context.route(frontend+'/api/**',async route=>{const u=new URL(route.request().url());if(u.pathname.endsWith('/map-config')){await route.fulfill({json:{enabled:false,key:null}});return;}const response=await route.fetch({url:apiBase+u.pathname+u.search});await route.fulfill({response});});
    await page.goto(frontend+'/my/work/batch-grids/'+fixture.grids[0][1]);await page.getByRole('button',{name:'展开筛选与搜索',exact:true}).click();
    const panel=page.getByRole('region',{name:'全量商户搜索',exact:true}),input=panel.getByLabel('全量商户关键词',{exact:true}),list=panel.locator('.merchant-search-results');await input.fill('chocoZAP');await list.locator('li').first().waitFor();
    assert.equal(await panel.getByRole('button',{name:'网格',exact:true}).getAttribute('aria-pressed'),'true');assert((await list.locator('mark').count())>0);
    const facet=panel.getByRole('combobox',{name:'结果分类筛选',exact:true});assert((await facet.locator('option').count())>2);await panel.getByRole('button',{name:'片区',exact:true}).click();await page.waitForResponse(r=>r.url().includes('/merchants/search?')&&r.status()===200);
    const group=await facet.locator('option').nth(1).getAttribute('value');assert(group);await Promise.all([page.waitForResponse(r=>new URL(r.url()).searchParams.get('groupRegion')===group&&r.status()===200),facet.selectOption(group)]);
    await list.getByRole('button').first().click();const confirmation=page.getByRole('region',{name:'搜索结果反馈',exact:true});await confirmation.getByRole('button',{name:'找到了',exact:true}).click();await confirmation.getByRole('button',{name:'已反馈找到',exact:true}).waitFor();
    await page.getByRole('region',{name:'商户资料',exact:true}).getByRole('button',{name:'关闭',exact:true}).click();await input.fill('长乐药店');await panel.getByText('以下仅为部分词相关候选，请核对名称和地址。',{exact:true}).waitFor();assert((await list.locator('mark').count())>0);
    assert.deepEqual(pageErrors,[]);assert.equal(Number((await db.query('SELECT count(*) FROM bd_store_task')).rows[0].count),taskCount);assert(Number((await db.query("SELECT count(*) FROM bd_merchant_search_feedback WHERE kind='confirmed' AND actor_id=$1",[fixture.users[2]])).rows[0].count)>0);
    const storage=(await db.query(`SELECT pg_size_pretty(pg_total_relation_size('bd_merchant_search_document')) AS documents,pg_size_pretty(pg_total_relation_size('bd_merchant_search_name')) AS names`)).rows[0];
    await context.close();return {realApi:true,realFrozenCorpus:true,mockOnly:'map provider configuration disabled',defaultGrid:true,regionFilter:true,partialHighlight:true,merchantFeedback:true,taskRowsUnchanged:true,pageErrors,storage};
   }finally{await browser?.close();await app.close();}
  });report.ok=true;
 }finally{await source?.end();await db.onModuleDestroy();await writeFile(join(out,'report.json'),JSON.stringify(report,null,2),{mode:0o600});console.log(JSON.stringify({evidence:join(out,'report.json'),ok:report.ok===true}));}
});
