import 'reflect-metadata';import test from 'node:test';import assert from 'node:assert/strict';import {hash} from '@node-rs/argon2';
import {createApp} from '../src/main';import {Database} from '../src/database';import {BatchWorkService} from '../src/batch-work';
import {normalizeStoreQuery,storeSearchSql,storeSearchTermsSql,storeQueryTerms,storeMatchTier} from '../../../packages/domain/src/store-search';
import {seedBatchWork,clearBatchWork} from '../../../scripts/batch-work-fixture.mjs';
import {mobileVisits} from '../src/mobile-visits';
import {mobileVisitList} from '../src/mobile-visit-list';
import {mobileVisits as archivedVisits} from '../../../scripts/search-legacy-reference';
import './store-search-log.test';import './batch-mobile.test';import './batch-map.test';
const url=process.env.NEXT_TEST_DATABASE_URL;
test('empty and whitespace-only searches execute the original list SQL with identical parameters',async()=>{
 const capture=async(fn:any,q:string)=>{let sql='',params:any[]=[];await fn({query:async(text:string,values:any[])=>{sql=text;params=values;return {rows:[{}]};}},42,{q,state:'pending',businessStatus:'closed',page:2,lat:35,lon:139,focus:7},false);return {sql,params};};
 const baseline=await capture(archivedVisits,'');assert.deepEqual(await capture(mobileVisitList,''),baseline);assert.deepEqual(await capture(mobileVisits,''),baseline);assert.deepEqual(await capture(mobileVisits,'　 \u0085'),baseline);
});

