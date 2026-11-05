(() => {
  'use strict';
  const byId = id => document.getElementById(id);
  const configNode = byId('batch-auto-config');
  if (!configNode) return;
  const config = JSON.parse(configNode.textContent);
  const drawer = byId('batch-auto-drawer'), backdrop = byId('batch-auto-backdrop');
  const openButton = byId('batch-auto-open'), closeButton = byId('batch-auto-close');
  const previewButton = byId('batch-auto-preview'), applyButton = byId('batch-auto-apply');
  const result = byId('batch-auto-result'), footer = byId('batch-auto-footer');
  const palette = ['#1769aa','#a33b20','#39754a','#7545a1','#a26a00','#147d78','#b13c73','#5b6470'];
  let signature = '', aiDecision = null, currentRequest = 0;

  function selectedMembers() {
    return [...drawer.querySelectorAll('input[name="auto_member"]:checked')].map(input => Number(input.value));
  }
  function invalidate() {
    signature = '';
    aiDecision = null;
    currentRequest++;
    previewButton.disabled = false;
    previewButton.textContent = config.texts.preview;
    window.setBatchGridAssignmentPreview?.(null);
    footer.hidden = true;
    applyButton.hidden = true;
    applyButton.disabled = true;
    result.hidden = true;
    result.replaceChildren();
  }
  function close() {
    invalidate();
    drawer.hidden = true;
    backdrop.hidden = true;
    openButton.focus();
  }
  function showError(message) {
    invalidate();
    const paragraph = document.createElement('p');
    paragraph.dataset.error = '1';
    paragraph.textContent = message;
    result.append(paragraph);
    result.hidden = false;
  }
  function params() {
    return {csrf_token:config.csrf, member_ids:selectedMembers(),
      include_batch:byId('batch-auto-load').checked,
      use_efficiency:byId('batch-auto-efficiency').checked};
  }
  function showPlan(plan) {
    if (!plan || typeof plan.signature !== 'string' || !plan.signature || !plan.ai_decision ||
        !Array.isArray(plan.members) || !plan.members.length || !plan.assignments) {
      throw new Error(config.texts.failed);
    }
    result.replaceChildren();
    const title = document.createElement('h3');
    title.textContent = config.texts.resultTitle;
    result.append(title);
    const intro = document.createElement('p');
    intro.className = 'batch-auto-summary';
    intro.textContent = `${plan.grid_count} ${config.texts.grid} · ${plan.workload.toLocaleString()} ${config.texts.stores}`;
    if (plan.locked_completed) intro.textContent += ` · ${plan.locked_completed} ${config.texts.locked}`;
    result.append(intro);
    const table = document.createElement('table');
    table.className = 'grid-tbl batch-auto-table';
    const head = document.createElement('thead'), heading = document.createElement('tr');
    for (const label of [config.texts.member, config.texts.gridsLabel, config.texts.pendingLabel]) {
      const cell = document.createElement('th'); cell.scope = 'col'; cell.textContent = label; heading.append(cell);
    }
    head.append(heading); table.append(head);
    const body = document.createElement('tbody');
    const owners = [...new Set(Object.values(plan.assignments))].sort((a,b) => a-b);
    const colors = new Map(owners.map((id,index) => [id,palette[index%palette.length]]));
    const explanation = document.createElement('details');
    explanation.className = 'batch-auto-explanation';
    const summary = document.createElement('summary'); summary.textContent = config.texts.aiReason;
    const reason = document.createElement('p'); reason.textContent = plan.ai_reason;
    explanation.append(summary); explanation.append(reason);
    const factors = document.createElement('ul');
    for (const member of plan.members) {
      const row = document.createElement('tr'), name = document.createElement('th'); name.scope = 'row';
      const label = document.createElement('span'); label.className = 'batch-auto-member-name';
      const dot = document.createElement('i'); dot.className = 'batch-auto-swatch';
      dot.style.backgroundColor = colors.get(member.member_id) || '#64748b';
      const text = document.createElement('span'); text.textContent = member.name;
      label.append(dot); label.append(text); name.append(label); row.append(name);
      for (const count of [member.grid_count, member.workload]) {
        const cell = document.createElement('td'); cell.textContent = count.toLocaleString(); row.append(cell);
      }
      body.append(row);
      const factor = document.createElement('li');
      factor.textContent = `${member.name} · ${config.texts.aiWeight} ${member.ai_weight}` +
        (byId('batch-auto-load').checked ? ` · ${config.texts.otherLoad} ${member.other_batch_workload.toLocaleString()}` : '') +
        (byId('batch-auto-efficiency').checked ? ` · ${config.texts.efficiency} ${member.efficiency}` : '');
      factors.append(factor);
    }
    table.append(body);
    const tableWrap = document.createElement('div'); tableWrap.className = 'tbl-wrap';
    tableWrap.append(table); result.append(tableWrap);
    explanation.append(factors); result.append(explanation);
    window.setBatchGridAssignmentPreview?.(plan.assignments);
    aiDecision = plan.ai_decision;
    signature = plan.signature;
    result.hidden = false;
    footer.hidden = false;
    applyButton.hidden = false;
    applyButton.disabled = false;
  }
  async function send(url, body) {
    const response = await fetch(url, {method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || config.texts.failed);
    return data;
  }
  invalidate();
  openButton.addEventListener('click', () => {
    invalidate(); drawer.hidden = false; backdrop.hidden = false;
    drawer.querySelector('input[name="auto_member"]')?.focus();
  });
  closeButton.addEventListener('click', close);
  backdrop.addEventListener('click', close);
  document.addEventListener('keydown', event => {
    if (drawer.hidden) return;
    if (event.key === 'Escape') close();
    if (event.key === 'Tab') {
      const controls = [...drawer.querySelectorAll('button:not([disabled]),input:not([disabled]),summary')].filter(control => control.getClientRects().length);
      const first = controls[0], last = controls[controls.length-1];
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    }
  });
  drawer.addEventListener('change', event => {
    if (event.target.matches('input')) invalidate();
  });
  previewButton.addEventListener('click', async () => {
    const body = params();
    invalidate();
    const requestId = currentRequest;
    if (!body.member_ids.length) return showError(config.texts.none);
    previewButton.disabled = true;
    previewButton.textContent = config.texts.working;
    try {
      const plan = await send(`/my/work/batch-regions/${config.regionTaskId}/auto-preview`, body);
      if (requestId === currentRequest) showPlan(plan);
    } catch (error) {
      if (requestId === currentRequest) showError(error.message || config.texts.failed);
    } finally {
      if (requestId === currentRequest) {
        previewButton.disabled = false;
        previewButton.textContent = config.texts.preview;
      }
    }
  });
  applyButton.addEventListener('click', async () => {
    if (!signature || !window.confirm(config.texts.confirm)) return;
    applyButton.disabled = true;
    try {
      await send(`/my/work/batch-regions/${config.regionTaskId}/auto-apply`, {...params(),signature,ai_decision:aiDecision});
      window.location.assign(`${window.location.pathname}?msg=${encodeURIComponent(config.texts.done)}`);
    } catch (error) {
      showError(error.message || config.texts.applyFailed);

    }
  });
})();
