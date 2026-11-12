import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const errors=[];
 for(const located of [true,false]){
 const context=await browser.newContext({viewport:{width:393,height:852}}),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(located=>{navigator.geolocation.getCurrentPosition=(ok,fail)=>located?ok({coords:{latitude:35,longitude:139}}):fail({code:1});},located);
 await page.route('**/api/**',async route=>{const u=new URL(route.request().url());let body={};
 if(u.pathname==='/api/auth/session')body={csrfToken:'test',user:{id:2,username:'test',displayName:'验收',personCode:'TEST',role:'staff',status:'active',languagePreference:'zh',mustChangePassword:false}};
 else if(u.pathname.endsWith('/visits')){const q=u.searchParams.get('q')||'';if(q==='失败'){await route.fulfill({status:503,json:{message:'请求失败'}});return;}const items=q==='杂货店'?[]:[{id:1,storeId:'S1',snapshot:{name:'测试商店'},distanceMeters:250,status:'pending',result:{},photos:[],revision:0,profileRevision:0},{id:2,storeId:'S2',snapshot:{name:'第二家商店'},distanceMeters:120000,status:'pending',result:{},photos:[],revision:0,profileRevision:0}];body={items,total:items.length,pageSize:20,grid:{id:11,snapshot:{name:'网格验收'}},region:{id:8},main:{id:3},canExecute:false};if(q)await new Promise(r=>setTimeout(r,200));}
 else if(u.pathname.endsWith('map-config'))body={enabled:false,key:null};else if(u.pathname==='/plans/mine')body={pending:null};
 await route.fulfill({json:body});});
 await page.goto((process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8002')+'/my/work/batch-grids/11');const input=page.getByRole('textbox',{name:'地图搜索门店',exact:true});await input.waitFor();
 await input.fill('商店');await expect(page.locator('.work-search-feedback')).toContainText('正在搜索');await expect(page.locator('.work-search-feedback')).toContainText('找到 2 家门店');await expect(page.locator('.work-search-preview>li')).toHaveCount(2);await expect(page.locator('.work-preview-tag')).toHaveCount(4);const dimensions=await page.locator('.work-search-preview').evaluate(el=>({width:el.getBoundingClientRect().width,parent:el.parentElement.clientWidth-16,border:getComputedStyle(el).borderTopWidth}));assert.ok(Math.abs(dimensions.width-dimensions.parent)<2);assert.equal(dimensions.border,'0px');assert.equal(await page.locator('.work-search-preview button').first().evaluate(el=>getComputedStyle(el).height),'30px');await page.getByRole('button',{name:'查看列表',exact:true}).click();await expect(page.locator('.work-store-list>li')).toHaveCount(2);
 assert.deepEqual(await page.locator('.work-store-distance').allTextContents(),located?['250m','100km+']:['未获取定位','未获取定位']);
 await page.getByRole('textbox',{name:'抽屉搜索门店',exact:true}).fill('杂货店');await expect(page.locator('.work-search-empty')).toContainText('当前网格未找到「杂货店」相关门店');assert.equal(await page.locator('.work-filter-results .h5-scroll-status').count(),0);
 await page.locator('.adm-mask:visible').last().click({position:{x:10,y:10}});await expect(page.locator('.work-search-feedback')).toContainText('当前网格未找到');await page.getByRole('button',{name:'清空搜索',exact:true}).click();await expect(input).toHaveValue('');await expect(page.locator('.work-search-feedback')).toHaveCount(0);
 await input.fill('失败');await expect(page.locator('.work-search-feedback')).toContainText('搜索失败，请重试');await expect(page.locator('.work-search-feedback button')).toHaveText('重试');assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);await context.close();
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,loading:true,resultsEntry:true,emptyState:true,clear:true,requestError:true,distanceUnits:true,locationDenied:true,overflow:false,errors}));
}finally{await browser.close();}
