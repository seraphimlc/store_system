const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
class Element {
  constructor() { this.handlers = {}; this.disabled = false; this.checked = false; this.textContent = ''; this.dataset = {}; this.style = {}; this.hidden = false; this.offsetWidth = 240; this.offsetHeight = 72; this.children = []; }
  addEventListener(name, fn) { this.handlers[name] = fn; }
  setAttribute(name, value) { this[name] = value; }
  appendChild(child) { this.children.push(child); }
  remove() { this.removed = true; }
  set innerHTML(value) {
    this.children = [new Element(), new Element(), new Element()];
    this.children[0].children = [this.children[0].span = new Element(), this.children[0].strong = new Element()];
    this.button = this.children[2];
  }
  querySelectorAll() { return [this.children[0].span, this.children[0].strong]; }
  querySelector(selector) { return selector === 'button' ? this.button : null; }
  matches() { return !!this.dataset; }
}
const ids = Object.fromEntries(['grid-region-editor','grid-region-form','grid-region-config',
  'grid-region-status','grid-region-save','grid-region-merge','grid-region-count','grid-region-area','grid-region-stores','grid-region-clear',
  'grid-conversion-panel','grid-conversion-close','grid-conversion-id','grid-conversion-revision',
  'grid-conversion-name','grid-conversion-area','grid-conversion-stores'].map(id => [id,new Element()]));
const boxes = [1,2].map(id => Object.assign(new Element(), {dataset:{gridId:String(id)},
  parentElement:{textContent:`grid${id}`,hidden:false},
  value:JSON.stringify({id,revision:0,zone_id:7,zone_revision:0})}));
boxes.push(Object.assign(new Element(), {dataset:{gridId:'4'},disabled:true,
  parentElement:{textContent:'locked grid4',hidden:false},
  value:JSON.stringify({id:4,revision:0,zone_id:8,zone_revision:0})}));
const form = ids['grid-region-form'];
form.querySelectorAll = () => boxes;
form.action = '/zones/merge-grids';
form.elements = {csrf_token:{value:'synthetic-csrf'}, name:{value:''}, region:{value:'kanagawa'}, _ft:{value:'token'}};
const actions = ids['grid-region-zone-actions'] = new Element();
actions.forms = [];
actions.querySelector = selector => {
  const id = /data-split-zone="([^"]+)"/.exec(selector)?.[1];
  return actions.forms.find(item => item.dataset.splitZone === id) || null;
};
actions.querySelectorAll = () => actions.forms;
actions.appendChild = child => { actions.forms.push(child); actions.children.push(child); };
ids['grid-region-editor'].hidden = true;
ids['grid-region-merge'].checked = true;
const texts = Object.fromEntries(['selected','empty','loading','saving','saved','split','ready','shape','error','mapMissing','merged','separate'].map(key => [key,key]));
ids['grid-region-config'].textContent = JSON.stringify({grids:[{id:1,zone_id:7,zone_name:'东京<片区>',area:1,stores:3},{id:2,zone_id:7,zone_name:'东京<片区>',area:.96,stores:4},{id:3,name:'低密度 <网格>',revision:9,disabled:true,convertible:true,area:1,stores:0},{id:5,disabled:true,convertible:false,area:1,stores:0},{id:6,zone_id:null,area:1,stores:1},{id:4,zone_id:8,zone_name:'片区 <四>',area:1,stores:2}],texts});
const layers=[];
const overlays=[];
class OverlayView {
  constructor() { overlays.push(this); }
  setMap(map) { this.map=map; this.onAdd(); this.draw(); }
  getMap() { return this.map; }
  getPanes() { return {overlayMouseTarget:{appendChild:node=>{this.node=node;}}}; }
  getProjection() { return {fromLatLngToDivPixel:position=>({x:position.lng,y:position.lat})}; }
}
class Data {
  constructor(options) { this.options=options; this.handlers={}; layers.push(this); }
  addGeoJson(data) { this.data=data; }
  setMap(map) { this.map=map; }
  setStyle(style) { this.style=style; }
  addListener(name, fn) { this.handlers[name]=fn; }
}
let fail=false, pending, delay=false, scheduled, calls=0;
const fetch = async (url,options) => {
  calls++;
  const data=JSON.parse(options.body);
  assert.equal(data.csrf_token,'synthetic-csrf');
  if (String(url).includes('/zones/merge-grids')) {
    assert.equal(options.headers.Accept,'application/json');
    assert.equal(data.name,'新区');
    assert.equal(data.region,'kanagawa');
    assert.equal(data.selections.length,2);
    return {ok:true, redirected:false, json:async()=>({ok:true,
      zone:{id:9,name:'新区',revision:0,area_km2:1.96,store_count:7,split_locked:false,geometry:{type:'Polygon',coordinates:[]}},
      zones:[], archived_zone_ids:[7],
      grids:[{id:1,revision:1,zone_id:9,zone_revision:0,zone_name:'新区'},
             {id:2,revision:1,zone_id:9,zone_revision:0,zone_name:'新区'}]})};
  }
  assert.equal(data.selections.length,2);
  assert.equal(typeof data.merge_boundaries,'boolean');
  if (delay) await new Promise(resolve => {pending=resolve;});
  return {ok:!fail,json:async()=>fail?{error:'not-contiguous'}:{
    geometry:{type:'Polygon',coordinates:[]},
    display_geometry:{type:data.merge_boundaries?'Polygon':'GeometryCollection',geometries:[]},
    properties:{grid_count:2,area_km2:1.96,store_count:7,shape_valid:true}}};
};
const map={id:'map',handlers:{},getDiv(){return {clientWidth:320,clientHeight:240};},addListener(name,fn){this.handlers[name]=fn;}};
const context={window:{},document:{getElementById:id=>ids[id],createElement:()=>new Element()},AbortController,fetch,
  setTimeout(fn){scheduled=fn;return 1;},clearTimeout(){scheduled=null;},google:{maps:{Data,OverlayView}}};
