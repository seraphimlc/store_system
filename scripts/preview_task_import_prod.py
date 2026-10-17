# -*- coding: utf-8 -*-
"""线上只读预览：三份「执行计划」导进去会新建多少 / 跳过多少 / 对不上多少。

为什么要有这个脚本：三份文件的「负责队」列**全是「待分配」**，而
`bd_task_import.build_plan()` 对"认不出队"的行一律 `skip`（整份都没队才拒收）
→ 想知道真实影响（哪些站已有任务、哪些站名对不上），就必须让分类跑完。
这里把 `resolve_team` 打个补丁，把「待分配」当成一个伪队名（id=0），
**只读，不写任何表**。
"""
import os
import sys

from app.db import SessionLocal
from app.services import bd_task_import as TI

FILES = [
    ("千叶B线(陈嘉溢)", "/tmp/tp/b.xlsx"),
    ("千叶C线(陈嘉溢)", "/tmp/tp/c.xlsx"),
    ("埼玉A线(甘子杰)", "/tmp/tp/a.xlsx"),
]

_orig = TI.resolve_team


def _patched(db, raw):
    if (raw or "").strip() in ("待分配", "未分配", "待派", "未派"):
        return (-1, raw.strip())
    return _orig(db, raw)


TI.resolve_team = _patched

total = {"create": 0, "existing": 0, "bad": 0, "skip": 0}
db = SessionLocal()
for label, path in FILES:
    if not os.path.exists(path):
        print("缺文件:", path)
        continue
    print("=" * 90)
    print(label, path)
    try:
        plan = TI.build_plan(db, path)
    except Exception as e:  # noqa: BLE001
        print("  解析失败:", type(e).__name__, e)
        continue
    lay = plan["layout"]
    s = plan["summary"]
    print("  表头行=%s  列映射=%s" % (lay.get("header_row"),
          {k: v for k, v in lay.items() if k != "n_cols"}))
    print("  读到 %d 行 → 新建 %d 行 / %d 个站；已有任务 %d 个；跳过 %d 行；对不上 %d 行"
          % (s["rows"], s["create_rows"], s["create_stations"], s["existing"],
             s["skipped"], s["bad"]))
    print("  认到的队名:", plan["team_names_found"])
    bad = [r for r in plan["rows"] if r.decision == "bad"]
    for r in bad[:30]:
        print("    ✗ 行%d %s | %s | %s | 近似: %s"
              % (r.row_no, r.line, r.station_raw, r.missing,
                 "、".join(r.near or [])))
    if len(bad) > 30:
        print("    … 还有 %d 行对不上" % (len(bad) - 30))
    reasons = {}
    for r in plan["rows"]:
        if r.decision == "skip":
            k = (r.skip_reason or "")[:40]
            reasons[k] = reasons.get(k, 0) + 1
    print("  跳过原因分布:", reasons)
    total["create"] += s["create_stations"]
    total["existing"] += s["existing"]
    total["bad"] += s["bad"]
    total["skip"] += s["skipped"]
print("=" * 90)
print("三份合计：新建 %d 个站 / 已有任务 %d 个 / 对不上 %d 行 / 跳过 %d 行"
      % (total["create"], total["existing"], total["bad"], total["skip"]))
db.close()
sys.stdout.flush()
