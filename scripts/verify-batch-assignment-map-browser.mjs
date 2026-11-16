/** Offline acceptance using existing admin Vite; no database or paid map calls. */
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
import {googleMapsMock} from './google-maps-mock.mjs';
const base=process.env.NEXT_ADMIN_BROWSER_BASE_URL||'http://127.0.0.1:8001';
const browser=await chromium.launch({headless:true,channel:'chrome'}),checks=[],errors=[];
const geometry={type:'Polygon',coordinates:[[[139,35],[139.01,35],[139.01,35.01],[139,35]]]};
const features=[{id:11,regionId:3},{id:12,regionId:3},{id:13,regionId:4}].map(({id,regionId})=>({type:'Feature',id,geometry,properties:{regionId,name:'网格'+id}}));
try {
 for(const width of [430,1280]) {
  const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();page.on('pageerror',error=>errors.push(error.message));
  let calls=[],fail=false,status='active',enabled=true;
  const regions=[3,4,5].map(id=>({id,teamId:id===3?1:null,leaderId:id===3?10:null,teamName:id===3?'原团队':null,status:'pending',progress:0,revision:7,snapshot:{name:'片区'+id},canAssign:false,members:[],grids:[{id:id+8,sourceId:id,assigneeId:null,assigneeName:null,status:'pending',progress:0,total:1,completed:0,revision:2,snapshot:{name:'网格'+id}}]}));
  await context.route('https://maps.googleapis.com/**',route=>route.fulfill({contentType:'text/javascript',body:googleMapsMock}));
  await context.route('**/api/**',async route=>{
   if(new URL(route.request().url()).hostname==='maps.googleapis.com'){await route.fallback();return;}
   const request=route.request(),path=new URL(request.url()).pathname.slice(4);let body={};
   if(path==='/auth/session')body={csrfToken:'fixture',user:{id:1,username:'admin',displayName:'验收',role:'admin',status:'active',mustChangePassword:false}};
   else if(path==='/work/batches')body={items:[{id:2,title:'地图分配验收',status,progress:0,revision:1}]};
   else if(path==='/teams')body={items:[{id:1,name:'原团队'},{id:2,name:'新团队'}]};
   else if(path==='/batch-work/batches/2')body={main:{id:2,title:'地图分配验收',status,progress:0,revision:1},regions};
   else if(path==='/batch-work/batches/2/map')body={features,enabled,key:enabled?'offline-map':null};
   else if(/\/batch-work\/regions\/\d+\/assign$/.test(path)){
    assert.equal(request.headers()['x-csrf-token'],'fixture');const value=request.postDataJSON();calls.push({path,...value});
    if(fail&&path.includes('/4/')){await route.fulfill({status:409,json:{code:'CONFLICT',message:'版本冲突'}});return;}
    body={ok:true};
   } else if(request.method()!=='GET')throw Error('Unexpected write '+path);
   await route.fulfill({json:body});
  });
  await page.goto(base+'/admin/zone-tasks/2');
  await page.waitForFunction(()=>window.__storeDataMock?.features.size===3);
  if(width<768)await page.locator('.admin-batch-detail').scrollIntoViewIfNeeded();
  const map=page.locator('.batch-assignment-map'),canvas=map.locator('.batch-assignment-canvas');
  const canvasBounds=await canvas.boundingBox();assert.ok(canvasBounds.height>=238,JSON.stringify({width,canvasBounds,frame:await page.locator('.batch-assignment-workspace').getAttribute('style')}));
  assert.equal(await page.locator('.batch-allocation-list').count(),0);
  assert.equal(await page.getByRole('tab',{name:'地图',exact:true}).getAttribute('aria-selected'),'true');
  assert.equal(await page.getByText('点击片区可多选，再分配给团队；同一片区内的网格会一起选中。',{exact:true}).count(),0);
  if(width<768)await page.getByRole('button',{name:'已选片区 (0)',exact:true}).click();
  await page.locator('.batch-assignment-drawer').waitFor();
  await page.waitForFunction(()=>{const drawer=document.querySelector('.batch-assignment-drawer'),canvas=document.querySelector('.batch-assignment-canvas');return drawer&&Math.abs(drawer.getBoundingClientRect().right-canvas.getBoundingClientRect().right)<3;});
  const layout=await page.evaluate(()=>{const rect=s=>{const r=document.querySelector(s).getBoundingClientRect();return {x:r.x,y:r.y,width:r.width,height:r.height,bottom:r.bottom};};return {canvas:rect('.batch-assignment-canvas'),drawer:rect('.batch-assignment-drawer'),footer:rect('.batch-assignment-drawer-footer'),body:rect('.batch-assignment-drawer [data-slot=drawer-body]'),tools:rect('.batch-assignment-floating'),viewport:innerHeight};});
  assert.ok(Math.abs(layout.canvas.bottom-(layout.viewport-25))<3,JSON.stringify(layout));
  assert.ok(Math.abs(layout.drawer.bottom-layout.canvas.bottom)<3,JSON.stringify(layout));
  assert.ok(layout.body.bottom<=layout.footer.y+1,JSON.stringify(layout));
  assert.equal(await page.locator('.batch-assignment-region-list li').count(),0,'map drawer must not show unselected regions');
  await page.getByText('尚未选择片区',{exact:true}).waitFor();
  assert.equal(await page.getByRole('checkbox',{name:'全选地图片区',exact:true}).count(),0);
  await page.getByRole('tab',{name:'列表',exact:true}).click();
  assert.equal(await page.locator('.batch-allocation-table tbody tr').count(),3,'only list tab shows all regions');
  await page.getByRole('checkbox',{name:'选择片区 片区5',exact:true}).check();
  await page.getByRole('tab',{name:'地图',exact:true}).click();
  await page.locator('.batch-assignment-drawer').waitFor();
  assert.deepEqual(await page.locator('.batch-assignment-region-list li').allTextContents(),['片区5未分配']);
  await page.getByRole('checkbox',{name:'地图选择片区 片区5',exact:true}).click();
  assert.equal(await page.locator('.batch-assignment-region-list li').count(),0,'removing selection removes drawer row');
  await page.getByText('尚未选择片区',{exact:true}).waitFor();
  const click=id=>page.evaluate(id=>window.__storeDataMock.click(id),id);
  await click(11);await page.getByRole('button',{name:'分配所选片区 (1)',exact:true}).waitFor();
  assert.deepEqual(await page.locator('.batch-assignment-region-list li').allTextContents(),['片区3原团队']);
  assert.equal(await page.evaluate(()=>window.__storeDataMock.style(window.__storeDataMock.features.get(12)).fillColor),'#2563eb','all grids in same region highlighted');
  await click(12);assert.equal(await page.getByRole('button',{name:'分配所选片区 (0)',exact:true}).isDisabled(),true);
  await click(11);await click(13);await page.getByRole('button',{name:'分配所选片区 (2)',exact:true}).click();
  const dialog=page.locator('[data-slot=dialog-popup][data-open]');await dialog.waitFor();
  await dialog.getByRole('button',{name:'取消',exact:true}).click();assert.equal(calls.length,0);
  await page.getByRole('button',{name:'分配所选片区 (2)',exact:true}).click();
  await dialog.getByRole('combobox',{name:'批量分配团队',exact:true}).click();await page.getByRole('option',{name:'新团队',exact:true}).click();
  await dialog.getByRole('button',{name:'确认批量分配',exact:true}).click();await page.getByText('已分配 2 项',{exact:true}).waitFor();
  assert.deepEqual(calls.map(row=>[row.teamId,row.revision]),[[2,7],[2,7]]);
  calls=[];fail=true;await click(11);await click(13);await page.getByRole('button',{name:'分配所选片区 (2)',exact:true}).click();
  await dialog.getByRole('button',{name:'确认批量分配',exact:true}).click();await page.getByRole('alert').filter({hasText:'已保存 1 项，其余未保存：版本冲突'}).waitFor();
  assert.ok(calls.every(row=>row.teamId===null),'recall keeps existing null contract');
  await page.getByRole('button',{name:'分配所选片区 (1)',exact:true}).waitFor();
  assert.deepEqual(await page.locator('.batch-assignment-region-list li').allTextContents(),['片区4未分配']);
  assert.equal(await page.evaluate(()=>window.__storeDataMock.style(window.__storeDataMock.features.get(11)).fillColor),'#059669');
  assert.equal(await page.evaluate(()=>window.__storeDataMock.style(window.__storeDataMock.features.get(13)).fillColor),'#2563eb');
  if(width<768)await page.getByRole('button',{name:'关闭已选片区',exact:true}).click();
  await page.getByRole('combobox',{name:'分配状态',exact:true}).click();await page.getByRole('option',{name:'未分配',exact:true}).click();
  assert.equal(await page.evaluate(()=>window.__storeDataMock.style(window.__storeDataMock.features.get(11)).visible),false);
  await click(11);
  if(width<768)await page.getByRole('button',{name:'已选片区 (0)',exact:true}).click();
  assert.equal(await page.getByRole('button',{name:'分配所选片区 (0)',exact:true}).isDisabled(),true);
  enabled=false;await page.reload();await map.getByRole('status').filter({hasText:'地图不可用'}).waitFor();
  await page.getByRole('tab',{name:'列表',exact:true}).click();await page.getByRole('checkbox',{name:'选择片区 片区5',exact:true}).check();
  await page.getByRole('button',{name:'分配所选片区 (1)',exact:true}).waitFor();
  enabled=true;status='completed';await page.reload();await page.waitForFunction(()=>window.__storeDataMock?.features.size===3);const before=calls.length;await click(11);
  assert.equal(await page.getByRole('button',{name:/分配所选片区/}).count(),0);assert.equal(calls.length,before);
  assert.equal(await page.evaluate(()=>window.__storeDataMock.style(window.__storeDataMock.features.get(11)).clickable),false);
  checks.push({width,mapMultiselect:true,sharedRegionHighlight:true,confirmedBulk:true,csrfAndRevision:true,partialFailure:true,filter:true,listFallback:true,tabsKeepSelection:true,drawerOnlySelected:true,drawerFooterPinned:true,remainingViewport:true,closedBatchReadonly:true});
  await context.close();
 }
 assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,checks,pageErrors:errors,api:'mocked; no database writes or provider calls'}));
} finally {await browser.close();}
