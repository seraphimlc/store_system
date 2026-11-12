const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');

class Element {
  constructor(tag = 'div') { this.tagName=tag; this.children=[]; this.hidden=false; this.handlers={}; this.attrs={}; this.style={}; this._text=''; }
  set textContent(value) { this._text=String(value); this.children=[]; }
  get textContent() { return this._text+this.children.map(child=>child.textContent).join(''); }
  set innerHTML(_) { throw new Error('Merchant strings must never become HTML'); }
  append(...items) { this.children.push(...items); }
  appendChild(child) { this.append(child); }
  replaceChildren(...items) { this._text=''; this.children=items; }
  setAttribute(key,value) { this.attrs[key]=String(value); }
  addEventListener(event,fn) { this.handlers[event]=fn; }
  remove() { this.removed=true; }
}
const texts=Object.fromEntries(['loading','error','mapError','missingKey','count','covered','rate','empty','zoom','dense','asOf',
  'detail','detailLoading','detailError','id','address','phone','website','source','coordinates','location','lastVisit','notVisited',
  'yes','no','pending','accurate','nonexistent','doubtful','region','group'].map(s=>[s,s]));
const elements={};
for (const id of ['config','summary','status','retry','detail','detail-title','detail-body','reset','close','fullscreen'])
  elements[`merchant-map-${id}`]=new Element();
elements['merchant-map-config'].textContent=JSON.stringify({key:'mock-key',language:'zh-CN',texts});
const stage=new Element(),pane=new Element();
let map,pointLayer,timer;
const clusters={mode:'clusters',summary:{merchant_count:100,covered_count:55,coverage_pct:55},as_of:'2026-11-05T12:00:00Z',cells:[
  {lat:35.6,lon:139.7,merchant_count:50,coverage_pct:80},
  {lat:35.65,lon:139.75,merchant_count:30,coverage_pct:50},
  {lat:35.7,lon:139.8,merchant_count:20,coverage_pct:0}],points:[]};
const points={mode:'points',summary:{merchant_count:2,covered_count:1,coverage_pct:50},as_of:clusters.as_of,
  points:[{id:'osm:node/12',lat:35.6,lon:139.7,covered:true},{id:'b',lat:35.61,lon:139.71,covered:false}]};
class MapMock {
  constructor(){map=this; this.zoom=9; this.handlers={};}
  getZoom(){return this.zoom;}
  setZoom(value){this.zoom=value;}
  panTo(value){this.center=value;}
  fitBounds(bounds){this.bounds=bounds;}
  getBounds(){return {toJSON:()=>this.bounds};}
  addListener(name,fn){this.handlers[name]=fn;}
}
class Data {
  constructor(){pointLayer=this;this.features=[];this.handlers={};}
  forEach(fn){[...this.features].forEach(fn);}
  remove(feature){this.features=this.features.filter(item=>item!==feature);}
  addGeoJson(collection){this.features=collection.features;}
  setStyle(fn){this.style=fn;}
  addListener(name,fn){this.handlers[name]=fn;}
}
class Overlay {
  setMap(map){if(map){this.onAdd();this.draw();}else this.onRemove();}
  getPanes(){return {overlayMouseTarget:pane};}
  getProjection(){return {fromLatLngToDivPixel:()=>({x:20,y:30})};}
  static preventMapHitsAndGesturesFrom(){}
}
let reply=clusters,fail=false,deferred,hold=false,detailHold=false,detailDeferred;
const window={location:{reload(){}},addEventListener(){}};
const context={window,document:{getElementById:id=>elements[id]||(elements[id]=new Element()),
  querySelector:()=>stage,createElement:tag=>new Element(tag),head:new Element(),addEventListener(){}},
  URLSearchParams,AbortController,setTimeout:fn=>{timer=fn;return 1;},clearTimeout:()=>{timer=null;},
  google:{maps:{Map:MapMock,Data,OverlayView:Overlay,SymbolPath:{CIRCLE:'circle'},LatLng:class{}}},
  fetch:async url=>{
    if(url.includes('/detail?')){
      if(detailHold) return new Promise(resolve=>{detailDeferred=resolve;});
      assert.ok(url.includes('osm%3Anode%2F12'));
      return {ok:true,json:async()=>({place_id:'osm:node/12',name:'<img src=x onerror=alert(1)>',lat:35.6,lon:139.7,
        address:'long address',covered:true,location_accuracy:'pending',sources:'osm'})};
    }
    if(hold)return new Promise(resolve=>{deferred=resolve;});
    return fail ? {ok:false,json:async()=>{throw new Error('not JSON');}} : {ok:true,json:async()=>reply};
  }};
vm.runInNewContext(fs.readFileSync('app/static/bd_merchant_map.js','utf8'),context);
async function load(){map.handlers.idle();await timer();}
(async()=>{
  window.initMerchantMap();
  await load();
  assert.equal(pane.children.length,3);
  assert.ok(pane.children[0].className.includes('green'));
  assert.ok(pane.children[1].className.includes('yellow'));
  assert.ok(pane.children[2].className.includes('red'));
  assert.ok(elements['merchant-map-summary'].textContent.includes('100'));
  pane.children[0].handlers.click();assert.equal(map.zoom,11);
  reply=points;map.zoom=18;await load();
  assert.equal(pointLayer.features.length,2);
  assert.equal(pane.children.filter(child=>!child.removed).length,0);
  await pointLayer.handlers.click({feature:{getProperty:()=> 'osm:node/12'}});
  assert.equal(elements['merchant-map-detail'].hidden,false);
  assert.equal(elements['merchant-map-detail-title'].textContent,'<img src=x onerror=alert(1)>');
  assert.ok(elements['merchant-map-detail-body'].textContent.includes('long address'));
  // Closing the panel discards a late detail response.
  detailHold=true;
  const pendingDetail=pointLayer.handlers.click({feature:{getProperty:()=> 'osm:node/12'}});
  elements['merchant-map-close'].handlers.click();
  detailDeferred({ok:true,json:async()=>({name:'late'})});await pendingDetail;
  assert.equal(elements['merchant-map-detail'].hidden,true);
  // Ignoring an old viewport must not remove the newer points.
  hold=true;map.handlers.idle();const old=timer();
  map.handlers.bounds_changed();hold=false;reply=points;await load();
  deferred({ok:true,json:async()=>clusters});await old;
  assert.equal(pointLayer.features.length,2);
  fail=true;await load();
  assert.equal(elements['merchant-map-retry'].hidden,false);
  assert.equal(elements['merchant-map-summary'].textContent,'error');
  assert.equal(pointLayer.features.length,0);
  fail=false;await elements['merchant-map-retry'].handlers.click();
  assert.equal(pointLayer.features.length,2);
  console.log('merchant map: colors, zoom, point details, safe text, stale responses, retry passed');
})().catch(error=>{console.error(error);process.exitCode=1;});
