/* 巡店页：点击行展开详情可编辑；地图点商户也能定位到行。 */
(() => {
  'use strict';
  const list = document.querySelector('.visit-list');
  if (!list) return;

  const locationDialog = document.getElementById('location-check-dialog');
  const locationEditor = locationDialog?.querySelector('[data-location-editor]');
  let activeLocationLink = null;
  const accuracyLabels = {
    accurate: locationDialog?.dataset.accurate,
    nonexistent: locationDialog?.dataset.nonexistent,
    doubtful: locationDialog?.dataset.doubtful,
    pending: locationDialog?.dataset.pending,
  };

  function openMap(url) {
    const tab = window.open('about:blank', '_blank');
    if (!tab) return false;
    tab.opener = null;
    tab.location.replace(url);
    return true;
  }

  async function saveLocation(action) {
    if (!activeLocationLink) return;
    const message = locationDialog.querySelector('[data-location-message]');
    const openAfter = action !== 'nonexistent';
    const tab = openAfter ? window.open('about:blank', '_blank') : null;
    if (openAfter && !tab) {
      message.textContent = message.dataset.popupBlocked;
      return;
    }
    if (tab) tab.opener = null;
    const params = new URLSearchParams({
      location_accuracy: action,
      csrf_token: locationDialog.dataset.csrf,
      _ft: locationDialog.dataset.formToken,
    });
    if (action === 'coordinates') {
      const lat = locationEditor.querySelector('[name="lat"]').value.trim();
      const lon = locationEditor.querySelector('[name="lon"]').value.trim();
      if (!lat || !lon) {
        if (tab) tab.close();
        message.textContent = message.dataset.coordinatesRequired;
        return;
      }
      params.set('location_accuracy', 'accurate');
      params.set('lat', lat);
      params.set('lon', lon);
    }
    const url = `${locationDialog.dataset.saveUrl}${activeLocationLink.dataset.storeTaskId}/location`;
    try {
      const response = await fetch(url, {method:'POST',headers:{'Content-Type':'application/x-www-form-urlencoded'},body:params});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || message.dataset.saveFailed);
      const row = activeLocationLink.closest('tr.visit-row');
      const status = row?.querySelector('[data-location-status]');
      if (status) {
        status.textContent = accuracyLabels[data.location_accuracy] || accuracyLabels.pending;
        status.className = `visit-location-status location-${data.location_accuracy}`;
      }
      if (action === 'coordinates') {
        const url = new URL('https://www.google.com/maps/search/');
        url.searchParams.set('api', '1');
        url.searchParams.set('query', `${data.lat},${data.lon}`);
        activeLocationLink.dataset.locationUrl = url.toString();
        activeLocationLink.href = url.toString();
      }
      locationDialog.close();
      if (tab) tab.location.replace(activeLocationLink.dataset.locationUrl);
      if (action === 'coordinates') window.setTimeout(() => window.location.reload(), 100);
    } catch (error) {
      if (tab) tab.close();
      message.textContent = error.message || message.dataset.saveFailed;
    }
  }

  if (locationDialog) {
    list.addEventListener('click', event => {
      const link = event.target.closest('a[data-location-url]');
      if (!link) return;
      event.preventDefault();
      activeLocationLink = link;
      locationDialog.querySelector('[data-location-name]').textContent = link.closest('tr').querySelector('.visit-name').textContent.trim();
      locationDialog.querySelector('[data-location-message]').textContent = '';
      const row = link.closest('tr');
      const mapConfig = JSON.parse(document.getElementById('work-map-config')?.textContent || '{}');
      const point = (mapConfig.stores || []).find(item => String(item.id) === row.dataset.storeId);
      locationEditor.querySelector('[name="lat"]').value = point?.lat ?? '';
      locationEditor.querySelector('[name="lon"]').value = point?.lon ?? '';
      locationEditor.hidden = true;
      locationDialog.showModal();
    });
    locationDialog.addEventListener('click', event => {
      const button = event.target.closest('[data-location-action]');
      if (!button) return;
      const action = button.dataset.locationAction;
      if (action === 'cancel') { locationDialog.close(); return; }
      if (action === 'edit') { locationEditor.hidden = false; locationEditor.querySelector('[name="lat"]').focus(); return; }
      if (action === 'skip') {
        if (activeLocationLink && !openMap(activeLocationLink.dataset.locationUrl)) {
          locationDialog.querySelector('[data-location-message]').textContent =
            locationDialog.querySelector('[data-location-message]').dataset.popupBlocked;
          return;
        }
        locationDialog.close();return;
      }
      saveLocation(action === 'save-coordinates' ? 'coordinates' : action);
    });
  }

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

  rows.forEach(row => {
    row.addEventListener('click', event => {
      if (event.target.closest('a')) return;
      window.focusVisitStore?.(row.dataset.storeId);
      rows.forEach(r=>r.classList.toggle('is-selected',r===row));
      reveal(row, true);
    });
    row.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      if (event.target.closest('a')) return;
      event.preventDefault();
      rows.forEach(r=>r.classList.toggle('is-selected',r===row));
      window.focusVisitStore?.(row.dataset.storeId);
      reveal(row, true);
    });
  });

  window.openVisitStore = id => {
    const row = rows.find(candidate => candidate.dataset.storeId === String(id));
    if (!row) { const url=new URL(location.href);url.search='';url.searchParams.set('focus',id);location.assign(url);return; }
    rows.forEach(r=>r.classList.toggle('is-selected',r===row));
    reveal(row, true);
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
