import {browserRelease} from './test-isolation-lib.mjs';
import {createRequire} from 'node:module';
import {chromium} from '@playwright/test';
import {mkdir,readFile,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {seedBatchWork,clearBatchWork} from './batch-work-fixture.mjs';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{Pool}=require('pg'),{hash}=require('@node-rs/argon2'),url=process.env.NEXT_TEST_DATABASE_URL;
if(!url||!new URL(url).pathname.endsWith('_refactor_p1'))throw Error('Isolated database required');
const db=new Pool({connectionString:url}),base=process.env.NEXT_BROWSER_BASE_URL||'http://localhost:8080',password='Grid-enable-browser-2026';let f,browser;
try{
 f=await seedBatchWork(db,'P5-GEB-'+Date.now().toString(36),await hash(password));const grid=f.baseGrids[0],protectedGrid=f.baseGrids[1],errors=[];
 await db.query("UPDATE bd_work_grid SET state='disabled',task_state='unvisited' WHERE id=ANY($1::int[])",[[grid,protectedGrid]]);await db.query('UPDATE bd_work_grid SET zone_id=NULL WHERE id=$1',[grid]);
 const before=(await db.query('SELECT geometry,store_count,area_km2 FROM bd_work_grid WHERE id=$1',[grid])).rows[0],snapshots=(await db.query('SELECT id,snapshot FROM bd_grid_task WHERE grid_id=$1 ORDER BY id',[grid])).rows;
 browser=await chromium.launch({headless:true,channel:'chrome'});const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base+'/admin/zones');await page.getByLabel('账号',{exact:true}).fill(f.codes[0]);await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.getByRole('button',{name:'网格管理',exact:true}).click();await page.locator('.grid-availability').getByRole('button',{name:'收起网格地图',exact:true}).click();
 const section=page.locator('.grid-availability');await section.getByLabel('搜索基础网格',{exact:true}).fill(f.prefix);await section.getByRole('button',{name:'筛选网格',exact:true}).click();const card=section.locator(`[data-grid-id="${grid}"]`),protectedCard=section.locator(`[data-grid-id="${protectedGrid}"]`);
 await card.getByRole('button',{name:'启用网格',exact:true}).click();await card.getByRole('button',{name:'取消启用',exact:true}).click();assert.equal((await db.query('SELECT state FROM bd_work_grid WHERE id=$1',[grid])).rows[0].state,'disabled');
 assert.equal(await protectedCard.getByRole('button',{name:'启用网格',exact:true}).count(),0);await protectedCard.getByText('已有片区、派发或巡店记录，无法启用。',{exact:true}).waitFor();
 await card.getByRole('button',{name:'启用网格',exact:true}).click();const [response]=await Promise.all([page.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/api/zone-assets/grids/${grid}/enable`),card.getByRole('button',{name:'确认启用网格',exact:true}).click()]);assert.equal(response.status(),201);await section.getByText('网格已转为可用，可继续划入片区',{exact:true}).waitFor();await card.waitFor({state:'detached'});
 await section.getByLabel('网格状态',{exact:true}).selectOption('ready');await section.getByRole('button',{name:'筛选网格',exact:true}).click();await card.getByRole('heading').waitFor();assert.equal(await card.getByRole('button',{name:'启用网格',exact:true}).count(),0);
 assert.deepEqual((await db.query('SELECT geometry,store_count,area_km2 FROM bd_work_grid WHERE id=$1',[grid])).rows[0],before);assert.deepEqual((await db.query('SELECT id,snapshot FROM bd_grid_task WHERE grid_id=$1 ORDER BY id',[grid])).rows,snapshots);
 await page.getByRole('button',{name:'中文 / 日本語',exact:true}).click();await page.getByRole('button',{name:'グリッド管理',exact:true}).waitFor();assert.equal(await section.getByLabel('グリッド状態',{exact:true}).inputValue(),'ready');
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false);assert.deepEqual(errors,[]);
 const result={ok:true,release:(await browserRelease()).current,search:true,cancel:true,protectedGrid:true,enable:response.status(),readyFilter:true,geometryPreserved:true,batchSnapshotPreserved:true,japanese:true,overflow,pageErrors:errors};await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/p5-grid-enable-browser.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser?.close();if(f){await db.query("DELETE FROM bd_log WHERE domain='grid' AND ref_id=ANY($1::int[]) AND actor=$2",[f.baseGrids,f.codes[0]]);await clearBatchWork(db,f);}await db.end();}
