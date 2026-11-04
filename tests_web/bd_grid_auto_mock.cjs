const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

class Element {
  constructor(id='') { this.id=id; this.handlers={}; this.hidden=false; this.disabled=false; this.value=''; this.checked=false; this.textContent=''; this.children=[]; this.attrs={}; this.elements={csrf_token:{value:'csrf'}}; }
  addEventListener(name,fn) { this.handlers[name]=fn; }
  setAttribute(name,value) { this.attrs[name]=String(value); }
  replaceChildren(...children) { this.children=children; this.textContent=''; }
  append(...children) { this.children.push(...children); this.textContent+=children.map(child=>child.textContent||'').join(''); }
  reportValidity() { return true; }
}

const ids=['grid-region-editor','grid-manual-tab','grid-auto-tab','grid-manual-pane','grid-auto-pane',
  'grid-auto-basis','grid-auto-target','grid-auto-unit','grid-auto-area','grid-auto-scope',
  'grid-auto-tolerance','grid-auto-max-grids','grid-auto-max-stores','grid-auto-filter',
  'grid-auto-min-stores','grid-auto-preview','grid-auto-apply','grid-auto-result','grid-region-form','grid-region-config',
  'grid-auto-open'].map(id=>[id,new Element(id)]);
const els=Object.fromEntries(ids);
els['grid-region-config'].textContent=JSON.stringify({region:'tokyo23',csrf:'csrf',texts:{
  gridCount:'grids',storeCount:'stores',previewLoading:'loading',previewReady:'ready',applyConfirm:'confirm',
  applyDone:'done',zoneCount:'zones',gridCount:'grids',storeCount:'stores',excludedCount:'excluded',
  unassignedCount:'unassigned',outOfTolerance:'outside',error:'error'}});
els['grid-auto-basis'].value='stores'; els['grid-auto-target'].value='8000';
els['grid-auto-area'].value='25'; els['grid-auto-scope'].value='current';
els['grid-auto-tolerance'].value='15'; els['grid-auto-min-stores'].value='5'; els['grid-auto-filter'].checked=true;
let applied=false, previewBody;
const fetch=async(url,options)=>{
  const body=JSON.parse(options.body);
  if(url.endsWith('grid-auto-preview')) {
    previewBody=body;
    return {ok:true,json:async()=>({signature:'a'.repeat(64),zone_count:1,grids_assigned:2,
      zones:[{grid_count:2,store_count:10,area_km2:2,geometry:{type:'Polygon',coordinates:[]}}],
      density:{disabled_count:1,disabled_grid_ids:[3]}})};
  }
  applied=true; assert.equal(body.signature,'a'.repeat(64)); return {ok:true,json:async()=>({ok:true})};
};
const context={document:{getElementById:id=>els[id],createElement:()=>new Element()},
  window:{confirm:()=>true,location:{reload(){}}},AbortController,fetch};
vm.runInNewContext(fs.readFileSync('app/static/bd_grid_auto.js','utf8'),context);
(async()=>{
  els['grid-auto-tab'].handlers.click();
  assert.equal(els['grid-region-editor'].hidden,false);
  assert.equal(els['grid-auto-pane'].hidden,false);
  els['grid-auto-basis'].value='grids'; els['grid-auto-basis'].handlers.change();
  assert.equal(els['grid-auto-target'].value,'8');
  assert.equal(els['grid-auto-unit'].textContent,'grids');
  els['grid-auto-target'].value='2';
  await els['grid-auto-preview'].handlers.click();
  assert.equal(previewBody.target_basis,'grids');
  assert.equal(previewBody.region,'tokyo23');
  assert.equal(els['grid-auto-apply'].hidden,false);
  assert.ok(els['grid-auto-result'].textContent.includes('ready'));
  els['grid-auto-min-stores'].value='6'; els['grid-auto-min-stores'].handlers.input();
  assert.equal(els['grid-auto-apply'].hidden,true);
  await els['grid-auto-preview'].handlers.click();
  await els['grid-auto-apply'].handlers.click();
  assert.equal(applied,true);
  els['grid-auto-scope'].value='all'; els['grid-auto-scope'].handlers.change();
  await els['grid-auto-preview'].handlers.click();
  assert.equal(previewBody.region,'all');
  console.log('auto partition: scope, target switch, preview invalidation and apply passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
