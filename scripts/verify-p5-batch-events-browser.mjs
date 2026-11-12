import {browserRelease,isolatedBrowser} from './test-isolation-lib.mjs';
import {createRequire} from 'node:module';
import {chromium} from '@playwright/test';
import {readFile,mkdir,writeFile} from 'node:fs/promises';
import assert from 'node:assert/strict';
import {seedBatchWork,clearBatchWork} from './batch-work-fixture.mjs';
const require=createRequire(new URL('../apps/server/package.json',import.meta.url)),{Pool}=require('pg'),{hash}=require('@node-rs/argon2'),url=process.env.NEXT_TEST_DATABASE_URL;
if(!url||!new URL(url).pathname.endsWith('_refactor_p1'))throw Error('Isolated database required');
const db=new Pool({connectionString:url}),base=process.env.NEXT_BROWSER_BASE_URL||'http://localhost:8080',password='Batch-events-browser-2026';let f,browser;
try{
 f=await seedBatchWork(db,'P5-EVENT-BROWSER',await hash(password));const main=f.mains[0],grid=f.grids[0][0],errors=[];
 await db.query("UPDATE bd_work_grid SET stations=$1 WHERE id=$2",[JSON.stringify(['HistoryStation']),f.baseGrids[0]]);
 browser=await chromium.launch({headless:true,...(process.env.NEXT_BROWSER_EXECUTABLE?{executablePath:process.env.NEXT_BROWSER_EXECUTABLE}:{channel:'chrome'})});
 const page=await browser.newPage({viewport:{width:390,height:844},isMobile:true,hasTouch:true});page.on('pageerror',error=>errors.push(error.message));
 await page.goto(base+'/my/work/batch-grids/'+grid);await page.getByLabel('账号',{exact:true}).fill(f.codes[2]);await page.getByLabel('密码',{exact:true}).fill(password);await page.getByRole('button',{name:'登录',exact:true}).click();await page.locator('.batch-store textarea').waitFor();
 const csrf=await page.evaluate(async()=>{const response=await fetch('/api/auth/session');return (await response.json()).csrfToken;});
 const completion=await page.request.post(base+'/api/batch-work/stores/'+f.stores[0][0][0],{headers:{'x-csrf-token':csrf},data:{revision:0,requestId:crypto.randomUUID(),status:'normal',note:'browser completion'}});assert.equal(completion.status(),201);
 await page.goto(base+`/my/work/batches/${main}/logs`);await page.getByRole('heading',{name:'批次日志与统计',exact:true}).waitFor();await page.locator('.batch-events strong').filter({hasText:/^商户完成$/}).waitFor();
 const events=await page.request.get(base+`/api/batch-work/batches/${main}/events`),payload=await events.json();assert.equal(events.status(),200);assert.equal(Number(payload.metrics.firstCompleted),1);assert.equal(payload.metrics.currentDone,1);assert.equal(payload.metrics.currentTotal,4);
 await page.getByLabel('关键字',{exact:true}).fill('HistoryStation');await page.getByRole('button',{name:'筛选日志',exact:true}).click();await page.locator('.batch-events strong').filter({hasText:/^商户完成$/}).waitFor();assert.match(page.url(),/kw=HistoryStation/);
 await page.getByLabel('关键字',{exact:true}).fill('not-present');await page.getByRole('button',{name:'筛选日志',exact:true}).click();await page.getByText('没有匹配的日志',{exact:true}).waitFor();
 await page.reload();await page.getByText('没有匹配的日志',{exact:true}).waitFor();assert.equal(await page.getByLabel('关键字',{exact:true}).inputValue(),'not-present');
 await db.query("UPDATE users SET lang='ja' WHERE id=$1",[f.users[2]]);await page.goto(base+`/my/work/batches/${main}/logs`);await page.getByRole('heading',{name:'バッチ履歴と集計',exact:true}).waitFor();await page.locator('.batch-events strong').filter({hasText:/^店舗完了$/}).waitFor();
 const overflow=await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);assert.equal(overflow,false);assert.deepEqual(errors,[]);
 const result={ok:true,release:(await browserRelease()).current,metrics:payload.metrics,deepLink:true,keyword:true,refresh:true,japanese:true,overflow,pageErrors:errors};await mkdir('data/refactor',{recursive:true});await writeFile('data/refactor/p5-batch-events-browser.json',JSON.stringify(result,null,2));console.log(JSON.stringify(result));
}finally{await browser?.close();if(f)await clearBatchWork(db,f);await db.end();}
