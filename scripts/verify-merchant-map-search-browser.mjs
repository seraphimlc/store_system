/** UI acceptance uses synthetic responses and the existing isolated browser runtime. */
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
import {googleMapsMock} from './google-maps-mock.mjs';
const browser=await chromium.launch({headless:true,channel:'chrome'});
try{
 const page=await browser.newPage({viewport:{width:1280,height:900}}),errors=[],queries=[];
 page.on('pageerror',error=>errors.push(error.message));
 let fail=false;
 await page.route('**/api/**',async route=>{
  const url=new URL(route.request().url());let body={};
  if(url.pathname==='/api/auth/session')body={user:{id:1,username:'synthetic-admin',displayName:'测试管理员',role:'admin',status:'active',mustChangePassword:false,languagePreference:'zh'},csrfToken:'mock'};
  else if(url.pathname==='/api/merchant-map/config')body={enabled:true,key:'mock'};
  else if(url.pathname==='/api/merchant-map/data')body={mode:'points',points:[],cells:[],dense:false,summary:{merchant_count:0,covered_count:0,coverage_pct:0},as_of:new Date().toISOString()};
  else if(url.pathname==='/api/merchant-map/search'){
   queries.push(Object.fromEntries(url.searchParams));
   if(fail){await route.fulfill({status:503,json:{error:{code:'UNAVAILABLE',message:'搜索暂不可用'}}});return;}
   const q=url.searchParams.get('q'),current=Number(url.searchParams.get('page'));
   body={total:q==='空结果'?0:21,page:current,pageSize:20,items:q==='空结果'?[]:[{merchantId:'outside-viewport-'+current,name:'合成商户 '+current,address:'大阪市测试地址',lat:34.69,lon:135.5,businessStatus:current===1?'closed':null}]};
  }
  await route.fulfill({json:body});
 });
 await page.route('https://maps.googleapis.com/maps/api/js?**',route=>route.fulfill({contentType:'application/javascript',body:googleMapsMock}));
 await page.goto((process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8001')+'/admin/merchant-map');
 const input=page.getByRole('textbox',{name:'搜索全地图商户',exact:true}),results=page.getByLabel('商户搜索结果',{exact:true});
 await input.fill('合成商户');await expect(results).toContainText('全地图 · 21 个结果');await expect(results).toContainText('大阪市测试地址');await expect(results).toContainText('店铺状态：关闭');
 await results.getByRole('button',{name:/合成商户 1/}).click();await page.waitForFunction(()=>window.__storeMapMock.center.lat===34.69&&window.__storeMapMock.getZoom()===18);
 assert.ok(queries.every(q=>!('west' in q)&&!('east' in q)));
 await results.getByRole('button',{name:'下一页',exact:true}).click();await expect(results).toContainText('合成商户 2');await expect(results).toContainText('店铺状态：未记录');
 await expect(results.getByRole('link',{name:'查看详情',exact:true})).toHaveAttribute('href','/admin/merchant-map/outside-viewport-2');
 await input.fill('空结果');await expect(results).toContainText('未找到商户');
 fail=true;await input.fill('错误');await expect(results.getByRole('alert')).toBeVisible();fail=false;await results.getByRole('button',{name:'重试',exact:true}).click();await expect(results).toContainText('合成商户 1');
 await input.press('Escape');await expect(results).toHaveCount(0);await input.press('ArrowDown');await expect(results).toHaveCount(1);
 await page.setViewportSize({width:390,height:844});assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
 const bounds=await results.boundingBox();assert.ok(bounds&&bounds.width<=366);
 await page.getByRole('button',{name:'清空搜索',exact:true}).click();await expect(input).toHaveValue('');await expect(results).toHaveCount(0);assert.deepEqual(errors,[]);
 console.log('[merchant-map-search] passed: global query, name/address/status, map focus, pagination, empty/error/retry, Escape, clear, mobile bounds; all external APIs mocked');
}finally{await browser.close();}
