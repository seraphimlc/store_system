/* 点行展开单网格派发；点地图仅定位列表，不切换展开状态。 */
(() => {
  'use strict';
  const workspace=document.querySelector('.batch-region-page .dispatch-workspace');
  if(!workspace)return;
  const rows=[...workspace.querySelectorAll('.dispatch-row')];
  function focus(row,fromMap){
    const id=Number(row.dataset.gridId);
    window.dispatchSelectedGrid=id;
    rows.forEach(r=>r.classList.toggle('is-focused',r===row));
    window.highlightDispatchGrid?.(id);
    if(fromMap)row.closest('.dispatch-grid').scrollIntoView({block:'nearest',behavior:'auto'});
    else window.focusTaskGrid?.(id);
  }
  rows.forEach(row=>row.addEventListener('click',()=>{
    const open=row.getAttribute('aria-expanded')!=='true';
    rows.forEach(r=>{const expanded=r===row&&open;r.setAttribute('aria-expanded',String(expanded));document.getElementById(r.getAttribute('aria-controls')).hidden=!expanded;});
    focus(row,false);
  }));
  window.openDispatchGrid=id=>{const row=rows.find(r=>Number(r.dataset.gridId)===Number(id));if(row)focus(row,true);};
})();
