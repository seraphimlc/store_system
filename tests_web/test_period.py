# -*- coding: utf-8 -*-
"""月度分期对账偏差表（薪资找平）：生成公式/上月修正递延/手改保留/页面。"""
from datetime import date, datetime

import app.db as appdb
from app.models import (PayrollPeriodRow, Person, PersonDailyStat,
                        ReconDataRow, ReconTask)
from app.services import v3_period


def test_sync_formula_and_prev_adjust(client):
    """偏差 = 对账 − (上半月+下半月) + 上月修正；上月修正取上月偏差。"""
    db = appdb.SessionLocal()
    db.add(Person(code="111", display_name="甲"))
    # 上月（7月）偏差2、找平0 → 未找平余量=2（递延源）；金额差=2×250=500
    db.add(PayrollPeriodRow(month="2026-07", person_code="111",
                            half1_points=0, half2_points=0,
                            settle_points=0, prev_adjust_points=0,
                            diff_points=2, adjust_points=0,
                            diff_amount=500, adjust_amount=0,
                            updated_at=datetime.utcnow()))
    # 8月统计表：8/1=1点(上半月)，8/16=2点+8/20=1点(下半月3点)
    for d, pts in ((date(2026, 8, 1), 1), (date(2026, 8, 16), 2),
                   (date(2026, 8, 20), 1)):
        db.add(PersonDailyStat(person_code="111", ref_date=d, points=pts))
    # 8月对账任务（当前）+ 对账数据表 甲=10 点
    t = ReconTask(kind="monthly_v3", status="done", created_by=1,
                  params={"month": "2026-08", "file": "t.xlsx"})
    db.add(t)
    db.commit()
    db.add(ReconDataRow(task_id=t.id, ref_date=date(2026, 8, 1),
                        person_code="111", person_name="甲", points=10, cnt=1))
    db.commit()
    res = v3_period.sync_period_table(db, "2026-08")
    assert res["rows"] == 1
    rows = v3_period.period_rows(db, "2026-08")
    r = rows[0]
    assert r["half1"] == 1 and r["half2"] == 3      # 上半月1 / 下半月3
    assert r["settle"] == 10                          # 对账点数（全量）
    assert r["prev"] == 2                       # 上月修正=上月余量(偏差2-找平0)
    assert r["prev_amt"] == 500                 # 上月金额余量=上月金额差−已找平金额
    assert r["diff"] == 10 - (1 + 3) + 2 == 8   # 偏差点数(参考)
    assert r["diff_amt"] == (250 + 750) - 2500 == -1500  # 金额差(含奖金)=系统已发−对账金额
    db.close()


def test_prev_adjust_is_remainder(client):
    """递延=未找平余量：上月偏差100、找平100→余量0；找平50→余量50。"""
    db = appdb.SessionLocal()
    db.add(Person(code="A", display_name="甲"))
    db.add(Person(code="B", display_name="乙"))
    # 上月：A 偏差100 找平100（余量0）；B 偏差100 找平50（余量50）
    db.add(PayrollPeriodRow(month="2026-07", person_code="A",
                            diff_points=100, adjust_points=100,
                            diff_amount=100 * 250, adjust_amount=100 * 250,
                            updated_at=datetime.utcnow()))
    db.add(PayrollPeriodRow(month="2026-07", person_code="B",
                            diff_points=100, adjust_points=50,
                            diff_amount=100 * 250, adjust_amount=50 * 250,
                            updated_at=datetime.utcnow()))
    for code in ("A", "B"):
        db.add(PersonDailyStat(person_code=code,
                               ref_date=date(2026, 8, 10), points=10))
    db.commit()
    v3_period.sync_period_table(db, "2026-08")
    rows = {r["code"]: r for r in v3_period.period_rows(db, "2026-08")}
    assert rows["A"]["prev"] == 0 and rows["A"]["prev_amt"] == 0      # 全找平→0
    assert rows["B"]["prev"] == 50 and rows["B"]["prev_amt"] == 12500  # 余50→递延50×250
    # carry_map（/perf 上月找平列来源）同源
    cm = v3_period.carry_map(db, "2026-08")
    assert cm.get("A") is None and cm["B"] == [50, 12500]
    db.close()


def test_manual_adjust_preserved_and_diff_auto(client):
    """找平=人工执行值（重新生成保留）；偏差=系统参考值（重新生成自动刷）。"""
    db = appdb.SessionLocal()
    db.add(Person(code="111", display_name="甲"))
    db.add(PersonDailyStat(person_code="111", ref_date=date(2026, 8, 1),
                           points=5))
    db.commit()
    v3_period.sync_period_table(db, "2026-08")
    # 未编辑时：找平默认 0
    rows = v3_period.period_rows(db, "2026-08")
    assert rows[0]["adj"] == 0 and rows[0]["adj_amt"] == 0
    # 保存找平 -99（金额）→ 找平金额=-99（按金额修正，不按点数）
    v3_period.set_period_diff(db, "2026-08", "111", -99, 1)
    v3_period.sync_period_table(db, "2026-08")        # 重新生成
    rows = v3_period.period_rows(db, "2026-08")
    assert rows[0]["adj_amt"] == -99                   # 找平金额保留（增量累计）
    assert rows[0]["diff"] == -5                       # 偏差点数=参考值自动算回公式
    assert rows[0]["diff_amt"] == 5 * 250             # 金额差(含奖金)=系统已发5×250−对账0
    # delta 累计语义：再存 +99 → 总找平归零（输入框剩余=金额差-已找平金额）
    v3_period.set_period_diff(db, "2026-08", "111", 99, 1)   # 撤销
    rows = v3_period.period_rows(db, "2026-08")
    assert rows[0]["adj_amt"] == 0
    db.close()