test('normalized literal search ranks by relevance before distance, preserves scope/filter/focus and stable pagination',{skip:!url},async()=>{
 const app=await createApp({databaseUrl:url!,sessionSecret:'store-search-tests-secret-at-least-forty-characters',cookieSecure:false,port:0,host:'127.0.0.1'}),db=app.get(Database),service=app.get(BatchWorkService);let f:any;
 try{f=await seedBatchWork(db,'SEARCH-RANK-'+Date.now(),await hash('fixture-pass'));const grid=f.grids[0][1],actor=f.users[2];
 const add=async(id:string,name:string,address:string,lat=35)=>{const row=(await db.query("INSERT INTO bd_store_task(grid_task_id,store_id,snapshot,status,result,revision,updated_at) VALUES($1,$2,$3,'pending','{}',0,now()) RETURNING id",[grid,id,JSON.stringify({name,address,lat,lon:139})])).rows[0];f.stores[0][1].push(row.id);return row.id;};
 const address=await add('search-address','Other','ABC',35),contains=await add('search-contains','The ABC store','',35.001),prefix=await add('search-prefix','ABC store','',35.002),exact=await add('search-exact','ＡＢＣ','',35.003),identifier=await add('ABC','Else','',35.004);
 const result=await service.visits(actor,grid,{q:'a b c',lat:35,lon:139});assert.equal(result.order,'relevance');assert.equal(result.searchVersion,'terms-nfkc-v2');assert.deepEqual(result.items.map((s:any)=>s.id),[identifier,exact,prefix,contains,address]);assert.equal(result.total,5);assert.equal('search_name' in result.items[0],false);
 const other=await service.stores(actor,grid,{q:'a b c'});assert.deepEqual(other.items.map((s:any)=>s.id),result.items.map((s:any)=>s.id));
 for(const [q,name]of [['％','100% shop'],['_','under_score'],['/','slash/shop']]){const id=await add('search-literal-'+q,name,'');const page=await service.visits(actor,grid,{q});assert.deepEqual(page.items.map((s:any)=>s.id),[id]);}
 const spaced=await add('search-width','ＡＢＣ　商店','');for(const q of ['ABC商店','a b c 商 店','ＡＢＣ\u00a0商店'])assert.equal((await service.visits(actor,grid,{q})).items[0].id,spaced);
 const empty=await service.visits(actor,grid,{lat:35,lon:139});assert.equal(empty.order,'nearest');assert.equal(empty.items[0].id,address);
 assert.equal((await service.visits(actor,grid,{q:'ABC',state:'completed'})).total,0);assert.equal((await service.visits(actor,grid,{q:'ABC',businessStatus:'closed'})).total,0);
 const focused=await service.visits(actor,grid,{q:'ABC',focus:address});assert.equal(focused.items[0].id,address);assert.equal((await service.visits(actor,grid,{q:'not-present',focus:exact})).total,0,'focus never bypasses keyword/scope');
 const otherGrid=f.stores[0][0][0];await db.query("UPDATE bd_store_task SET snapshot=jsonb_build_object('name','outside-only','address','') WHERE id=$1",[otherGrid]);assert.equal((await service.visits(actor,grid,{q:'outside-only'})).total,0);await assert.rejects(()=>service.visits(f.users[4],grid,{q:'ABC'}),{code:'FORBIDDEN'});
 const cross=await add('search-cross','さくら薬局','東京都新宿区'),sameName=await add('search-all-name','新宿さくら薬局','東京都'),partial=await add('search-partial','さくら薬局','渋谷区');
 for(const q of ['新宿 薬局','薬局　新宿','新宿 薬局 新宿']){const page=await service.visits(actor,grid,{q});assert.deepEqual(page.items.map((s:any)=>s.id),[sameName,cross]);assert.equal(page.total,2);assert.deepEqual((await service.stores(actor,grid,{q})).items.map((s:any)=>s.id),[sameName,cross]);}
 assert.equal((await service.visits(actor,grid,{q:'薬局 薬局'})).total,3,'repeating one term does not require a doubled name');
 assert.equal((await service.visits(actor,grid,{q:'新宿 薬局 ない店'})).total,0,'every word must match; no silent token dropping');
 assert.equal((await service.visits(actor,grid,{q:'新宿 %'})).total,0,'multiword punctuation stays literal');
 assert.equal((await service.visits(actor,grid,{q:'薬局',state:'completed'})).total,0);
 await db.query("UPDATE bd_store_task SET result='{\"business_status\":\"closed\"}' WHERE id=$1",[cross]);
 assert.deepEqual((await service.visits(actor,grid,{q:'新宿 薬局',businessStatus:'closed'})).items.map((s:any)=>s.id),[cross]);
 const rankingRows=(await db.query(`WITH search_input AS(SELECT ${storeSearchSql('$1::text')} AS q,${storeSearchTermsSql('$1::text')} AS terms),scoped AS(SELECT ${storeSearchSql("'001'")} AS search_id,${storeSearchSql("'新宿さくら薬局'")} AS search_name,${storeSearchSql("'東京都'")} AS search_address) SELECT * FROM search_input`,['薬局 新宿'])).rows[0];
 assert.deepEqual(rankingRows.terms,[...storeQueryTerms('薬局 新宿')].sort());assert.equal(storeMatchTier('薬局 新宿',{storeId:'001',name:'新宿さくら薬局',address:'東京都'}),4);
 for(let i=0;i<25;i++)await add('search-paged-'+i,'paging sample '+i,'');
 const a=await service.visits(actor,grid,{q:'paging'}),b=await service.visits(actor,grid,{q:'paging',page:2});assert.equal(a.total,25);assert.equal(b.total,25);assert.equal(a.items.length,20);assert.equal(b.items.length,5);assert.equal(new Set([...a.items,...b.items].map((r:any)=>r.id)).size,25);assert.deepEqual((await service.visits(actor,grid,{q:'paging'})).items.map((r:any)=>r.id),a.items.map((r:any)=>r.id));
 const vectors=['ＡＢＣ　商店','ﾀｶｼﾏﾔ','ÉCOLE','Σ','００１２',' A%_/-B ','abc\u0085商店','abc\ufeff商店','abc\u001c商店','abc\u2003商店'];
 const normalized=(await db.query(`SELECT input,${storeSearchSql('input')} AS normalized FROM unnest($1::text[]) AS input`,[vectors])).rows;for(const row of normalized)assert.equal(row.normalized,normalizeStoreQuery(row.input),'SQL/TS normalization '+JSON.stringify(row.input));
 const tokenVectors=['薬局 新宿','new york new','A\u0085B','ＡＢＣ　商店',' / % _ ','\u001c'];const tokens=(await db.query(`SELECT input,${storeSearchTermsSql('input')} AS terms FROM unnest($1::text[]) AS input`,[tokenVectors])).rows;for(const row of tokens)assert.deepEqual(row.terms,[...storeQueryTerms(row.input)].sort());
 }finally{if(f){await db.query('DELETE FROM bd_store_search_log WHERE grid_task_id=ANY($1::int[])',[f.grids.flat()]);await clearBatchWork(db,f);}await app.close();}
});
