/* 创建批次：原生dialog负责焦点约束和Escape，提交仍走原表单。 */
(() => {
  'use strict';
  const dialog=document.getElementById('create-batch');
  if(!dialog)return;
  let opener;
  const open=()=>{if(!dialog.open){opener=document.activeElement;dialog.showModal();dialog.querySelector('input[name="title"]')?.focus();}};
  const clearHash=()=>{if(location.hash==='#create-batch')history.replaceState(null,'',location.pathname+location.search);};
  document.querySelectorAll('[data-create-batch]').forEach(button=>button.addEventListener('click',open));
  dialog.querySelector('[data-close-batch]')?.addEventListener('click',()=>dialog.close());
  dialog.addEventListener('close',()=>{clearHash();opener?.focus();});
  dialog.addEventListener('click',event=>{if(event.target!==dialog)return;const r=dialog.getBoundingClientRect();if(event.clientX<r.left||event.clientX>r.right||event.clientY<r.top||event.clientY>r.bottom)dialog.close();});
  addEventListener('hashchange',()=>{if(location.hash==='#create-batch')open();});
  if(location.hash==='#create-batch')open();
})();
