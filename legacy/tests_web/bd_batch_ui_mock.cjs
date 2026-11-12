const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
// Map clicks locate and highlight without expanding or changing an open form.
const panels={},rows=[1,2].map(id=>{const a={'aria-expanded':'false','aria-controls':`detail-${id}`};panels[`detail-${id}`]={hidden:true};return {dataset:{gridId:String(id)},handlers:{},classes:new Set(),classList:{toggle(){}},addEventListener(k,f){this.handlers[k]=f},getAttribute(k){return a[k]},setAttribute(k,v){a[k]=v},closest(){return {scrollIntoView:()=>this.scrolled=true}}}});
const window={focusTaskGrid(id){this.focused=id},highlightDispatchGrid(id){this.highlight=id}};
const document={querySelector(){return {querySelectorAll(){return rows}}},getElementById(id){return panels[id]}};
vm.runInNewContext(fs.readFileSync('app/static/bd_task_region.js','utf8'),{window,document});
window.openDispatchGrid(2);assert.equal(rows[1].scrolled,true);assert.equal(window.highlight,2);assert.ok(Object.values(panels).every(p=>p.hidden));
rows[0].handlers.click();assert.equal(panels['detail-1'].hidden,false);assert.equal(window.focused,1);
window.openDispatchGrid(2);assert.equal(panels['detail-1'].hidden,false);assert.equal(panels['detail-2'].hidden,true);
rows[1].handlers.click();assert.equal(panels['detail-1'].hidden,true);assert.equal(panels['detail-2'].hidden,false);
console.log('region: map locates only; row expands one assignment form');
// Native dialog opens from button/deep link, focuses title, closes and restores focus.
const handlers={},closeButton={},openButton={addEventListener(k,f){this[k]=f}},input={focus(){this.focused=true}},opener={focus(){this.focused=true}};
const search={value:'',handlers:{},addEventListener(k,f){this.handlers[k]=f}};
const choices=[{dataset:{regionSearch:'丸の内 東京駅 東京国際フォーラム'},hidden:false},{dataset:{regionSearch:'新宿'},hidden:false}];const empty={hidden:true};
const dialog={querySelectorAll(){return choices},open:false,showModal(){this.open=true},close(){this.open=false;handlers.close()},querySelector(s){return s==='[data-close-batch]'?closeButton:s==='#batch-region-search'?search:s==='#batch-region-empty'?empty:input},addEventListener(k,f){handlers[k]=f}};
closeButton.addEventListener=(k,f)=>closeButton[k]=f;
const location={hash:'#create-batch',pathname:'/zone-tasks',search:''};
const history={replaceState(a,b,url){this.url=url;location.hash=''}};
vm.runInNewContext(fs.readFileSync('app/static/bd_batch_create.js','utf8'),{document:{activeElement:opener,getElementById(){return dialog},querySelectorAll(){return [openButton]}},location,history,addEventListener(){}});
assert.equal(dialog.open,true);assert.equal(input.focused,true);closeButton.click();assert.equal(dialog.open,false);assert.equal(history.url,'/zone-tasks');assert.equal(opener.focused,true);openButton.click();assert.equal(dialog.open,true);
console.log('create dialog: deep link/button open; close restores focus and clears hash');

search.value='東京駅';search.handlers.input();assert.equal(choices[0].hidden,false);assert.equal(choices[1].hidden,true);
search.value='東京国際フォーラム';search.handlers.input();assert.equal(choices[0].hidden,false);
search.value='not-found';search.handlers.input();assert.ok(choices.every(c=>c.hidden));assert.equal(empty.hidden,false);
search.value='';search.handlers.input();assert.ok(choices.every(c=>!c.hidden));
let prevented=false;search.handlers.keydown({key:'Enter',preventDefault(){prevented=true}});assert.equal(prevented,true);
console.log('create search: station/landmark matching; empty/reset; Enter preserves form');
