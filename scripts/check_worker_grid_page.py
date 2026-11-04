"""队员端批次网格页**只读**体检：结构、作业进展口径、三种筛选。

前提：本地已起服务并用 `scripts/preview_visit_seed.py` 造好演示数据。
    DATABASE_URL="sqlite:///./data/preview_visit.db" ./.venv/bin/python -m uvicorn app.main:app --port 8766
    ./.venv/bin/python scripts/check_worker_grid_page.py
只读：不提交任何表单、不改数据。
"""
import os
import re
import urllib.request
import urllib.parse
import http.cookiejar

BASE = os.environ.get('PREVIEW_BASE', 'http://127.0.0.1:8766')
USERNAME = os.environ.get('PREVIEW_USER', 'demo-worker')
PASSWORD = os.environ.get('PREVIEW_PASSWORD', 'demo123')
GRID_TASK = os.environ.get('PREVIEW_GRID_TASK', '1')
BATCH_TASK = os.environ.get('PREVIEW_BATCH_TASK', '1')

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(path):
    if '?' in path:
        head, query = path.split('?', 1)
        path = head + '?' + urllib.parse.quote(query, safe='=&')
    return opener.open(BASE + path).read().decode()


def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    return opener.open(urllib.request.Request(BASE + path, data=body)).read().decode()


post('/login', {'username': USERNAME, 'password': PASSWORD})
html = get(f'/my/work/batch-grids/{GRID_TASK}')

print('作业进展状态 :', re.search(r'data-testid="visit-progress-state"[^>]*>([^<]*)<', html).group(1))
print('作业进展计数 :', re.search(r'data-testid="visit-progress-count"[^>]*>([^<]*)<', html).group(1))
print('表头         :', re.findall(r'<th[^>]*>([^<]*)</th>', html))
print('导航标题     :', re.search(r'class="dispatch-title"[^>]*>([^<]*)<', html).group(1))
print('返回链接     :', re.search(r'class="dispatch-back" href="([^"]+)"', html).group(1))
print('筛选控件     :', re.findall(r'name="(task_state|status|kw)"', re.search(r'<form class="bd-filters".*?</form>', html, re.S).group(0)))
print('行数         :', len(re.findall(r'class="visit-row"', html)))
print('手填进度表单 :', 'name="pct"' in html)
print('管理侧栏     :', 'has-sidenav' in html)
print('商户行       :', re.findall(r'<td class="visit-name">([^<]*)</td>', html))

filtered = get(f'/my/work/batch-grids/{GRID_TASK}?task_state=completed')
print('筛选后行数   :', len(re.findall(r'class="visit-row"', filtered)))
search = get(f'/my/work/batch-grids/{GRID_TASK}?kw=西大泉駅東口')
print('搜索命中     :', re.findall(r'<td class="visit-name">([^<]*)</td>', search))
status = get(f'/my/work/batch-grids/{GRID_TASK}?status=abnormal')
print('门店状态筛选 :', re.findall(r'<td class="visit-name">([^<]*)</td>', status))
tree = get(f'/my/work/batches/{BATCH_TASK}')
print('批次树筛选栏 :', bool(re.search(r'<form class="bd-filters".*?</form>', tree, re.S)))
print('批次树网格数 :', len(re.findall(r'/my/work/batch-grids/\d+', tree)))
