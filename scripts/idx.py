# -*- coding: utf-8 -*-
"""**按关键词/功能域查文件**（省 token 的日常入口）。

用户 2026-10-06："把现有文件都索引一下，后面改到哪个功能再往上下文里放哪个功能"。
→ 索引在 `docs/文件索引.tsv`，本脚本是查询器：**先查，再只读命中的那几个文件**。

用法：
    ./.venv/bin/python scripts/idx.py                    # 功能域清单 + 文件数
    ./.venv/bin/python scripts/idx.py 车站 任务           # 多关键词（**取交集**）
    ./.venv/bin/python scripts/idx.py --dom 作业域         # 整个功能域
    ./.venv/bin/python scripts/idx.py --reload 车站        # 先重建索引再查
"""
from __future__ import annotations

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TSV = os.path.join(ROOT, "docs", "文件索引.tsv")


def load():
    rows = []
    with open(TSV, encoding="utf-8") as f:
        next(f, None)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3:
                rows.append((parts[0], parts[1], parts[2]))
    return rows


def main(argv):
    if "--reload" in argv:
        subprocess.run([sys.executable, os.path.join(ROOT, "scripts",
                                                     "build_file_index.py")], cwd=ROOT)
        argv = [a for a in argv if a != "--reload"]
    rows = load()
    dom = None
    if "--dom" in argv:
        i = argv.index("--dom")
        dom = argv[i + 1] if i + 1 < len(argv) else None
        argv = argv[:i] + argv[i + 2:]
    kws = [a for a in argv if not a.startswith("--")]

    if not kws and not dom:
        from collections import Counter
        print("功能域（用 --dom <域名> 看全部；或用关键词查）：")
        for k, n in Counter(r[1] for r in rows).most_common():
            print("   %-22s %d" % (k, n))
        print("\n共 %d 个文件。示例：scripts/idx.py 车站 任务" % len(rows))
        return 0

    hits = []
    for path, d, desc in rows:
        if dom and dom not in d:
            continue
        blob = (path + "\t" + d + "\t" + desc).lower()
        if all(k.lower() in blob for k in kws):
            hits.append((path, d, desc))
    print("命中 %d 个文件%s：" % (len(hits), ("（域含 %s）" % dom) if dom else ""))
    for path, d, desc in hits:
        print("  %-46s [%s] %s" % (path, d, desc[:80]))
    if len(hits) > 25:
        print("… 共 %d 个，建议加关键词或指定功能域" % len(hits))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
