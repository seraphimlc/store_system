import {chromium,expect} from '@playwright/test';
async function openModule(page,label){await page.getByRole('button',{name:/^(菜单|メニュー)$/,exact:true}).click();await page.locator('#staff-menu').getByText(label,{exact:true}).click();}

import assert from 'node:assert/strict';
const base=process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8002';
const browser=await chromium.launch({headless:true,channel:'chrome'});
const errors=[],unhandled=[],results=[];
const half={amount:500,adjustment:-100,payable:400};
const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tokyo',year:'numeric',month:'2-digit',day:'2-digit'}).format(new Date());
const month=today.slice(0,7);
try {
 for(const role of ['staff','leader','admin']) {
  const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
  let failSave=true,saved=null;
  const page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
   const url=new URL(route.request().url()),path=url.pathname.slice(4);
   let body;
   if(path==='/auth/session')body={csrfToken:'fixture',user:{id:1,username:'fixture',displayName:'移动验收',personCode:'fixture',role,status:'active',language:'zh',languagePreference:'zh',mustChangePassword:false}};
   else if(path==='/messages')body={items:[],total:0,unread:3,page:1,pageSize:30};
   else if(path==='/work/batches')body={items:[{id:1,title:'东京巡店批次',status:'active',progress:50}]};
   else if(path==='/station-tasks')body={items:[{id:1,label:'东京车站任务',state:'doing',pct:20,teamId:1}],total:1};
   else if(path==='/payroll/mine/months')body={items:[month]};
   else if(path==='/payroll/mine')body={rows:[{half1:half,half2:half,recoveredThisMonth:0,unrecovered:0,payments:[]}],performance:[{records:3,points:4,p1:2,p2:1,salary:1000}]};
   else if(path==='/payroll/mine/daily')body={items:[{date:today,records:3,p1:2,p2:1,points:4}],total:1,pageSize:30,monthSummary:{records:3,points:4}};
   else if(path==='/reports/feedback')body={periods:[{id:1,start:today,end:today}],selected:{id:1,start:today,end:today},person:{sys_total:3,rep_total:2,acc:0.66,gaps:1},days:[{date:today,sys_total:3,rep_total:2,dt:1,acc:0.66}]};
   else if(path==='/reports/mine')body={items:[],summary:{p1:0,p2:0,total:0,points:0},coverage:{start:null,end:null}};
   else if(path==='/self-report/mine'){
    if(route.request().method()==='POST'){
     const input=route.request().postDataJSON();if(failSave){failSave=false;await route.fulfill({status:409,json:{code:'TASK_CHANGED',message:'任务已变化，日报与进度未保存，请刷新后重试',requestId:'fixture'}});return;}
     saved=input;body={ok:true,tasks:1};
    }else body={items:[{taskId:1,label:'联合提交测试车站',pct:20,currentPct:20,reportedPct:null,review:'pending',editable:true,token:'0'.repeat(64),storeCount:null}],date:today,personCode:'fixture',backfill:!saved,locked:false,report:saved,token:'0'.repeat(64)};
   }
   else if(path==='/plans/mine')body={key:month+'-1',start:today,end:today,openAt:today,closeAt:today,deadline:today,today,window_open:true,late_join:false,days:[{date:today,wd:3,editable:true,available:true,off:false,mark:'○'}],editable_cnt:1,free_cnt:1,off_cnt:0,done_cnt:0,previous:month+'-1',next:month+'-2',leave:null};
   else if(path==='/teams')body={items:[]};
   else {unhandled.push(path);body={items:[],total:0};}
   await route.fulfill({json:body});
  });
  await page.goto(base+'/my/work');await page.getByText('东京巡店批次',{exact:true}).waitFor();
  assert.equal(await page.locator('.management-table').count(),0);
  const card=page.locator('.work-card-button').first();
  const cardLayout=await card.evaluate(node=>({outer:node.clientWidth,inner:node.querySelector('.work-row').getBoundingClientRect().width}));assert.ok(cardLayout.inner>=cardLayout.outer-34);
  assert.equal(await page.locator('.bottom-navigation').count(),0);
  const verify=async(label)=>{
   const metrics=await page.evaluate(()=>{
    const content=document.querySelector('.staff-content'),shell=document.querySelector('.staff-shell');
    const style=getComputedStyle(content);
    const small=[...document.querySelectorAll('.staff-content button,.staff-content summary,.staff-menu-trigger,.adm-tab-bar-item')].filter(el=>!el.disabled&&el.getBoundingClientRect().width&&el.getBoundingClientRect().height).filter(el=>{const r=el.getBoundingClientRect();return r.width<43.9||r.height<43.9;}).map(el=>({text:el.textContent?.trim(),width:el.getBoundingClientRect().width,height:el.getBoundingClientRect().height}));
    return {padding:[style.paddingLeft,style.paddingRight],overflow:document.documentElement.scrollWidth>innerWidth,tabHeight:0,bottomPadding:parseFloat(getComputedStyle(shell).paddingBottom),small};
   });
   assert.deepEqual(metrics.padding,['16px','16px']);assert.equal(metrics.overflow,false,label);assert.ok(metrics.bottomPadding>=metrics.tabHeight+16,label);assert.deepEqual(metrics.small,[],label);results.push({role,label,...metrics});
  };
  for(const width of [390,320]){
   await page.setViewportSize({width,height:844});
   for(const label of [role==='leader'?'任务分派':'我的任务','我的绩效','每日填报','出勤计划','巡店任务']){
    await openModule(page,label);
    await page.locator('.staff-content').waitFor();
    if(label==='我的绩效'){await page.locator('.daily-data-list li').waitFor();assert.equal(await page.locator('.my-daily-performance table').count(),0);}
    if(label==='每日填报')await page.locator('.h5-report-form').waitFor();
    if(label==='出勤计划')await page.locator('.plan-day').waitFor();
    await verify(width+':'+label);
   }
  }
  await openModule(page,role==='leader'?'任务分派':'我的任务');
  const form=page.locator('.h5-report-form');await form.locator('input[name=p1]').fill('8');await form.locator('input[name=p2]').fill('2');
  await form.getByLabel('进度 联合提交测试车站',{exact:true}).fill('50');
  await form.getByRole('button',{name:'提交今日自报与任务进度',exact:true}).click();await page.getByRole('alert').filter({hasText:'任务已变化'}).waitFor();
  assert.equal(saved,null);await expect(form.locator('input[name=p1]')).toHaveValue('8');await expect(form.getByLabel('进度 联合提交测试车站',{exact:true})).toHaveValue('50');
  await form.getByRole('button',{name:'提交今日自报与任务进度',exact:true}).click();await page.getByRole('status').filter({hasText:'今日填报已保存'}).waitFor();
  assert.equal(saved.p1,8);assert.equal(saved.p2,2);assert.equal(saved.items[0].pct,50);await verify('联合提交与失败保留草稿');
  await page.getByRole('button',{name:'菜单',exact:true}).click();await page.locator('#staff-menu').getByText('核对结果',{exact:true}).click();await page.locator('.my-feedback-page .daily-data-list li').waitFor();await verify('核对结果');
  assert.match(page.url(),/\/my\/report\/feedback$/);await page.reload();await page.locator('.my-feedback-page .daily-data-list li').waitFor();assert.equal(await page.locator('.h5-report-form').count(),0);
  await page.getByRole('button',{name:'菜单',exact:true}).click();await page.locator('#staff-menu').getByRole('button',{name:'中文 / 日本語'}).click();await page.locator('#staff-menu').getByText('メッセージ',{exact:true}).click();await page.locator('.message-center').waitFor();await verify('日本語メニュー');
  await page.getByRole('button',{name:'メニュー',exact:true}).click();await page.locator('#staff-menu').getByRole('button',{name:'閉じる',exact:true}).click();
  await page.goBack();await page.locator('.my-feedback-page').waitFor();
  if(role==='staff'){await page.setViewportSize({width:390,height:844});await openModule(page,'日次報告');await page.locator('.h5-report-form').waitFor();await page.screenshot({path:'/tmp/h5-four-modules-implemented.png'});}
  await page.getByRole('button',{name:'メニュー',exact:true}).click();
  const inline=await page.locator('#staff-menu>.adm-button').last().evaluate(n=>{const x=n.querySelector('.h5-button-content'),svg=x.querySelector('svg').getBoundingClientRect(),r=x.getBoundingClientRect();return {display:getComputedStyle(x).display,height:r.height,iconHeight:svg.height};});assert.equal(inline.display,'inline-flex');assert.ok(inline.height<40);
  await page.locator('#staff-menu').getByText('マイアカウント',{exact:true}).click();
  assert.equal(await page.locator('.logout-button .h5-button-content').count(),1);
  assert.equal(await page.locator('.staff-content>section>h1').count(),0);
  await context.close();
 }
 assert.deepEqual(errors,[]);assert.deepEqual(unhandled,[]);
 console.log(JSON.stringify({ok:true,checks:results.length,roles:['staff','leader','admin'],widths:[320,390],api:'mocked; no business writes',pageErrors:errors,unhandled,results}));
} finally {await browser.close();}
