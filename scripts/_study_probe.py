# -*- coding: utf-8 -*-
"""只读探测：本地演示库现状（学习用，临时脚本）。"""
import os
import sqlite3

DB = os.environ.get("DB", "store_settle_live.db")
con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
cur = con.cursor()

print("== 表清单（行数） ==")
tabs = [r[0] for r in cur.execute(
    "select name from sqlite_master where type='table' order by name")]
for t in tabs:
    if t.startswith("sqlite_"):
        continue
    try:
        n = cur.execute(f'select count(*) from "{t}"').fetchone()[0]
    except Exception as e:
        n = f"ERR {e}"
    if n:
        print(f"  {t:34s} {n}")

print("\n== formal_records 按月 ==")
for r in cur.execute("""
    select substr(japan_date,1,7) m, count(*) rows, sum(points) pts,
           count(distinct person_code) people
    from formal_records group by m order by m"""):
    print(f"  {r[0]}  行={r[1]:6d}  点数={r[2]:7d}  人数={r[3]}")

print("\n== month_perf_records 按月 ==")
for r in cur.execute("""
    select month, count(*) people, sum(points) pts, sum(salary) salary,
           min(per_point), max(per_point)
    from month_perf_records group by month order by month"""):
    print(f"  {r[0]}  人数={r[1]:3d}  点数={r[2]:7d}  工资={r[3]:10d}  单价={r[4]}~{r[5]}")

print("\n== raw_records 判定分布 ==")
for r in cur.execute("""
    select coalesce(clean_status,'(null)') s, coalesce(filter_reason,'-') f, count(*)
    from raw_records group by s, f order by 3 desc"""):
    print(f"  clean_status={r[0]:16s} filter_reason={r[1]:18s} {r[2]}")

print("\n== imports ==")
for r in cur.execute("""
    select id, substr(file_name,1,40), month_label, status, parsed_rows
    from imports order by id"""):
    print(f"  #{r[0]:3d} {r[1]:42s} {str(r[2]):8s} {r[3]:8s} rows={r[4]}")

print("\n== recon_tasks ==")
for r in cur.execute("select id, kind, status, created_at from recon_tasks order by id"):
    print(f"  #{r[0]:3d} {r[1]:16s} {r[2]:10s} {r[3]}")

print("\n== users / 角色 ==")
for r in cur.execute("select role, status, count(*) from users group by role, status"):
    print(f"  {r[0]:6s} {r[1]:10s} {r[2]}")

print("\n== sys_configs（最新一条） ==")
for r in cur.execute("""select id, config_month, per_point, bonus_group, bonus_amount,
                               staff_visible_from
                        from sys_configs order by id desc limit 3"""):
    print("  ", r)

print("\n== payroll 四表 ==")
for t in ("payroll_period_rows", "payroll_payments", "payroll_adjusts",
          "payroll_settlement_links", "sealed_months"):
    try:
        n = cur.execute(f"select count(*) from {t}").fetchone()[0]
        print(f"  {t:28s} {n}")
    except Exception as e:
        print(f"  {t:28s} ERR {e}")

con.close()
