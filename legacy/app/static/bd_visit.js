/* 巡店页：点击行展开详情可编辑；地图点商户也能定位到行。 */
(() => {
  'use strict';
  const list = document.querySelector('.visit-list');
  if (!list) return;

  const fullscreenButton = document.getElementById('work-fullscreen');
  const mapStage = document.querySelector('.dispatch-map-stage');
  if (fullscreenButton && mapStage) {
    const syncFullscreen = () => {
      const expanded = document.fullscreenElement === mapStage || mapStage.classList.contains('is-expanded');
      fullscreenButton.setAttribute('aria-pressed', String(expanded));
      fullscreenButton.title = expanded ? fullscreenButton.dataset.exitLabel : fullscreenButton.dataset.enterLabel;
      fullscreenButton.setAttribute('aria-label', fullscreenButton.title);
    };
    const closeFullscreen = async () => {
      if (document.fullscreenElement === mapStage) await document.exitFullscreen();
      mapStage.classList.remove('is-expanded');
      syncFullscreen();
    };
    fullscreenButton.addEventListener('click', async () => {
      if (document.fullscreenElement === mapStage || mapStage.classList.contains('is-expanded')) {
        await closeFullscreen();
      } else {
        try {
          if (!mapStage.requestFullscreen) throw new Error('fullscreen unavailable');
          await mapStage.requestFullscreen();
        } catch (_) {
          mapStage.classList.add('is-expanded');
        }
        syncFullscreen();
      }
    });
    document.addEventListener('fullscreenchange', syncFullscreen);
    document.addEventListener('keydown', event => {
      if (event.key === 'Escape' && mapStage.classList.contains('is-expanded')) closeFullscreen();
    });
  }

  const locationDialog = document.getElementById('location-check-dialog');
  const locationEditor = locationDialog?.querySelector('[data-location-editor]');
  let activeLocationLink = null;
  let activeMapTab = null;
  const accuracyLabels = {
    accurate: locationDialog?.dataset.accurate,
    nonexistent: locationDialog?.dataset.nonexistent,
    doubtful: locationDialog?.dataset.doubtful,
    pending: locationDialog?.dataset.pending,
  };

  function openMap(url) {
    const tab = window.open(url, '_blank');
    if (!tab) return false;
    tab.opener = null;
    return true;
  }

  async function saveLocation(action) {
    if (!activeLocationLink) return;
    const message = locationDialog.querySelector('[data-location-message]');
    const openAfter = action !== 'nonexistent';
    const params = new URLSearchParams({
      location_accuracy: action,
      csrf_token: locationDialog.dataset.csrf,
      _ft: locationDialog.dataset.formToken,
    });
    if (action === 'coordinates') {
      const lat = locationEditor.querySelector('[name="lat"]').value.trim();
      const lon = locationEditor.querySelector('[name="lon"]').value.trim();
      if (!lat || !lon) {
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
      if (action === 'nonexistent') {
        activeMapTab?.close();
        activeMapTab = null;
      } else if (activeMapTab && !activeMapTab.closed) {
        activeMapTab.location = activeLocationLink.dataset.locationUrl;
      } else if (openAfter && !openMap(activeLocationLink.dataset.locationUrl)) {
        message.textContent = message.dataset.popupBlocked;
        return;
      }
      locationDialog.close();
      activeMapTab = null;
      if (action === 'coordinates') window.setTimeout(() => window.location.reload(), 100);
    } catch (error) {
      message.textContent = error.message || message.dataset.saveFailed;
    }
  }

  if (locationDialog) {
    list.addEventListener('click', event => {
      const link = event.target.closest('a[data-location-url]');
      if (!link) return;
      event.preventDefault();
      activeLocationLink = link;
      activeMapTab?.close();
      activeMapTab = null;
      locationDialog.querySelector('[data-location-name]').textContent = link.closest('tr').querySelector('.visit-name').textContent.trim();
      locationDialog.querySelector('[data-location-message]').textContent = '';
      const row = link.closest('tr');
      const mapConfig = JSON.parse(document.getElementById('work-map-config')?.textContent || '{}');
      const point = (mapConfig.stores || []).find(item => String(item.id) === row.dataset.storeId);
      locationEditor.querySelector('[name="lat"]').value = point?.lat ?? '';
      locationEditor.querySelector('[name="lon"]').value = point?.lon ?? '';
      locationEditor.hidden = true;
      locationDialog.showModal();
      activeMapTab = window.open(link.dataset.locationUrl, '_blank');
      if (activeMapTab) activeMapTab.opener = null;
      else locationDialog.querySelector('[data-location-message]').textContent =
        locationDialog.querySelector('[data-location-message]').dataset.popupBlocked;
    });
    locationDialog.addEventListener('click', event => {
      const button = event.target.closest('[data-location-action]');
      if (!button) return;
      const action = button.dataset.locationAction;
      if (action === 'cancel') { activeMapTab?.close(); activeMapTab = null; locationDialog.close(); return; }
      if (action === 'edit') { locationEditor.hidden = false; locationEditor.querySelector('[name="lat"]').focus(); return; }
      if (action === 'skip') {
        if ((!activeMapTab || activeMapTab.closed) && activeLocationLink && !openMap(activeLocationLink.dataset.locationUrl)) {
          locationDialog.querySelector('[data-location-message]').textContent =
            locationDialog.querySelector('[data-location-message]').dataset.popupBlocked;
          return;
        }
        locationDialog.close();activeMapTab = null;return;
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

  window.clearVisitStoreSelection=()=>{
    rows.forEach(row=>row.classList.remove('is-selected'));
    reveal(null,false);
  };

  async function copyStoreName(button) {
    const text = button.dataset.copyText || '';
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(text);
      } else {
        const input = document.createElement('textarea');
        input.value = text;
        input.setAttribute('readonly', '');
        input.style.position = 'fixed';
        input.style.opacity = '0';
        document.body.append(input);
        input.select();
        if (!document.execCommand('copy')) throw new Error('copy failed');
        input.remove();
      }
      button.setAttribute('aria-label', button.dataset.copiedLabel);
      button.title = button.dataset.copiedLabel;
      button.classList.add('is-copied');
      window.setTimeout(() => {
        button.setAttribute('aria-label', button.dataset.copyLabel);
        button.title = button.dataset.copyLabel;
        button.classList.remove('is-copied');
      }, 1200);
    } catch (_error) {
      button.setAttribute('aria-label', button.dataset.copyFailedLabel);
      button.title = button.dataset.copyFailedLabel;
      window.setTimeout(() => {
        button.setAttribute('aria-label', button.dataset.copyLabel);
        button.title = button.dataset.copyLabel;
      }, 1200);
    }
  }

  list.querySelectorAll('[data-copy-store]').forEach(button => {
    button.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      copyStoreName(button);
    });
    button.addEventListener('keydown', event => {
      if (event.key === 'Enter' || event.key === ' ') event.stopPropagation();
    });
  });

  function toggleRow(row) {
    const open = row.getAttribute('aria-expanded') !== 'true';
    if (open) window.focusVisitStore?.(row.dataset.storeId);
    rows.forEach(candidate => candidate.classList.toggle('is-selected', candidate === row && open));
    reveal(row, open);
  }

  rows.forEach(row => {
    row.addEventListener('click', event => {
      if (event.target.closest('a')) return;
      toggleRow(row);
    });
    row.addEventListener('keydown', event => {
      if (event.key !== 'Enter' && event.key !== ' ') return;
      if (event.target.closest('a')) return;
      event.preventDefault();
      toggleRow(row);
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
