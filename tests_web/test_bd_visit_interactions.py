"""Real rendered page, real CSS/JS, no server/provider or native file picker."""
import html
import json
import re
import subprocess
from pathlib import Path
import pytest
import app.db as appdb
from tests_web.test_bd_zone_operations import seed,login
from tests_web.test_bd_visit_page import multi_batch


def test_visit_rows_photo_cancel_and_scroll_header(client,seed,tmp_path):
    chrome=Path('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome')
    if not chrome.exists():pytest.skip('Chrome is required for actual browser layout checks')
    with appdb.SessionLocal() as db:
        _,_,grid,_=multi_batch(db,seed);gid=grid.id;db.commit()
    login(client,'worker');response=client.get(f'/my/work/batch-grids/{gid}')
    assert response.status_code==200
    page=re.sub(r'<script\b[^>]*\bsrc="[^"]*"[^>]*>\s*</script>','',response.text)
    page=re.sub(r'<link\b[^>]*rel="stylesheet"[^>]*>','',page)
    styles='\n'.join(Path(p).read_text() for p in (
        'app/static/app.css','app/static/bd_dispatch.css','app/static/bd_tasks.css','app/static/bd_visit.css'))
    page=page.replace('</head>','<style>'+styles+'</style></head>')
    harness=r'''
window.addEventListener('load',async()=>{const releaseFocus=await parent.claimVisitQa();try{
 const q=s=>document.querySelector(s),qa=s=>[...document.querySelectorAll(s)];

 const filters=q('[data-testid=visit-filters]'),search=filters.querySelector('[type=search]');
 search.focus();search.value='東京';search.dispatchEvent(new Event('input',{bubbles:true}));
 await new Promise(resolve=>setTimeout(resolve,650));
 if(document.activeElement!==search||search.value!=='東京')throw Error('typing reloaded page or lost focus');
 search.value+=' 渋谷';search.dispatchEvent(new Event('input',{bubbles:true}));
 search.value='';search.dispatchEvent(new Event('search',{bubbles:true}));
 await new Promise(resolve=>setTimeout(resolve,650));
 if(document.activeElement!==search)throw Error('clearing search lost focus');
 const clone=filters.cloneNode(true);clone.classList.remove('visit-equal-filters');
 let navigations=[];const fakeWindow={location:{set search(value){navigations.push(value);}}};
 new Function('window','document',FILTER_SCRIPT)(fakeWindow,{querySelectorAll:()=>[clone]});
 const typed=clone.querySelector('[type=search]');
 typed.value='  Cafe 100%_  ';typed.dispatchEvent(new Event('compositionstart'));
 const enter=new KeyboardEvent('keydown',{key:'Enter',isComposing:true,cancelable:true});typed.dispatchEvent(enter);
 clone.dispatchEvent(new Event('submit',{cancelable:true}));
 if(!enter.defaultPrevented||navigations.length)throw Error('IME confirmation submitted search');
 typed.dispatchEvent(new Event('compositionend'));
 clone.querySelector('[name=task_state]').value='pending';
 clone.dispatchEvent(new Event('submit',{cancelable:true}));
 let params=new URLSearchParams(navigations.at(-1));
 if(params.get('kw')!=='Cafe 100%_'||params.get('task_state')!=='pending'||params.has('page'))throw Error('search query or pagination reset');
 const status=clone.querySelector('[name=status]');status.value='abnormal';status.dispatchEvent(new Event('change',{bubbles:true}));
 if(navigations.length!==2||new URLSearchParams(navigations.at(-1)).get('status')!=='abnormal')throw Error('dropdown did not submit');
 typed.value=' ';clone.dispatchEvent(new Event('submit',{cancelable:true}));
 if(new URLSearchParams(navigations.at(-1)).has('kw'))throw Error('blank query not cleared');
 const searchBox=q('.visit-search-control').getBoundingClientRect(),queryButton=q('.visit-search-control [type=submit]').getBoundingClientRect();
 if(searchBox.left<0||searchBox.right>innerWidth||queryButton.width<44||queryButton.right>searchBox.right+1)throw Error('search controls overflow');
 const rows=qa('tr.visit-row'),form=rows[0].nextElementSibling.querySelector('.visit-form');
 if(q('[data-edit-store]'))throw Error('small details trigger still exists');
 let copied='';Object.defineProperty(navigator,'clipboard',{value:{writeText:async text=>{copied=text;}}});
 const copy=rows[0].querySelector('[data-copy-store]');
 copy.click();await new Promise(resolve=>setTimeout(resolve,0));
 if(copied!==copy.dataset.copyText||rows[0].getAttribute('aria-expanded')!=='false'||!copy.classList.contains('is-copied'))throw Error('copy store name failed or opened row');
 rows[0].click();
 if(rows[0].getAttribute('aria-expanded')!=='true'||window.lastFocused!==rows[0].dataset.storeId)throw Error('row did not open/focus');
 const note=form.querySelector('[name=note]');note.value='keep my unsaved text';
 const input=form.querySelector('[data-photo-files]');
 input.dispatchEvent(new Event('cancel'));
 if(new FormData(form).getAll('photos').length)throw Error('cancel produced an upload field');
 const trigger=form.querySelector('[data-open-photo-picker]'),picker=q('#visit-photo-picker');
 trigger.click();if(!picker.open)throw Error('picker did not open');
 picker.querySelector('[data-photo-choice=cancel]').click();if(picker.open)throw Error('cancel did not close');
 let cameraOpened=false;form.querySelector('[data-photo-camera]').click=()=>{cameraOpened=true};
 trigger.click();picker.querySelector('[data-photo-choice=camera]').click();
 if(!cameraOpened||picker.open)throw Error('camera action failed');
 const transfer=new DataTransfer();transfer.items.add(new File(['photo'],'shop.jpg',{type:'image/jpeg'}));
 input.files=transfer.files;input.dispatchEvent(new Event('change'));
 rows[1].click();
 if(rows[0].getAttribute('aria-expanded')!=='false'||rows[1].getAttribute('aria-expanded')!=='true')throw Error('exclusive expansion failed');
 rows[0].click();
 if(note.value!=='keep my unsaved text'||input.files.length!==1)throw Error('collapse lost form data');
 const showAll=q('#work-show-all');if(!showAll||!showAll.hidden)throw Error('overview reset button missing or initially visible');
 showAll.hidden=false;const resetBox=showAll.getBoundingClientRect(),mapBox=q('#work-map').getBoundingClientRect();
 if(resetBox.width<44||resetBox.height<44||resetBox.left<mapBox.left||resetBox.right>mapBox.right||resetBox.top<mapBox.top)throw Error('reset button bounds');
 window.clearVisitStoreSelection();
 if(rows.some(row=>row.classList.contains('is-selected')||row.getAttribute('aria-expanded')!=='false'))throw Error('reset retained list selection');
 if(note.value!=='keep my unsaved text'||input.files.length!==1)throw Error('reset lost form data');
 rows[0].click();showAll.hidden=true;

 const location=q('.visit-location-info').getBoundingClientRect(),summary=q('.visit-location-editor summary').getBoundingClientRect();
 if(Math.abs(location.right-summary.right)>3)throw Error('update location not right aligned');
 const button=form.querySelector('button[type=submit]'),label=button.textContent;
 form.addEventListener('submit',e=>e.preventDefault());
 form.dispatchEvent(new Event('submit',{cancelable:true,bubbles:true}));
 if(!button.disabled)throw Error('submit did not lock');
 window.dispatchEvent(new PageTransitionEvent('pageshow',{persisted:true}));
 if(button.disabled||button.textContent!==label)throw Error('restored page left save button stuck');
 const nav=q('a[data-location-url]'),dialog=q('#location-check-dialog'),message=q('[data-location-message]');
 const opened=[];let blocked=false,resolveSave;
 window.open=url=>{if(blocked)return null;const tab={opener:window,closed:false,location:url,close(){this.closed=true;}};opened.push(tab);return tab;};
 window.fetch=()=>new Promise(resolve=>{resolveSave=resolve;});
 const action=value=>dialog.querySelector(`[data-location-action="${value}"]`).click();
 const openPrompt=()=>{nav.click();if(!dialog.open)throw Error('navigation did not show prompt');};
 openPrompt();if(opened.length!==1||!dialog.open)throw Error('map and prompt did not open together');
 const canceledTab=opened.length-1;action('cancel');if(dialog.open||!opened[canceledTab].closed)throw Error('cancel did not close map tab');
 openPrompt();const editTab=opened.length-1;action('edit');dialog.querySelector('[name=lat]').value='';
 action('save-coordinates');if(opened.length!==editTab+1||!message.textContent)throw Error('invalid coordinates changed map tab');
 action('cancel');
 const finish=async(ok=true)=>{resolveSave({ok,json:async()=>({ok,location_accuracy:'accurate',error:'save failed'})});for(let i=0;i<5;i++)await Promise.resolve();};
 for(const choice of ['accurate','doubtful','nonexistent']){
  const before=opened.length;openPrompt();if(opened.length!==before+1||!dialog.open)throw Error('map and prompt did not open together');action(choice);
  await finish();
  if(dialog.open||opened.length!==before+1||((choice==='nonexistent')!==opened[before].closed))throw Error('save/navigation sequence failed');
 }
 const beforeFailure=opened.length;openPrompt();action('accurate');await finish(false);
 if(!dialog.open||opened.length!==beforeFailure+1||!message.textContent)throw Error('failed save closed map/prompt');
 action('cancel');blocked=true;openPrompt();if(!dialog.open||opened.length!==beforeFailure+1||!message.textContent)throw Error('blocked popup prompt missing');
 action('accurate');await finish();
 if(!dialog.open||message.textContent!==message.dataset.popupBlocked)throw Error('blocked navigation lost retry prompt');
 action('skip');
 blocked=false;action('skip');
 if(dialog.open||opened.some(tab=>!tab.location.startsWith('https://www.google.com/maps/search/')))throw Error('navigation did not open map directly');
 const scroll=q('.visit-list .dispatch-scroll');scroll.scrollTop=80;
 requestAnimationFrame(()=>{try{
  const header=q('.visit-tbl thead th'),bounds=header.getBoundingClientRect(),box=scroll.getBoundingClientRect();
  if(Math.abs(bounds.top-box.top)>2)throw Error('header not sticky');
  const hit=document.elementFromPoint(bounds.left+Math.min(20,bounds.width/2),bounds.top+bounds.height/2);
  if(hit?.closest('th')!==header)throw Error('header covered by body row');
  if(scroll.scrollWidth>scroll.clientWidth+1)throw Error('horizontal overflow');
  releaseFocus();parent.postMessage({qa:true,width:innerWidth,persistentForm:true,cancelWithoutUpload:true,photoDrawer:true,
    searchFocus:true,imeSafe:true,explicitQuery:true,dropdownSubmit:true,backButtonReset:true,headerOnTop:true,locationRight:true,navigationOrder:true},'*');
 }catch(error){releaseFocus();parent.postMessage({qa:true,width:innerWidth,error:String(error)},'*');}});
}catch(error){releaseFocus();parent.postMessage({qa:true,width:innerWidth,error:String(error)},'*');}});
'''
    harness=harness.replace('FILTER_SCRIPT',json.dumps(Path('app/static/bd_filters.js').read_text()))
    scripts='<script>window.focusVisitStore=id=>{window.lastFocused=id};</script>'
    scripts+=''.join('<script>'+Path(p).read_text()+'</script>' for p in (
        'app/static/bd_visit_editor.js','app/static/bd_visit.js','app/static/bd_filters.js'))
    fixture=page.replace('</body>',scripts+'<script>'+harness+'</script></body>')
    parent='<meta charset="utf-8"><pre id="qa">waiting</pre><script>let turn=Promise.resolve();window.claimVisitQa=()=>{let unlock;const next=new Promise(resolve=>unlock=resolve),ready=turn;turn=next;return ready.then(()=>unlock);};let r=[];addEventListener("message",e=>{if(e.data.qa){r.push(e.data);document.getElementById("qa").textContent=JSON.stringify(r)}})</script>'
    for w,h in [(393,852),(1100,900)]:parent+=f'<iframe style="width:{w}px;height:{h}px;border:0" srcdoc="{html.escape(fixture,quote=True)}"></iframe>'
    file=tmp_path/'visit.html';file.write_text(parent)
    result=subprocess.run([str(chrome),'--headless','--disable-gpu','--no-first-run','--disable-background-networking',
        f'--user-data-dir={tmp_path}/chrome','--dump-dom','--virtual-time-budget=4000',file.as_uri()],capture_output=True,text=True,timeout=35)
    match=re.search(r'<pre id="qa">(.*?)</pre>',result.stdout,re.S)
    assert match,result.stderr[-400:]
    data=json.loads(html.unescape(match.group(1)))
    assert len(data)==2 and not any('error' in item for item in data),data
