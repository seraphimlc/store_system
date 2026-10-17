# -*- coding: utf-8 -*-
"""月度对账页/报告（2026-10-17 批次）：报告金额口径 + 员工×日明细分页。"""
from datetime import date, datetime

import app.db as appdb
from app.models import (AdjustRecord, MonthPerfRecord, PayrollAdjust, Person,
                        ReconDayRow, ReconTask, User)
from app.services import recon


def test_recon_report_money_uses_carry_map_and_never_crashes(client):
    """🐞 对账报告 Sheet1「应付合计」原来写
    `sum(perf.adjust_map(db, month).values()) * 250`：

    - `adjust_map` 的值是 `[点数, 金额]` **列表** → `sum()` 对列表求和 →
      **一旦有人点过「确认找平」（`adjust_records` 非空）就 TypeError**；
    - 单价还硬编码 250。

    线上 `adjust_records` 长期 0 行，所以只是"还没炸"。现在统一走 `carry_map()`。
    """
    from app.auth import hash_password
    db = appdb.SessionLocal()
    db.add(User(id=1, username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True))
    db.commit()
    # 对账任务（2026-10 月）
    t = ReconTask(kind="monthly_v3", status="done", created_by=1,
                  params={"month": "2026-10", "file": "对账.xlsx"},
                  summary={"compared": 1, "diff_count": 1})
    db.add(t)
    db.commit()
    db.add(Person(code="P1", display_name="甲"))
    # 公司正式口径：1000 円
    db.add(MonthPerfRecord(month="2026-10", person_code="P1", records=1,
                           p1=4, p2=0, points=4, salary=1000, per_point=250))
    # 9 月偏差 −250（仍在回收）→ 10 月「应付合计」应为 1000−250=750
    db.add(PayrollAdjust(source_month="2026-09", person_code="P1",
                         adjust_amount=-250, settled_amount=0, remaining=-250,
                         status="in_progress", updated_at=datetime.utcnow()))
    # 关键：手工「确认找平」留痕（正是让老代码 TypeError 的那张表）
    db.add(AdjustRecord(month="2026-09", applied_to_month="2026-10",
                        person_code="P1", amount=-250, per_point=250,
                        reason="对账差异", source_task_id=t.id,
                        source_points_diff=-1))
    db.commit()

    wb = recon.build_report(db, t.id, "管理员")     # 修前：TypeError
    assert wb is not None
    ws = wb.worksheets[0]
    got = {}
    for row in ws.iter_rows(values_only=True):
        if row and isinstance(row[0], str):
            got[row[0]] = row[1]
    assert got.get("应付合计(円)") == 750, got
    assert got.get("本任务确认找平(点)") == -250, got
    db.close()


def test_recon_day_rows_paginate(client):
    """`/recon` 的员工×日明细原来一次性全渲染（大月上千行）→ 现在真分页。"""
    from tests_web.test_flow import _seed_admin
    _seed_admin(client)
    db = appdb.SessionLocal()
    t = ReconTask(kind="monthly_v3", status="done", created_by=1,
                  params={"month": "2026-09", "file": "对账.xlsx"},
                  # kind=daily_records：模板只在日级模式下渲染「员工×日 明细」表
                  summary={"kind": "daily_records", "compared": 3,
                           "diff_count": 3})
    db.add(t)
    db.commit()
    for i, code in enumerate(("P1", "P2", "P3"), start=1):
        db.add(ReconDayRow(task_id=t.id, ref_date=date(2026, 9, i),
                           person_code=code, sys_points=i, rep_points=0,
                           diff=i, side="local_only"))
    db.commit()
    tid = t.id
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)

    p1 = client.get(f"/recon?task_id={tid}&per=2&page=1").text
    p2 = client.get(f"/recon?task_id={tid}&per=2&page=2").text
    assert "员工编号" in p1, p1[:300]
    assert "共" in p1 and "3" in p1
    assert "P1" in p1 and "P2" in p1 and "P3" not in p1      # 第 1 页 2 行
    assert "P3" in p2 and "P1" not in p2                     # 第 2 页剩 1 行
    assert 'data-testid="pager"' in p1
    # 越界页码自动夹到最后一页（不是空表）
    p99 = client.get(f"/recon?task_id={tid}&per=2&page=99").text
    assert "P3" in p99
