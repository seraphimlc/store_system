# -*- coding: utf-8 -*-
"""**日期计划演示数据**（本地演示库专用，可反复跑）。

用法（cwd = 项目根）：

```bash
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/seed_date_plan_demo.py
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/seed_date_plan_demo.py --preview 2026-10-04
DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/seed_date_plan_demo.py --clean
```

造的场景（覆盖所有口径）：

| # | 员工 | 10 月上半月计划 | 自报 | 期望 |
|---|---|---|---|---|
| 1 | A | 已登记，全可出勤 | 10-01 自报 | □ + 实色 ○（已登记） |
| 2 | B | 已登记，10-06/07 不出勤 | — | × × |
| 3 | C | **未登记** | — | – （未提交计划） |
| 4 | D | **未登记** | 10-01 自报 | □ + –（**只自报不算已登记**） |
| 5 | E | 已登记，全可出勤 | — | 今天视角 ○；**10-04 视角变 ×** |
| 6 | 演示账号 | 未登记（留着让你自己点着填） | — | 员工端可点选 |

另在**9 月下半月**（已过去、窗口早关）造两人：一个有自报（□）、一个计划出勤没自报（×）。

> 9 月的自报走不了 `save_by_admin`（≤ 正式数据最后一天 = 已对账锁定），所以那几条是
> **直接插自报表 + 手动调 `date_plan.mark_reported`**（等价于写路径的写透）。
> 10 月的自报走**真实路径** `daily_report.save_by_admin`。
"""
import argparse
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# **安全闸门必须放在 import app.* 之前**：app.db 在 import 时就按 DATABASE_URL 建引擎，
# 生产是 PG，先 import 会在连库阶段就炸（而不是给出人话提示）。
if not os.environ.get("DATABASE_URL", "").startswith("sqlite"):
    sys.exit("拒绝执行：本脚本只用于本地演示库（DATABASE_URL 必须是 sqlite）。\n当前：%s"
             % os.environ.get("DATABASE_URL", "<未设置>"))

from app.auth import hash_password                      # noqa: E402
from app.db import SessionLocal                         # noqa: E402
from app.models import (Person, StaffDailyReport, StaffDatePlan,  # noqa: E402
                        User)
from app.services import daily_report as dr             # noqa: E402
from app.services import date_plan as dp                # noqa: E402

H1 = "2026-10-H1"
SEP_H2 = "2026-09-H2"
DEMO_CODE = "DP-DEMO-01"
DEMO_LOGIN = "demo-plan"
DEMO_PW = "demo12345"


class _U:
    """假的"当前用户"（save_plan 只用到 person_code）。"""

    def __init__(self, code):
        self.person_code = code
        self.id = None


def _pick_codes(db, n=5):
    """挑 n 个在岗员工账号（编号排序，保证每次一样）。"""
    rows = (db.query(User.person_code)
            .filter(User.role == "staff", User.is_active.is_(True),
                    User.person_code.isnot(None))
            .order_by(User.person_code).limit(n).all())
    return [c for (c,) in rows if c]


def clean(db) -> None:
    """删掉本脚本造的数据（演示账号、10 月 H1/9 月 H2 的计划行与自报行）。"""
    codes = _pick_codes(db, 5) + [DEMO_CODE]
    for key in (H1, SEP_H2):
        s, e, _ = dp.period_bounds(key)
        db.query(StaffDatePlan).filter(
            StaffDatePlan.person_code.in_(codes),
            StaffDatePlan.plan_date >= s, StaffDatePlan.plan_date <= e).delete(
                synchronize_session=False)
        db.query(StaffDailyReport).filter(
            StaffDailyReport.person_code.in_(codes),
            StaffDailyReport.report_date >= s,
            StaffDailyReport.report_date <= e).delete(synchronize_session=False)
        # 9 月演示用的另外两个人（第 6/7 个账号）
        others = _pick_codes(db, 7)[5:]
        db.query(StaffDatePlan).filter(
            StaffDatePlan.person_code.in_(others),
            StaffDatePlan.plan_date >= s, StaffDatePlan.plan_date <= e).delete(
                synchronize_session=False)
        db.query(StaffDailyReport).filter(
            StaffDailyReport.person_code.in_(others),
            StaffDailyReport.report_date >= s,
            StaffDailyReport.report_date <= e).delete(synchronize_session=False)
    u = db.query(User).filter(User.username == DEMO_LOGIN).first()
    if u is not None:
        db.delete(u)
    p = db.get(Person, DEMO_CODE)
    if p is not None and (p.display_name or "").startswith("出勤计划演示"):
        db.delete(p)
    db.commit()


