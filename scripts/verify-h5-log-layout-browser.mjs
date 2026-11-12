import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage({viewport:{width:393,height:852}}),queries=[],errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.route('**/api/**',async route=>{const url=new URL(route.request().url());let body={items:[],total:0};
 if(url.pathname==='/api/auth/session')body={csrfToken:'test',user:{id:1,username:'fixture',displayName:'验收',personCode:'FIX',role:'staff',status:'active',languagePreference:'zh',mustChangePassword:false}};
 if(url.pathname.endsWith('/events')){queries.push(Object.fromEntries(url.searchParams));body={items:[{id:1,event_type:'assign_grid_task',actor_label:'队员',team_label:'团队',region_label:'长名称片区'.repeat(5),grid_label:'网格',created_at:'2026-11-12T00:00:00',old_value:{name:'旧值'.repeat(100)},new_value:{name:'新值'}}],total:1,limit:30,actors:[{id:2,label:'队员'}],teams:[],regions:[],grids:[],eventTypes:['assign_grid_task'],metrics:{firstCompleted:1,completionEvents:2,currentDone:3,currentTotal:10,performers:[]}};}
 await route.fulfill({contentType:'application/json',body:JSON.stringify(body)});});
 await page.goto('http://127.0.0.1:8002/my/work/batches/3/logs');await expect(page.locator('.batch-log-entry')).toHaveCount(1);
 assert.equal(await page.locator('.batch-events h3').count(),0);assert.equal(await page.locator('.batch-log-advanced').getAttribute('open'),null);
 const beforeSearch=queries.length;await page.getByRole('button',{name:'搜索',exact:true}).click();await expect.poll(()=>queries.length).toBeGreaterThan(beforeSearch);
 await page.getByText('更多筛选',{exact:true}).click();await expect(page.getByRole('combobox',{name:'操作人',exact:true})).toBeVisible();await page.getByRole('combobox',{name:'操作人',exact:true}).selectOption('2');await page.getByRole('button',{name:'筛选日志',exact:true}).click();await expect.poll(()=>queries.at(-1)?.actorId).toBe('2');
 await page.getByText('变更详情',{exact:true}).click();await expect(page.locator('.batch-log-entry pre')).toBeVisible();
 const layout=await page.evaluate(()=>({overflow:document.documentElement.scrollWidth>innerWidth,columns:getComputedStyle(document.querySelector('.batch-log-metrics')).gridTemplateColumns.split(' ').length,detailsScroll:getComputedStyle(document.querySelector('.batch-log-entry pre')).overflowX}));assert.deepEqual(layout,{overflow:false,columns:3,detailsScroll:'auto'});assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,collapsedFilters:true,search:true,actorFilter:true,details:true,...layout,errors}));
}finally{await browser.close();}
