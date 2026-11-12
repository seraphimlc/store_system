import {browserRelease,isolatedBrowser} from './test-isolation-lib.mjs';
import {createRequire} from 'node:module';
import {chromium} from '@playwright/test';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {seedBatchWork,clearBatchWork} from './batch-work-fixture.mjs';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{Pool}=require('pg'),{hash}=require('@node-rs/argon2'),url=process.env.NEXT_TEST_DATABASE_URL;
if(!url||!new URL(url).pathname.endsWith('_refactor_p1'))throw Error('Isolated database required');
const db=new Pool({connectionString:url}),base=process.env.NEXT_BROWSER_BASE_URL||'http://localhost:8080',password='Batch-disable-browser-2026';let f,browser;
try{
 f=await seedBatchWork(db,'P5-DISABLE-BROWSER',await hash(password));const main=f.mains[0],grid=f.grids[0][0],errors=[];
 browser=await chromium.launch({headless:true,...(process.env.NEXT_BROWSER_EXECUTABLE?{executablePath:process.env.NEXT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
 async function login(index,path){const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});page.on('pageerror',error=>errors.push(error.message));await page.goto(base+path);await page.getByLabel('账号',{exact:true}).fill(f.codes[index]);await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();return page;}
 const staff=await login(2,'/my/work/batch-grids/'+grid);await staff.locator('.batch-store textarea').fill('stale form must reject');
 assert.equal(await staff.getByRole('button',{name:'禁用批次',exact:true}).count(),0);
 const admin=await login(0,'/admin/zone-tasks/'+main);await admin.getByRole('button',{name:'禁用批次',exact:true}).click();await admin.getByRole('button',{name:'取消禁用',exact:true}).click();assert.equal((await db.query('SELECT status FROM bd_main_task WHERE id=$1',[main])).rows[0].status,'active');
 await admin.getByRole('button',{name:'禁用批次',exact:true}).click();const [disabled]=await Promise.all([admin.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/api/batch-work/batches/${main}/disable`),admin.getByRole('button',{name:'确认禁用批次',exact:true}).click()]);assert.equal(disabled.status(),201);await admin.getByText('批次状态：已禁用',{exact:true}).waitFor();
 const [blocked]=await Promise.all([staff.waitForResponse(r=>r.request().method()==='POST'&&new URL(r.url()).pathname===`/api/batch-work/stores/${f.stores[0][0][0]}`),staff.getByRole('button',{name:'提交巡店完成',exact:true}).click()]);assert.equal(blocked.status(),409);assert.equal((await blocked.json()).code,'BATCH_DISABLED');
 await staff.reload();await staff.getByText('当前为只读：仅在岗的本次网格负责人可执行，批次完成或禁用后不接受新修改。',{exact:true}).waitFor();assert.equal(await staff.getByRole('button',{name:'提交巡店完成',exact:true}).isDisabled(),true);
 await admin.getByRole('button',{name:'查看批次日志',exact:true}).click();await admin.locator('.batch-events strong').filter({hasText:/^禁用批次$/}).waitFor();
 await db.query("UPDATE users SET lang='ja' WHERE id=$1",[f.users[0]]);await admin.goto(base+'/admin/zone-tasks/'+main);await admin.getByText('バッチ状態：無効',{exact:true}).waitFor();
 const overflow=await admin.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false);assert.deepEqual(errors,[]);
 const state=(await db.query('SELECT status,result,revision FROM bd_store_task WHERE id=$1',[f.stores[0][0][0]])).rows[0];assert.deepEqual(state,{status:'pending',result:{},revision:0});
 const result={ok:true,release:(await browserRelease()).current,cancelPreservesState:true,disableStatus:disabled.status(),staleSubmission:blocked.status(),history:true,storeUnchanged:true,japanese:true,overflow,pageErrors:errors};await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/p5-batch-disable-browser.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser?.close();if(f)await clearBatchWork(db,f);await db.end();}
