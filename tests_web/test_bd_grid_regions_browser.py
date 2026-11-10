"""Render the real grid editor at desktop/mobile sizes with Maps mocked."""
import html
import json
import re
import subprocess
from pathlib import Path

import pytest
from tests_web.test_bd_zone_operations import seed, login  # noqa: F401


def test_grid_colors_conversion_and_drawer_fit_viewport(client, seed, tmp_path):
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not chrome.exists():
        pytest.skip('Chrome is required for rendered layout verification')
    login(client, 'ops-admin')
    import app.db as appdb
    from app.models import BdWorkGrid
    with appdb.SessionLocal() as db:
        original = db.get(BdWorkGrid, seed['grid'])
        original.name = 'LongUnbrokenGridName' * 6
        original.keywords = ['商店街', '改行する長いキーワード' * 6]
        original.stations = ['新宿駅', 'とても長い車站名' * 5]
        for i in range(20):
            db.add(BdWorkGrid(release_id=original.release_id, zone_id=original.zone_id,
                source_id=f'layout-{i}', name=f'网格 {i}', keywords=original.keywords,
                stations=original.stations, geometry=original.geometry,
                area_km2=original.area_km2, store_count=0, state='ready'))
        db.commit()
    page = client.get('/zones?mode=manage').text
    page = re.sub(r'<script\b[^>]*\bsrc="[^"]*"[^>]*>\s*</script>', '', page)
    page = re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', '', page)
    css = '\n'.join(Path(p).read_text() for p in ('app/static/app.css', 'app/static/bd_zone_manage.css'))
    page = page.replace('</head>', '<style>' + css + '</style></head>')
    harness = r'''
window.addEventListener('load',async()=>{try{
 const q=s=>document.querySelector(s), config=JSON.parse(q('#grid-region-config').textContent);
 config.fit_grids=false;
 config.grids.push({id:999,name:'低密度 <网格>',revision:9,disabled:true,convertible:true,area:1,stores:0},
   {id:998,zone_id:null,area:1,stores:1},{id:997,disabled:true,convertible:false,area:1,stores:0});
 q('#grid-region-config').textContent=JSON.stringify(config);
 let previewRequests=0;
 const layers=[];
 class Data {
   constructor(){this.handlers={};layers.push(this);} setMap(map){this.map=map;}
   addGeoJson(){} setStyle(style){this.style=style;} addListener(name,fn){this.handlers[name]=fn;}
 }
 class OverlayView {
   setMap(map){this.map=map;this.onAdd();this.draw();}
   getMap(){return this.map;} getPanes(){return {overlayMouseTarget:q('#zone-map')};}
   getProjection(){return {fromLatLngToDivPixel:()=>({x:160,y:80})};}
 }
 window.google={maps:{Data,OverlayView}};
 window.fetch=async()=>{previewRequests++;return {ok:true,json:async()=>({geometry:{type:'Polygon',coordinates:[]},
   properties:{area_km2:2,store_count:1,grid_count:1,shape_valid:true}})};};
 const map={addListener(){},getDiv(){return q('#zone-map');}};
 new Function(GRID_SCRIPT)();window.initGridRegionEditor(map);
 const feature=id=>({getProperty:()=>id});
 const layer=layers[0], assigned=config.grids.find(g=>g.zone_id!=null),
   colors=[assigned.id,998,999].map(id=>layer.style(feature(id)).fillColor);
 if(new Set(colors).size!==3)throw Error('grid states share background colors');
 const swatches=[...document.querySelectorAll('.grid-map-legend i')].map(e=>getComputedStyle(e).backgroundColor);
 if(swatches.length!==4||new Set(swatches).size!==4)throw Error('legend colors');
 layer.handlers.click({feature:feature(assigned.id),latLng:{},stop(){}});
 await new Promise(r=>setTimeout(r,250));
 if(q('#grid-region-editor').hidden)throw Error('selection drawer');
 const list=q('.grid-region-list'),save=q('#grid-region-save'),buttonBox=save.getBoundingClientRect();
 if(getComputedStyle(list).overflowY!=='auto'||list.clientHeight<40||list.scrollHeight<=list.clientHeight)throw Error('grid list scroll bounds '+JSON.stringify({height:list.clientHeight,scroll:list.scrollHeight}));
 list.scrollTop=list.scrollHeight;if(!list.scrollTop)throw Error('grid list cannot scroll');
 const requestsBeforeTyping=previewRequests, name=q('#grid-region-name');
 for(const char of '重新合并后的片区'){name.value+=char;name.dispatchEvent(new Event('input',{bubbles:true}));}
 if(name.value!=='重新合并后的片区'||previewRequests!==requestsBeforeTyping||save.disabled)throw Error('name input recalculates geometry');
 if(save.disabled||buttonBox.bottom>innerHeight+1||buttonBox.left<0||buttonBox.right>innerWidth+1)throw Error('save button not reachable '+JSON.stringify(buttonBox));
 if(q('.grid-region-list').scrollWidth>q('.grid-region-list').clientWidth+1)throw Error('tag overflow');
 if(!q('.grid-tag-zone')||!q('.grid-tag-station')||!q('.grid-row-tags'))throw Error('missing metadata tags');
 const split=q('#grid-region-zone-actions [data-split-zone]');
 if(split.hidden||split.action.split('?')[0].endsWith('/zones/split')===false)throw Error('selected region split action');
 if(!q('.grid-map-info form[action="/zones/split"]'))throw Error('popup split action');
 if(q('.grid-map-info').scrollWidth>q('.grid-map-info').clientWidth+1)throw Error('popup name overflow');
 const card=q('.grid-map-info'),cardBox=card.getBoundingClientRect(),
   popupForm=card.querySelector('form'),action=popupForm.querySelector('button'),
   actionBox=action.getBoundingClientRect(),copyBox=card.querySelector('.grid-map-info-copy').getBoundingClientRect(),
   closeBox=card.querySelector(':scope > button').getBoundingClientRect();
 if(popupForm.querySelector('span'))throw Error('duplicate popup region name');
 if(actionBox.width<96||actionBox.height<36||actionBox.top<copyBox.bottom||actionBox.right>cardBox.right||action.scrollWidth>action.clientWidth+1)throw Error('split button layout');
 if(closeBox.width!==30||closeBox.height!==30||cardBox.width>300||cardBox.left<0||cardBox.right>innerWidth)throw Error('popup bounds');
 if(!popupForm.elements.csrf_token.value||!popupForm.elements._ft.value||popupForm.elements.zone_id.value!==String(assigned.zone_id))throw Error('popup split contract');
 layer.handlers.click({feature:feature(999),stop(){}});
 await new Promise(r=>setTimeout(r,220));
 const panel=q('#grid-conversion-panel'),rect=panel.getBoundingClientRect();
 if(panel.hidden||!q('#grid-region-editor').hidden)throw Error('conversion drawer');
 if(rect.width<200||rect.left<0||rect.right>innerWidth+1||rect.bottom>innerHeight+1)throw Error('conversion bounds '+JSON.stringify({panel:rect,parents:['main.wrap','.zone-manager','.zone-map-layout','#zone-map'].map(s=>({s,rect:q(s).getBoundingClientRect(),width:getComputedStyle(q(s)).width,box:getComputedStyle(q(s)).boxSizing})),viewport:[innerWidth,innerHeight]}));
 if(panel.scrollWidth>panel.clientWidth+1)throw Error('drawer overflow');
 if(q('#grid-conversion-name').textContent!=='低密度 <网格>'||q('#grid-conversion-name img'))throw Error('unsafe name');
 const form=panel.querySelector('form');
 if(!form.action.endsWith('/zones/grids/enable')||!form.elements.csrf_token.value||!form.elements._ft.value)throw Error('form contract');
 if(form.elements.grid_id.value!=='999'||form.elements.revision.value!=='9')throw Error('conversion identity');
 q('#grid-conversion-close').click();
 if(!panel.hidden||q('#grid-region-editor').hidden)throw Error('restore selection');
 layer.handlers.click({feature:feature(997),stop(){}});
 if(!panel.hidden)throw Error('protected grid conversion');
 const visit=document.createElement('div');visit.className='visit-list';
 visit.innerHTML='<table><tbody>'+[1,2].map(id=>'<tr class="visit-row" data-store-id="'+id+'" aria-expanded="false" tabindex="0"><td>Shop '+id+'</td></tr><tr class="visit-detail-row" data-detail-for="'+id+'" hidden><td>Detail</td></tr>').join('')+'</tbody></table>';
 document.body.append(visit);
 let mapFocusCalls=0;window.focusVisitStore=()=>mapFocusCalls++;
 new Function(VISIT_SCRIPT)();
 const rows=[...visit.querySelectorAll('.visit-row')],details=[...visit.querySelectorAll('.visit-detail-row')];
 rows[0].click();if(details[0].hidden||rows[0].getAttribute('aria-expanded')!=='true')throw Error('shop did not open');
 rows[0].click();if(!details[0].hidden||mapFocusCalls!==1||rows[0].getAttribute('aria-expanded')!=='false')throw Error('shop did not collapse');
 for(const key of ['Enter',' ']){rows[0].dispatchEvent(new KeyboardEvent('keydown',{key,bubbles:true}));if(details[0].hidden)throw Error('keyboard did not open');rows[0].dispatchEvent(new KeyboardEvent('keydown',{key,bubbles:true}));if(!details[0].hidden)throw Error('keyboard did not collapse');}
 rows[0].click();rows[1].click();if(!details[0].hidden||details[1].hidden)throw Error('shops do not switch');
 window.openVisitStore(2);window.openVisitStore(2);if(details[1].hidden)throw Error('map should keep details open');
 parent.postMessage({gridQA:true,width:innerWidth,colors,conversion:true,noOverflow:true,shopToggle:true,nameInput:true},'*');
}catch(error){parent.postMessage({gridQA:true,width:innerWidth,error:String(error)},'*');}});
'''
    harness = harness.replace('GRID_SCRIPT', json.dumps(Path('app/static/bd_grid_regions.js').read_text()))
    harness = harness.replace('VISIT_SCRIPT', json.dumps(Path('app/static/bd_visit.js').read_text()))
    fixture = page.replace('</body>', '<script>' + harness + '</script></body>')
    parent = '<meta charset="utf-8"><pre id="qa">waiting</pre><script>let results=[];addEventListener("message",e=>{if(e.data.gridQA){results.push(e.data);document.getElementById("qa").textContent=JSON.stringify(results)}})</script>'
    for width, height in ((393, 852), (1100, 900)):
        parent += f'<iframe style="width:{width}px;height:{height}px;border:0" srcdoc="{html.escape(fixture, quote=True)}"></iframe>'
    file = tmp_path / 'grid-regions.html'
    file.write_text(parent)
    result = subprocess.run([str(chrome), '--headless', '--disable-gpu', '--no-first-run',
        '--disable-background-networking', f'--user-data-dir={tmp_path}/chrome', '--dump-dom',
        '--virtual-time-budget=2500', file.as_uri()], capture_output=True, text=True, timeout=35)
    match = re.search(r'<pre id="qa">(.*?)</pre>', result.stdout, re.S)
    assert match, result.stderr[-400:]
    results = json.loads(html.unescape(match.group(1)))
    assert len(results) == 2 and not any('error' in item for item in results), results
