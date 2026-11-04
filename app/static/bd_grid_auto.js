/* 自动分区先预览；参数、商户快照或网格分组变化后必须重新预览。 */
(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const root = byId('grid-region-editor');
  if (!root) return;
  const config = JSON.parse(byId('grid-region-config').textContent);
  const tabs = {manual:byId('grid-manual-tab'), auto:byId('grid-auto-tab')};
  const panes = {manual:byId('grid-manual-pane'), auto:byId('grid-auto-pane')};
  const basis = byId('grid-auto-basis'), target = byId('grid-auto-target');
  const unit = byId('grid-auto-unit'), area = byId('grid-auto-area');
  const scope = byId('grid-auto-scope'), tolerance = byId('grid-auto-tolerance');
  const maxGrids = byId('grid-auto-max-grids'), maxStores = byId('grid-auto-max-stores');
  const filter = byId('grid-auto-filter'), minStores = byId('grid-auto-min-stores');
  const previewButton = byId('grid-auto-preview'), applyButton = byId('grid-auto-apply');
  const result = byId('grid-auto-result');
  const csrf = config.csrf;
  let signature = '', requestNumber = 0, controller;
  let lastParams;

  function clearPreview() {
    requestNumber++;
    controller?.abort();
    signature = '';
    applyButton.hidden = true;
    applyButton.disabled = true;
    result.hidden = true;
    result.replaceChildren();
    window.clearAutomaticGridPreview?.();
  }

  function activate(name) {
    root.hidden = false;
    for (const key of Object.keys(tabs)) {
      tabs[key].setAttribute('aria-selected', String(key === name));
      panes[key].hidden = key !== name;
    }
    if (name === 'manual') clearPreview();
  }

  function params() {
    return {
      csrf_token: csrf,
      region: scope.value === 'all' ? 'all' : config.region,
      target_basis: basis.value,
      target_per_zone: Number(target.value),
      max_area_km2: Number(area.value),
      min_stores: filter.checked ? Number(minStores.value) : 0,
      tolerance_pct: Number(tolerance.value),
      max_grids_per_zone: maxGrids.value ? Number(maxGrids.value) : null,
      max_stores_per_zone: maxStores.value ? Number(maxStores.value) : null
    };
  }

  function showReport(report) {
    const text = config.texts;
    const summary = document.createElement('p');
    summary.textContent = `${report.zone_count} ${text.zoneCount} · ${report.grids_assigned} ${text.gridCount} · ${report.density.disabled_count} ${text.excludedCount}`;
    result.append(summary);
    const list = document.createElement('ul');
    report.zones.forEach((zone, index) => {
      const item = document.createElement('li');
      item.textContent = `${index + 1}. ${zone.grid_count} ${text.gridCount} · ${zone.store_count.toLocaleString()} ${text.storeCount} · ${zone.area_km2.toFixed(2)} km²`;
      list.append(item);
    });
    result.append(list);
    if (report.unassigned_grid_ids?.length) {
      const unassigned = document.createElement('p');
      unassigned.textContent = `${text.unassignedCount}: ${report.unassigned_grid_ids.map(id => `#${id}`).join(', ')}`;
      result.append(unassigned);
    }
    if (report.out_of_tolerance) {
      const warning = document.createElement('p');
      warning.textContent = text.outOfTolerance;
      result.append(warning);
    }
    if (report.density.disabled_grid_ids.length) {
      const excluded = document.createElement('p');
      excluded.textContent = `${text.excludedCount}: ${report.density.disabled_grid_ids.map(id => `#${id}`).join(', ')}`;
      result.append(excluded);
    }
    applyButton.disabled = report.zone_count === 0;
    const note = document.createElement('p');
    note.textContent = text.previewReady;
    result.append(note);
    result.hidden = false;
  }

  tabs.manual.addEventListener('click', () => activate('manual'));
  tabs.auto.addEventListener('click', () => activate('auto'));
  byId('grid-auto-open').addEventListener('click', () => activate('auto'));
  basis.addEventListener('change', () => {
    target.value = basis.value === 'grids' ? '8' : '8000';
    unit.textContent = basis.value === 'grids' ? config.texts.gridCount : config.texts.storeCount;
    clearPreview();
  });
  filter.addEventListener('change', () => {
    minStores.disabled = !filter.checked;
    clearPreview();
  });
  minStores.disabled = !filter.checked;
  for (const field of [target, area, minStores, tolerance, maxGrids, maxStores]) field.addEventListener('input', clearPreview);
  scope.addEventListener('change', clearPreview);

  previewButton.addEventListener('click', async () => {
    clearPreview();
    if (!target.reportValidity() || !area.reportValidity() || (filter.checked && !minStores.reportValidity())) return;
    const body = params(), bodyKey = JSON.stringify(body), current = ++requestNumber;
    controller?.abort();
    controller = new AbortController();
    previewButton.disabled = true;
    result.textContent = config.texts.previewLoading;
    result.hidden = false;
    try {
      const response = await fetch('/zones/grid-auto-preview', {method:'POST',signal:controller.signal,
        headers:{'Content-Type':'application/json'},body:bodyKey});
      const data = await response.json();
      if (current !== requestNumber || bodyKey !== JSON.stringify(params())) return;
      if (!response.ok) throw new Error(data.error || config.texts.error);
      signature = data.signature;
      lastParams = body;
      result.replaceChildren();
      showReport(data);
      window.showAutomaticGridPreview?.(data);
      applyButton.hidden = false;
    } catch (error) {
      if (error.name !== 'AbortError' && current === requestNumber) {
        result.textContent = error.message || config.texts.error;
        result.hidden = false;
      }
    } finally {
      if (current === requestNumber) previewButton.disabled = false;
    }
  });

  applyButton.addEventListener('click', async () => {
    if (!signature || !lastParams || !window.confirm(config.texts.applyConfirm)) return;
    const expected = signature;
    applyButton.disabled = true;
    previewButton.disabled = true;
    try {
      const response = await fetch('/zones/grid-auto-apply', {method:'POST',
        headers:{'Content-Type':'application/json'},
        body:JSON.stringify({...lastParams,signature:expected})});
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || config.texts.error);
      result.textContent = config.texts.applyDone;
      window.location.reload();
    } catch (error) {
      clearPreview();
      result.textContent = error.message || config.texts.error;
      result.hidden = false;
    } finally {
      applyButton.disabled = false;
      previewButton.disabled = false;
    }
  });

  window.gridRegionAutoTab = activate;
})();
