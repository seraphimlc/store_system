// Read-only guide/navigation regression against an existing staff dev service.
// Every API request is mocked; this script never connects to a business database.
import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'chrome',headless:true});
const base=process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8002';
const results=[];
try{
 for(const role of ['staff','leader','admin']){
  const context=await browser.newContext({viewport:{width:390,height:844},locale:'zh-CN',serviceWorkers:'block'});
  const page=await context.newPage(),errors=[],writes=[];
  page.on('pageerror',error=>errors.push(error.message));
  await page.route('**/api/**',async route=>{
   const request=route.request(),path=new URL(request.url()).pathname.slice(4);
   if(request.method()!=='GET'){writes.push(path);await route.fulfill({status:405,json:{message:'Read-only guide regression'}});return;}
   const session={csrfToken:'fixture',landingPath:'/my/work',user:{id:1,username:'guide-fixture',displayName:'指南验收',role,personCode:'GUIDE',status:'active',languagePreference:'zh',mustChangePassword:false}};
   if(path==='/auth/session')await route.fulfill({json:session});
   else if(path==='/messages')await route.fulfill({json:{items:[],total:0,unread:0}});
   else if(path==='/plans/mine')await route.fulfill({json:{pending:null,days:[]}});
   else if(path.startsWith('/attendance'))await route.fulfill({status:403,json:{code:'FORBIDDEN',message:'Fixture: attendance not requested'}});
   else await route.fulfill({json:{items:[],total:0}});
  });
  await page.goto(base+'/my/work?lang=zh');
  await page.getByRole('button',{name:'菜单',exact:true}).click();
  const entry=page.locator('#staff-menu').getByRole('link',{name:'使用指南',exact:true});
  await expect(entry).toHaveAttribute('href','/my/guide');
  await entry.click();
  await expect(page).toHaveURL(base+'/my/guide');
  await expect(page.locator('.staff-shell>.adm-nav-bar h1')).toHaveText('使用指南');
  await expect(page.locator('.usage-guide-chapter')).toHaveCount(7);
  assert.equal(await page.locator('.usage-guide-index a').count(),7);
  const targets=await page.locator('.usage-guide-index a').evaluateAll(links=>links.every(link=>document.querySelector(link.getAttribute('href'))));
  assert.equal(targets,true);
  await page.getByRole('link',{name:'使用地图找店',exact:true}).click();
  await expect(page).toHaveURL(base+'/my/guide#guide-map');
  const anchor=await page.locator('#guide-map').boundingBox();
  assert.ok(anchor&&anchor.y>=50&&anchor.y<200,'Chapter heading must remain below navigation');
  await page.locator('#guide-map').getByRole('link',{name:'返回目录与操作顺序'}).click();
  await expect(page).toHaveURL(base+'/my/guide#guide-top');
  for(const width of [320,390,768]){
   await page.setViewportSize({width,height:844});
   assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,`Overflow at ${width}px`);
  }
  await page.reload();
  await expect(page.locator('.usage-guide')).toBeVisible();
  await page.getByRole('button',{name:'返回',exact:true}).click();
  await expect(page).toHaveURL(base+'/my/work');
  await page.goto(base+'/my/guide/?lang=ja');
  await expect(page.locator('.staff-shell>.adm-nav-bar h1')).toHaveText('使い方ガイド');
  await expect(page.locator('.usage-guide')).toHaveAttribute('lang','ja');
  await expect(page.getByRole('heading',{name:'地図で店舗を探す',exact:true})).toBeVisible();
  assert.deepEqual(writes,[]);assert.deepEqual(errors,[]);
  results.push({role,chapters:7,anchors:true,widths:[320,390,768],refresh:true,return:true,japanese:true,apiWrites:0});
  await context.close();
 }
 console.log(JSON.stringify({ok:true,results,databaseUsed:false}));
}finally{await browser.close();}
