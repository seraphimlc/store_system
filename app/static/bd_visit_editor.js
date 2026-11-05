/* Visit outcome is independent of completion: every successful save counts as visited. */
(() => {
  'use strict';
  const node = document.getElementById('visit-editor-config');
  if (!node) return;
  const text = JSON.parse(node.textContent);
  const picker = document.getElementById('visit-photo-picker');
  let activePhotoForm = null;
  picker?.addEventListener('click', event => {
    const choice = event.target.closest('[data-photo-choice]');
    if (!choice) { if (event.target === picker) picker.close(); return; }
    const form = activePhotoForm;
    picker.close();
    if (choice.dataset.photoChoice === 'camera') form?.querySelector('[data-photo-camera]').click();
    else if (choice.dataset.photoChoice === 'file') form?.querySelector('[data-photo-files]').click();
  });
  const saveButtons = [...document.querySelectorAll('[data-visit-photo-form] button[type="submit"], [data-location-editor] button[type="submit"]')];
  saveButtons.forEach(button => { button.dataset.idleLabel = button.textContent; });
  // The back/forward cache restores the DOM, including disabled buttons and their labels.
  window.addEventListener('pageshow', () => saveButtons.forEach(button => {
    button.disabled = false; button.textContent = button.dataset.idleLabel;
  }));
  document.querySelectorAll('[data-visit-photo-form]').forEach(form => {
    const filesInput = form.querySelector('[data-photo-files]');
    const camera = form.querySelector('[data-photo-camera]');
    const previews = form.querySelector('[data-photo-previews]');
    const message = form.querySelector('[data-photo-message]');
    let files = [], objectUrls = [];
    function sync() {
      const transfer = new DataTransfer();
      files.forEach(file => transfer.items.add(file));
      filesInput.files = transfer.files;
      if (files.length) filesInput.name = 'photos'; else filesInput.removeAttribute('name');
      previews.querySelectorAll('[data-new-photo]').forEach(item => item.remove());
      objectUrls.forEach(url => URL.revokeObjectURL(url)); objectUrls = [];
      files.forEach((file, index) => {
        const item = document.createElement('div'); item.className = 'visit-photo-item'; item.dataset.newPhoto = '1';
        const img = document.createElement('img'); img.alt = text.photo;
        img.src = URL.createObjectURL(file); objectUrls.push(img.src);
        const remove = document.createElement('button'); remove.type = 'button'; remove.textContent = text.remove;
        remove.addEventListener('click', () => {files.splice(index, 1); message.textContent = ''; sync();});
        item.append(img, remove); previews.append(item);
      });
    }
    function choose(input) {
      const incoming = [...input.files];
      const retained = form.querySelectorAll('[name="keep_photos"]').length;
      const total = retained + files.length + incoming.length;
      const error = total > 3 ? text.photoLimit : incoming.some(file => file.size > 5*1024*1024) ? text.photoSize :
        incoming.some(file => !['image/jpeg','image/png','image/webp'].includes(file.type)) ? text.photoType : '';
      if (!error) files.push(...incoming);
      camera.value = ''; message.textContent = error; sync();
    }
    form.querySelector('[data-open-photo-picker]')?.addEventListener('click', () => {
      activePhotoForm = form; picker?.showModal();
    });
    filesInput.addEventListener('cancel', sync);
    camera.addEventListener('cancel', sync);
    filesInput.addEventListener('change', () => choose(filesInput));
    camera.addEventListener('change', () => choose(camera));
    previews.querySelectorAll('[data-remove-photo]').forEach(button => button.addEventListener('click', () => {
      button.closest('[data-kept-photo]').remove(); message.textContent = '';
    }));
    sync();
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true; button.textContent = text.saving;
    });
  });
  document.querySelectorAll('[data-location-editor]').forEach(form => form.addEventListener('submit', async event => {
    event.preventDefault();
    const button = form.querySelector('button[type="submit"]'), message = form.querySelector('[data-coordinate-message]');
    const label = button.textContent; button.disabled = true; button.textContent = text.saving;
    try {
      const response = await fetch(form.action, {method:'POST',body:new FormData(form)});
      const data = await response.json();
      if (!response.ok || !data.ok) throw new Error(data.error || text.failed);
      const lookup = {candidate:text.candidate,ambiguous:text.ambiguous,no_match:text.no_match,
        failed:text.failedLookup,unconfigured:text.unconfigured};
      const url = new URL(location.href);
      url.searchParams.set('msg', lookup[data.place_lookup_state] || text.locationSaved);
      location.assign(url.toString());
    } catch (error) {message.textContent = error.message || text.failed; button.disabled = false; button.textContent = label;}
  }));
})();