vm.runInNewContext(fs.readFileSync('app/static/bd_grid_regions.js','utf8'),context);
const changed=target=>form.handlers.change({target});
const flush=async()=>{const callback=scheduled;scheduled=null;await callback?.();};
(async()=>{
  context.window.initGridRegionEditor(map);
  assert.equal(layers[0].handlers.mouseover,undefined);
  ids['grid-conversion-panel'].hidden=true;
  layers[0].handlers.click({feature:{getProperty(){return 3;}},stop(){}});
  assert.equal(ids['grid-conversion-panel'].hidden,false);
  assert.equal(ids['grid-region-editor'].hidden,true);
  assert.equal(ids['grid-conversion-id'].value,'3');
  assert.equal(ids['grid-conversion-revision'].value,'9');
  assert.equal(ids['grid-conversion-name'].textContent,'低密度 <网格>');
  assert.equal(ids['grid-conversion-area'].textContent,'1.00');
  assert.equal(ids['grid-conversion-stores'].textContent,'0');
  assert.equal(calls,0);
  ids['grid-conversion-close'].handlers.click();
  assert.equal(ids['grid-conversion-panel'].hidden,true);
  layers[0].handlers.click({feature:{getProperty(){return 5;}},stop(){}});
  assert.equal(ids['grid-conversion-panel'].hidden,true);
  const featureClick={feature:{getProperty(){return 1;}},latLng:{lat:30,lng:10},stop(){this.stopped=true;}};
  layers[0].handlers.click(featureClick);
  assert.equal(featureClick.stopped,true);
  assert.equal(overlays.length,1);
  assert.equal(overlays[0].node.children[0].span.textContent,'东京<片区>');
  assert.equal(overlays[0].node.children[0].strong.textContent,'网格 #1');
  assert.equal(overlays[0].node.hidden,false);
  assert.equal(overlays[0].node.dataset.placement,'below');
  assert.equal(overlays[0].node.style.left,'132px');
  assert.equal(overlays[0].node.style.top,'30px');
  const lockedClick={feature:{getProperty(){return 4;}},latLng:{lat:100,lng:310},stop(){this.stopped=true;}};
  layers[0].handlers.click(lockedClick);
  assert.equal(overlays[0].node.children[0].span.textContent,'片区 <四>');
  assert.equal(boxes[2].checked,false);
  assert.equal(layers[0].style({getProperty(){return 4;}}).clickable,true);
  overlays[0].node.button.handlers.click({stopPropagation(){}});
  assert.equal(overlays[0].node.hidden,true);
  layers[0].handlers.click({feature:{getProperty(){return 1;}},latLng:{lat:100,lng:160},stop(){}});
  map.handlers.click();
  assert.equal(overlays[0].node.hidden,true);
  boxes.forEach(box=>{box.checked=true;});
  changed(boxes[0]);
  assert.equal(calls,0); assert.equal(ids['grid-region-save'].disabled,true);
  await flush();
  assert.equal(calls,1); assert.equal(ids['grid-region-save'].disabled,false);
  assert.equal(layers.at(-1).data.features[0].geometry.type,'Polygon');
  assert.equal(layers[0].style({getProperty(){return 1;}}).strokeOpacity,0);
  const disabledFeature={getProperty(){return 3;}};
  assert.equal(layers[0].style(disabledFeature).fillColor,'#9ca3af');
  assert.equal(layers[0].style(disabledFeature).clickable,true);
  assert.equal(layers[0].style({getProperty(){return 5;}}).clickable,false);
  assert.equal(layers[0].style({getProperty(){return 6;}}).fillColor,'#176b57');
  const selectedFeature={getProperty(){return 1;}};
  const unselectedFeature={getProperty(){return 2;}};
  const otherRegionFeature={getProperty(){return 4;}};
  assert.equal(layers[0].style(selectedFeature).fillColor,'#176b57');
  assert.equal(layers[0].style(unselectedFeature).fillColor,'#176b57');
  assert.equal(layers[0].style(otherRegionFeature).fillColor,'#2459d3');
  ids['grid-region-merge'].checked=false; changed(ids['grid-region-merge']); await flush();
  assert.equal(calls,2);
  assert.equal(layers.at(-1).data.features[0].geometry.type,'GeometryCollection');
  assert.equal(layers[0].style({getProperty(){return 1;}}).strokeOpacity,1);
  assert.ok(ids['grid-region-status'].textContent.includes('separate'));
  assert.equal(ids['grid-region-editor'].hidden,false);
  assert.equal(ids['grid-region-count'].textContent,'2');
  layers[0].handlers.click({feature:{getProperty(){return 3;}},stop(){}});
  assert.equal(ids['grid-conversion-panel'].hidden,false);
  assert.equal(ids['grid-region-editor'].hidden,true);
  ids['grid-conversion-close'].handlers.click();
  assert.equal(ids['grid-region-editor'].hidden,false);
  assert.equal(ids['grid-conversion-panel'].hidden,true);
  assert.equal(ids['grid-region-area'].textContent,'1.96');
  assert.equal(ids['grid-region-stores'].textContent,'7');
  assert.ok(boxes.every(box=>!box.parentElement.hidden));
  fail=true; changed(boxes[0]); await flush();
  assert.equal(ids['grid-region-save'].disabled,true);
  assert.equal(ids['grid-region-status'].textContent,'not-contiguous');
  fail=false; delay=true; changed(boxes[0]); const old=flush(); await Promise.resolve();
  ids['grid-region-clear'].handlers.click(); pending(); await old;
  assert.equal(ids['grid-region-save'].disabled,true);
  assert.ok(boxes.every(box=>!box.checked && box.parentElement.hidden));
  assert.equal(ids['grid-region-editor'].hidden,true);
  layers[0].handlers.click({feature:{getProperty(){return 1;}}});
  assert.ok(boxes.slice(0,2).every(box=>box.checked));
  assert.equal(ids['grid-region-editor'].hidden,false);
  assert.equal(ids['grid-region-count'].textContent,'2');
  assert.ok(boxes.slice(0,2).every(box=>!box.parentElement.hidden));
  assert.equal(layers[0].style(selectedFeature).fillColor,'#176b57');
  assert.equal(layers[0].style(otherRegionFeature).fillColor,'#2459d3');
  layers[0].handlers.click({feature:{getProperty(){return 2;}}});
  assert.ok(boxes.every(box=>!box.checked));
  assert.equal(layers[0].style(selectedFeature).fillColor,'#2459d3');
  assert.equal(layers[0].style(unselectedFeature).fillColor,'#2459d3');
  assert.equal(layers[0].style(otherRegionFeature).fillColor,'#2459d3');
  boxes[0].checked=true;
  changed(boxes[0]);
  assert.ok(boxes.slice(0,2).every(box=>box.checked));
  boxes[1].checked=false;
  changed(boxes[1]);
  assert.ok(boxes.slice(0,2).every(box=>!box.checked));
  changed(boxes[0]);
  assert.ok(boxes.slice(0,2).every(box=>!box.checked));
  layers[0].handlers.click({feature:{getProperty(){return 1;}}});
  assert.ok(boxes.slice(0,2).every(box=>box.checked));
  layers[0].handlers.click({feature:{getProperty(){return 1;}}});
  assert.equal(ids['grid-region-editor'].hidden,true);
  layers[0].handlers.click({feature:{getProperty(){return 1;}}});
  await flush();
  assert.equal(ids['grid-region-save'].disabled,false);
  form.elements.name.value='新区';
  const beforeLayers=layers.length;
  let patched=null;
  context.window.patchZoneFeatures=payload=>{patched=payload.zone.id;};
  const oldForm=new Element();
  oldForm.dataset.splitZone='7';
  actions.forms.push(oldForm);
  oldForm.remove=()=>{actions.forms=actions.forms.filter(item=>item!==oldForm);};
  let prevented=false;
  await form.handlers.submit({preventDefault(){prevented=true;}});
  assert.equal(prevented,true);
  assert.equal(patched,9);
  assert.equal(layers.length,beforeLayers);
  assert.equal(form.elements.name.value,'');
  assert.ok(boxes.slice(0,2).every(box=>!box.checked));
  assert.equal(JSON.parse(boxes[0].value).zone_id,9);
  assert.equal(JSON.parse(boxes[1].value).revision,1);
  assert.equal(layers[0].style({getProperty(){return 1;}}).fillColor,'#2459d3');
  assert.equal(ids['grid-region-status'].textContent,'saved');
  assert.equal(actions.forms.some(item=>item.dataset.splitZone==='7'),false);
  const created=actions.forms.find(item=>item.dataset.splitZone==='9');
  assert.equal(created._label.textContent,'新区');
  assert.equal([...created.children].find(child=>child.name==='revision').value,'0');
  assert.equal(ids['grid-region-editor'].hidden,true);
  console.log('grid map: click-only region card, placement, close behavior, group selection and stale response passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
