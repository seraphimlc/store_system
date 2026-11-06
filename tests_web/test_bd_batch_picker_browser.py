"""Exercise the rendered batch picker with Maps mocked; no live provider calls."""
import html
import json
import re
import subprocess
from pathlib import Path

import pytest
from tests_web.test_bd_zone_operations import seed, login  # noqa: F401


def test_batch_map_selection_form_and_responsive_layout(client, seed, tmp_path):
    chrome = Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not chrome.exists():
        pytest.skip('Chrome is required for rendered layout verification')
    import app.db as appdb
    from app.models import BdRegion, BdZoneRelease
    with appdb.SessionLocal() as db:
        original = db.get(BdRegion, seed['zone'])
        release = BdZoneRelease(fingerprint='picker-other', source={'region_code': 'chiba'}, parameters={}, audit={},
                                reviews=[], created_by=seed['users']['ops-admin'])
        db.add(release)
        db.flush()
        db.add_all([BdRegion(release_id=original.release_id, source_id='picker-second',
            name='片区 <安全文本>', geometry=original.geometry, area_km2=2, store_count=7,
            review=[], state='ready'), BdRegion(release_id=release.id, source_id='picker-other',
            name='其他地区片区', geometry=original.geometry, area_km2=3, store_count=9,
            review=[], state='ready'), BdRegion(release_id=release.id, source_id='picker-other-2',
            name='其他地区片区二', geometry=original.geometry, area_km2=4, store_count=11,
            review=[], state='ready')])
        db.commit()
    login(client, 'ops-admin')
    page = client.get('/zone-tasks').text
    page = re.sub(r'<script\b[^>]*\bsrc="[^"]*"[^>]*>\s*</script>', '', page)
    page = re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>', '', page)
    css = '\n'.join(Path('app/static/' + name).read_text() for name in
                    ('app.css', 'bd_batch.css', 'bd_tasks.css', 'bd_batch_picker.css'))
    page = page.replace('</head>', '<style>' + css + '</style></head>')
    harness = r'''
addEventListener('load',()=>{try{
 const q=s=>document.querySelector(s),config=JSON.parse(q('#batch-map-config').textContent);
 config.key='mock-key';q('#batch-map-config').textContent=JSON.stringify(config);
 const layers=[];let fitted=0;
 class Data{constructor(){this.handlers={};layers.push(this);}addGeoJson(collection){this.features=collection.features.map(f=>({getProperty:key=>f.properties[key],getGeometry:()=>({forEachLatLng:cb=>cb({lat:1,lng:1})})}));}setStyle(fn){this.style=fn;}addListener(k,f){this.handlers[k]=f;}forEach(f){this.features.forEach(f);}overrideStyle(){}revertStyle(){}}
 window.google={maps:{Map:class{fitBounds(){fitted++;}},Data,LatLngBounds:class{extend(){}},event:{trigger(){}}}};
 new Function(CONTROLLER)();new Function(PICKER)();
 q('[data-create-batch]').click();const dialog=q('#create-batch'),layer=layers[0];
 if(!dialog.open||!layer||!fitted)throw Error('map not initialized on open');
 const rows=[...dialog.querySelectorAll('[data-region-search]')], first=rows.find(r=>!r.hidden),id=Number(first.querySelector('input').value),feature=layer.features.find(f=>f.getProperty('id')===id);
 const form=dialog.querySelector('form');form.elements.title.value='地图创建验收';
 layer.handlers.click({feature});
 if(q('#batch-selected-count').textContent!=='1'||q('#batch-create-submit').disabled||!first.querySelector('input').checked)throw Error('map selection did not sync');
 if(new FormData(form).getAll('region_ids').join()!==String(id))throw Error('submission contract');
 if(layer.style(feature).fillColor!=='#315fe9')throw Error('selected map color');
 q('.batch-picker-remove').click();if(!q('#batch-create-submit').disabled||new FormData(form).has('region_ids'))throw Error('remove did not sync');
 const second=rows.find(r=>r!==first);
 layer.handlers.click({feature});second.querySelector('input').click();
 if(q('#batch-selected-list').children.length!==2||q('#batch-selected-list img'))throw Error('list selection or escaping');
 if(q('#batch-release-filter')||q('.batch-picker-map-toolbar').textContent.includes('正式版本'))throw Error('internal source is visible');
 if(!q('#batch-selection-summary').textContent.includes('km²'))throw Error('summary');
 const search=q('#batch-region-search');search.value=second.dataset.name;search.dispatchEvent(new Event('input'));
 if(!first.hidden||second.hidden||q('#batch-selected-count').textContent!=='2')throw Error('search lost selection');
 q('#batch-selection-clear').click();if(new FormData(form).has('region_ids'))throw Error('clear did not sync');
 search.value='';search.dispatchEvent(new Event('input'));layer.handlers.click({feature});
 const other=rows.find(r=>r.dataset.name==='其他地区片区');
 if(!other||other.hidden)throw Error('other region not shown');
 other.querySelector('input').click();
 const chosen=new FormData(form).getAll('region_ids');
 if(chosen.length!==2||!chosen.includes(other.querySelector('input').value)||!chosen.includes(String(id)))throw Error('cross-region selection not submitted');
 const regionFilter=q('#batch-region-filter'),chibaRows=rows.filter(r=>r.dataset.region==='chiba');
 regionFilter.value='chiba';regionFilter.dispatchEvent(new Event('change'));
 if(chibaRows.length!==2||chibaRows.some(r=>r.hidden)||!first.hidden||q('#batch-selected-count').textContent!=='2')throw Error('region filter lost selection or scope');
 q('#batch-select-all').click();
 if(q('#batch-selected-count').textContent!=='3'||chibaRows.some(r=>!r.querySelector('input').checked))throw Error('select all not scoped to region');
 q('#batch-select-invert').click();
 if(q('#batch-selected-count').textContent!=='1'||chibaRows.some(r=>r.querySelector('input').checked)||!first.querySelector('input').checked)throw Error('invert changed another region');
 q('#batch-select-invert').click();regionFilter.value='all';regionFilter.dispatchEvent(new Event('change'));
 const bounds=dialog.getBoundingClientRect(),map=q('#batch-region-map').getBoundingClientRect(),side=q('.batch-picker-sidebar').getBoundingClientRect();
 if(bounds.left<0||bounds.right>innerWidth+1||dialog.scrollWidth>dialog.clientWidth+1||map.height<280)throw Error('layout overflow');
 if(innerWidth>760&&side.left<map.right-1)throw Error('selected list not right of map');
 q('#batch-create-submit').scrollIntoView({block:'nearest'});const button=q('#batch-create-submit').getBoundingClientRect();if(button.bottom>innerHeight+1||button.top<0)throw Error('submit unreachable');
 q('[data-close-batch]').click();if(dialog.open)throw Error('close');
 // Reinitialize without a map key: the keyboard checkbox path remains usable.
 config.key='';q('#batch-map-config').textContent=JSON.stringify(config);new Function(PICKER)();q('[data-create-batch]').click();
 if(!q('.batch-picker-options').open||q('#batch-map-status').hidden)throw Error('missing key fallback');
 parent.postMessage({batchPicker:true,width:innerWidth,selection:true,form:true,layout:true,fallback:true},'*');
}catch(error){parent.postMessage({batchPicker:true,width:innerWidth,error:String(error)},'*');}});
'''
    harness = harness.replace('CONTROLLER', json.dumps(Path('app/static/bd_batch_create.js').read_text()))
    harness = harness.replace('PICKER', json.dumps(Path('app/static/bd_batch_picker.js').read_text()))
    fixture = page.replace('</body>', '<script>' + harness + '</script></body>')
    parent = '<meta charset="utf-8"><pre id="qa">waiting</pre><script>let results=[];addEventListener("message",e=>{if(e.data.batchPicker){results.push(e.data);document.getElementById("qa").textContent=JSON.stringify(results)}})</script>'
    for width, height in ((393, 852), (1280, 900)):
        parent += f'<iframe style="width:{width}px;height:{height}px;border:0" srcdoc="{html.escape(fixture, quote=True)}"></iframe>'
    file = tmp_path / 'batch-picker.html'
    file.write_text(parent)
    result = subprocess.run([str(chrome), '--headless', '--disable-gpu', '--no-first-run',
        '--disable-background-networking', f'--user-data-dir={tmp_path}/chrome', '--dump-dom',
        '--virtual-time-budget=2500', file.as_uri()], capture_output=True, text=True, timeout=35)
    match = re.search(r'<pre id="qa">(.*?)</pre>', result.stdout, re.S)
    assert match, result.stderr[-400:]
    results = json.loads(html.unescape(match.group(1)))
    assert len(results) == 2 and not any('error' in item for item in results), results
