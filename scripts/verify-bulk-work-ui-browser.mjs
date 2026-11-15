/** Mocked acceptance against existing Vite services; never writes a database or calls a provider. */
import {chromium} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[],checks=[];
const geometry={type:'Polygon',coordinates:[[[139,35],[139.01,35],[139.01,35.01],[139,35]]]};
const session={csrfToken:'fixture',user:{id:1,username:'fixture',displayName:'验收',personCode:'fixture',role:'leader',status:'active',languagePreference:'zh',mustChangePassword:false}};
const grids=[1,2].map(id=>({id,sourceId:id,assigneeId:10,assigneeName:'原负责人',status:'assigned',progress:0,total:3,completed:0,revision:0,snapshot:{name:'网格'+id}}));
const region={id:3,mainId:2,snapshot:{name:'片区'},teamId:1,teamName:'团队',leaderId:1,revision:0,status:'assigned',progress:0,canAssign:true,members:[{id:10,name:'原负责人'},{id:11,name:'新负责人'}],grids,summary:{grids:2,total:6,completed:0}};
try{
 const staff=await browser.newContext({viewport:{width:390,height:844}}),page=await staff.newPage();page.on('pageerror',e=>errors.push(e.message));let calls=[],fail=false;
 await page.route('**/api/**',async r=>{const req=r.request(),path=new URL(req.url()).pathname.slice(4);let body={};
 if(path==='/auth/session')body=session;
 else if(path==='/plans/mine')body={pending:null};
 else if(path==='/messages')body={unread:0,items:[]};
 else if(path==='/batch-work/regions/3')body=region;
 else if(path==='/batch-work/batches/2/mobile')body={main:{id:2,title:'批次',status:'active',progress:0},regions:[region]};
 else if(path==='/batch-work/grids/1/visits')body={items:[{id:5,gridTaskId:1,storeId:'fixture-store',snapshot:{name:'测试门店',address:'测试地址'},lat:35.005,lon:139.005,status:'pending',revision:0,profileRevision:0,photoRevision:0,result:{},photos:[],accuracy:'accurate',googlePlaceId:null,placeLookupState:'unconfigured'}],total:1,page:1,pageSize:20,main:{id:2,title:'批次',status:'active'},region:{id:3,name:'片区',leaderId:1},grid:{id:1,grid_id:8,snapshot:{name:'网格',geometry},total_stores:1,completed_stores:0,progress:0},canExecute:true};
 else if(path==='/batch-work/grids/1/completions')body={status:'ready',query:'',total:0,candidateTotal:0,items:[]};
 else if(path==='/batch-work/grids/1/proposals')body={items:[],total:0,canSubmit:true,canReview:true,canApprove:true};
 else if(path.endsWith('/map')||path.endsWith('/map-config'))body={features:grids.map(g=>({type:'Feature',id:g.id,geometry,properties:{regionId:3}})),enabled:false,key:null};
 else if(/\/grids\/\d+\/assign$/.test(path)){assert.equal(req.headers()['x-csrf-token'],'fixture');const value=req.postDataJSON();calls.push({path,...value});if(fail&&path.includes('/2/')){await r.fulfill({status:409,json:{code:'CONFLICT',message:'版本冲突'}});return;}body={ok:true};}
 else if(req.method()!=='GET')throw Error('Unexpected write '+path);
 await r.fulfill({json:body});});
 await page.goto('http://127.0.0.1:8002/my/work/regions/3');await page.getByRole('button',{name:'多选',exact:true}).click();await page.getByRole('checkbox',{name:'选择网格 网格1',exact:true}).check();await page.getByRole('checkbox',{name:'选择网格 网格2',exact:true}).check();await page.getByRole('button',{name:'分配 (2)',exact:true}).click();await page.getByRole('radio',{name:'新负责人',exact:true}).check();assert.equal(calls.length,0);await page.getByRole('alert').filter({hasText:'覆盖所选网格已有'}).waitFor();await page.getByRole('button',{name:'确认分配',exact:true}).click();await page.getByRole('button',{name:'确认分配',exact:true}).waitFor({state:'hidden'});await page.getByText('分配已保存',{exact:true}).waitFor();assert.equal(calls.length,2,JSON.stringify(calls));assert.ok(calls.every(c=>c.assigneeId===11&&c.revision===0));checks.push('staff bulk overwrite with final warning and CSRF');
 calls=[];fail=true;await page.getByRole('checkbox',{name:'选择网格 网格1',exact:true}).check();await page.getByRole('checkbox',{name:'选择网格 网格2',exact:true}).check();await page.getByRole('button',{name:'分配 (2)',exact:true}).click();await page.getByRole('button',{name:'确认分配',exact:true}).click();await page.getByRole('alert').filter({hasText:'已保存 1 项；其余未保存'}).first().waitFor();assert.equal(calls.length,2);checks.push('partial failure keeps remaining selection');
 await page.goto('http://127.0.0.1:8002/my/work/batches/2');await page.locator('.work-batch-detail>.work-map').waitFor();const bounds=await page.locator('.work-batch-detail').evaluate(e=>({parent:e.getBoundingClientRect().height,map:e.querySelector('.work-map').getBoundingClientRect().height}));assert.ok(Math.abs(bounds.map/bounds.parent-.5)<.03,JSON.stringify(bounds));await page.getByRole('button',{name:'菜单',exact:true}).click();assert.equal(await page.getByText('任务分派',{exact:true}).count(),0);checks.push('batch map 50 percent and navigation');
 await page.goto('http://127.0.0.1:8002/my/work/batch-grids/1?store=5');
 await page.getByRole('radio',{name:'营业',exact:true}).waitFor();assert.equal(await page.locator('.work-store-form select').count(),0);await page.getByRole('radio',{name:'未完成',exact:true}).check();assert.equal(await page.getByRole('radio',{name:'未完成',exact:true}).isChecked(),true);await page.getByRole('button',{name:'上传照片',exact:true}).click();await page.getByText('选择图片',{exact:true}).waitFor();const chooser=page.waitForEvent('filechooser');await page.getByText('选择图片',{exact:true}).click();assert.equal((await chooser).isMultiple(),true);await (await chooser).setFiles([]);checks.push('direct radio choices and single photo action sheet');await staff.close();
 const admin=await browser.newContext({viewport:{width:1280,height:900}}),ap=await admin.newPage();ap.on('pageerror',e=>errors.push(e.message));let writes=[],failEmployee=false;
 const employees=[10,11].map(id=>({id,username:'user'+id,displayName:'员工'+id,personCode:'p'+id,role:'staff',status:'active',language:'zh',isActive:true,mustChangePassword:false}));
 await ap.route('**/api/**',async r=>{const req=r.request(),path=new URL(req.url()).pathname.slice(4);let body={};
 if(path==='/auth/session')body={...session,user:{...session.user,role:'admin'}};
 else if(path==='/employees')body={items:employees,total:2,page:1,pageSize:30};
 else if(/^\/employees\/\d+\/edit$/.test(path)){const value=req.postDataJSON();assert.equal(req.headers()['x-csrf-token'],'fixture');assert.equal(value.expected.status,'active');writes.push({path,...value});if(failEmployee&&path.includes('/11/')){await r.fulfill({status:409,json:{code:'CONFLICT',message:'版本冲突'}});return;}body={ok:true};}
 else if(path==='/work/batches')body={items:[{id:2,title:'测试批次',status:'active',progress:0,revision:0}]};
 else if(path==='/batch-work/batches/2')body={main:{id:2,title:'测试批次',status:'active',progress:0,revision:0},regions:[{...structuredClone(region),canAssign:false}]};
 else if(path==='/teams')body={items:[{id:1,name:'原团队'},{id:2,name:'新团队'}]};
 else if(path==='/batch-work/regions/3/assign'||path==='/batch-work/grids/1/assign'||path==='/batch-work/grids/2/assign'){writes.push({path,...req.postDataJSON()});body={ok:true};}
 else if(path==='/reports')body={items:[],total:0,page:1,pageSize:30,summary:{p1:0,p2:0,total:0,points:0},coverage:{start:null,end:null}};
 else if(path==='/reports/compare')body={persons:[]};
 else if(path==='/reports/people')body={items:[]};
 else if(req.method()!=='GET')throw Error('Unexpected admin write '+path);
 await r.fulfill({json:body});});
 await ap.goto('http://127.0.0.1:8001/admin/');await ap.waitForURL('**/admin/staff-reports');assert.equal(await ap.locator('a[href="/admin/messages"]').count(),0);assert.equal(await ap.locator('[value="overview"]').count(),0);checks.push('admin default reports and removed menus');
 await ap.goto('http://127.0.0.1:8001/admin/employees');await ap.getByRole('checkbox',{name:'全选本页员工',exact:true}).check();await ap.getByRole('combobox',{name:'批量状态',exact:true}).click();await ap.getByRole('option',{name:'停用',exact:true}).click();await ap.getByRole('button',{name:'批量修改状态',exact:true}).click();assert.equal(writes.length,0);await ap.getByRole('button',{name:'确认操作',exact:true}).click();await ap.getByText('已处理 2 位员工',{exact:true}).waitFor();assert.equal(writes.length,2);assert.ok(writes.every(w=>w.status==='disabled'));checks.push('employee page selection and confirmed bulk state with expected fields');
 writes=[];failEmployee=true;await ap.getByRole('checkbox',{name:'全选本页员工',exact:true}).check();await ap.getByRole('button',{name:'批量修改状态',exact:true}).click();await ap.getByRole('button',{name:'确认操作',exact:true}).click();await ap.getByRole('alert').filter({hasText:'已保存 1 项，其余未保存'}).waitFor();assert.equal(await ap.getByRole('checkbox',{name:'选择员工 员工10',exact:true}).isChecked(),false);assert.equal(await ap.getByRole('checkbox',{name:'选择员工 员工11',exact:true}).isChecked(),true);checks.push('employee partial failure retained');
 writes=[];await ap.goto('http://127.0.0.1:8001/admin/zone-tasks/2');await ap.getByRole('checkbox',{name:'全选片区',exact:true}).check();await ap.getByRole('combobox',{name:'批量分配团队',exact:true}).click();await ap.getByRole('option',{name:'新团队',exact:true}).click();await ap.getByRole('button',{name:'批量分配片区',exact:true}).click();assert.equal(writes.length,0);await ap.getByRole('button',{name:'确认批量分配',exact:true}).click();await ap.getByText('已分配 1 项',{exact:true}).waitFor();assert.equal(writes[0].teamId,2);
 await ap.getByRole('button',{name:'查看网格',exact:true}).click();assert.equal(await ap.getByRole('combobox',{name:'批量分配负责人',exact:true}).count(),0,'admin cannot bypass captain-only grid assignment');checks.push('admin bulk team assignment preserves captain-only grid permission');await admin.close();
 assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,checks,pageErrors:errors,api:'mocked; no database writes or provider calls'}));
}catch(e){console.log(JSON.stringify({pageErrors:errors,checks}));throw e;}finally{await browser.close();}
