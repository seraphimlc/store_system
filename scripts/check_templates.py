# -*- coding: utf-8 -*-
"""模板 HTML 标签配对自检（Jinja 不校验 HTML，切文件容易切坏结构）。

    ./.venv/bin/python scripts/check_templates.py [app/templates/bd_team_detail.html ...]
"""
import glob
import re
import sys

TAGS = ("html", "body", "form", "table", "thead", "tbody", "tr", "td", "th",
        "div", "script", "aside", "nav", "main", "select", "label")


def check(path):
    h = open(path, encoding="utf-8").read()
    # 去掉 Jinja 注释/语句（它们不影响 HTML 结构）
    h = re.sub(r"\{#.*?#\}", "", h, flags=re.S)
    bad = []
    for tag in TAGS:
        o = len(re.findall(r"<%s(?=[\s>{])" % tag, h))
        c = len(re.findall(r"</%s>" % tag, h))
        if o != c:
            bad.append("%s 开 %d / 闭 %d" % (tag, o, c))
    return bad


def main():
    files = sys.argv[1:] or sorted(glob.glob("app/templates/*.html"))
    total = 0
    for f in files:
        bad = check(f)
        if bad:
            total += 1
            print("★ %s → %s" % (f, "；".join(bad)))
    print("检查 %d 个模板，%d 个标签不配对" % (len(files), total))
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
