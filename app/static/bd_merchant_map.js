/* Only viewport aggregates or bounded points; merchant details are fetched on click. */
(() => {
  'use strict';
  const root = document.getElementById('merchant-map-config');
  if (!root) return;
  const config = JSON.parse(root.textContent), text = config.texts;
  const byId = id => document.getElementById(id);
  const status = byId('merchant-map-status'), summary = byId('merchant-map-summary');
  const stage = document.querySelector('.merchant-map-stage');
  const retry = byId('merchant-map-retry'), panel = byId('merchant-map-detail');
  let map, points, overlays = [], timer, controller, detailController;
  let generation = 0, detailGeneration = 0;
  const format = value => Number(value).toLocaleString(config.language, {maximumFractionDigits:1});
  const initialBounds = {south:34.8, west:138.4, north:36.5, east:140.9};

  async function json(response) {
    if (response.redirected) throw new Error(text.error);
    let data;
    try { data = await response.json(); } catch { throw new Error(text.error); }
    if (!response.ok) throw new Error(text.error);
    return data;
  }
  function clearLayers() {
    overlays.forEach(overlay => overlay.setMap(null)); overlays = [];
    points?.forEach(feature => points.remove(feature));
  }
  function closeDetail() {
    detailGeneration++; detailController?.abort(); panel.hidden = true;
  }
  async function openDetail(id) {
    detailController?.abort(); detailController = new AbortController();
    const current = ++detailGeneration;
    panel.hidden = false;
    byId('merchant-map-detail-title').textContent = text.detail;
    const body = byId('merchant-map-detail-body');
    body.textContent = text.detailLoading;
    try {
      const data = await json(await fetch(`/merchant-map/detail?${new URLSearchParams({identity:id})}`, {signal:detailController.signal}));
      if (current !== detailGeneration) return;
      byId('merchant-map-detail-title').textContent = data.name;
      const badge = document.createElement('p'); badge.textContent = data.covered ? text.yes : text.no;
      const facts = document.createElement('dl');
      for (const [label,value] of [[text.address,data.address],[text.region,`${data.prefecture || ''} ${data.municipality || ''}`],
        [text.phone,data.phone],[text.website,data.website],[text.source,data.sources],[text.id,data.place_id],
        [text.coordinates,`${Number(data.lat).toFixed(6)}, ${Number(data.lon).toFixed(6)}`],
        [text.location,text[data.location_accuracy] || text.pending],
        [text.lastVisit,data.last_visited_at ? new Date(data.last_visited_at).toLocaleString(config.language) : text.notVisited]]) {
        const item = document.createElement('div'), title = document.createElement('dt'), content = document.createElement('dd');
        title.textContent = label; content.textContent = value || '—'; item.append(title,content); facts.append(item);
      }
      body.replaceChildren(badge,facts);
    } catch (error) {
      if (current === detailGeneration && error.name !== 'AbortError') body.textContent = text.detailError;
    }
  }
  async function load() {
    if (!map?.getBounds()) return;
    controller?.abort(); controller = new AbortController();
    const current = ++generation, bounds = map.getBounds().toJSON();
    const query = new URLSearchParams({west:bounds.west,east:bounds.east,
      south:Math.max(-85.05112878,bounds.south),north:Math.min(85.05112878,bounds.north),zoom:map.getZoom()});
    stage.setAttribute('aria-busy','true'); retry.hidden = true; status.textContent = text.loading;
    try {
      const data = await json(await fetch(`/merchant-map/data?${query}`, {signal:controller.signal}));
      if (current !== generation) return;
      clearLayers();
      const stats = data.summary;
      summary.textContent = stats.merchant_count ? `${text.count} ${format(stats.merchant_count)} · ${text.covered} ${format(stats.covered_count)} · ${text.rate} ${format(stats.coverage_pct)}%` : text.empty;
      status.textContent = `${data.dense ? text.dense : data.mode === 'clusters' ? text.zoom : ''} ${text.asOf} ${new Date(data.as_of).toLocaleTimeString(config.language)}`;
      if (data.mode === 'points') {
        points.addGeoJson({type:'FeatureCollection',features:data.points.map(p => ({type:'Feature',
          geometry:{type:'Point',coordinates:[p.lon,p.lat]},properties:{id:p.id,covered:p.covered}}))});
      } else {
        overlays = data.cells.map(cell => new Cluster(cell));
        overlays.forEach(overlay => overlay.setMap(map));
      }
    } catch (error) {
      if (current === generation && error.name !== 'AbortError') {
        clearLayers(); summary.textContent = text.error; status.textContent = ''; retry.hidden = false;
      }
    } finally {
      if (current === generation) stage.setAttribute('aria-busy','false');
    }
  }
  let Cluster;
  window.initMerchantMap = () => {
    Cluster = class extends google.maps.OverlayView {
      constructor(cell) { super(); this.cell = cell; }
      onAdd() {
        const cell = this.cell, button = document.createElement('button');
        button.type = 'button';
        button.className = `merchant-map-cluster coverage-${cell.coverage_pct >= 75 ? 'green' : cell.coverage_pct >= 40 ? 'yellow' : 'red'}`;
        const count = document.createElement('strong'), rate = document.createElement('small');
        count.textContent = format(cell.merchant_count); rate.textContent = `${format(cell.coverage_pct)}%`;
        button.setAttribute('aria-label', `${text.group}: ${cell.merchant_count} · ${text.rate} ${cell.coverage_pct}%`);
        button.append(count,rate);
        button.addEventListener('click', () => { map.panTo({lat:cell.lat,lng:cell.lon}); map.setZoom(Math.min(22,map.getZoom()+2)); });
        google.maps.OverlayView.preventMapHitsAndGesturesFrom(button);
        this.button = button; this.getPanes().overlayMouseTarget.appendChild(button);
      }
      draw() {
        const pixel = this.getProjection().fromLatLngToDivPixel(new google.maps.LatLng(this.cell.lat,this.cell.lon));
        this.button.style.left = `${pixel.x}px`; this.button.style.top = `${pixel.y}px`;
      }
      onRemove() { this.button?.remove(); }
    };
    map = new google.maps.Map(byId('merchant-map'), {center:{lat:35.68,lng:139.76},zoom:9,
      mapTypeControl:false,streetViewControl:false,fullscreenControl:false,maxZoom:22,minZoom:3});
    points = new google.maps.Data({map});
    points.setStyle(feature => ({icon:{path:google.maps.SymbolPath.CIRCLE,scale:6,
      fillColor:feature.getProperty('covered') ? '#176448' : '#a93230',fillOpacity:1,strokeColor:'#fff',strokeWeight:1.5}}));
    points.addListener('click', event => openDetail(event.feature.getProperty('id')));
    map.addListener('bounds_changed', () => { generation++; controller?.abort(); clearTimeout(timer); stage.setAttribute('aria-busy','false'); });
    map.addListener('idle', () => { clearTimeout(timer); timer = setTimeout(load,200); });
    map.fitBounds(initialBounds,35);
  };
  retry.addEventListener('click', () => map ? load() : window.location.reload());
  byId('merchant-map-reset').addEventListener('click', () => { closeDetail(); map?.fitBounds(initialBounds,35); });
  byId('merchant-map-close').addEventListener('click',closeDetail);
  const fullscreen = byId('merchant-map-fullscreen');
  fullscreen.hidden = !document.fullscreenEnabled;
  fullscreen.addEventListener('click', async () => {
    try {
      if (document.fullscreenElement) await document.exitFullscreen();
      else await stage.requestFullscreen();
    } catch { fullscreen.hidden = true; }
  });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeDetail(); });
  window.addEventListener('pagehide', () => { generation++; detailGeneration++; clearTimeout(timer); controller?.abort(); detailController?.abort(); });
  window.gm_authFailure = () => { summary.textContent = text.mapError; status.textContent = ''; retry.hidden = false; };
  if (!config.key) { summary.textContent = text.missingKey; status.textContent = ''; return; }
  const script = document.createElement('script');
  script.src = `https://maps.googleapis.com/maps/api/js?${new URLSearchParams({key:config.key,callback:'initMerchantMap',loading:'async',language:config.language})}`;
  script.async = true; script.onerror = window.gm_authFailure; document.head.appendChild(script);
})();
