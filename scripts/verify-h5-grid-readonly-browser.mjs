import {chromium,expect} from '@playwright/test';
import assert from 'node:assert/strict';
const browser=await chromium.launch({channel:'chrome',headless:true});
try{
 const page=await browser.newPage({viewport:{width:393,height:852}}),errors=[];
 page.on('pageerror',e=>errors.push(e.message));
 await page.addInitScript(()=>{navigator.geolocation.getCurrentPosition=(_,fail)=>fail({code:1});});
 let reason=null,canExecute=true;
 await page.route('**/api/**',async route=>{
 const p=new URL(route.request().url()).pathname;let body={};
 if(p==='/api/auth/session')body={csrfToken:'test',user:{id:2,username:'captain',displayName:'队长',personCode:'T',role:'leader',status:'active',languagePreference:'zh',mustChangePassword:false}};
 else if(p.endsWith('/visits'))body={items:[],total:0,grid:{id:11,assignee_id:2,snapshot:{name:'网格验收'}},region:{id:8},main:{id:3},canExecute,readOnlyReason:reason};
 else if(p.endsWith('/map-config'))body={enabled:false};
 await route.fulfill({json:body});});
 const cases=[['batch_disabled','批次已禁用，仅查看'],['batch_completed','批次已完成，仅查看'],['account_role','当前账号无作业权限，仅查看'],['unassigned','网格未分配负责人，仅查看'],['not_assignee','非负责网格，仅查看'],['team_membership','当前团队作业身份无效，仅查看'],['leader_identity','片区队长身份不一致，仅查看'],[null,'当前无编辑权限，仅查看']];
 const base=process.env.NEXT_BROWSER_BASE_URL||'http://127.0.0.1:8012';
 for(const [code,text] of cases){reason=code;canExecute=false;await page.goto(base+'/my/work/batch-grids/11');await expect(page.locator('.work-grid-readonly-label')).toHaveText(text);}
 const position=await page.locator('.work-grid-readonly-label').evaluate(el=>{const box=el.getBoundingClientRect(),parent=el.parentElement.getBoundingClientRect();return {left:Math.round(box.left-parent.left),top:Math.round(box.top-parent.top),overflow:document.documentElement.scrollWidth>innerWidth};});assert.deepEqual(position,{left:16,top:12,overflow:false});
 reason=null;canExecute=true;await page.reload();await page.getByRole('textbox',{name:'地图搜索门店',exact:true}).waitFor();await expect(page.locator('.work-grid-readonly-label')).toHaveCount(0);
 assert.deepEqual(errors,[]);console.log(JSON.stringify({ok:true,reasons:cases.length,assignedCaptainEditable:true,position,errors}));
}finally{await browser.close();}
