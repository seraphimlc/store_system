/* 列表头筛选：改动即搜索（不用点按钮）。
   任何 <form data-autosubmit="1"> 都生效：下拉选择立刻提交，搜索框输入停 500ms 提交。 */
(() => {
  'use strict';
  document.querySelectorAll('form[data-autosubmit="1"]').forEach(form => {
    let timer = null;

    // 空值不带进 URL（?visit=&kw= 这种噪音看着脏）
    const go = () => {
      if (timer) { clearTimeout(timer); timer = null; }
      const params = new URLSearchParams(new FormData(form));
      for (const [key, value] of [...params]) {
        if (!value) params.delete(key);
      }
      window.location.search = params.toString();
    };

    // 原生移动端 select 弹层不受页面布局控制；本网格使用随控件定位的菜单。
    if (form.classList.contains('visit-equal-filters')) {
      const closeMenus = () => form.querySelectorAll('.visit-filter-picker').forEach(picker => {
        picker.querySelector('[role=listbox]').hidden = true;
        picker.querySelector('[aria-haspopup]').setAttribute('aria-expanded', 'false');
      });
      form.querySelectorAll('select').forEach(select => {
        const picker = document.createElement('div');
        picker.className = 'visit-filter-picker';
        const trigger = document.createElement('button');
        trigger.type = 'button';
        trigger.className = 'visit-filter-trigger';
        trigger.setAttribute('aria-haspopup', 'listbox');
        trigger.setAttribute('aria-expanded', 'false');
        trigger.setAttribute('aria-label', select.getAttribute('aria-label'));
        trigger.textContent = select.selectedOptions[0].textContent;
        const menu = document.createElement('div');
        menu.id = `visit-filter-${select.name}`;
        menu.className = 'visit-filter-menu';
        menu.setAttribute('role', 'listbox');
        menu.setAttribute('aria-label', select.getAttribute('aria-label'));
        menu.hidden = true;
        trigger.setAttribute('aria-controls', menu.id);
        const options = Array.from(select.options, option => {
          const item = document.createElement('button');
          item.type = 'button';
          item.tabIndex = -1;
          item.setAttribute('role', 'option');
          item.setAttribute('aria-selected', String(option.selected));
          item.textContent = option.textContent;
          item.addEventListener('click', () => {
            select.value = option.value;
            trigger.textContent = option.textContent;
            options.forEach(button => button.setAttribute('aria-selected', String(button === item)));
            closeMenus();
            trigger.focus();
            select.dispatchEvent(new Event('change', {bubbles: true}));
          });
          menu.append(item);
          return item;
        });
        const open = (index = select.selectedIndex) => {
          closeMenus();
          menu.hidden = false;
          trigger.setAttribute('aria-expanded', 'true');
          options[index].focus();
        };
        trigger.addEventListener('click', () => menu.hidden ? open() : closeMenus());
        picker.addEventListener('keydown', event => {
          const index = options.indexOf(document.activeElement);
          if (event.key === 'Escape') {
            closeMenus();
            trigger.focus();
          } else if (['ArrowDown', 'ArrowUp', 'Home', 'End'].includes(event.key)) {
            event.preventDefault();
            if (menu.hidden) open(event.key === 'End' ? options.length - 1 : event.key === 'Home' ? 0 : select.selectedIndex);
            else options[event.key === 'Home' ? 0 : event.key === 'End' ? options.length - 1 : (index + (event.key === 'ArrowDown' ? 1 : -1) + options.length) % options.length].focus();
          }
        });
        picker.addEventListener('focusout', event => {
          if (!picker.contains(event.relatedTarget)) closeMenus();
        });
        picker.append(trigger, menu);
        select.after(picker);
        select.hidden = true;
      });
      document.addEventListener('click', event => {
        if (!event.target.closest('.visit-filter-picker')) closeMenus();
      });
    }

    form.addEventListener('change', event => {
      if (event.target.tagName === 'SELECT') go();
    });
    const search = form.querySelector('input[type=search]');
    if (search) {
      search.addEventListener('input', () => {
        if (timer) clearTimeout(timer);
        timer = setTimeout(go, 500);
      });
      search.addEventListener('search', go);   // type=search 自带的清空 ×
    }
    form.addEventListener('submit', event => {
      event.preventDefault();
      go();
    });
  });
})();
