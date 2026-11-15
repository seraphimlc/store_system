/** Existing Vite/Appica module fixture. No backend requests or business writes. */
import {chromium} from '@playwright/test';import assert from 'node:assert/strict';import {mkdir,writeFile} from 'node:fs/promises';
const base='http://127.0.0.1:8001',browser=await chromium.launch({headless:true,channel:'chrome'}),errors=[],checks=[];
try{for(const width of [430,1200]){
 const context=await browser.newContext({viewport:{width,height:900}}),page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));
 await context.route(base+'/api/**',r=>r.fulfill({status:401,json:{code:'UNAUTHENTICATED',message:'mock'}}));
 await page.goto(base+'/admin/');await page.waitForTimeout(300);
 await page.evaluate(async()=>{
  const React=(await import('/admin/node_modules/.vite/deps/react.js')).default,{createRoot}=(await import('/admin/node_modules/.vite/deps/react-dom_client.js')).default,{AdminSelect}=await import('/admin/src/AdminSelect.tsx');
  const h=React.createElement,node=document.createElement('div');node.id='select-test';node.style.cssText='position:fixed;inset:0;z-index:20;background:var(--background);padding:24px;overflow:auto';document.body.append(node);
  const opts=[['','请选择'],['E001','王小明 E001'],['E002','山田 太郎 E002'],['E003','停用员工 E003']];
  function Harness(){const [employee,setEmployee]=React.useState('E001'),[saved,setSaved]=React.useState(''),[changes,setChanges]=React.useState(0);
   return h('form',{id:'fixture-form',onSubmit:e=>{e.preventDefault();setSaved(JSON.stringify(Object.fromEntries(new FormData(e.currentTarget))));}},
    h('label',null,'员工',h(AdminSelect,{'aria-label':'员工',name:'employee',value:employee,onChange:e=>{setEmployee(e.target.value);setChanges(v=>v+1);}},opts.map(([value,label])=>h('option',{key:value,value,disabled:value==='E003'},label)))),
    h('label',null,'网格',h(AdminSelect,{'aria-label':'网格',name:'grid',defaultValue:'',required:true},[['','请选择网格'],['G001','新宿 G001'],['G002','大久保 G002'],['G003','北新宿 G003']].map(([value,label])=>h('option',{key:value,value},label)))),
    h('label',null,'禁用',h(AdminSelect,{'aria-label':'禁用',name:'disabled',disabled:true,defaultValue:'x'},h('option',{value:'x'},'disabled'))),
    h('button',{type:'submit'},'提交测试'),h('button',{type:'reset'},'重置测试'),h('button',{type:'button',onClick:()=>setEmployee('E002')},'外部更新'),h('output',{'data-testid':'saved'},saved),h('output',{'data-testid':'changes'},changes));}
  createRoot(node).render(h(Harness));
 });
 const fixture=page.locator('#select-test'),employee=fixture.getByRole('combobox',{name:'员工',exact:true}),grid=fixture.getByRole('combobox',{name:'网格',exact:true});await employee.waitFor();
 await employee.click();const search=page.getByRole('combobox',{name:'筛选：员工',exact:true});await search.waitFor();await search.fill('ｅ００２');await page.getByRole('option',{name:'山田 太郎 E002',exact:true}).waitFor();assert.equal(await page.getByRole('option',{name:'王小明 E001',exact:true}).count(),0);assert.equal(await fixture.getByTestId('changes').innerText(),'0','typing filters but never selects');
 await page.getByRole('option',{name:'山田 太郎 E002',exact:true}).click();assert.ok((await employee.innerText()).includes('山田'));assert.equal(await fixture.getByTestId('changes').innerText(),'1');
 await employee.click();await search.fill('not-present');await page.getByText('没有匹配选项',{exact:true}).waitFor();await search.press('Escape');if(await employee.getAttribute('aria-expanded')==='true')await employee.click();await page.locator('.admin-searchable-select[data-open]').waitFor({state:'hidden'});assert.ok((await employee.innerText()).includes('山田'));
 await employee.click();await search.fill('E003');assert.equal(await page.getByRole('option',{name:'停用员工 E003',exact:true}).getAttribute('aria-disabled'),'true');await search.press('Escape');if(await employee.getAttribute('aria-expanded')==='true')await employee.click();await page.locator('.admin-searchable-select[data-open]').waitFor({state:'hidden'});
 await fixture.getByRole('button',{name:'提交测试',exact:true}).click();assert.equal(await fixture.getByTestId('saved').innerText(),'','required blank prevents submission');
 await grid.click();const gridSearch=page.getByRole('combobox',{name:'筛选：网格',exact:true});await gridSearch.fill('大久');await page.getByRole('option',{name:'大久保 G002',exact:true}).click();await fixture.getByRole('button',{name:'提交测试',exact:true}).click();const values=JSON.parse(await fixture.getByTestId('saved').innerText());assert.equal(values.employee,'E002');assert.equal(values.grid,'G002');assert.equal('disabled'in values,false);
 await grid.click();await gridSearch.fill('G001');await gridSearch.press('ArrowDown');await gridSearch.press('Enter');assert.ok((await grid.innerText()).includes('新宿'));await fixture.getByRole('button',{name:'重置测试',exact:true}).click();await page.waitForTimeout(100);assert.ok((await grid.innerText()).includes('请选择网格'),'uncontrolled reset returns initial value');
 await employee.click();await search.fill('王 小 明');await page.getByRole('option',{name:'王小明 E001',exact:true}).click();await fixture.getByRole('button',{name:'外部更新',exact:true}).click();assert.ok((await employee.innerText()).includes('山田'),'controlled external update stays visible');
 assert.equal(await fixture.getByRole('combobox',{name:'禁用',exact:true}).isDisabled(),true);
 await grid.click();await gridSearch.fill('G002');const bounds=await page.locator('.admin-searchable-select[data-open]').boundingBox();assert.ok(bounds&&bounds.x>=0&&bounds.x+bounds.width<=width+1);await gridSearch.press('Escape');
 checks.push({width,employeeNameCodeSearch:true,gridNameCodeSearch:true,widthAndSpaceNormalization:true,noImplicitSelection:true,noResults:true,disabledOption:true,required:true,formValues:true,reset:true,keyboard:true,controlledUpdates:true,bounds});await context.close();
 }
 assert.deepEqual(errors,[]);await mkdir('data/refactor/search-evaluation',{recursive:true,mode:0o700});const report={ok:true,checks,pageErrors:errors,frontend:base,api:'mocked; no database writes',productionAccessed:false};const path='data/refactor/search-evaluation/admin-select-browser-'+Date.now()+'.json';await writeFile(path,JSON.stringify(report,null,2),{mode:0o600,flag:'wx'});console.log(JSON.stringify({evidence:path,...report}));
}finally{await browser.close();}
