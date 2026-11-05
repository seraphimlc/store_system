"""Actual admin HTML/CSS/JS at mobile/desktop sizes, with Maps and network mocked."""
import html
import json
import re
import subprocess
from pathlib import Path

import pytest
from tests_web.test_bd_zone_operations import seed, login  # noqa: F401


def test_merchant_map_clusters_points_and_detail_fit_the_viewport(client,seed,tmp_path):
    chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not chrome.exists():pytest.skip('Chrome is required for rendered layout verification')
    login(client,'ops-admin')
    page=client.get('/merchant-map').text
    page=re.sub(r'<script\b[^>]*\bsrc="[^"]*"[^>]*>\s*</script>','',page)
    page=re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>','',page)
    css='\n'.join(Path(p).read_text() for p in ('app/static/app.css','app/static/bd_merchant_map.css'))
    page=page.replace('</head>','<style>'+css+'</style></head>')
    harness=r'''
window.addEventListener('load',async()=>{try{
 const q=s=>document.querySelector(s),qa=s=>[...document.querySelectorAll(s)];
 const config=JSON.parse(q('#merchant-map-config').textContent);config.key='';q('#merchant-map-config').textContent=JSON.stringify(config);
 let map,layer,showPoints=false;
 const pane=document.createElement('div');pane.style.cssText='position:absolute;inset:0;pointer-events:none';q('#merchant-map').append(pane);
 class MapMock {
  constructor(){map=this;this.listeners={};this.zoom=9;}
  getZoom(){return this.zoom;} getBounds(){return {toJSON:()=>({west:139,south:35,east:140,north:36})};}
  addListener(n,f){this.listeners[n]=f;} panTo(){}
  fitBounds(){queueMicrotask(()=>this.listeners.idle());}
  setZoom(v){this.zoom=v;showPoints=true;this.listeners.bounds_changed();this.listeners.idle();}
 }
 class Data {
  constructor(){layer=this;this.features=[];this.listeners={};}
  setStyle(){} addListener(n,f){this.listeners[n]=f;}
  forEach(f){[...this.features].forEach(f);} remove(item){this.features=this.features.filter(v=>v!==item);}
  addGeoJson(g){this.features=g.features;}
 }
 class Overlay {
  setMap(m){if(m){this.onAdd();this.draw();this.button.style.pointerEvents='auto';}else this.onRemove();}
  getPanes(){return {overlayMouseTarget:pane};}
  getProjection(){return {fromLatLngToDivPixel:point=>({x:90+(point.lng-139)*90,y:250})};}
  static preventMapHitsAndGesturesFrom(){}
 }
 window.google={maps:{Map:MapMock,Data,OverlayView:Overlay,SymbolPath:{CIRCLE:'circle'},LatLng:class{constructor(lat,lng){this.lat=lat;this.lng=lng;}}}};
 window.fetch=async url=>({ok:true,json:async()=>url.includes('/detail?') ? {
   name:'<img src=x onerror=alert(1)>',place_id:'osm:node/1',address:'東京都新宿区'+ '長い住所'.repeat(30),
   lat:35.7,lon:139.7,covered:true,sources:'osm',location_accuracy:'accurate'} : {
   mode:showPoints?'points':'clusters',summary:{merchant_count:100,covered_count:55,coverage_pct:55},as_of:'2026-11-05T12:00:00Z',
   points:[{id:'osm:node/1',lat:35.7,lon:139.7,covered:true}],
   cells:[{lat:35,lon:139,merchant_count:50,coverage_pct:80},{lat:35,lon:140,merchant_count:30,coverage_pct:50},{lat:35,lon:141,merchant_count:20,coverage_pct:0}]}});
 const script=document.createElement('script');script.textContent=MAP_SCRIPT;document.body.append(script);
 window.initMerchantMap();await new Promise(r=>setTimeout(r,350));
 if(qa('.merchant-map-cluster').length!==3)throw Error('cluster count');
 const green=q('.coverage-green.merchant-map-cluster'),yellow=q('.coverage-yellow.merchant-map-cluster'),red=q('.coverage-red.merchant-map-cluster');
 if(!green||!yellow||!red)throw Error('missing three coverage colors');
 for(const badge of [green,yellow,red]){
   if(!badge.textContent.includes('%'))throw Error('color without percentage');
   if(getComputedStyle(badge).backgroundColor==='rgba(0, 0, 0, 0)')throw Error('transparent number background');
 }
 green.click();await new Promise(r=>setTimeout(r,350));
 if(layer.features.length!==1||qa('.merchant-map-cluster').length)throw Error('zoom did not replace aggregates with points');
 await layer.listeners.click({feature:{getProperty:()=> 'osm:node/1'}});
 const detail=q('#merchant-map-detail'),box=detail.getBoundingClientRect();
 if(detail.hidden||box.width<200||box.left<0||box.right>innerWidth+1)throw Error('detail bounds');
 if(q('#merchant-map-detail-title').textContent!=='<img src=x onerror=alert(1)>'||detail.querySelector('img'))throw Error('unsafe detail HTML');
 if(detail.scrollWidth>detail.clientWidth+1)throw Error('detail overflow');
 if(document.documentElement.scrollWidth>innerWidth+1)throw Error('page overflow');
 q('#merchant-map-close').click();if(!detail.hidden)throw Error('close failed');
 parent.postMessage({merchantMapQA:true,width:innerWidth,clusters:3,points:1,detailSafe:true,noOverflow:true},'*');
}catch(error){parent.postMessage({merchantMapQA:true,width:innerWidth,error:String(error)},'*');}});
'''
    harness=harness.replace('MAP_SCRIPT',json.dumps(Path('app/static/bd_merchant_map.js').read_text()))
    fixture=page.replace('</body>','<script>'+harness+'</script></body>')
    parent='<meta charset="utf-8"><pre id="qa">waiting</pre><script>let r=[];addEventListener("message",e=>{if(e.data.merchantMapQA){r.push(e.data);document.getElementById("qa").textContent=JSON.stringify(r)}})</script>'
    for w,h in ((393,852),(1100,900)):
        parent+=f'<iframe style="width:{w}px;height:{h}px;border:0" srcdoc="{html.escape(fixture,quote=True)}"></iframe>'
    file=tmp_path/'merchant-map.html';file.write_text(parent)
    result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
        f'--user-data-dir={tmp_path}/chrome','--dump-dom','--virtual-time-budget=2500',file.as_uri()],capture_output=True,text=True,timeout=35)
    match=re.search(r'<pre id="qa">(.*?)</pre>',result.stdout,re.S)
    assert match,result.stderr[-400:]
    results=json.loads(html.unescape(match.group(1)))
    assert len(results)==2 and not any('error' in item for item in results),results
