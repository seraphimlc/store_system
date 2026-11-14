/** Existing Vite UI with explicit mocked suggestions. No real API, DB or provider calls. */
import {chromium} from '@playwright/test';import assert from 'node:assert/strict';import {mkdir,writeFile} from 'node:fs/promises';
const base=process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8002',url=new URL(base);if(url.protocol!=='http:'||!['localhost','127.0.0.1'].includes(url.hostname))throw Error('Local frontend required');
const browser=await chromium.launch({headless:true,channel:'chrome'}),checks=[],errors=[];let finishSlow;
try{for(const width of [430,1100]){
 const context=await browser.newContext({viewport:{width,height:932}}),page=await context.newPage(),calls=[];page.on('pageerror',e=>errors.push(e.message));let failed=false;
 const store={id:1,storeId:'mock-store',snapshot:{name:'ABCDEF',address:'mock address'},lat:35,lon:139,status:'pending',revision:0,profileRevision:0,photoRevision:0,result:{},photos:[],accuracy:'accurate',googlePlaceId:null,placeLookupState:'unconfigured',distanceMeters:null};
 await context.route(base+'/api/**',async route=>{const u=new URL(route.request().url()),path=u.pathname.slice(4),q=u.searchParams.get('q')||'',number=Number(u.searchParams.get('page')||1);let body={};
  if(path==='/auth/session')body={csrfToken:'mock',user:{id:1,username:'mock',displayName:'mock',personCode:'mock',role:'leader',status:'active',languagePreference:'zh',mustChangePassword:false}};
  else if(path==='/messages')body={items:[],total:0,unread:0};else if(path==='/plans/mine')body={pending:null};else if(path==='/work/batches')body={items:[]};
  else if(path==='/batch-work/grids/1/completions')body={status:'ready',query:'',total:0,candidateTotal:0,items:[]};
 else if(path==='/batch-work/grids/1/map-config')body={enabled:false,key:null};
  else if(path==='/batch-work/grids/1/visits'){calls.push({type:'search',q,state:u.searchParams.get('state')||''});const total=q&&q!=='ABCDEF'?0:1;body={items:total?[store]:[],total,page:1,pageSize:20,order:q?'relevance':'source',main:{id:2,title:'mock',status:'active'},region:{id:3,name:'mock',leaderId:1},grid:{id:1,grid_id:8,snapshot:{name:'建议验收网格'},total_stores:1,completed_stores:0,progress:0},canExecute:false,readOnlyReason:'not_assignee'};}
  else if(path==='/batch-work/grids/1/suggestions'){calls.push({type:'suggestions',q,state:u.searchParams.get('state')||'',page:number});
   if(q==='FAILURE'&&!failed){failed=true;await route.fulfill({status:503,json:{code:'NETWORK',message:'建议暂不可用'}});return;}
   if(q==='SLOWOLD')await new Promise(resolve=>{finishSlow=resolve;});
   body={status:q==='123456'?'unsupported_query':q==='HUGESCOPE'?'scope_too_large':'ready',query:q,strategyVersion:'name-edit-one-v1',page:number,pageSize:5,total:q==='ABCDEX'?6:q==='NOCANDIDATE'?0:1,candidateTotal:q==='ABCDEX'?7:q==='NOCANDIDATE'?0:1,hasMore:q==='ABCDEX'&&number===1,items:q==='ABCDEX'?(number===1?[{name:'ABCDEF',candidateCount:2},...['A','B','C','D'].map(c=>({name:'ABCDE'+c,candidateCount:1}))]:[{name:'ABCDEG',candidateCount:1}]):q==='NOCANDIDATE'?[]:[{name:q==='SLOWOLD'?'过期建议':'<img src=x onerror=alert(1)>',candidateCount:1}]};
  }else if(path.endsWith('/proposals'))body={items:[],total:0,canSubmit:false,canReview:false,canApprove:false};else throw Error('Unexpected API '+path);
  await route.fulfill({json:body});
 });
 await page.goto(base+'/my/work/batch-grids/1');const mapInput=page.getByLabel('地图搜索门店',{exact:true});await mapInput.waitFor();await mapInput.fill('ABCDEX');await page.getByRole('button',{name:'查看相似名称',exact:true}).waitFor();
 assert.equal(calls.filter(c=>c.type==='suggestions').length,0,'no automatic fuzzy request');await page.getByRole('button',{name:'查看相似名称',exact:true}).click();
 const drawer=page.locator('.work-filter-drawer'),panel=drawer.getByRole('region',{name:'相似名称建议',exact:true}),input=drawer.getByLabel('抽屉搜索门店',{exact:true});await panel.getByRole('button',{name:'查找相似名称',exact:true}).click();await panel.getByText('相似名称 6 个，候选门店 7 家',{exact:true}).waitFor();
 assert.equal(await input.inputValue(),'ABCDEX');assert.equal(await drawer.locator('.work-store-list li').count(),0,'suggestions never become exact result rows');
 const buttons=await panel.locator('.work-suggestion-list button').evaluateAll(nodes=>nodes.map(node=>{const r=node.getBoundingClientRect();return {width:r.width,height:r.height};}));assert.ok(buttons.every(r=>r.height>=44&&r.width<=width),'candidate choices fit and retain touch target size');
 await panel.getByRole('button',{name:'下一页建议',exact:true}).click();await panel.getByRole('button',{name:/ABCDEG/}).waitFor();assert.equal(calls.filter(c=>c.type==='suggestions').at(-1).page,2);
 await panel.getByRole('button',{name:'上一页建议',exact:true}).click();await panel.getByRole('button',{name:/ABCDEF/}).click();await drawer.locator('.work-store-list').getByText('ABCDEF',{exact:true}).waitFor();assert.equal(await input.inputValue(),'ABCDEF');
 await drawer.locator('.work-store-list').getByText('ABCDEF',{exact:true}).click();await page.locator('.work-store-drawer').getByText('mock address',{exact:true}).waitFor();await page.locator('.work-store-drawer').getByRole('button',{name:'关闭',exact:true}).click();await page.getByRole('button',{name:'展开筛选与搜索',exact:true}).click();
 await drawer.getByRole('button',{name:'撤销改词',exact:true}).click();await panel.getByRole('button',{name:'查找相似名称',exact:true}).waitFor();assert.equal(await input.inputValue(),'ABCDEX');
 await drawer.locator('select').first().selectOption('pending');await panel.getByRole('button',{name:'查找相似名称',exact:true}).click();await panel.getByText(/相似名称 6/).waitFor();assert.equal(calls.filter(c=>c.type==='suggestions').at(-1).state,'pending');
 await input.fill('FAILURE');await panel.getByRole('button',{name:'查找相似名称',exact:true}).waitFor();await panel.getByRole('button',{name:'查找相似名称',exact:true}).click();await panel.getByRole('alert').waitFor();assert.equal(await input.inputValue(),'FAILURE');
 await panel.getByRole('button',{name:'重试相似名称',exact:true}).click();await panel.getByText('<img src=x onerror=alert(1)>',{exact:true}).waitFor();assert.equal(await panel.locator('img,script').count(),0,'candidate names render as text');
 await input.fill('123456');await panel.getByRole('button',{name:'查找相似名称',exact:true}).waitFor();await panel.getByRole('button',{name:'查找相似名称',exact:true}).click();await panel.getByText('暂不为短词、编号或多词查询提供相似名称。',{exact:true}).waitFor();
 await input.fill('HUGESCOPE');await panel.getByRole('button',{name:'查找相似名称',exact:true}).waitFor();await panel.getByRole('button',{name:'查找相似名称',exact:true}).click();await panel.getByText('当前网格超出建议范围，请继续使用普通搜索或状态筛选。',{exact:true}).waitFor();
 await input.fill('SLOWOLD');await panel.getByRole('button',{name:'查找相似名称',exact:true}).waitFor();await panel.getByRole('button',{name:'查找相似名称',exact:true}).click();await page.waitForTimeout(100);assert.ok(finishSlow);
 await input.fill('NOCANDIDATE');await panel.getByRole('button',{name:'查找相似名称',exact:true}).waitFor();await panel.getByRole('button',{name:'查找相似名称',exact:true}).click();await panel.getByText('当前筛选下没有相似名称',{exact:true}).waitFor();finishSlow();finishSlow=null;await page.waitForTimeout(100);assert.equal(await panel.getByText('过期建议',{exact:true}).count(),0);
 const bounds=await panel.boundingBox();assert.ok(bounds&&bounds.x>=0&&bounds.x+bounds.width<=width+1,'suggestion panel fits viewport');
 await input.fill('');await page.waitForTimeout(300);assert.equal(await drawer.getByRole('region',{name:'相似名称建议',exact:true}).count(),0);
 checks.push({width,explicitOnly:true,noSilentReplacement:true,countsAndPages:true,chooseAndUndo:true,addressDetailAndReturn:true,touchTargets:true,filters:true,failureRetry:true,textOnlyXss:true,unsupportedAndLimit:true,staleIgnored:true,clear:true,bounds});await context.close();
 }
 assert.deepEqual(errors,[]);await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});const report={ok:true,checks,pageErrors:errors,api:'fully mocked; zero database/provider writes',frontend:base,productionAccessed:false};await writeFile('data/refactor/search-evaluation/suggestions-browser-'+Date.now()+'.json',JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify(report));
}finally{finishSlow?.();await browser.close();}
