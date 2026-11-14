/** Real Vite UI, mocked API only. No fixtures, sessions or writes to any database. */
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8002';
const url=new URL(base);if(url.protocol!=='http:'||!['127.0.0.1','localhost'].includes(url.hostname))throw Error('Only a local frontend is allowed');
const browser=await chromium.launch({headless:true,channel:'chrome'}),checks=[],errors=[];
let finishOld=null;
try{for(const width of [430,1100]){
 const context=await browser.newContext({viewport:{width,height:932}}),page=await context.newPage(),requests=[];
 page.on('pageerror',e=>errors.push(e.message));
 const store={id:1,storeId:'mock-store',snapshot:{name:'ＡＢＣ　商店',address:'mock address'},lat:35,lon:139,status:'pending',revision:0,profileRevision:0,photoRevision:0,result:{},photos:[],accuracy:'accurate',googlePlaceId:null,placeLookupState:'unconfigured',distanceMeters:null};
 const response=q=>({items:q==='missing-test'?[]:[{...store,snapshot:{...store.snapshot,name:q==='fresh-result'?'最新门店':q==='slow-old'?'过期门店':store.snapshot.name}}],total:q==='missing-test'?0:1,page:1,pageSize:20,order:q?'relevance':'source',searchVersion:'terms-nfkc-v2',main:{id:2,title:'mock batch',status:'active'},region:{id:3,name:'mock region',leaderId:1},grid:{id:1,grid_id:8,snapshot:{name:'输入法验收网格'},total_stores:1,completed_stores:0,progress:0},canExecute:false,readOnlyReason:'not_assignee'});
 await context.route(base+'/api/**',async route=>{const u=new URL(route.request().url()),path=u.pathname.slice(4);let body={};
  if(path==='/auth/session')body={csrfToken:'mock',user:{id:1,username:'mock',displayName:'mock',personCode:'mock',role:'leader',status:'active',languagePreference:'zh',mustChangePassword:false}};
  else if(path==='/messages')body={items:[],total:0,unread:0};else if(path==='/plans/mine')body={pending:null};else if(path==='/work/batches')body={items:[]};
  else if(path==='/batch-work/grids/1/completions')body={status:'ready',query:'',total:0,candidateTotal:0,items:[]};
 else if(path==='/batch-work/grids/1/map-config')body={enabled:false,key:null};
  else if(path==='/batch-work/grids/1/visits'){const q=u.searchParams.get('q')||'';requests.push({q,state:u.searchParams.get('state')||'',businessStatus:u.searchParams.get('businessStatus')||'',page:u.searchParams.get('page')||'1'});
   if(q==='slow-old')await new Promise(resolve=>{finishOld=resolve;});body=response(q);
  }else if(path.endsWith('/proposals'))body={items:[],total:0,canSubmit:false,canReview:false,canApprove:false};
  else throw Error('Unexpected mocked API path '+path);
  await route.fulfill({json:body});
 });
 const input=page.getByLabel('地图搜索门店',{exact:true});
 await page.goto(base+'/my/work/batch-grids/1');await input.waitFor();await page.waitForFunction(()=>!document.querySelector('.work-grid-loading'));await page.waitForTimeout(300);
 const compose=async(field,partial,final)=>{
  const before=requests.length;await field.dispatchEvent('compositionstart',{data:''});await field.fill(partial);
  await field.dispatchEvent('keydown',{key:'Enter',keyCode:229,isComposing:true});await page.waitForTimeout(300);assert.equal(requests.length,before,'composition must not query');
  await field.fill(final);await field.dispatchEvent('compositionend',{data:final});
  await page.waitForFunction(()=>document.querySelector('.work-search-feedback')?.textContent.includes('找到'));await page.waitForTimeout(240);
  assert.equal(requests.length,before+1,'one request for committed composition');assert.equal(requests.at(-1).q,final);
 };
 await input.fill('pending-before-ime');
 await compose(input,'xin su','新宿 薬局');
 await page.getByRole('button',{name:'展开筛选与搜索',exact:true}).click();const drawer=page.locator('.work-filter-drawer'),drawerInput=drawer.getByLabel('抽屉搜索门店',{exact:true});
 assert.equal(await drawerInput.inputValue(),'新宿 薬局');
 await compose(drawerInput,'やっきょ','薬局 新宿');
 const beforeButton=requests.length;await drawerInput.dispatchEvent('compositionstart');await drawerInput.fill('とうきょう');await drawer.getByRole('button',{name:'搜索',exact:true}).click();await page.waitForTimeout(300);assert.equal(requests.length,beforeButton,'explicit button also waits for composition');
 await drawerInput.fill('東京 薬局');await drawerInput.dispatchEvent('compositionend',{data:'東京 薬局'});await page.waitForTimeout(350);assert.equal(requests.length,beforeButton+1);
 const beforeEnter=requests.length;await drawerInput.press('Enter');await page.waitForTimeout(350);assert.equal(requests.length,beforeEnter+1,'Enter after composition executes normally');
 const beforeFilter=requests.length;await drawer.locator('select').first().selectOption('pending');await page.waitForTimeout(350);assert.equal(requests.length,beforeFilter+1);assert.equal(requests.at(-1).state,'pending');assert.equal(requests.at(-1).page,'1');
 await drawerInput.fill('slow-old');await page.waitForTimeout(350);assert.equal(requests.at(-1).q,'slow-old');assert.ok(finishOld);
 await drawerInput.fill('fresh-result');await drawer.getByText('最新门店',{exact:true}).waitFor();finishOld();finishOld=null;await page.waitForTimeout(250);assert.equal(await drawer.getByText('过期门店',{exact:true}).count(),0,'late response cannot replace current query');
 await drawerInput.fill('missing-test');await drawer.locator('.work-search-empty').waitFor();assert.equal(requests.at(-1).q,'missing-test');
 await drawerInput.fill('');await page.waitForTimeout(350);assert.equal(requests.at(-1).q,'');assert.equal(await input.inputValue(),'');
 assert.equal(await drawerInput.evaluate(e=>e===document.activeElement),true,'typing/clearing retains focus');
 checks.push({width,mapIme:true,pendingDebounceCancelled:true,drawerIme:true,compositionEnterSuppressed:true,compositionButtonSuppressed:true,committedQueryExactlyOnce:true,ordinaryEnter:true,filterResetPage:true,lateResponseIgnored:true,emptyAndClear:true,focusRetained:true,requests:requests.length});
 await context.close();
 }
 assert.deepEqual(errors,[]);await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});const report={ok:true,checks,pageErrors:errors,api:'all API responses mocked; no database or provider calls',frontend:base,productionAccessed:false};await writeFile('data/refactor/search-evaluation/input-browser-'+Date.now()+'.json',JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify(report));
}finally{finishOld?.();await browser.close();}
