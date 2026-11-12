# -*- coding: utf-8 -*-
"""把已登录的页面抓成**本地预览 HTML**（CSS 用绝对 URL），供无头 Chrome 截图。

用途：改版式/配色这类**测试断言看不出来**的改动，先自己截图看一眼再交付。
只读：登录本地服务取 HTML，不写任何业务数据。

    ./.venv/bin/python scripts/preview_page.py /dashboard admin /tmp/preview.html
"""
import os
import re
import sys
import http.cookiejar
import urllib.parse
import urllib.request

B = os.environ.get("PREVIEW_BASE", "http://127.0.0.1:8000")


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else "/dashboard"
    user = sys.argv[2] if len(sys.argv) > 2 else "admin"
    out = sys.argv[3] if len(sys.argv) > 3 else "/tmp/preview.html"
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    op.open(B + "/login", urllib.parse.urlencode(
        {"username": user, "password": "demo123"}).encode())
    html = op.open(B + path).read().decode("utf-8")
    # 静态资源换绝对 URL（file:// 页面才加载得到）
    html = re.sub(r'(href|src)="(/static/[^"]*)"', r'\1="%s\2"' % B, html)
    # 去掉外部 CDN（截图时不需要交互，避免离线卡住）
    html = re.sub(r'<script src="https://[^"]+"[^>]*></script>', "", html)
    open(out, "w", encoding="utf-8").write(html)
    print("已写出 %s（%d 字节，来自 %s %s）" % (out, len(html), user, path))


if __name__ == "__main__":
    main()
