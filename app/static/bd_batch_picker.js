/* Map selection and the accessible checkbox list share one source of truth. */
(() => {
  'use strict';
  const dialog=document.getElementById('create-batch'), node=document.getElementById('batch-map-config');
  if(!dialog||!node)return;
  const config=JSON.parse(node.textContent), q=s=>dialog.querySelector(s);
  const rows=[...dialog.querySelectorAll('[data-region-search]')], selected=new Set();
  const byId=new Map(rows.map(row=>[Number(row.querySelector('input').value),row]));
  const search=q('#batch-region-search'), regionFilter=q('#batch-region-filter'), notice=q('#batch-map-status');
  let currentRegion=regionFilter.value;
  let map, layer, loading=false, loadTimer;
  const number=value=>Number(value).toLocaleString(config.language);
  function notify(text){notice.textContent=text;notice.hidden=!text;}
  function fallback(text){clearTimeout(loadTimer);notify(text);q('.batch-picker-options').open=true;}
  function inCurrentRegion(row){return currentRegion==='all'||row.dataset.region===currentRegion;}
  function visible(id){const row=byId.get(id);return row&&inCurrentRegion(row)&&!row.hidden;}
  function style(feature){const id=Number(feature.getProperty('id')), checked=selected.has(id);return {
    title:feature.getProperty('name'),visible:visible(id),fillColor:checked?'#315fe9':'#8ba7c2',fillOpacity:checked?.48:.16,
    strokeColor:checked?'#234bd1':'#5e7893',strokeWeight:checked?3:1.3,zIndex:checked?2:1};}
  function fit(){if(!map||!layer)return;const bounds=new google.maps.LatLngBounds();let count=0;
    layer.forEach(feature=>{if(visible(Number(feature.getProperty('id'))))feature.getGeometry()?.forEachLatLng(point=>{bounds.extend(point);count++;});});
    if(count)map.fitBounds(bounds,48);
  }
  function filter(){const keyword=search.value.trim().normalize('NFKC').toLocaleLowerCase();let count=0;
    rows.forEach(row=>{row.hidden=!inCurrentRegion(row)||!row.dataset.regionSearch.normalize('NFKC').toLocaleLowerCase().includes(keyword);if(!row.hidden)count++;});
    q('#batch-region-empty').hidden=count!==0;if(layer)layer.setStyle(style);fit();
  }
  function bulkSelect(invert){rows.forEach(row=>{if(!inCurrentRegion(row))return;const id=Number(row.querySelector('input').value);
    if(invert){selected.has(id)?selected.delete(id):selected.add(id);}else selected.add(id);
  });render();}
  function render(){const list=q('#batch-selected-list');list.replaceChildren();let area=0,stores=0;
    selected.forEach(id=>{const row=byId.get(id);area+=Number(row.dataset.area);stores+=Number(row.dataset.stores);
      const item=document.createElement('li'),copy=document.createElement('div'),name=document.createElement('strong'),meta=document.createElement('small'),remove=document.createElement('button');
      name.textContent=row.dataset.name;meta.textContent=number(row.dataset.stores)+' '+config.texts.stores+' · '+Number(row.dataset.area).toFixed(2)+' km²';
      copy.append(name,meta);remove.type='button';remove.className='batch-picker-remove';remove.textContent='×';remove.setAttribute('aria-label',config.texts.remove+' '+row.dataset.name);remove.addEventListener('click',()=>toggle(id));item.append(copy,remove);list.append(item);
    });
    rows.forEach(row=>{row.querySelector('input').checked=selected.has(Number(row.querySelector('input').value));});
    q('#batch-selected-count').textContent=selected.size;q('#batch-selected-empty').hidden=selected.size>0;
    q('#batch-selection-clear').disabled=!selected.size;q('#batch-create-submit').disabled=!selected.size;
    q('#batch-selection-summary').textContent=number(selected.size)+' '+config.texts.regions+' · '+number(stores)+' '+config.texts.stores+' · '+area.toFixed(2)+' km²';
    if(layer)layer.setStyle(style);
  }
  function toggle(id){if(!byId.has(id))return;selected.has(id)?selected.delete(id):selected.add(id);render();}
  rows.forEach(row=>row.querySelector('input').addEventListener('change',()=>toggle(Number(row.querySelector('input').value))));
  q('#batch-selection-clear').addEventListener('click',()=>{selected.clear();render();});
  q('#batch-select-all').addEventListener('click',()=>bulkSelect(false));
  q('#batch-select-invert').addEventListener('click',()=>bulkSelect(true));
  regionFilter.addEventListener('change',()=>{currentRegion=regionFilter.value;filter();});
  search.addEventListener('input',()=>{filter();if(search.value)q('.batch-picker-options').open=true;});
  q('#batch-map-fit').addEventListener('click',fit);
  dialog.querySelector('form').addEventListener('submit',event=>{if(!selected.size)event.preventDefault();});
  window.initBatchPickerMap=async()=>{try{
    const response=await fetch(config.geometry_url,{credentials:'same-origin',headers:{Accept:'application/json'}});
    if(!response.ok)throw Error('map geometry unavailable');
    const geometry=await response.json();
    clearTimeout(loadTimer);
    map=new google.maps.Map(q('#batch-region-map'),{center:{lat:35.68,lng:139.76},zoom:11,mapTypeControl:false,streetViewControl:false,fullscreenControl:false,gestureHandling:'greedy'});
    layer=new google.maps.Data({map});layer.addGeoJson(geometry);layer.setStyle(style);
    layer.addListener('click',event=>{const id=Number(event.feature.getProperty('id'));if(visible(id))toggle(id);});
    layer.addListener('mouseover',event=>layer.overrideStyle(event.feature,{strokeWeight:3,fillOpacity:.4}));
    layer.addListener('mouseout',event=>layer.revertStyle(event.feature));notify('');fit();
  }catch{map=null;loading=false;fallback(config.texts.error);}};
  function openMap(){if(map){google.maps.event.trigger(map,'resize');fit();return;}if(loading)return;
    if(!rows.length){fallback(config.texts.empty);return;}if(!config.key){fallback(config.texts.missing);return;}
    loading=true;if(window.google?.maps?.Map){window.initBatchPickerMap();return;}
    const script=document.createElement('script');script.src='https://maps.googleapis.com/maps/api/js?'+new URLSearchParams({key:config.key,callback:'initBatchPickerMap',language:config.language,v:'weekly',loading:'async'});
    script.async=true;script.onerror=()=>fallback(config.texts.error);document.head.append(script);loadTimer=setTimeout(()=>fallback(config.texts.error),15000);
  }
  // The existing dialog controller owns focus, dismissal and deep links.
  document.querySelectorAll('[data-create-batch]').forEach(button=>button.addEventListener('click',openMap));
  addEventListener('hashchange',()=>{if(location.hash==='#create-batch')openMap();});
  dialog.addEventListener('toggle',()=>{if(dialog.open)openMap();});
  filter();render();if(location.hash==='#create-batch')openMap();
})();