def test_payroll_settle_page_and_edit(client):
    """薪资找平页面可看、可编辑「找平」。"""
    from tests_web.test_v3_flow import _seed_admin
    _seed_admin(client)
    db = appdb.SessionLocal()
    db.add(Person(code="111", display_name="甲"))
    db.add(PersonDailyStat(person_code="111", ref_date=date(2026, 8, 1),
                           points=5))
    db.commit()
    v3_period.sync_period_table(db, "2026-08")
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    p = client.get("/payroll-settle?month=2026-08").text
    assert "薪资找平" in p and "找平(执行)" in p and "生成/更新" in p
    assert "本月对账偏差(参考)" in p
    assert 'name="adjust_delta"' in p                 # 输入框=剩余未找平(偏差−已找平)
    pp = client.get("/perf?month=2026-08").text
    # 主页无薪资找平区块（区块特有元素不出现；导航菜单不算）
    assert "奖金合计(円)" not in pp and "导出薪资找平 Excel" not in pp
    from tests_web.test_v3_flow import _csrf_of  # noqa
    csrf = _csrf_of(client, "/payroll-settle?month=2026-08")
    r = client.post("/payroll-settle/2026-08/111/update",
                    data={"half1_amount": "0", "half2_amount": "0", "adjust_delta": "-5", "csrf_token": csrf},
                    follow_redirects=False)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    rows = v3_period.period_rows(db, "2026-08")
    assert rows[0]["adj_amt"] == -5                    # 找平执行金额（累计，按金额）
    assert rows[0]["diff"] == -5                       # 偏差点数参考值不随保存变
    # 输入框=剩余未找平金额（=金额差−已找平金额 → 1250 − (-5) = 1255）
    z = client.get("/payroll-settle?month=2026-08").text
    assert 'name="adjust_delta"' in z and 'value="1255"' in z
    db.close()


def test_per_point_lock_and_change(client):
    """单价按月可变：设置本月单价只影响本月；已确认找平按当时单价不变。"""
    import app.db as appdb
    from app.models import (Person, PersonDailyStat, AdjustRecord)
    from app.services import v3_period, v3_perf
    db = appdb.SessionLocal()
    from app.models import MonthPerfRecord
    db.add(Person(code="111", display_name="甲"))
    db.add(PersonDailyStat(person_code="111", ref_date=date(2026, 8, 1),
                           points=80))
    # 直插一条月绩效行（绕开 formal 依赖），作为该月单价载体
    db.add(MonthPerfRecord(month="2026-08", person_code="111",
                           records=40, p1=40, p2=0, points=80,
                           salary=v3_perf.salary_for(80), per_point=250))
    db.commit()
    sync1 = v3_period.sync_period_table(db, "2026-08")
    rows = v3_period.period_rows(db, "2026-08")
    r = rows[0]
    # 默认单价 250：下半月 0，上半月 80 点 → 分期=80×250+3000=23000
    assert r["half1_amt"] == 80 * 250 + 3000
    # 8月确认一条找平（锁存当时单价250）
    per_point = v3_perf.month_per_point(db, "2026-08")
    assert per_point == 250
    rec = AdjustRecord(month="2026-08", applied_to_month="2026-09",
                       person_code="111", amount=10, per_point=per_point,
                       source_task_id=0, reason="测试")
    db.add(rec); db.commit()
    # 设置 8 月单价为 300：月绩效工资与薪资找平重算，但找平记录仍 250
    v3_perf.set_month_per_point(db, "2026-08", 300)
    rows = v3_period.period_rows(db, "2026-08")
    assert rows[0]["half1_amt"] == 80 * 300 + 3000   # 按新单价重算
    mp = db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == "2026-08").first()
    assert mp.per_point == 300 and mp.salary == 80 * 300 + 3000
    rec = db.query(AdjustRecord).first()
    assert rec.per_point == 250 and rec.amount == 10   # 锁存不动
    assert v3_perf.adjust_map(db, "2026-09") == {"111": [10, 10 * 250]}
    db.close()


def test_bonus_configurable(client, monkeypatch):
    """奖金门槛/金额可配置：改 BONUS_GROUP/BONUS_AMOUNT 后工资按新值算。"""
    import app.config as cfg
    from app.services import v3_perf
    monkeypatch.setenv("BONUS_GROUP", "50")
    monkeypatch.setenv("BONUS_AMOUNT", "2000")
    cfg.get_settings.cache_clear()
    try:
        assert v3_perf.salary_for(100) == 100 * 250 + 2 * 2000   # 100//50=2
        g, a = v3_perf.bonus_params()
        assert (g, a) == (50, 2000)
    finally:
        monkeypatch.delenv("BONUS_GROUP", raising=False)
        monkeypatch.delenv("BONUS_AMOUNT", raising=False)
        cfg.get_settings.cache_clear()
    assert v3_perf.salary_for(100) == 100 * 250 + 1 * 3000       # 恢复默认 68/3000
