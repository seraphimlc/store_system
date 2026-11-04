/* 巡店页：点击行展开详情可编辑；地图点商户也能定位到行。 */
(() => {
  'use strict';
  const list = document.querySelector('.visit-list');
  if (!list) return;

  /* ── 行 ↔ 详情 ─────────────────────────────────────────── */
  const rows = [...list.querySelectorAll('tr.visit-row')];
  const detailOf = id => list.querySelector(`tr.visit-detail-row[data-detail-for="${CSS.escape(String(id))}"]`);

  function reveal(row, open) {
    rows.forEach(candidate => {
      const expanded = candidate === row && open;
      candidate.setAttribute('aria-expanded', String(expanded));
      const detail = detailOf(candidate.dataset.storeId);
      if (detail) detail.hidden = !expanded;
    });
  }

  function toggle(row) {
    reveal(row, row.getAttribute('aria-expanded') !== 'true');
  }

  rows.forEach(row => {
    row.addEventListener('click', event => {
      if (event.target.closest('a')) return;
      window.focusVisitStore?.(row.dataset.storeId);
      rows.forEach(r=>r.classList.toggle('is-selected',r===row));
      if(event.target.closest('[data-edit-store]'))toggle(row);
    });
    row.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      if (event.target.closest('a')) return;
      event.preventDefault();
      window.focusVisitStore?.(row.dataset.storeId);
      if(event.target.closest('[data-edit-store]'))toggle(row);
    });
  });

  window.openVisitStore = id => {
    const row = rows.find(candidate => candidate.dataset.storeId === String(id));
    if (!row) { const url=new URL(location.href);url.search='';url.searchParams.set('focus',id);location.assign(url);return; }
    rows.forEach(r=>r.classList.toggle('is-selected',r===row));
    window.focusVisitStore?.(id);
    row.scrollIntoView({block:'nearest', behavior:'auto'});
    row.focus({preventScroll:true});
  };
  const focused=new URL(location.href).searchParams.get('focus');
  if(focused)window.openVisitStore(focused);
  const details=document.getElementById('new-store');
  if(location.hash==='#new-store'&&details)details.open=true;
  document.getElementById('locate-new-store')?.addEventListener('click',()=>{
    const message=document.getElementById('new-location-status');
    const failure=()=>message.textContent=message.dataset.error;
    if(!navigator.geolocation){failure();return;}
    navigator.geolocation.getCurrentPosition(p=>{document.querySelector('#new-store [name="lat"]').value=p.coords.latitude;document.querySelector('#new-store [name="lon"]').value=p.coords.longitude;message.textContent=message.dataset.ready;window.showNewStoreLocation?.(p.coords.latitude,p.coords.longitude);},failure,{enableHighAccuracy:true,timeout:15000});
  });
  let previewUrl;
  document.querySelector('#new-store [name="photo"]')?.addEventListener('change',e=>{
    if(previewUrl)URL.revokeObjectURL(previewUrl);
    const preview=document.getElementById('new-store-photo-preview'),file=e.target.files[0];
    preview.hidden=!file;if(file){previewUrl=URL.createObjectURL(file);preview.src=previewUrl;}
  });
})();
