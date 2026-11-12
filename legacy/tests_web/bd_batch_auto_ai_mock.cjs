/* Exercise the actual drawer script without a server, Google SDK or live AI. */
const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.handlers = {}; this.children = []; this.hidden = false; this.disabled = false; this.checked = true; this.dataset = {}; this.style = {}; this.open = false; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  append(child) { this.children.push(child); }
  replaceChildren() { this.children = []; }
  focus() {}
}
(async () => {
  const ids = ['batch-auto-config','batch-auto-drawer','batch-auto-backdrop','batch-auto-open',
    'batch-auto-close','batch-auto-footer','batch-auto-preview','batch-auto-apply','batch-auto-result','batch-auto-load','batch-auto-efficiency'];
  const elements = Object.fromEntries(ids.map(id => [id,new Element()]));
  const member = new Element(); member.value = '7';
  const drawer = elements['batch-auto-drawer'];
  drawer.querySelectorAll = () => [member]; drawer.querySelector = () => member;
  elements['batch-auto-config'].textContent = JSON.stringify({csrf:'csrf',regionTaskId:4,
    texts:{resultTitle:'preview result',member:'member',gridsLabel:'grids',pendingLabel:'pending',preview:'preview',working:'working',none:'select',failed:'failed',applyFailed:'applyFailed',
      confirm:'confirm',grid:'grids',stores:'pending',locked:'locked',otherLoad:'other',
      efficiency:'efficiency',aiWeight:'AI weight',aiReason:'AI reason',done:'done'}});
  let deferred, calls = [], paints = [], navigation, applyFails = false;
  const window = {confirm:() => true,location:{pathname:'/my/work/batches/2/regions/4',assign:url=>{navigation=url;}},
    setBatchGridAssignmentPreview:assignments => paints.push(assignments)};
  const context = {window,Map,Set,JSON,Number,encodeURIComponent,document:{
    getElementById:id=>elements[id],createElement:()=>new Element(),addEventListener(){},activeElement:null},
    fetch:async(url,options) => {
      calls.push({url,body:JSON.parse(options.body)});
      if (url.endsWith('auto-preview')) return await new Promise(resolve=>{deferred=resolve;});
      return applyFails ? {ok:false,json:async()=>({error:'apply failed'})} : {ok:true,json:async()=>({ok:true})};
    }};
  vm.runInNewContext(fs.readFileSync('app/static/bd_batch_auto_assign.js','utf8'),context);
  const result = {signature:'signed',ai_decision:{weights:{'7':1.25},reason:'AI chooses more capacity',expires_at:9999999999},
    ai_reason:'<img src=x onerror=alert(1)>',assignments:{'12':7},grid_count:1,workload:10,locked_completed:0,
    members:[{member_id:7,name:'member',grid_count:1,workload:10,other_batch_workload:0,efficiency:1,ai_weight:1.25}]};
  assert.equal(elements['batch-auto-footer'].hidden,true);
  assert.equal(elements['batch-auto-apply'].hidden,true);
  elements['batch-auto-open'].handlers.click();
  assert.equal(drawer.hidden,false);
  assert.equal(elements['batch-auto-footer'].hidden,true);
  const first = elements['batch-auto-preview'].handlers.click();
  // Changing configuration while AI runs must discard its stale response.
  drawer.handlers.change({target:{matches:()=>true}});
  deferred({ok:true,json:async()=>result}); await first;
  assert.equal(elements['batch-auto-apply'].hidden,true);
  assert.equal(elements['batch-auto-preview'].disabled,false);
  const second = elements['batch-auto-preview'].handlers.click();
  deferred({ok:true,json:async()=>result}); await second;
  assert.equal(elements['batch-auto-apply'].disabled,false);
  assert.equal(elements['batch-auto-footer'].hidden,false);
  assert.equal(paints.at(-1)['12'],7);
  const renderedReason = elements['batch-auto-result'].children.at(-1);
  assert.equal(renderedReason.open,false); // Long explanations start collapsed.
  assert.equal(renderedReason.children[1].textContent,result.ai_reason); // text, never HTML.
  applyFails = true;
  await elements['batch-auto-apply'].handlers.click();
  assert.equal(elements['batch-auto-footer'].hidden,true);
  assert.equal(elements['batch-auto-apply'].hidden,true);
  applyFails = false;
  const regenerated = elements['batch-auto-preview'].handlers.click();
  assert.equal(elements['batch-auto-footer'].hidden,true);
  deferred({ok:true,json:async()=>result}); await regenerated;
  await elements['batch-auto-apply'].handlers.click();
  assert.equal(calls.at(-1).body.ai_decision.weights['7'],1.25);
  assert.equal(calls.at(-1).body.signature,'signed');
  assert.ok(navigation.endsWith('?msg=done'));
  // Closing also invalidates an in-flight preview.
  const third = elements['batch-auto-preview'].handlers.click();
  elements['batch-auto-close'].handlers.click();
  deferred({ok:true,json:async()=>result}); await third;
  assert.equal(drawer.hidden,true);
  assert.equal(elements['batch-auto-apply'].hidden,true);
  const failed = elements['batch-auto-preview'].handlers.click();
  deferred({ok:false,json:async()=>({error:'provider failed'})}); await failed;
  assert.equal(elements['batch-auto-footer'].hidden,true);
  assert.equal(elements['batch-auto-apply'].disabled,true);
  const malformed = elements['batch-auto-preview'].handlers.click();
  deferred({ok:true,json:async()=>({members:[]})}); await malformed;
  assert.equal(elements['batch-auto-footer'].hidden,true);
  assert.equal(elements['batch-auto-apply'].hidden,true);
  console.log('AI drawer: reason, weights, signed apply and stale-response rejection passed');
})().catch(error => { console.error(error); process.exitCode=1; });
