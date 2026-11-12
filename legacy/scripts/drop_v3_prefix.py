# -*- coding: utf-8 -*-
"""去掉 v3_ 版本前缀：文件重命名 + 全仓引用替换（URL 里的 /v3/ 别名保留兼容）。"""
import io
import os
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
os.chdir(ROOT)

# 内容替换映射（长前缀优先）
MAP = [
    ("period", "period"),
    ("report", "report"),
    ("recon", "recon"),
    ("month", "month"),
    ("flow", "flow"),
    ("perf", "perf"),
    ("settle_r", "settle_r"),
]

# 文件重命名（旧→新）
RENAME = [
    ("app/routers/settle_r.py", "app/routers/settle_r.py"),
    ("app/services/flow.py", "app/services/flow.py"),
    ("app/services/perf.py", "app/services/perf.py"),
    ("app/services/period.py", "app/services/period.py"),
    ("app/services/recon.py", "app/services/recon.py"),
    ("app/services/report.py", "app/services/report.py"),
    ("app/templates/month.html", "app/templates/month.html"),
    ("app/templates/perf.html", "app/templates/perf.html"),
    ("app/templates/recon.html", "app/templates/recon.html"),
    ("tests_web/test_flow.py", "tests_web/test_flow.py"),
    ("migrations/versions/a9b8c7d6e5f4_v3_formal_确认任务_主从档字段.py",
     "migrations/versions/a9b8c7d6e5f4_formal_确认任务_主从档字段.py"),
]

# 1) 先改内容（在重命名之前，避免路径失效）
EXTS = (".py", ".html", ".md", ".sh", ".txt", ".yaml", ".yml", ".ini")
changed = []
for base in ("app", "tests", "tests_web", "scripts", "docs", "migrations", "deploy"):
    for dirpath, _dirs, files in os.walk(base):
        if "__pycache__" in dirpath:
            continue
        for fn in files:
            if not fn.endswith(EXTS):
                continue
            p = os.path.join(dirpath, fn)
            try:
                s = io.open(p, encoding="utf-8").read()
            except (UnicodeDecodeError, OSError):
                continue
            o = s
            for a, b in MAP:
                s = s.replace(a, b)
            if s != o:
                io.open(p, "w", encoding="utf-8").write(s)
                changed.append(p)
print("内容替换文件数:", len(changed))
for c in changed:
    print("  ", c)

# 2) 文件重命名（git mv 保留历史）
for old, new in RENAME:
    if os.path.exists(old):
        subprocess.run(["git", "mv", old, new], check=True)
        print("rename:", old, "->", new)
    else:
        print("skip(不存在):", old)
