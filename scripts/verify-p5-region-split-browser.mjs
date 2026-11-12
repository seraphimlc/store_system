import {browserRelease} from './test-isolation-lib.mjs';
import {createRequire} from 'node:module';
import {chromium} from '@playwright/test';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {seedBatchWork,clearBatchWork} from './batch-work-fixture.mjs';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{Pool}=require('pg'),{hash}=require('@node-rs/argon2'),url=process.env.NEXT_TEST_DATABASE_URL;
if(!url||!new URL(url).pathname.endsWith('_refactor_p1'))throw Error('Isolated database required');
const db=new Pool({connectionString:url}),base=process.env.NEXT_BROWSER_BASE_URL||'http://localhost:8080',password='Region-split-browser-2026';let f,browser;
try{
 f=await seedBatchWork(db,'P5-RSB-'+Date.now().toString(36),await hash(password));const id=f.region,errors=[];
 await db.query("UPDATE bd_work_grid SET task_state='unvisited' WHERE id=ANY($1::int[])",[f.baseGrids]);
 const snapshots=(await db.query('SELECT id,snapshot,revision FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows;
 const before=(await db.query('SELECT id,geometry,area_km2,store_count,state FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows;
 browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/admin/zones/'+id);await page.getByLabel('账号',{exact:true}).fill(f.codes[0]);await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();
 const split=page.locator('.region-split');await split.getByRole('button',{name:'预览片区拆分',exact:true}).click();await split.getByRole('button',{name:'确认拆分并归档',exact:true}).waitFor();
 assert.deepEqual((await db.query('SELECT state,revision FROM bd_region WHERE id=$1',[id])).rows[0],{state:'ready',revision:0});
 await split.getByRole('button',{name:'取消拆分',exact:true}).click();assert.equal(await split.getByRole('button',{name:'确认拆分并归档',exact:true}).count(),0);
 // Confirm uses a complete content token; a changed grid must invalidate the prepared action.
 await split.getByRole('button',{name:'预览片区拆分',exact:true}).click();await split.getByRole('button',{name:'确认拆分并归档',exact:true}).waitFor();await db.query("UPDATE bd_work_grid SET revision=revision+1 WHERE id=$1",[f.baseGrids[0]]);
 const [conflict]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/api/zone-assets/regions/${id}/split`),split.getByRole('button',{name:'确认拆分并归档',exact:true}).click()]);assert.equal(conflict.status(),409);assert.equal((await conflict.json()).code,'PREVIEW_CHANGED');await split.getByRole('alert').waitFor();assert.equal((await db.query('SELECT state FROM bd_region WHERE id=$1',[id])).rows[0].state,'ready');
 await page.getByRole('dialog').getByRole('button',{name:'关闭',exact:true}).click();await page.getByRole('button',{name:'中文 / 日本語',exact:true}).click();await page.goto(base+'/admin/zones/'+id);await split.getByRole('heading',{name:'基礎地域を分割',exact:true}).waitFor();await split.getByRole('button',{name:'地域分割をプレビュー',exact:true}).click();await split.getByRole('button',{name:'分割とアーカイブを確定',exact:true}).waitFor();
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false);
 const [done]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/api/zone-assets/regions/${id}/split`),split.getByRole('button',{name:'分割とアーカイブを確定',exact:true}).click()]);assert.equal(done.status(),201);
 await page.getByText('地域を分割してアーカイブしました。グリッドと過去のバッチは保持されています',{exact:true}).waitFor();assert.match(page.url(),/\/admin\/zones\?/);
 assert.deepEqual((await db.query('SELECT state,revision,store_count,workload FROM bd_region WHERE id=$1',[id])).rows[0],{state:'archived',revision:1,store_count:0,workload:null});
 assert.equal((await db.query('SELECT id FROM bd_work_grid WHERE zone_id=$1',[id])).rowCount,0);
 assert.deepEqual((await db.query('SELECT id,geometry,area_km2,store_count,state FROM bd_work_grid WHERE id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows,before);
 assert.deepEqual((await db.query('SELECT id,snapshot,revision FROM bd_grid_task WHERE grid_id=ANY($1::int[]) ORDER BY id',[f.baseGrids])).rows,snapshots);
 assert.deepEqual(errors,[]);const result={ok:true,release:(await browserRelease()).current,previewReadOnly:true,cancel:true,stalePreview:409,split:201,archived:true,geometryPreserved:true,batchSnapshotPreserved:true,japanese:true,overflow,pageErrors:errors};await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/p5-region-split-browser.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser?.close();if(f){await db.query("DELETE FROM bd_log WHERE domain='zone' AND ref_id=$1 AND actor=$2",[f.region,f.codes[0]]);await clearBatchWork(db,f);}await db.end();}
