"""手机端顶栏标题验收：抓队员端页面 → 存静态快照 → 用 393px 宽 iframe 模拟手机视口。

只读：不改任何业务数据。快照里的 CSS 指向本仓库真实文件，媒体查询按 iframe 宽度生效。
用法：先起演示服务，再跑本脚本，最后用浏览器打开 data/preview_shots/wrapper.html 看效果。
"""
import os
import re
import subprocess
import urllib.request
import urllib.parse
import http.cookiejar
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'data' / 'preview_shots'
BASE = os.environ.get('PREVIEW_BASE', 'http://127.0.0.1:8766')
PAGES = [
    ('region', '/my/work/batches/1/regions/1', '片区总览（页面标题=片区名）'),
    ('grid', '/my/work/batch-grids/1', '批次网格页（页面标题=网格名）'),
    ('home', '/my/work', '批次首页（页面标题=批次名）'),
    ('batch', '/my/work/batches/1', '批次详情（片区列表，批次日志入口在这）'),
    ('perf', '/my/perf', '没有页面标题的页面（应保留品牌名）'),
]
WIDTH = int(os.environ.get('PREVIEW_WIDTH', '393'))
HEIGHT = int(os.environ.get('PREVIEW_HEIGHT', '852'))
PC_WIDTH = int(os.environ.get('PREVIEW_PC_WIDTH', '1200'))
PC_HEIGHT = int(os.environ.get('PREVIEW_PC_HEIGHT', '420'))

jar = http.cookiejar.CookieJar()
opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def get(path):
    return opener.open(BASE + path).read().decode()


def post(path, data):
    body = urllib.parse.urlencode(data).encode()
    return opener.open(urllib.request.Request(BASE + path, data=body)).read().decode()


def snapshot(html, name):
    """把页面存成静态快照：CSS 指向本仓库真实文件，JS 去掉（地图/筛选脚本不需要）。"""
    OUT.mkdir(parents=True, exist_ok=True)
    local = html.replace('/static/', f'file://{ROOT}/app/static/')
    local = re.sub(r'<script[^>]*src="/static/[^"]*"[^>]*></script>', '', local)
    path = OUT / f'{name}.html'
    path.write_text(local, encoding='utf-8')
    return path


def main():
    post('/login', {'username': 'demo-worker', 'password': 'demo123'})
    frames = []
    for name, path, desc in PAGES:
        try:
            html = get(path)
        except Exception as exc:                      # noqa: BLE001
            print(f'{name:8} 取页面失败：{exc}')
            continue
        brand = re.search(r'class="brand topbar-brand"[^>]*>([^<]*)<', html)
        title = re.search(r'class="mobile-navigation-title">(.*?)</div>', html, re.S)
        inner = re.sub(r'<[^>]+>', ' ', title.group(1)).split() if title else []
        snap = snapshot(html, name)
        frames.append((name, desc, snap))
        print(f'{name:8} {desc}')
        print(f'{"":8}   品牌名={"有" if brand else "无"}  页面标题={inner or "（无）"}')
    # 手机视口容器：iframe 宽度即媒体查询依据
    def frame(name, desc, snap, width, height, tag):
        return (f'<figure style="margin:0 0 14px"><figcaption style="font:600 13px/1.6 sans-serif;'
                f'padding:4px 8px;background:#eef2f7">{name} · {desc} · {tag}（{width}px）</figcaption>'
                f'<iframe src="file://{snap}" width="{width}" height="{height}" '
                f'style="border:1px solid #cbd5e1;border-radius:6px;display:block"></iframe></figure>')

    body = ''.join(
        frame(name, desc, snap, WIDTH, HEIGHT, '手机')
        + frame(name, desc, snap, PC_WIDTH, PC_HEIGHT, 'PC')
        for name, desc, snap in frames)
    wrapper = OUT / 'wrapper.html'
    wrapper.write_text(
        f'<!doctype html><meta charset="utf-8"><title>手机视口预览</title>'
        f'<body style="margin:0;padding:12px;background:#f4f7fa">{body}</body>',
        encoding='utf-8')
    print(f'\n手机视口容器（{WIDTH}×{HEIGHT}）：{wrapper}')


if __name__ == '__main__':
    main()