def seed(db) -> dict:
    codes = _pick_codes(db, 7)
    a, b, c, d, e = codes[:5]
    f, g = codes[5:7]
    out = {"A": a, "B": b, "C": c, "D": d, "E": e, "F_9月": f, "G_9月": g}
    names = dict(db.query(Person.code, Person.display_name).all())

    # ---- 10 月上半月：窗口 09-24 ~ 10-03，用 10-01 作为"填表那天" ----
    fill_day = date(2026, 10, 1)
    dp.save_plan(db, _U(a), H1, [], today=fill_day)                    # A 全可出勤
    dp.save_plan(db, _U(b), H1, ["2026-10-06", "2026-10-07"], today=fill_day)
    dp.save_plan(db, _U(e), H1, [], today=fill_day)
    # C / D / 演示账号：**故意不登记**

    # 10-01 的自报：A（登记过）与 D（没登记 → 只自报）
    for code in (a, d):
        dr.save_by_admin(db, person_code=code, report_date=date(2026, 10, 1),
                         area="名古屋", p1_cnt=3, p2_cnt=1, user_id=None)

    # ---- 9 月下半月：已过去（窗口 09-09~09-18 早关），演示"过去看事实" ----
    dp.save_plan(db, _U(f), SEP_H2, [], today=date(2026, 9, 15))       # F 全可出勤
    dp.save_plan(db, _U(g), SEP_H2, [], today=date(2026, 9, 15))       # G 全可出勤
    # F 在 9-20 自报过（□）；G 没有（→ ×）
    db.add(StaffDailyReport(person_code=f, report_date=date(2026, 9, 20),
                            area="名古屋", p1_cnt=2, p2_cnt=1, total_cnt=3,
                            source="admin"))
    db.commit()
    dp.mark_reported(db, f, date(2026, 9, 20), True)

    # ---- 员工端演示账号（编号 DP-DEMO-01，方便登录看点半月表）----
    if db.get(Person, DEMO_CODE) is None:
        db.add(Person(code=DEMO_CODE, display_name="出勤计划演示"))
    if db.query(User).filter(User.username == DEMO_LOGIN).first() is None:
        db.add(User(username=DEMO_LOGIN, password_hash=hash_password(DEMO_PW),
                    display_name="出勤计划演示", role="staff", person_code=DEMO_CODE,
                    is_active=True, status="active", must_change_password=False))
    db.commit()
    out["names"] = names
    return out


def _line(label, marks):
    return "  %-14s %s" % (label, " ".join(marks))


def report(db, today=None) -> None:
    codes = _pick_codes(db, 7)
    names = dict(db.query(Person.code, Person.display_name).all())
    today = today or dp.jst_today()
    for key in (H1, SEP_H2):
        m = dp.admin_matrix(db, key, today=today)
        print("\n=== 管理端矩阵 %s（今天 = %s，窗口 %s ~ %s）==="
              % (dp.period_label(key), today, m["open_at"], m["close_at"]))
        print(_line("", [d.strftime("%m-%d") for d in m["days"]]))
        for r in m["rows"]:
            if r["person_code"] not in codes and r["person_code"] != DEMO_CODE:
                continue
            tag = names.get(r["person_code"]) or r["person_code"]
            if r["person_code"] == DEMO_CODE:
                tag = "演示账号"
            print(_line(tag, [r["marks"][d] for d in m["days"]])
                  + ("   [未提交计划]" if not r["submitted"] else ""))
        print(_line("可出勤/默认", ["%s/%s" % (
            (m["free_cnt"][d] if not m["past"][d] else "-"),
            (m["default_cnt"][d] if not m["past"][d] else "-")) for d in m["days"]]))
        print("  未提交计划 %d / %d 人；未登记人数(今天起) %s"
              % (len(m["unsubmitted"]), m["total"],
                 [m["none_cnt"][d] for d in m["days"] if not m["past"][d]]))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clean", action="store_true", help="清掉演示数据")
    ap.add_argument("--preview", default="", help="用注入的今天打印矩阵，如 2026-10-04（不改数据）")
    ap.add_argument("--no-seed", action="store_true", help="只用来看现状，不造数据")
    args = ap.parse_args()
    db = SessionLocal()
    try:
        if args.clean:
            clean(db)
            print("已清掉日期计划演示数据（演示账号 + 10 月 H1 / 9 月 H2 的计划与自报）")
            return
        if not args.no_seed:
            info = seed(db)
            print("演示数据已写入：A=%s  B=%s  C=%s（未登记） D=%s（只自报） E=%s"
                  % (info["A"], info["B"], info["C"], info["D"], info["E"]))
            print("9 月下半月演示：F=%s（有自报） G=%s（计划出勤没自报）"
                  % (info["F_9月"], info["G_9月"]))
            print("员工端演示账号：登录名 %s / 密码 %s（编号 %s）"
                  % (DEMO_LOGIN, DEMO_PW, DEMO_CODE))
        report(db)
        if args.preview:
            y, mo, dd = (int(x) for x in args.preview.split("-"))
            report(db, today=date(y, mo, dd))
    finally:
        db.close()


if __name__ == "__main__":
    main()
