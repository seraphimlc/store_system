const fs=require('node:fs'),vm=require('node:vm'),assert=require('node:assert/strict');
const source=fs.readFileSync('app/static/bd_work.js','utf8');
function setup(stores,{earlyFocus,query=''}={}){
 const layers=[],moves=[],opened=[],restoredUrls=[];let firstPosition,map,styles=0,fits=0,cleared=0;
 const resetButton={hidden:true,handlers:{},addEventListener(name,handler){this.handlers[name]=handler;},click(){this.handlers.click();}};
 const ring=[[139,35],[139.01,35],[139.01,35.01],[139,35]];
 const config={key:'synthetic',locate:true,stores,geometry:{type:'FeatureCollection',features:[{
  type:'Feature',geometry:{type:'Polygon',coordinates:[ring]},properties:{id:1}}]}};
 class Data{
  constructor(options){this.map=options.map;this.features=[];this.handlers={};layers.push(this);}
  addGeoJson(data){for(const raw of data.features){const feature={...raw,getProperty:key=>raw.properties[key]};
   this.features.push(feature);if(this.style&&raw.geometry.type==='Point'){styles++;this.style(feature);}}}
  setStyle(style){this.style=style;for(const feature of this.features){if(feature.geometry.type==='Point')styles++;style(feature);}}
  setMap(value){this.map=value;}
  addListener(event,handler){this.handlers[event]=handler;}
  forEach(callback){this.features.forEach(callback);}
  remove(feature){this.features.splice(this.features.indexOf(feature),1);}
 }
 class MapView{
  constructor(){map=this;this.zoom=11;}
  fitBounds(){fits++;}
  addListener(){}
  moveCamera(options){moves.push(options);this.zoom=options.zoom;}
  getZoom(){return this.zoom;}
  panTo(){throw Error('focus must use one camera update');}
  setZoom(){throw Error('focus must use one camera update');}
 }
 const context={URL,URLSearchParams,location:{href:'https://example.test/my/work/batch-grids/1'+query},window:{},
  document:{getElementById:id=>id==='work-show-all'?resetButton:id==='work-map-config'?{textContent:JSON.stringify(config)}:id==='new-store'?null:{},
   createElement:()=>({}),head:{append(){}}},google:{maps:{Data,Map:MapView,SymbolPath:{CIRCLE:'circle'},
   LatLngBounds:class{extend(){}isEmpty(){return false;}}}}};
 context.window.startWorkLocation=(_map,_text,callback)=>{firstPosition=callback;};
 context.window.openVisitStore=id=>opened.push(id);
 context.window.clearVisitStoreSelection=()=>cleared++;
 context.window.history={replaceState(_state,_title,url){restoredUrls.push(url);context.location.href=new URL(url,context.location.href).href;}};
 vm.runInNewContext(source,context);
 if(earlyFocus!==undefined)context.window.focusVisitStore(earlyFocus);
 context.window.initWorkMap();
 return {layers,moves,opened,map,resetButton,restoredUrls,reset:()=>resetButton.click(),get cleared(){return cleared;},focus:id=>context.window.focusVisitStore(id),gps:()=>firstPosition({lat:36,lng:140}),
  get styles(){return styles;},get fits(){return fits;},get visible(){return layers.filter(layer=>layer.map===map)
   .flatMap(layer=>layer.features.filter(f=>f.geometry.type==='Point'));}};
}
const counts=[];
for(const count of [50,1000,5000]){
 const stores=Array.from({length:count},(_,i)=>({id:i,lat:35+i/100000,lon:139,name:String(i),visited:i%2===0}));
 const app=setup(stores);assert.equal(app.visible.length,count);
 assert.equal(app.resetButton.hidden,true);
 const before=app.styles;app.focus(count-1);assert.equal(app.resetButton.hidden,false);
 assert.equal(app.visible.length,1);assert.equal(app.layers[2].map,null);
 assert.deepEqual(app.visible.map(f=>f.properties.store_id),[count-1]);
 assert.equal(app.styles-before,1);assert.equal(app.moves.length,1);
 assert.equal(app.moves[0].center.lat,stores[count-1].lat);assert.equal(app.moves[0].zoom,17);
 const selected=app.visible.find(f=>f.properties.store_id===count-1),nearby=app.layers[3];
 assert.equal(nearby.style(selected).icon.scale,10);
 nearby.handlers.click({feature:app.visible[0]});assert.equal(app.opened[0],count-1);
 const fitCount=app.fits;app.gps();assert.equal(app.fits,fitCount,'GPS must preserve the focused shop');
 app.map.zoom=19;const again=app.styles;app.focus(0);
 assert.deepEqual(app.visible.map(f=>f.properties.store_id),[0]);
 assert.equal(app.styles-again,1);assert.equal(app.layers.length,4);assert.equal(app.moves[1].zoom,19);
 counts.push({stores:count,focusedPoints:app.visible.length,stylesPerFocus:app.styles-again,cameraUpdatesPerFocus:1});
 const fitsBeforeReset=app.fits;app.reset();
 assert.equal(app.visible.length,count);assert.equal(app.resetButton.hidden,true);assert.equal(app.cleared,1);assert.equal(app.fits,fitsBeforeReset+1);
 app.focus(count-1);assert.equal(app.visible.length,1);assert.equal(app.resetButton.hidden,false);assert.equal(app.layers.length,4);
 app.reset();assert.equal(app.visible.length,count);assert.equal(app.cleared,2);
}
const sparse=setup([{id:'shop:001/alpha',lat:35,lon:139},{id:'near',lat:35.001,lon:139},
 {id:'missing',lat:null,lon:null}],{earlyFocus:'shop:001/alpha'});
assert.equal(sparse.visible.length,1);assert.equal(sparse.moves.length,1);
sparse.focus('missing');sparse.focus('unknown');assert.equal(sparse.moves.length,1);assert.equal(sparse.visible.length,1);
const ties=setup(Array.from({length:25},(_,i)=>({id:i,lat:35,lon:139})),{query:'?focus=24&kw=shop#section'});
assert.equal(ties.visible.length,1);assert.equal(ties.visible[0].properties.store_id,24);
ties.reset();assert.equal(ties.visible.length,25);assert.equal(ties.restoredUrls[0],'/my/work/batch-grids/1?kw=shop#section');
const overview=setup([{id:1,lat:35,lon:139}]);const fits=overview.fits;overview.gps();assert.equal(overview.fits,fits+1);
console.log(JSON.stringify({singleTarget:true,restoreAll:true,reselect:true,earlyFocus:true,gpsPreserved:true,invalidCoordinates:true,counts}));
