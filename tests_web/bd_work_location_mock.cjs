const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function setup(supported=true){
  const events={},mapEvents={},attrs={},pans=[],markers=[],circles=[],cleared=[],firstPositions=[];
  const button={setAttribute:(k,v)=>attrs[k]=v,addEventListener:(k,v)=>events[k]=v};
  const status={textContent:''};let success,failure,watches=0;
  const geo={watchPosition(ok,err){success=ok;failure=err;watches++;return watches;},clearWatch:id=>cleared.push(id)};
  const window={addEventListener:(k,v)=>events[k]=v};
  const map={addListener:(k,v)=>mapEvents[k]=v,panTo:p=>pans.push(p)};
  const google={maps:{SymbolPath:{CIRCLE:'circle'},Marker:class{constructor(options){this.options=options;markers.push(this);}setPosition(p){this.position=p;}},Circle:class{constructor(){circles.push(this);}setCenter(p){this.center=p;}setRadius(r){this.radius=r;}}}};
  vm.runInNewContext(fs.readFileSync('app/static/bd_work_location.js','utf8'),{
    window,navigator:supported?{geolocation:geo}:{},google,document:{getElementById:id=>id==='work-location'?button:status}});
  const unavailable=[];
  window.startWorkLocation(map,{mine:'mine',loading:'loading',failed:'failed',unsupported:'unsupported'},p=>firstPositions.push(p),()=>unavailable.push(true));
  return {events,mapEvents,attrs,pans,markers,circles,cleared,firstPositions,unavailable,status,success:p=>success(p),failure:()=>failure(),watches:()=>watches};
}
let h=setup();assert.equal(h.watches(),1);assert.equal(h.status.textContent,'loading');
assert.equal(h.attrs['aria-pressed'],'false');
h.success({coords:{latitude:35.6,longitude:139.7,accuracy:18}});
assert.equal(h.markers.length,1);assert.equal(h.markers[0].position.lat,35.6);
assert.equal(h.circles[0].radius,18);assert.equal(h.pans.length,0);
assert.equal(h.firstPositions.length,1);assert.equal(h.firstPositions[0].lat,35.6);assert.equal(h.firstPositions[0].lng,139.7);
h.mapEvents.dragstart();h.success({coords:{latitude:35.61,longitude:139.71,accuracy:12}});
assert.equal(h.pans.length,0,'initial location does not replace the grid view');
assert.equal(h.markers.length,1);assert.equal(h.markers[0].position.lat,35.61);
h.events.click();assert.equal(h.pans.length,1);assert.equal(h.attrs['aria-pressed'],'true');assert.equal(h.watches(),1,'reuse watcher');
h.success({coords:{latitude:35.62,longitude:139.72,accuracy:10}});
assert.equal(h.pans.length,2,'manual follow pans to updated location');
h.events.pagehide();assert.deepEqual(h.cleared,[1]);
h.events.pageshow({persisted:true});assert.equal(h.watches(),2,'resume after history navigation');
h.failure();assert.equal(h.status.textContent,'failed');assert.equal(h.attrs['aria-pressed'],'false');
assert.equal(h.unavailable.length,0,'a later timeout keeps the shops already chosen from the first fix');
h.events.click();assert.equal(h.watches(),3,'retry after permission or timeout error');
const denied=setup();denied.failure();assert.equal(denied.unavailable.length,1);assert.equal(denied.firstPositions.length,0);
h=setup(false);assert.equal(h.status.textContent,'unsupported');assert.equal(h.attrs['aria-pressed'],'false');assert.equal(h.unavailable.length,1);
console.log('location display, follow/pause, accuracy, retry and watcher cleanup passed');
