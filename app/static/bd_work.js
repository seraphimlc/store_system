/* H5地图仅渲染服务端已授权范围，不把未派发区域下载给队员。 */
(() => {
  'use strict';
  const config=JSON.parse(document.getElementById('work-map-config').textContent);
  const notice=document.getElementById('work-map-status');
  const assignmentColors=['#1769aa','#a33b20','#39754a','#7545a1','#a26a00','#147d78','#b13c73','#5b6470'];
  let mapLayer, previewStyle, previewOwnerColors={};
  window.setBatchGridAssignmentPreview=assignments=>{
    window.batchGridAssignmentPreview=assignments;
    const owners=[...new Set(Object.values(assignments||{}))].sort((a,b)=>a-b);
    const colors=new Map(owners.map((owner,index)=>[owner,assignmentColors[index%assignmentColors.length]]));
    previewOwnerColors=Object.fromEntries(Object.entries(assignments||{}).map(([grid,owner])=>[grid,colors.get(owner)]));
    if(mapLayer&&previewStyle)mapLayer.setStyle(previewStyle);
  };
  window.gm_authFailure=()=>{notice.textContent=config.error;};
  window.initWorkMap=()=>{
    const map=new google.maps.Map(document.getElementById('work-map'),{center:{lat:35.68,lng:139.76},zoom:11,mapTypeControl:false,streetViewControl:false});
    const layer=new google.maps.Data({map});mapLayer=layer;layer.addGeoJson(config.geometry);
    let selectedGrid=window.dispatchSelectedGrid ?? null;
    const dispatch=Boolean(config.dispatch);
    const fillStyle=f=>{
      const color=previewOwnerColors[String(f.getProperty('id'))];
      return {fillColor:color||(dispatch?(f.getProperty('assigned')?'#2463b5':'#d59a27'):(f.getProperty('review')?'#a65f00':f.getProperty('assigned')?'#225fa2':'#2c7751')),fillOpacity:dispatch?.32:.25,strokeWeight:0};
    };
    previewStyle=fillStyle;
    layer.setStyle(fillStyle);
    const outlines=new google.maps.Data({map});
    outlines.addGeoJson({type:'FeatureCollection',features:config.geometry.features.map(f=>({type:'Feature',geometry:{type:'MultiLineString',coordinates:(f.geometry.type==='Polygon'?[f.geometry.coordinates]:f.geometry.coordinates).map(r=>r[0])},properties:f.properties}))});
    const outlineStyle=f=>{
      const active=dispatch && selectedGrid!==null && Number(f.getProperty('id'))===selectedGrid;
      return {strokeColor:active?'#e34b18':'#193a5e',strokeWeight:active?5:2,
        strokeOpacity:1,clickable:false,zIndex:active?5:1};
    };
    outlines.setStyle(outlineStyle);
    if(dispatch)window.highlightDispatchGrid=id=>{
      selectedGrid=id;outlines.setStyle(outlineStyle);
    };
    let selectedIds=new Set();
    window.highlightSelectedGrids=ids=>{selectedIds=new Set(ids);layer.setStyle(f=>({...fillStyle(f),fillColor:selectedIds.has(Number(f.getProperty('id')))?'#2463b5':fillStyle(f).fillColor,fillOpacity:selectedIds.has(Number(f.getProperty('id')))?.65:.22}));};
    window.focusTaskGrid=id=>{window.highlightDispatchGrid?.(id);const feature=config.geometry.features.find(f=>Number(f.properties.id)===id);if(feature){const b=new google.maps.LatLngBounds();const walk=c=>{if(typeof c[0]==='number')b.extend({lng:c[0],lat:c[1]});else c.forEach(walk);};walk(feature.geometry.coordinates);map.fitBounds(b,30);}};
    if(window.taskSelectedIds)window.highlightSelectedGrids(window.taskSelectedIds);
    const bounds=new google.maps.LatLngBounds();
    function extend(coords){if(typeof coords[0]==='number')bounds.extend({lng:coords[0],lat:coords[1]});else coords.forEach(extend);}
    config.geometry.features.forEach(f=>extend(f.geometry.coordinates));
    if(!bounds.isEmpty())map.fitBounds(bounds,24);
    const shops=new google.maps.Data({map});
    shops.addGeoJson({type:'FeatureCollection',features:(config.stores||[]).filter(s=>Number.isFinite(s.lat)&&Number.isFinite(s.lon)).map(s=>({type:'Feature',geometry:{type:'Point',coordinates:[s.lon,s.lat]},properties:{store_id:s.id,visited:s.visited,title:s.name}}))});
    shops.setStyle(f=>({title:f.getProperty('title'),icon:{path:google.maps.SymbolPath.CIRCLE,scale:6,fillColor:f.getProperty('visited')?'#2c7751':'#225fa2',fillOpacity:1,strokeColor:'#fff',strokeWeight:2}}));
    window.focusVisitStore=id=>{
      const store=(config.stores||[]).find(s=>String(s.id)===String(id));
      if(store&&Number.isFinite(store.lat)&&Number.isFinite(store.lon)){map.panTo({lat:store.lat,lng:store.lon});map.setZoom(Math.max(map.getZoom()||16,17));shops.setStyle(f=>({title:f.getProperty('title'),icon:{path:google.maps.SymbolPath.CIRCLE,scale:String(f.getProperty('store_id'))===String(id)?10:6,fillColor:f.getProperty('visited')?'#2c7751':'#225fa2',fillOpacity:1,strokeColor:'#fff',strokeWeight:2}}));}
    };
    let newMarker;
    window.showNewStoreLocation=(lat,lng)=>{if(!newMarker)newMarker=new google.maps.Marker({map});newMarker.setPosition({lat,lng});map.panTo({lat,lng});};
    function pickLocation(event){const editor=document.getElementById('new-store');if(!editor||(!editor.open&&!editor.hasAttribute('data-new-store-page')))return false;const lat=event.latLng.lat(),lng=event.latLng.lng();document.querySelector('#new-store [name="lat"]').value=lat;document.querySelector('#new-store [name="lon"]').value=lng;window.showNewStoreLocation(lat,lng);return true;}
    map.addListener('click',pickLocation);
    const focus=new URL(location.href).searchParams.get('focus');if(focus)window.focusVisitStore(focus);
    if(config.locate)window.startWorkLocation?.(map,config.locationText);
    shops.addListener('click',event=>{const id=event.feature.getProperty('store_id');
      if(window.openVisitStore){window.openVisitStore(id);return;}
      const card=document.getElementById('store-'+id);
      if(card){card.querySelector('details').open=true;card.scrollIntoView({behavior:'smooth',block:'start'});}});
    layer.addListener('click',event=>{if(pickLocation(event))return;const id=Number(event.feature.getProperty('id'));if(Number.isInteger(id)){if(window.openDispatchGrid)window.openDispatchGrid(id);else document.getElementById((config.selectPrefix || 'grid-').replace(/^#/, '')+id)?.scrollIntoView({behavior:'smooth',block:'start'});}});
  };
  if(!config.key){notice.textContent=config.error;return;}
  const url=new URL('https://maps.googleapis.com/maps/api/js');
  url.search=new URLSearchParams({key:config.key,callback:'initWorkMap',loading:'async',language:config.language==='ja'?'ja':'zh-CN'});
  const script=document.createElement('script');script.src=url.toString();script.async=true;script.onerror=()=>{notice.textContent=config.error;};document.head.append(script);
})();
