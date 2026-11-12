# -*- coding: utf-8 -*-
"""修复 recon.html 中 {% for t in tasks %} 覆盖全局 t() 函数的问题：
循环变量 t → task（t 保留为翻译函数）。"""
f = "app/templates/recon.html"
s = open(f, encoding="utf-8").read()
old = """    {% for t in tasks %}
    <tr>
      <td><a href="/recon?task_id={{ t.id }}">#{{ t.id }}</a>
        {% if (t.params or {}).get('replaced_by') %}<span class="pill">{{ t('上一版') }}</span>
        {% else %}<span class="pill ok">{{ t('当前') }}</span>{% endif %}</td>
      <td>{{ (t.params or {}).get('month','') }}</td>
      <td>{{ (t.params or {}).get('file','') }}</td>
      <td>{{ (t.params or {}).get('kind','') }}</td>
      <td class="num">{{ (t.summary or {}).get('compared', 0) }}</td>
      <td class="num">{{ (t.summary or {}).get('diff_count', 0) }}</td>
      <td>{% if t.status == 'done' or t.status == 'parsed' %}<span class="pill ok">{{ t('已完成') }}</span>
          {% elif t.status == 'running' %}<span class="pill run">{{ t('处理中') }}</span>
          {% elif t.status == 'pending' %}<span class="pill run">{{ t('排队中') }}</span>
          {% elif t.status == 'failed' %}<span class="pill err">{{ t('失败') }}</span>
          {% else %}<span class="pill">{{ t.status }}</span>{% endif %}</td>
      <td>{{ t.created_at.strftime('%m-%d %H:%M') }}</td>
      <td>
        {% if t.status in ('done', 'parsed') %}
        <a class="btn ghost" style="padding:.12rem .45rem;font-size:.82rem"
           href="/recon/result?task_id={{ t.id }}">{{ t('结果') }}</a>
        <a class="btn ghost" style="padding:.12rem .45rem;font-size:.82rem"
        {% else %}—{% endif %}
      </td>
    </tr>
    {% endfor %}"""
new = """    {% for task in tasks %}
    <tr>
      <td><a href="/recon?task_id={{ task.id }}">#{{ task.id }}</a>
        {% if (task.params or {}).get('replaced_by') %}<span class="pill">{{ t('上一版') }}</span>
        {% else %}<span class="pill ok">{{ t('当前') }}</span>{% endif %}</td>
      <td>{{ (task.params or {}).get('month','') }}</td>
      <td>{{ (task.params or {}).get('file','') }}</td>
      <td>{{ (task.params or {}).get('kind','') }}</td>
      <td class="num">{{ (task.summary or {}).get('compared', 0) }}</td>
      <td class="num">{{ (task.summary or {}).get('diff_count', 0) }}</td>
      <td>{% if task.status == 'done' or task.status == 'parsed' %}<span class="pill ok">{{ t('已完成') }}</span>
          {% elif task.status == 'running' %}<span class="pill run">{{ t('处理中') }}</span>
          {% elif task.status == 'pending' %}<span class="pill run">{{ t('排队中') }}</span>
          {% elif task.status == 'failed' %}<span class="pill err">{{ t('失败') }}</span>
          {% else %}<span class="pill">{{ task.status }}</span>{% endif %}</td>
      <td>{{ task.created_at.strftime('%m-%d %H:%M') }}</td>
      <td>
        {% if task.status in ('done', 'parsed') %}
        <a class="btn ghost" style="padding:.12rem .45rem;font-size:.82rem"
           href="/recon/result?task_id={{ task.id }}">{{ t('结果') }}</a>
        <a class="btn ghost" style="padding:.12rem .45rem;font-size:.82rem"
        {% else %}—{% endif %}
      </td>
    </tr>
    {% endfor %}"""
if old in s:
    s = s.replace(old, new)
    open(f, "w", encoding="utf-8").write(s)
    print("fixed recon loop t→task")
else:
    print("PATTERN NOT FOUND")
