const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
function setup(supported=true){
  const events={},mapEvents={},attrs={},pans=[],markers=[],circles=[],cleared=[];
  const button={setAttribute:(k,v)=>attrs[k]=v,addEventListener:(k,v)=>events[k]=v};
  const status={textContent:''};let success,failure,watches=0;
  const geo={watchPosition(ok,err){success=ok;failure=err;watches++;return watches;},clearWatch:id=>cleared.push(id)};
  const window={addEventListener:(k,v)=>events[k]=v};
  const map={addListener:(k,v)=>mapEvents[k]=v,panTo:p=>pans.push(p)};
  const google={maps:{SymbolPath:{CIRCLE:'circle'},Marker:class{constructor(options){this.options=options;markers.push(this);}setPosition(p){this.position=p;}},Circle:class{constructor(){circles.push(this);}setCenter(p){this.center=p;}setRadius(r){this.radius=r;}}}};
  vm.runInNewContext(fs.readFileSync('app/static/bd_work_location.js','utf8'),{
    window,navigator:supported?{geolocation:geo}:{},google,document:{getElementById:id=>id==='work-location'?button:status}});
  window.startWorkLocation(map,{mine:'mine',loading:'loading',failed:'failed',unsupported:'unsupported'});
  return {events,mapEvents,attrs,pans,markers,circles,cleared,status,success:p=>success(p),failure:()=>failure(),watches:()=>watches};
}
let h=setup();assert.equal(h.watches(),1);assert.equal(h.status.textContent,'loading');
h.success({coords:{latitude:35.6,longitude:139.7,accuracy:18}});
assert.equal(h.markers.length,1);assert.equal(h.markers[0].position.lat,35.6);
assert.equal(h.circles[0].radius,18);assert.equal(h.pans.length,1);assert.equal(h.attrs['aria-pressed'],'true');
h.mapEvents.dragstart();h.success({coords:{latitude:35.61,longitude:139.71,accuracy:12}});
assert.equal(h.pans.length,1,'manual drag pauses following while location still updates');
assert.equal(h.markers.length,1);assert.equal(h.markers[0].position.lat,35.61);
h.events.click();assert.equal(h.pans.length,2);assert.equal(h.watches(),1,'reuse watcher');
h.events.pagehide();assert.deepEqual(h.cleared,[1]);
h.events.pageshow({persisted:true});assert.equal(h.watches(),2,'resume after history navigation');
h.failure();assert.equal(h.status.textContent,'failed');assert.equal(h.attrs['aria-pressed'],'false');
h.events.click();assert.equal(h.watches(),3,'retry after permission or timeout error');
h=setup(false);assert.equal(h.status.textContent,'unsupported');assert.equal(h.attrs['aria-pressed'],'false');
console.log('location display, follow/pause, accuracy, retry and watcher cleanup passed');
