# -*- coding: utf-8 -*-
"""只读诊断：dashboard 到底慢在哪 + 分析缓存指纹是否命中。不写任何表。"""
import hashlib
import time

from app.db import SessionLocal
from app.models import StaffAnalysis
from app.services import dashboard as D

db = SessionLocal()


def t(label, fn):
    t0 = time.perf_counter()
    try:
        r = fn()
    except Exception as e:                                    # noqa: BLE001
        print(f"{label:34s} ERROR {type(e).__name__}: {e}")
        return None
    dt = (time.perf_counter() - t0) * 1000
    print(f"{label:34s} {dt:9.1f} ms")
    return r


print("=== /dashboard 路由里逐个调用 ===")
ms = t("monthly_series_from_db", lambda: D.monthly_series_from_db(db))
if ms:
    sel = ms[-1]["month"]
    print(f"   （月份数={len(ms)} 最新={sel}）")
    t("month_has_metrics", lambda: D.month_has_metrics(db, sel))
    t("svg_line x4", lambda: [D.svg_line([m["points"] for m in ms], ["x"] * len(ms)) for _ in range(4)])
    t("staff_options", lambda: D.staff_options(db))
    t("headcount_changes", lambda: D.headcount_changes(db))
    t("month_payloads", lambda: D.month_payloads(db, sel))
    t("top_staff", lambda: D.top_staff(db, sel, 8))
    t("staff_changes", lambda: D.staff_changes(db, sel))
    t("quality_stats", lambda: D.quality_stats(db, sel))

print()
print("=== fact_text（/dashboard 与 /dashboard/analysis 各算一次）===")
facts = t("fact_text(db, None)", lambda: D.fact_text(db, None))
if facts:
    fp = hashlib.md5(facts.encode("utf-8")).hexdigest()
    print("   facts 长度 =", len(facts), " 指纹 =", fp[:12])
    row = db.query(StaffAnalysis).filter(
        StaffAnalysis.person_code == "COMPANY",
        StaffAnalysis.month == "ALL").first()
    if row is None:
        print("   >>> 缓存行不存在 → 每次进页面都会**实时调 AI**")
    else:
        print("   库里指纹 =", (row.fingerprint or "")[:12],
              " 内容长度 =", len(row.content or ""))
        print("   >>> 指纹命中（走缓存）=", row.fingerprint == fp)
    # 指纹稳定性：同样的数据算两次必须一样，否则缓存永远不命中
    fp2 = hashlib.md5(D.fact_text(db, None).encode("utf-8")).hexdigest()
    print("   指纹稳定（连算两次一致）=", fp == fp2)

print()
print("=== monthly_series vs monthly_series_from_db（图表 vs 喂给模型的数）===")
a = t("monthly_series", lambda: D.monthly_series(db))
if a and ms:
    for x, y in zip(a, ms):
        same = all(abs((x.get(k) or 0) - (y.get(k) or 0)) < 1e-6
                   for k in ("employees", "records", "points", "amount"))
        if not same:
            print(f"   ⚠️ {x['month']} 不一致：")
            for k in ("employees", "records", "p1", "p2", "points", "amount"):
                if (x.get(k) or 0) != (y.get(k) or 0):
                    print(f"        {k}: monthly_series={x.get(k)}  from_db={y.get(k)}")
db.close()
