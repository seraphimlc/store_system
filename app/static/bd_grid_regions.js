/* 选择与边界模式变化后自动计算；仅最终保存改变数据库。 */
(() => {
  'use strict';
  const byElementId = id => document.getElementById(id);
  const root = byElementId('grid-region-editor');
  if (!root) return;
  const config = JSON.parse(byElementId('grid-region-config').textContent);
  const form = byElementId('grid-region-form');
  const boxes = [...form.querySelectorAll('[data-grid-id]')];
  const gridMetadata = new Map(config.grids.map(grid => [Number(grid.id), grid]));
  const byId = new Map(boxes.map(box => [Number(box.dataset.gridId), box]));
  const status = byElementId('grid-region-status'), save = byElementId('grid-region-save');
  const mode = byElementId('grid-region-merge');
  const conversion = byElementId('grid-conversion-panel');
  const conversionForm = conversion?.querySelector('form');
  const conversionButton = conversionForm?.querySelector('[type="submit"]');
  conversionForm?.addEventListener('submit', async event => {
    event.preventDefault();
    if (conversionButton.disabled) return;
    conversionButton.disabled = true;
    try {
      const response = await fetch(conversionForm.action, {
        method: 'POST', body: new FormData(conversionForm),
        headers: {Accept: 'application/json'}
      });
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || config.texts.error);
      const gridId = Number(result.id), grid = gridMetadata.get(gridId), box = byId.get(gridId);
      if (grid) { grid.disabled = false; grid.convertible = false; grid.revision = result.revision; }
      if (box) {
        box.disabled = false;
        box.value = JSON.stringify({...JSON.parse(box.value), revision: result.revision});
        box.parentElement.hidden = false;
      }
      layer?.setStyle(style);
      conversion.hidden = true;
      root.hidden = false;
      byElementId('grid-region-status').textContent = config.texts.converted;
    } catch (error) {
      byElementId('grid-region-status').textContent = error.message || config.texts.error;
    } finally {
      conversionButton.disabled = false;
    }
  });
  byElementId('grid-conversion-close')?.addEventListener('click', () => {
    conversion.hidden = true;
    root.hidden = selection().length === 0;
    previewLayer?.setMap(!root.hidden ? map : null);
    window.setGridRegionPreviewZones?.(!root.hidden ? selection().map(grid => grid.zone_id).filter(id => id !== null) : []);
  });
  byElementId('grid-auto-open')?.addEventListener('click', () => { if (conversion) conversion.hidden = true; });
  let map, layer, previewLayer, controller, timer, requestNumber = 0, calculated = '';
  let saving = false;
  let automaticPreviewLayer, gridInfoOverlay;
  const selection = () => boxes.filter(box => box.checked && !box.disabled).map(box => JSON.parse(box.value));
  const key = () => JSON.stringify({selections: selection(), merge_boundaries: mode.checked});
  function setSelection(gridId, checked) {
    const grid = gridMetadata.get(gridId);
    const zoneId = grid?.zone_id;
    const related = zoneId === null || zoneId === undefined
      ? [byId.get(gridId)]
      : boxes.filter(box => String(gridMetadata.get(Number(box.dataset.gridId))?.zone_id) === String(zoneId));
    related.filter(box => box && !box.disabled).forEach(box => { box.checked = checked; });
  }
  function style(feature) {
    const box = byId.get(Number(feature.getProperty('id')));
    const grid = gridMetadata.get(Number(feature.getProperty('id')));
    if (grid?.disabled) {
      return {fillColor: '#9ca3af', fillOpacity: .45, strokeColor: '#6b7280',
        strokeWeight: 1, strokeOpacity: 1,
        clickable: !!grid.convertible || grid.zone_id !== null && grid.zone_id !== undefined, zIndex: 5};
    }
    const assigned = grid?.zone_id !== null && grid?.zone_id !== undefined;
    const selected = !!box?.checked && !box.disabled;
    const dissolved = selected && mode.checked && !!calculated;
    return {fillColor: assigned && !selected ? '#2459d3' : '#176b57',
      fillOpacity: assigned && !selected ? .28 : dissolved ? 0 : selected ? .48 : .08,
      strokeColor: selected ? '#094b3b' : assigned ? '#2459d3' : '#176b57',
      strokeWeight: dissolved ? 0 : selected ? 3 : 1,
      strokeOpacity: dissolved ? 0 : 1,
      clickable: !grid?.disabled && (assigned || !box?.disabled), zIndex: selected ? 8 : assigned ? 7 : 5};
  }
  function createGridInfoOverlay() {
    const OverlayView = google.maps.OverlayView;
    return new class extends OverlayView {
      constructor() {
        super();
        this.element = document.createElement('section');
        this.element.className = 'grid-map-info';
        this.element.hidden = true;
        this.element.setAttribute('role', 'group');
        this.element.innerHTML = '<div class="grid-map-info-copy"><span></span><strong></strong></div><button type="button"></button>';
        [this.zoneName, this.gridLabel] = this.element.querySelectorAll('span, strong');
        this.closeButton = this.element.querySelector('button');
        this.closeButton.textContent = '×';
        this.closeButton.setAttribute('aria-label', config.texts.close);
        this.closeButton.title = config.texts.close;
        this.closeButton.addEventListener('click', event => {
          event.stopPropagation();
          this.close();
        });
        this.element.addEventListener('click', event => event.stopPropagation());
        this.setMap(map);
      }

      onAdd() {
        this.getPanes().overlayMouseTarget.appendChild(this.element);
      }

      draw() {
        if (!this.position || this.element.hidden) return;
        const point = this.getProjection().fromLatLngToDivPixel(this.position);
        const mapWidth = this.getMap().getDiv().clientWidth;
        const halfWidth = this.element.offsetWidth / 2;
        const x = Math.max(halfWidth + 12, Math.min(mapWidth - halfWidth - 12, point.x));
        this.element.style.left = `${x}px`;
        this.element.style.top = `${point.y}px`;
        this.element.dataset.placement = point.y < this.element.offsetHeight + 24 ? 'below' : 'above';
      }

      show(grid, gridId, position) {
        this.zoneName.textContent = grid.zone_name;
        this.gridLabel.textContent = `${grid.name || '网格'} #${gridId}`;
        this.splitForm?.remove();
        const source = byElementId('grid-region-zone-actions')?.querySelector(`[data-split-zone="${grid.zone_id}"]`);
        if (source) {
          this.splitForm = source.cloneNode(true);
          this.splitForm.hidden = false;
          this.splitForm.querySelector('span')?.remove();
          this.element.appendChild(this.splitForm);
        }
        this.element.setAttribute('aria-label', `${grid.zone_name}，${this.gridLabel.textContent}`);
        this.position = position;
        this.element.hidden = false;
        this.draw();
      }

      close() {
        this.element.hidden = true;
      }

      onRemove() {
        this.element.remove();
      }
    }();
  }
  window.showAutomaticGridPreview = report => {
    automaticPreviewLayer?.setMap(null);
    if (!map) return false;
    automaticPreviewLayer = new google.maps.Data({map});
    const excluded = new Set(report.density.disabled_grid_ids || []);
    const features = report.zones.map((zone, index) => ({type:'Feature',geometry:zone.geometry,
      properties:{kind:'zone',index}}));
    for (const grid of config.grids) {
      if (excluded.has(Number(grid.id))) features.push({type:'Feature',geometry:grid.geometry,
        properties:{kind:'excluded'}});
    }
    automaticPreviewLayer.addGeoJson({type:'FeatureCollection',features});
    automaticPreviewLayer.setStyle(feature => feature.getProperty('kind') === 'excluded'
      ? {fillColor:'#9ca3af',fillOpacity:.5,strokeColor:'#6b7280',strokeWeight:1.5,clickable:false,zIndex:7}
      : {fillColor:['#176b57','#2459d3','#a45a20','#8b4c78','#487c36'][Number(feature.getProperty('index'))%5],
        fillOpacity:.12,strokeColor:'#173d33',strokeWeight:2.5,clickable:false,zIndex:6});
    const previewBounds = new google.maps.LatLngBounds();
    automaticPreviewLayer.forEach(feature => feature.getGeometry().forEachLatLng(point => previewBounds.extend(point)));
    if (!previewBounds.isEmpty()) map.fitBounds(previewBounds, 40);
    return true;
  };
  window.clearAutomaticGridPreview = () => {
    automaticPreviewLayer?.setMap(null);
    automaticPreviewLayer = null;
  };
  function upsertSplitForm(zone) {
    const host = byElementId('grid-region-zone-actions');
    if (!host || !zone) return;
    let action = host.querySelector(`[data-split-zone="${zone.id}"]`);
    if (!action) {
      action = document.createElement('form');
      action.method = 'post';
      action.action = '/zones/split';
      action.hidden = true;
      action.dataset.splitZone = String(zone.id);
      for (const [name, value] of [
        ['_ft', form.elements._ft?.value || ''],
        ['csrf_token', form.elements.csrf_token.value],
        ['region', form.elements.region.value],
        ['zone_id', String(zone.id)],
        ['revision', String(zone.revision)],
      ]) {
        const input = document.createElement('input');
        input.type = 'hidden';
        input.name = name;
        input.value = value;
        action.appendChild(input);
      }
      const label = document.createElement('span');
      const button = document.createElement('button');
      button.type = 'submit';
      button.className = 'btn ghost';
      button.textContent = config.texts.split;
      action.appendChild(label);
      action.appendChild(button);
      action._label = label;
      action._button = button;
      host.appendChild(action);
    }
    const revision = [...action.children].find(child => child.name === 'revision');
    if (revision) revision.value = String(zone.revision);
    const label = action._label || action.querySelector('span');
    if (label) label.textContent = zone.name;
    const button = action._button || action.querySelector('button');
    if (button) button.disabled = !!zone.split_locked;
  }
  function applyComposition(patch) {
    const host = byElementId('grid-region-zone-actions');
    for (const id of patch.archived_zone_ids || []) host?.querySelector(`[data-split-zone="${id}"]`)?.remove();
    for (const zone of [...(patch.zones || []), patch.zone].filter(Boolean)) upsertSplitForm(zone);
    for (const row of patch.grids || []) {
      const grid = gridMetadata.get(Number(row.id));
      if (grid) {
        grid.revision = row.revision;
        grid.zone_id = row.zone_id;
        grid.zone_revision = row.zone_revision;
        grid.zone_name = row.zone_name || '';
      }
      const box = byId.get(Number(row.id));
      if (!box) continue;
      box.value = JSON.stringify({id: Number(row.id), revision: row.revision,
        zone_id: row.zone_id, zone_revision: row.zone_revision});
      const title = box.parentElement?.querySelector?.('.grid-row-title');
      if (!title) continue;
      let tag = title.querySelector('.grid-tag-zone');
      if (row.zone_name) {
        if (!tag) {
          tag = document.createElement('span');
          tag.className = 'grid-tag grid-tag-zone';
          title.appendChild(tag);
        }
        tag.textContent = row.zone_name;
      } else tag?.remove();
    }
    window.patchZoneFeatures?.(patch);
    const header = document.querySelector?.('.zone-workspace-header');
    if (!header) return;
    let banner = byElementId('grid-region-save-status');
    if (!banner) {
      banner = document.createElement('div');
      banner.id = 'grid-region-save-status';
      banner.className = 'msg ok';
      banner.setAttribute('role', 'status');
      header.insertAdjacentElement('afterend', banner);
    }
    banner.hidden = false;
    banner.textContent = config.texts.saved;
  }
  async function saveComposition() {
    saving = true;
    save.disabled = true;
    clearTimeout(timer); controller?.abort(); requestNumber++;
    status.textContent = config.texts.saving;
    const selectedKey = key();
    try {
      const response = await fetch(form.action, {
        method: 'POST', headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
        body: JSON.stringify({name: form.elements.name.value, selections: selection(),
          merge_boundaries: mode.checked, region: form.elements.region.value,
          csrf_token: form.elements.csrf_token.value})});
      if (response.redirected) throw new Error(config.texts.error);
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || config.texts.error);
      if (selectedKey !== key()) return;
      applyComposition(data);
      form.elements.name.value = '';
      boxes.forEach(box => { box.checked = false; });
      gridInfoOverlay?.close();
      saving = false;
      changed();
      status.textContent = config.texts.saved;
    } catch (error) {
      if (selectedKey === key()) status.textContent = error.message || config.texts.error;
      save.disabled = calculated !== key();
    } finally {
      saving = false;
    }
  }
  function changed() {
    if (saving) return;
    if (conversion) conversion.hidden = true;
    clearTimeout(timer); controller?.abort(); requestNumber++;
    calculated = ''; save.disabled = true;
    window.setGridRegionPreviewZones?.([]);
    previewLayer?.setMap(null); previewLayer = null;
    const chosen = selection();
    const count = chosen.length;
    const selectedZones = new Set(chosen.map(grid => String(grid.zone_id)));
    byElementId('grid-region-zone-actions')?.querySelectorAll('[data-split-zone]').forEach(action => {
      action.hidden = !selectedZones.has(action.dataset.splitZone);
    });
    root.hidden = count === 0;
    boxes.forEach(box => { box.parentElement.hidden = !box.checked; });
    byElementId('grid-region-count').textContent = String(count);
    byElementId('grid-region-area').textContent = chosen.reduce((sum, grid) => sum + Number(gridMetadata.get(grid.id)?.area || 0), 0).toFixed(2);
    byElementId('grid-region-stores').textContent = chosen.reduce((sum, grid) => sum + Number(gridMetadata.get(grid.id)?.stores || 0), 0).toLocaleString();
    status.textContent = count ? `${config.texts.selected}: ${count} · ${config.texts.loading}` : config.texts.empty;
    layer?.setStyle(style);
    if (count) timer = setTimeout(calculate, 200);
  }
  async function calculate() {
    if (!selection().length) return;
    controller = new AbortController();
    const current = ++requestNumber, selectedKey = key();
    try {
      const response = await fetch('/zones/grid-merge-preview', {
        method: 'POST', signal: controller.signal, headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({selections: selection(), merge_boundaries: mode.checked,
          csrf_token: form.elements.csrf_token.value})});
      if (response.redirected) throw new Error(config.texts.error);
      const data = await response.json();
      if (current !== requestNumber || selectedKey !== key()) return;
      if (!response.ok) throw new Error(data.error || config.texts.error);
      if (map) {
        previewLayer = new google.maps.Data({map: !root.hidden ? map : null});
        previewLayer.addGeoJson({type: 'FeatureCollection', features: [{...data,
          geometry: data.display_geometry || data.geometry}]});
        previewLayer.setStyle({fillColor: '#176b57', fillOpacity: .48, strokeColor: '#094b3b',
          strokeWeight: 3, clickable: false, zIndex: 6});
      }
      calculated = selectedKey; save.disabled = false;
      byElementId('grid-region-area').textContent = data.properties.area_km2.toFixed(2);
      byElementId('grid-region-stores').textContent = Number(data.properties.store_count).toLocaleString();
      layer?.setStyle(style);
      window.setGridRegionPreviewZones?.(!root.hidden ? selection().map(grid => grid.zone_id).filter(id => id !== null) : []);
      status.textContent = `${data.properties.grid_count} ${config.texts.selected} · ` +
        `${data.properties.area_km2.toFixed(2)} km² · ${data.properties.store_count} · ` +
        (mode.checked ? config.texts.merged : config.texts.separate) + ' · ' +
        (data.properties.shape_valid ? config.texts.ready : config.texts.shape) +
        (map ? '' : ` ${config.texts.mapMissing}`);
    } catch (error) {
      if (error.name !== 'AbortError' && current === requestNumber) status.textContent = error.message || config.texts.error;
    }
  }
  form.addEventListener('change', event => {
    if (saving) return;
    if (event.target.matches('[data-grid-id]')) {
      setSelection(Number(event.target.dataset.gridId), event.target.checked);
      changed();
    } else if (event.target === mode) changed();
  });
  byElementId('grid-region-clear').addEventListener('click', () => {
    boxes.forEach(box => { box.checked = false; }); changed();
  });
  window.initGridRegionEditor = targetMap => {
    if (layer) return;
    map = targetMap;
    layer = new google.maps.Data({map});
    layer.addGeoJson({type: 'FeatureCollection', features: config.grids.map(grid => ({
      type: 'Feature', geometry: grid.geometry, properties: {id: grid.id}}))});
    layer.setStyle(style);
    if (config.fit_grids) {
      const bounds = new google.maps.LatLngBounds();
      layer.forEach(feature => feature.getGeometry().forEachLatLng(point => bounds.extend(point)));
      if (!bounds.isEmpty()) map.fitBounds(bounds, 32);
    }
    layer.addListener('click', event => {
      if (saving) { event.stop?.(); return; }
      const gridId = Number(event.feature.getProperty('id'));
      const box = byId.get(gridId);
      const grid = gridMetadata.get(gridId);
      if (grid?.zone_id !== null && grid?.zone_id !== undefined && grid.zone_name && event.latLng) {
        gridInfoOverlay ||= createGridInfoOverlay();
        gridInfoOverlay.show(grid, gridId, event.latLng);
      } else {
        gridInfoOverlay?.close();
      }
      if (grid?.disabled && grid.convertible && conversion
          && (grid.zone_id === null || grid.zone_id === undefined)) {
        event.stop?.();
        gridInfoOverlay?.close();
        root.hidden = true;
        previewLayer?.setMap(null);
        window.setGridRegionPreviewZones?.([]);
        byElementId('grid-conversion-id').value = String(gridId);
        byElementId('grid-conversion-revision').value = String(grid.revision);
        byElementId('grid-conversion-name').textContent = grid.name;
        byElementId('grid-conversion-area').textContent = Number(grid.area).toFixed(2);
        byElementId('grid-conversion-stores').textContent = Number(grid.stores).toLocaleString();
        conversion.hidden = false;
        return;
      }
      if (!box) return;
      event.stop?.();
      if (box.disabled) return;
      setSelection(gridId, !box.checked); changed();
    });
    map.addListener('click', () => gridInfoOverlay?.close());
    map.addListener('idle', () => gridInfoOverlay?.draw());
  };
  if (window.zoneManagementMap) window.initGridRegionEditor(window.zoneManagementMap);
  form.addEventListener('submit', event => {
    event.preventDefault();
    if (saving || !calculated || calculated !== key()) { save.disabled = true; return; }
    return saveComposition();
  });
  window.addEventListener?.('pagehide', () => { clearTimeout(timer); controller?.abort(); });
})();
