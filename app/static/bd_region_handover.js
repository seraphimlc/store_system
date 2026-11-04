/* Confirm only actual team transfers; first assignment and unchanged selection are direct. */
(() => {
  'use strict';
  document.querySelectorAll('.batch-region-handover').forEach(form => {
    const select=form.querySelector('select[name="team_id"]');
    const button=form.querySelector('button[type="submit"]');
    const current=String(form.dataset.currentTeam||'');
    const update=()=>{
      const changing=Boolean(current && select.value && select.value!==current);
      button.textContent=changing?button.dataset.transferLabel:button.dataset.defaultLabel;
      button.classList.toggle('is-transfer',changing);
    };
    button.dataset.defaultLabel=button.textContent.trim();
    button.dataset.transferLabel=form.dataset.transferLabel;
    select.addEventListener('change',update);
    form.addEventListener('submit',event=>{
      if(!current || select.value===current)return;
      const warning=form.dataset.confirmMessage.replace('{old}',form.dataset.currentTeamName).replace('{new}',select.selectedOptions[0]?.textContent.trim()||'');
      if(!window.confirm(warning)){event.preventDefault();select.focus();}
    });
    update();
  });
  document.querySelectorAll('[data-region-toggle]').forEach(button=>button.addEventListener('click',()=>{
    const open=button.getAttribute('aria-expanded')!=='true';
    button.setAttribute('aria-expanded',String(open));
    const content=document.getElementById(button.getAttribute('aria-controls'));
    if(content)content.hidden=!open;
  }));
})();
