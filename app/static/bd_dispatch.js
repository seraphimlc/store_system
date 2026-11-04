/* 同时只展开一个网格；地图点击仅定位列表，不滚动整页。 */
(() => {
  'use strict';
  const workspace = document.querySelector('.dispatch-workspace');
  if (!workspace) return;
  const rows = [...workspace.querySelectorAll('.dispatch-row')];
  function reveal(row, open, scroll) {
    window.dispatchSelectedGrid = open ? Number(row.dataset.gridId) : null;
    window.highlightDispatchGrid?.(window.dispatchSelectedGrid);
    rows.forEach(candidate => {
      const expanded = candidate === row && open;
      candidate.setAttribute('aria-expanded', String(expanded));
      document.getElementById(candidate.getAttribute('aria-controls')).hidden = !expanded;
    });
    if (scroll && open) row.closest('.dispatch-grid').scrollIntoView({block:'nearest',behavior:'auto'});
  }
  rows.forEach(row => row.addEventListener('click', () => reveal(row, row.getAttribute('aria-expanded') !== 'true', false)));
  window.openDispatchGrid = id => {
    const row = rows.find(candidate => Number(candidate.dataset.gridId) === id);
    if (row) reveal(row, true, true);
  };
})();
