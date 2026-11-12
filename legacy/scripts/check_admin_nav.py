# -*- coding: utf-8 -*-
"""快速检查管理端左侧菜单渲染（结构 + 当前页高亮 + 员工端不受影响）。

只读：登录本地服务取 HTML，不写任何数据。
"""
import re
import sys
import urllib.parse
import urllib.request
import http.cookiejar

B = "http://127.0.0.1:8000"


def login(user):
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(cj))
    data = urllib.parse.urlencode({"username": user, "password": "demo123"}).encode()
    op.open(B + "/login", data)
    return op


def html(op, path):
    return op.open(B + path).read().decode("utf-8")


def items(page):
    """抓 side-item 标签（跨行也算）→ [(href, active, text)]"""
    out = []
    for m in re.finditer(r'<a class="side-item([^"]*)"\s*\n?\s*href="([^"]+)"[^>]*>(.*?)</a>',
                         page, re.S):
        cls, href, inner = m.group(1), m.group(2), m.group(3)
        text = re.sub(r"<[^>]+>", "", inner).strip()
        out.append((href, "on" in cls.split(), text))
    return out


def main():
    adm = login("admin")
    ok = True
    for path, want in (("/dashboard", "/dashboard"), ("/tasks", "/tasks"),
                       ("/logs", "/logs"), ("/messages", "/messages"),
                       ("/staff-plans", "/staff-plans")):
        page = html(adm, path)
        rows = items(page)
        actives = [h for h, a, _t in rows if a]
        mark = "✓" if actives == [want] else "✗"
        if actives != [want]:
            ok = False
        print("%s %-14s 菜单项 %2d 个，高亮=%s（期望 %s）"
              % (mark, path, len(rows), actives or "无", want))
        if path == "/dashboard":
            print("    分组:", "、".join(
                re.findall(r'class="side-title">([^<]+)', page)))
            print("    菜单:", "、".join(t for _h, _a, t in rows))
    # 员工端不受影响
    st = login("chujianwen")
    p = html(st, "/my/tasks")
    print("员工 /my/tasks: 顶部细条 %s / 左侧菜单 %s / 底栏 %s"
          % ('class="topnav"' in p, 'data-testid="sidenav"' in p,
             'data-testid="tab-messages"' in p))
    print("结果:", "全部符合预期" if ok else "有不符项")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
