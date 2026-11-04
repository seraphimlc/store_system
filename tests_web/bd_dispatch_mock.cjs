const fs=require('node:fs');
const vm=require('node:vm');
const assert=require('node:assert/strict');
const panels={};
const rows=[12,13].map(id=>{
 const attributes={'aria-expanded':'false','aria-controls':`grid-detail-${id}`};
 panels[`grid-detail-${id}`]={hidden:true};
 return {dataset:{gridId:String(id)},handlers:{},
  addEventListener(name,fn){this.handlers[name]=fn;},
  getAttribute(key){return attributes[key];},setAttribute(key,value){attributes[key]=value;},
  closest(){return {scrollIntoView:options=>{this.scroll=options;}};}};
});
const context={window:{},document:{querySelector(){return {querySelectorAll(){return rows;}};},
 getElementById(id){return panels[id];}}};
vm.runInNewContext(fs.readFileSync('app/static/bd_dispatch.js','utf8'),context);
rows[0].handlers.click();
assert.equal(rows[0].getAttribute('aria-expanded'),'true');
assert.equal(panels['grid-detail-12'].hidden,false);
assert.equal(panels['grid-detail-13'].hidden,true);
rows[1].handlers.click();
assert.equal(panels['grid-detail-12'].hidden,true);
assert.equal(panels['grid-detail-13'].hidden,false);
rows[1].handlers.click();
assert.ok(Object.values(panels).every(panel=>panel.hidden));
context.window.openDispatchGrid(12);
assert.equal(panels['grid-detail-12'].hidden,false);
assert.equal(rows[0].scroll.block,'nearest');
context.window.openDispatchGrid(999);
assert.equal(panels['grid-detail-12'].hidden,false);
console.log('dispatch: row toggle, single expanded detail, map selection and bounded scroll passed');

// 实际地图脚本与列表一起运行：分配底色不随选中改变，只有当前外边界高亮。
const layers=[];
const ring=[[139,35],[139.01,35],[139.01,35.01],[139,35]];
const config={key:'synthetic',dispatch:true,language:'zh',geometry:{type:'FeatureCollection',features:[
 {type:'Feature',geometry:{type:'Polygon',coordinates:[ring]},properties:{id:12,assigned:true}},
 {type:'Feature',geometry:{type:'Polygon',coordinates:[ring]},properties:{id:13,assigned:false,review:true}}]}};
class Data {
 constructor(){this.handlers={};layers.push(this);}
 addGeoJson(data){this.data=data;}
 setStyle(style){this.style=style;}
 addListener(name,fn){this.handlers[name]=fn;}
}
const previousLookup=context.document.getElementById;
context.document.getElementById=id=>id==='work-map-config'?{textContent:JSON.stringify(config)}:
 id==='work-map-status'?{}:previousLookup(id);
context.document.createElement=()=>({});context.document.head={append(){}};
context.URL=URL;context.URLSearchParams=URLSearchParams;context.location={href:'http://localhost/'};
context.google={maps:{Data,Map:class {fitBounds(){this.fitted=true;}addListener(){}panTo(){}getZoom(){return 16;}setZoom(){}},LatLngBounds:class {extend(){} isEmpty(){return false;}},SymbolPath:{CIRCLE:'circle'}}};
vm.runInNewContext(fs.readFileSync('app/static/bd_work.js','utf8'),context);
context.window.initWorkMap();
const feature=id=>({getProperty(key){return config.geometry.features.find(f=>f.properties.id===id).properties[key];}});
assert.equal(layers[1].style(feature(12)).strokeWeight,5,'selection before SDK ready must be restored');
const assignedColor=layers[0].style(feature(12)).fillColor;
const unassignedColor=layers[0].style(feature(13)).fillColor;
assert.notEqual(assignedColor,unassignedColor);
assert.equal(assignedColor,'#2463b5');assert.equal(unassignedColor,'#d59a27');
rows[1].handlers.click();
assert.equal(layers[1].style(feature(12)).strokeWeight,2);
assert.equal(layers[1].style(feature(13)).strokeWeight,5);
assert.equal(layers[1].style(feature(13)).strokeColor,'#e34b18');
assert.equal(layers[0].style(feature(13)).fillColor,unassignedColor);
layers[0].handlers.click({feature:feature(12)});
assert.equal(panels['grid-detail-12'].hidden,false);
assert.equal(layers[1].style(feature(12)).strokeWeight,5);
assert.equal(layers[1].style(feature(13)).strokeWeight,2);
rows[0].handlers.click();
assert.equal(layers[1].style(feature(12)).strokeWeight,2);
assert.equal(layers[1].style(feature(13)).strokeWeight,2);
console.log('dispatch map: distinct assignment fills, list/map highlight, deselection and pre-load selection passed');

context.window.highlightSelectedGrids([12,13]);
assert.equal(layers[0].style(feature(12)).fillOpacity,.65);
assert.equal(layers[0].style(feature(13)).fillOpacity,.65);
context.window.highlightSelectedGrids([]);
assert.equal(layers[0].style(feature(13)).fillColor,unassignedColor);
context.window.focusTaskGrid(12);
assert.equal(layers[1].style(feature(12)).strokeWeight,5);
console.log('batch map: multiple selected polygons and row-driven bounds passed');
