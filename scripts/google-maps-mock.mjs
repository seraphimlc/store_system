/** Google Maps JS contract stub, used only by offline browser regression. Never shipped to the app. */
export const googleMapsMock=`(()=>{
 const emit=(object,name,event)=>{for(const callback of object.listeners.get(name)||[])callback(event);};
 const listen=(object,name,callback)=>{const list=object.listeners.get(name)||[];list.push(callback);object.listeners.set(name,list);return {remove(){const i=list.indexOf(callback);if(i>=0)list.splice(i,1);}};};
 class MapMock{
  constructor(element,options){this.element=element;this.listeners=new globalThis.Map();this.setCenter(options.center);window.__storeMapMock=this;setTimeout(()=>emit(this,'idle'),0);}
  setCenter(center){this.center=center;this.bounds={west:center.lng-.01,east:center.lng+.01,south:center.lat-.01,north:center.lat+.01};setTimeout(()=>emit(this,'idle'),0);}
  panTo(center){this.setCenter(center);(this.panToCalls??=[]).push(center);}
  panBy(x,y){(this.panByCalls??=[]).push({x,y});}
  setOptions(options){this.options={...this.options,...options};}
  drag(){emit(this,'dragstart');}
  setZoom(zoom){this.zoom=zoom;setTimeout(()=>emit(this,'idle'),0);}
  fitBounds(bounds){bounds=bounds.toJSON?bounds.toJSON():bounds;this.bounds=bounds;this.center={lat:(bounds.south+bounds.north)/2,lng:(bounds.west+bounds.east)/2};setTimeout(()=>emit(this,'idle'),0);}
  getZoom(){return this.zoom;}
  getBounds(){return {toJSON:()=>this.bounds};}addListener(name,callback){return listen(this,name,callback);}
  move(bounds){this.bounds=bounds;emit(this,'idle');}
 }
 class DataMock{
  constructor(options){this.map=options.map;this.features=new globalThis.Map();this.listeners=new globalThis.Map();window.__storeDataMock=this;(window.__storeAllDataMocks??=[]).push(this);}
  setMap(map){this.map=map;}setStyle(style){this.style=style;}revertStyle(){}
  addListener(name,callback){return listen(this,name,callback);}
  addGeoJson(collection){const result=[];for(const feature of (collection.type==='Feature'?[collection]:collection.features)){const row={raw:feature,getId:()=>feature.id,getProperty:name=>feature.properties[name],getGeometry:()=>({forEachLatLng:callback=>{const walk=value=>{if(typeof value[0]==='number')callback({lat:()=>value[1],lng:()=>value[0]});else value.forEach(walk);};walk(feature.geometry.coordinates);}})};this.features.set(feature.id,row);result.push(row);}return result;}
  getFeatureById(id){return this.features.get(id)??this.features.get(Number(id));}
  forEach(callback){this.features.forEach(callback);}remove(feature){this.features.delete(feature.getId());}
  click(id){emit(this,'click',{feature:this.features.get(id)});}
 }
 class BoundsMock{constructor(){this.value={west:Infinity,east:-Infinity,south:Infinity,north:-Infinity};}extend(p){const lat=typeof p.lat==='function'?p.lat():p.lat,lng=typeof p.lng==='function'?p.lng():p.lng;this.value={west:Math.min(this.value.west,lng),east:Math.max(this.value.east,lng),south:Math.min(this.value.south,lat),north:Math.max(this.value.north,lat)};return this;}toJSON(){return this.value;}}
 window.google={maps:{LatLngBounds:BoundsMock,Map:MapMock,Data:DataMock,event:{clearInstanceListeners:object=>object.listeners?.clear(),trigger:(object,name)=>emit(object,name)}}};const callback=new URL(document.currentScript.src).searchParams.get('callback');window[callback]();
})();`;
