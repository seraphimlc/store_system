import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage({viewport:{width:393,height:852}}),errors=[],calls=[];page.on('pageerror',e=>errors.push(e.message));
 const records=[{id:101,revision:3,state:'pending',reason:'',name:'新店甲',address:'测试地址',lat:35,lon:139,submittedBy:20,submitterName:'提交队员',gridId:11,regionId:8,canReview:true}];
 await page.route('**/api/**',async route=>{const request=route.request(),url=new URL(request.url()),path=url.pathname.slice(4);let data={items:[],total:0};
 if(path==='/auth/session')data={csrfToken:'fixture',user:{id:10,username:'captain',displayName:'队长',personCode:'CAP',role:'leader',status:'active',mustChangePassword:false,languagePreference:'zh'}};
 else if(path==='/batch-work/batches/3/mobile')data={main:{id:3,title:'批次验收',progress:60},regions:[{id:8,snapshot:{name:'负责片区'},progress:50,summary:{grids:1,total:20,completed:10},grids:[]}]};
 else if(path==='/batch-work/batches/3/proposals'){
 calls.push(Object.fromEntries(url.searchParams));const state=url.searchParams.get('state');data={items:records.filter(r=>!state||r.state===state),total:records.filter(r=>!state||r.state===state).length,pageSize:50,canReview:true,canApprove:true,regions:[{id:8,name:'负责片区'}],grids:[{id:11,name:'网格甲',regionId:8}],submitters:[{id:20,name:'提交队员'}]};
 }else if(path==='/batch-work/batches/3/proposal-review'){calls.push(request.postDataJSON());records[0].state=request.postDataJSON().action==='approve'?'approved':'rejected';data={ok:true};}
 await route.fulfill({status:200,contentType:'application/json',body:JSON.stringify(data)});
 });
 await page.goto('http://127.0.0.1:8002/my/work/batches/3');await expect(page.locator('.work-batch-summary')).toHaveText('批次进度 60% · 10/20');
 const bounds=await page.locator('.work-batch-toolbar').evaluate(el=>{const left=el.querySelector('.work-batch-summary').getBoundingClientRect(),right=el.querySelector('.work-batch-tools').getBoundingClientRect();return {separate:left.right<=right.left,overflow:document.documentElement.scrollWidth>innerWidth};});assert.deepEqual(bounds,{separate:true,overflow:false});
 await page.getByRole('button',{name:'新店核实',exact:true}).click();await expect(page.locator('.batch-proposal-list>li')).toHaveCount(1);assert.equal(await page.locator('details.store-proposals').count(),0);
 await expect(page.getByText('提交人：提交队员 · 网格甲')).toBeVisible();await expect(page.getByRole('link',{name:'门店照片',exact:true})).toHaveAttribute('href','/api/batch-work/proposals/101/photo');
 await page.getByLabel('片区',{exact:true}).selectOption('8');await page.getByLabel('网格',{exact:true}).selectOption('11');await page.getByLabel('提交人',{exact:true}).selectOption('20');await expect(page.locator('.batch-proposal-list>li')).toHaveCount(1);
 await page.getByLabel('选择核实 新店甲',{exact:true}).check();await expect(page.getByRole('button',{name:'批量驳回',exact:true})).toBeDisabled();await page.getByRole('button',{name:'批量通过',exact:true}).click();await expect(page.getByText('核实结果已保存',{exact:true})).toBeVisible();await expect(page.getByText('暂无新店申请',{exact:true})).toBeVisible();
 assert.ok(calls.some(c=>c.region==='8'&&c.grid==='11'&&c.submitter==='20'));assert.deepEqual(calls.find(c=>c.action),{action:'approve',reason:'',selections:[{id:101,revision:3}]});
 await page.getByLabel('核实状态',{exact:true}).selectOption('approved');await expect(page.locator('.batch-proposal-list>li')).toHaveCount(1);assert.equal(await page.getByLabel('选择核实 新店甲',{exact:true}).count(),0);
 assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,batchScopeSummary:true,rightAlignedActions:true,unifiedApplicationList:true,fourFilters:true,revisionReview:true,emptyAfterReview:true,overflow:false,errors,dockerUsed:false}));
}finally{await browser.close();}
