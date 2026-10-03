/* 员工下拉的"搜索过滤"组件（2026-10-02 用户要求）
 *
 * 用法：给任意 <select> 加 `data-emp-filter` 属性即可（选项文本形如 `姓名（编号）`）。
 * 组件会在下拉上方插入一个筛选输入框，输入即过滤：
 *   - **编号匹配**：直接输后 5 位就能命中（选项文本里含完整编号，子串命中即可）；
 *   - **姓名匹配**：输姓名里任意一个字即可；
 *   - 查询串做 NFKC 归一（全角数字/字母、大小写都能匹配）；
 *   - **唯一命中时自动选中**（省一次点击），命中多个则保留当前选择、由人工挑；
 *   - 无命中时输入框标红并提示"无匹配"；
 *   - 当前已选项始终保留在列表里，避免"过滤"悄悄改掉已提交的筛选条件。
 *
 * 纯原生 JS、不依赖任何库（CDN 挂了也能用）；htmx 局部替换后会自动重新挂载。
 */
(function () {
  'use strict';

  var HINT = '筛选：输入编号后5位 或 姓名一个字';

  function norm(s) {
    s = s || '';
    if (s.normalize) { s = s.normalize('NFKC'); }
    return s.toLowerCase().trim();
  }

  function mount(sel) {
    if (sel.dataset.empFilterDone === '1') { return; }
    sel.dataset.empFilterDone = '1';

    // 记下全部原始选项（value / innerHTML / 是否选中）
    var all = Array.prototype.slice.call(sel.options).map(function (o) {
      return { value: o.value, html: o.innerHTML, text: o.textContent };
    });

    var wrap = document.createElement('div');
    wrap.className = 'emp-filter-wrap';
    var box = document.createElement('input');
    box.type = 'search';
    box.className = 'emp-filter';
    box.placeholder = sel.dataset.empFilterHint || HINT;
    box.setAttribute('autocomplete', 'off');
    box.setAttribute('aria-label', HINT);
    var tip = document.createElement('div');
    tip.className = 'emp-filter-tip';

    sel.parentNode.insertBefore(wrap, sel);
    wrap.appendChild(box);
    wrap.appendChild(tip);
    wrap.appendChild(sel);

    function apply(fireChange) {
      var q = norm(box.value);
      var matched = all.filter(function (o) {
        return o.value && (!q || norm(o.text).indexOf(q) >= 0);
      });
      // 已选项永远保留（否则提交时筛选会被偷偷改掉）
      var cur = sel.value;
      var keep = matched.slice();
      if (cur && !keep.some(function (o) { return o.value === cur; })) {
        all.forEach(function (o) { if (o.value === cur) { keep.push(o); } });
      } else if (!q) {
        keep = all.slice();
      }

      // 唯一命中 → 直接选中（"输后 5 位就不用再点一下"）
      var auto = q && matched.length === 1 ? matched[0].value : null;
      if (auto) { cur = auto; if (!keep.some(function (o) { return o.value === auto; })) { keep.push({ value: auto }); } }

      sel.innerHTML = '';
      keep.forEach(function (o) {
        var op = document.createElement('option');
        op.value = o.value;
        if (o.html !== undefined) { op.innerHTML = o.html; } else { op.textContent = o.value; }
        if (o.value === cur) { op.selected = true; }
        sel.appendChild(op);
      });

      var none = !!q && matched.length === 0;
      box.classList.toggle('is-none', none);
      tip.textContent = none ? '无匹配' : (q && matched.length > 1 ? matched.length + ' 个匹配' : '');
      if (auto && fireChange) {
        sel.dispatchEvent(new Event('change', { bubbles: true }));   // 看板那种 change 即加载的下拉
      }
    }

    box.addEventListener('input', function () { apply(true); });
    box.addEventListener('keydown', function (e) {
      if (e.key === 'Escape') { box.value = ''; apply(false); }
      if (e.key === 'Enter') { e.preventDefault(); apply(true); sel.focus(); }
    });
  }

  function scan(root) {
    (root || document).querySelectorAll('select[data-emp-filter]').forEach(mount);
  }

  document.addEventListener('DOMContentLoaded', function () { scan(document); });
  document.addEventListener('htmx:afterSwap', function (e) {
    scan((e && e.target) || document);
  });
  scan(document);      // 脚本在页面底部时直接挂载
})();
