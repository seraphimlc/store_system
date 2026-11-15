import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage({viewport:{width:393,height:852}}),requests=[],errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 const today=new Intl.DateTimeFormat('en-CA',{timeZone:'Asia/Tokyo'}).format(new Date());
 await page.route('**/api/**',async route=>{const url=new URL(route.request().url()),path=url.pathname;requests.push(path);let body={items:[],total:0};
 if(path==='/api/auth/session')body={csrfToken:'test',user:{id:2,username:'test',displayName:'员工',personCode:'T',role:'staff',status:'active',mustChangePassword:false,languagePreference:'zh'}};
 else if(path==='/api/reports/mine')body={items:[{id:1,date:today,area:'自报区域',p1:3,p2:2,total:5,points:7}],summary:{total:5,points:7},canViewTeam:false};
 else if(path==='/api/self-report/mine')body={date:today,report:{area:'自报区域',p1:3,p2:2},token:'0'.repeat(64),items:[],locked:false};
 else if(path==='/api/plans/mine')body={pending:null};
 await route.fulfill({json:body});});
 for(const path of ['/my/report','/my/perf']){await page.goto((process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8012')+path);await expect(page.locator('.adm-nav-bar h1')).toHaveText('每日填报');await expect(page.locator('.report-history')).toContainText('自报区域');await expect(page.locator('.report-history')).toContainText('7 点数');await expect(page.getByRole('button',{name:'修改今日日报',exact:true})).toBeVisible();}
 await page.getByRole('button',{name:'菜单',exact:true}).click();await expect(page.locator('#staff-menu').getByText('每日填报',{exact:true})).toBeVisible();assert.equal(await page.locator('#staff-menu').getByText('我的绩效',{exact:true}).count(),0);assert.ok(!requests.some(p=>p.startsWith('/api/payroll/')));assert.deepEqual(errors,[]);
 console.log(JSON.stringify({ok:true,ownReport:true,history:true,editableToday:true,legacyPerfShowsReports:true,payrollRequests:0,errors}));
}finally{await browser.close();}
