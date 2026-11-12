/* 员工下拉：可搜索的 combobox（2026-10-02 用户要求）
 *
 * 目标交互：**点开下拉就能直接打字**——面板里自带搜索框，输入即过滤，回车/点击即选。
 * 给任意 <select> 加 `data-emp-filter` 即可；脚本会把它"包装"成 combobox：
 *   - 原 select 仍留在 DOM 里（`display:none`），**表单提交值 / htmx 行为完全不变**；
 *   - 可见部分 = 一个按钮（显示当前选中项）+ 点开后的面板（搜索框 + 选项列表）；
 *   - 匹配：**编号后 5 位**（选项文本含完整编号，子串即命中）、**姓名任一个字**；
 *     查询串做 NFKC 归一（全角数字/字母、大小写都能匹配）；
 *   - 键盘：↑/↓ 移动、Enter 选中、Esc 关闭、点面板外关闭；
 *   - 选中后派发 `change`（看板那种"change 即加载"的下拉直接出结果）。
 *
 * 纯原生 JS、零依赖；htmx 局部替换后自动重新挂载。
 */
(function () {
  'use strict';

  var HINT = '输入编号后5位 或 姓名一个字';
  var HINT_CB = '输入名称（支持中文/日文）';

  function norm(s) {
    s = s || '';
    if (s.normalize) { s = s.normalize('NFKC'); }
    return s.toLowerCase().trim();
  }

  function mount(sel) {
    if (sel.dataset.empFilterDone === '1') { return; }
    sel.dataset.empFilterDone = '1';

    var opts = Array.prototype.slice.call(sel.options).map(function (o) {
      // ⚠️ 中日字形：选项上可挂 data-zh（中文名，服务端现算）→ 搜索时一并匹配。
      //    没有 data-zh 的（员工下拉）行为完全不变。
      var zh = o.dataset.zh || o.dataset.alt || '';
      return { value: o.value, label: o.textContent, zh: zh,
               hay: norm(o.textContent + ' ' + zh) };
    });
    var hint = sel.dataset.empFilterHint || (sel.dataset.cbFilter !== undefined && !sel.dataset.empFilter ? HINT_CB : HINT);

    var box = document.createElement('div');
    box.className = 'emp-cb';
    var btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'emp-cb-btn';
    btn.setAttribute('aria-haspopup', 'listbox');
    btn.setAttribute('aria-expanded', 'false');
    var label = document.createElement('span');
    label.className = 'emp-cb-label';
    var caret = document.createElement('span');
    caret.className = 'emp-cb-caret';
    caret.textContent = '▾';
    btn.appendChild(label);
    btn.appendChild(caret);

    var panel = document.createElement('div');
    panel.className = 'emp-cb-panel';
    panel.hidden = true;
    var search = document.createElement('input');
    search.type = 'search';
    search.className = 'emp-cb-search';
    search.placeholder = hint;
    search.setAttribute('autocomplete', 'off');
    var list = document.createElement('div');
    list.className = 'emp-cb-list';
    list.setAttribute('role', 'listbox');
    panel.appendChild(search);
    panel.appendChild(list);

    box.appendChild(btn);
    box.appendChild(panel);
    sel.parentNode.insertBefore(box, sel);
    sel.classList.add('emp-cb-native');        // 原 select 只作为"值的载体"
    sel.setAttribute('tabindex', '-1');

    var hi = 0;

    function current() {
      for (var i = 0; i < opts.length; i++) { if (opts[i].value === sel.value) { return opts[i]; } }
      return opts[0];
    }
    function syncLabel() {
      var o = current();
      label.textContent = o ? o.label : '';
      btn.title = o && o.value ? o.label : '';
    }
    function items() {
      var q = norm(search.value);
      return opts.filter(function (o) {
        return !q || !o.value || o.hay.indexOf(q) >= 0;
      });
    }
    function render() {
      var arr = items();
      if (hi > arr.length - 1) { hi = Math.max(0, arr.length - 1); }
      list.innerHTML = '';
      arr.forEach(function (o, i) {
        var it = document.createElement('div');
        it.className = 'emp-cb-item' + (o.value === sel.value ? ' is-cur' : '')
                     + (i === hi ? ' is-hi' : '');
        it.setAttribute('role', 'option');
        it.textContent = o.label;
        it.addEventListener('mousedown', function (e) { e.preventDefault(); pick(o); });
        list.appendChild(it);
      });
      if (!arr.some(function (o) { return o.value; }) && norm(search.value)) {
        var none = document.createElement('div');
        none.className = 'emp-cb-none';
        none.textContent = '无匹配';
        list.appendChild(none);
      }
    }
    function open() {
      panel.hidden = false;
      box.classList.add('is-open');
      btn.setAttribute('aria-expanded', 'true');
      search.value = '';
      hi = 0;
      render();
      search.focus();
    }
    function close() {
      panel.hidden = true;
      box.classList.remove('is-open');
      btn.setAttribute('aria-expanded', 'false');
    }
    function pick(o) {
      if (sel.value !== o.value) {
        sel.value = o.value;
        sel.dispatchEvent(new Event('change', { bubbles: true }));   // htmx / onchange 都能收到
      }
      syncLabel();
      close();
      btn.focus();
    }

    btn.addEventListener('click', function () { panel.hidden ? open() : close(); });
    btn.addEventListener('keydown', function (e) {
      if (e.key === 'ArrowDown' && panel.hidden) { e.preventDefault(); open(); }
    });
    search.addEventListener('input', function () { hi = 0; render(); });
    search.addEventListener('keydown', function (e) {
      var arr = items();
      if (e.key === 'ArrowDown') {
        e.preventDefault(); hi = Math.min(hi + 1, arr.length - 1); render(); scrollTo(list, hi);
      } else if (e.key === 'ArrowUp') {
        e.preventDefault(); hi = Math.max(hi - 1, 0); render(); scrollTo(list, hi);
      } else if (e.key === 'Enter') {
        e.preventDefault();
        if (arr[hi]) { pick(arr[hi]); }
      } else if (e.key === 'Escape') {
        close(); btn.focus();
      }
    });
    document.addEventListener('click', function (e) { if (!box.contains(e.target)) { close(); } });
    sel.addEventListener('change', syncLabel);
    syncLabel();
  }

  function scrollTo(list, idx) {
    var el = list.children[idx];
    if (el && el.scrollIntoView) { el.scrollIntoView({ block: 'nearest' }); }
  }

  function scan(root) {
    // `data-emp-filter`（员工下拉）/ `data-cb-filter`（通用：线路下拉等，用户 2026-10-06）
    (root || document).querySelectorAll('select[data-emp-filter],select[data-cb-filter]')
      .forEach(mount);
  }

  document.addEventListener('DOMContentLoaded', function () { scan(document); });
  document.addEventListener('htmx:afterSwap', function (e) { scan((e && e.target) || document); });
  scan(document);
})();
