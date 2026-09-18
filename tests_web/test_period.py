# -*- coding: utf-8 -*-
"""月度分期对账偏差表（薪资找平）：生成公式/上月修正递延/手改保留/页面。"""
from datetime import date, datetime

import app.db as appdb
from app.models import (PayrollPeriodRow, Person, PersonDailyStat,
                        ReconDataRow, ReconTask)
from app.services import period


def test_sync_formula_and_prev_adjust(client):
    """偏差 = 对账 − (上半月+下半月) + 上月修正；找平自动=金额差；上月修正=链式余额。"""
    db = appdb.SessionLocal()
    db.add(Person(code="111", display_name="甲"))
    # 上月（7月）结转余额 -500（要扣，上月工资没扣完）；金额差=500
    db.add(PayrollPeriodRow(month="2026-07", person_code="111",
                            half1_points=0, half2_points=0,
                            settle_points=0, prev_adjust_points=0,
                            diff_points=2, adjust_points=2,
                            diff_amount=500, adjust_amount=500,
                            prev_adjust_amount=-500,
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
    res = period.sync_period_table(db, "2026-08")
    assert res["rows"] == 1
    rows = period.period_rows(db, "2026-08")
    r = rows[0]
    assert r["half1"] == 1 and r["half2"] == 3      # 上半月1 / 下半月3
    assert r["settle"] == 10                          # 对账点数（全量）
    assert r["prev"] == 0                       # 上月余量点数(参考；已自动找平→0)
    assert r["prev_amt"] == -500                 # 上月结转余额-500(要扣)经链式递延
    assert r["diff"] == 10 - (1 + 3) + 0 == 6   # 偏差点数(参考；上月余量0)
    assert r["diff_amt"] == 2500 - (250 + 750) == 1500  # 金额差(含奖金)=对账−系统
    assert r["adj_amt"] == r["diff_amt"] == 1500  # 找平自动=金额差(无点击)
    db.close()


def test_prev_adjust_chain_carry(client):
    """链式递延：本月结转 = −本月金额差 + 本月两期工资吸收上月结转后的剩余(<0才递延)。"""
    db = appdb.SessionLocal()
    db.add(Person(code="A", display_name="甲"))
    db.add(Person(code="B", display_name="乙"))
    # 上月结转：A=-25,000(要扣,本月工资吸收不完)、B=0(无结转)
    db.add(PayrollPeriodRow(month="2026-07", person_code="A",
                            diff_points=100, adjust_points=100,
                            diff_amount=100 * 250, adjust_amount=100 * 250,
                            half1_amount=10000, half2_amount=0,
                            prev_adjust_amount=-25000,
                            updated_at=datetime.utcnow()))
    db.add(PayrollPeriodRow(month="2026-07", person_code="B",
                            diff_points=100, adjust_points=100,
                            diff_amount=100 * 250, adjust_amount=100 * 250,
                            half1_amount=10000, half2_amount=20000,
                            prev_adjust_amount=0,
                            updated_at=datetime.utcnow()))
    for code in ("A", "B"):
        db.add(PersonDailyStat(person_code=code,
                               ref_date=date(2026, 8, 10), points=10))
    db.commit()
    period.sync_period_table(db, "2026-08")
    rows = {r["code"]: r for r in period.period_rows(db, "2026-08")}
    # 8月：两期=2,500、diff=+2,500；A 上月结转-25,000 吸收2,500后剩-22,500
    #   → 结转9月 = −2,500 + (−22,500) = −25,000；B 无结转 → 结转9月 = −2,500
    assert rows["A"]["prev_amt"] == -25000        # 上月修正列=上月结转
    assert rows["B"]["prev_amt"] == 0             # B 无结转
    assert rows["A"]["adj_amt"] == rows["A"]["diff_amt"] == -2500  # 负=扣款
    # carry_map（发薪表上月找平列）：上月结转余额（正补/负扣）
    cm = period.carry_map(db, "2026-08")
    assert cm["A"] == [0, -25000] and cm.get("B") is None
    db.close()


def test_adjust_auto_equals_diff_amount(client):
    """找平自动=金额差（无点击操作）：生成后 adj_amt==diff_amt，重新生成保持。"""
    db = appdb.SessionLocal()
    db.add(Person(code="111", display_name="甲"))
    db.add(PersonDailyStat(person_code="111", ref_date=date(2026, 8, 1),
                           points=5))
    db.commit()
    period.sync_period_table(db, "2026-08")
    rows = period.period_rows(db, "2026-08")
    assert rows[0]["adj_amt"] == rows[0]["diff_amt"] == -5 * 250  # 负=扣款
    assert rows[0]["diff"] == -5                       # 偏差点数=参考值
    period.sync_period_table(db, "2026-08")         # 重新生成
    rows = period.period_rows(db, "2026-08")
    assert rows[0]["adj_amt"] == rows[0]["diff_amt"] == -5 * 250  # 负=扣款  # 自动保持
    db.close()


def test_payroll_settle_page_readonly(client):
    """薪资找平页面只读：找平自动=金额差，无输入框/保存（去掉点击操作）。"""
    from tests_web.test_flow import _seed_admin
    _seed_admin(client)
    db = appdb.SessionLocal()
    db.add(Person(code="111", display_name="甲"))
    db.add(PersonDailyStat(person_code="111", ref_date=date(2026, 8, 1),
                           points=5))
    db.commit()
    period.sync_period_table(db, "2026-08")
    db.close()
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    p = client.get("/payroll-settle?month=2026-08").text
    assert "薪资找平" in p and "本月对账偏差(参考)=找平" in p and "生成/更新" in p
    assert "本月对账偏差(参考)" in p
    assert 'name="adjust_delta"' not in p               # 无找平输入框（自动）
    assert "即自动找平金额" in p                        # 自动找平说明
    pp = client.get("/perf?month=2026-08").text
    assert "奖金合计(円)" not in pp and "导出薪资找平 Excel" not in pp
    db.close()


def test_per_point_lock_and_change(client):
    """单价按月可变：设置本月单价只影响本月；已确认找平按当时单价不变。"""
    import app.db as appdb
    from app.models import (Person, PersonDailyStat, AdjustRecord)
    from app.services import period, perf
    db = appdb.SessionLocal()
    from app.models import MonthPerfRecord
    db.add(Person(code="111", display_name="甲"))
    db.add(PersonDailyStat(person_code="111", ref_date=date(2026, 8, 1),
                           points=80))
    # 直插一条月绩效行（绕开 formal 依赖），作为该月单价载体
    db.add(MonthPerfRecord(month="2026-08", person_code="111",
                           records=40, p1=40, p2=0, points=80,
                           salary=perf.salary_for(80), per_point=250))
    db.commit()
    sync1 = period.sync_period_table(db, "2026-08")
    rows = period.period_rows(db, "2026-08")
    r = rows[0]
    # 默认单价 250：下半月 0，上半月 80 点 → 分期=80×250+3000=23000
    assert r["half1_amt"] == 80 * 250 + 3000
    # 8月确认一条找平（锁存当时单价250）
    per_point = perf.month_per_point(db, "2026-08")
    assert per_point == 250
    rec = AdjustRecord(month="2026-08", applied_to_month="2026-09",
                       person_code="111", amount=10, per_point=per_point,
                       source_task_id=0, reason="测试")
    db.add(rec); db.commit()
    # 设置 8 月单价为 300：月绩效工资与薪资找平重算，但找平记录仍 250
    perf.set_month_per_point(db, "2026-08", 300)
    rows = period.period_rows(db, "2026-08")
    assert rows[0]["half1_amt"] == 80 * 300 + 3000   # 按新单价重算
    mp = db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == "2026-08").first()
    assert mp.per_point == 300 and mp.salary == 80 * 300 + 3000
    rec = db.query(AdjustRecord).first()
    assert rec.per_point == 250 and rec.amount == 10   # 锁存不动
    assert perf.adjust_map(db, "2026-09") == {"111": [10, 10 * 250]}
    db.close()


def test_bonus_configurable(client, monkeypatch):
    """奖金门槛/金额可配置：改 BONUS_GROUP/BONUS_AMOUNT 后工资按新值算。"""
    import app.config as cfg
    from app.services import perf
    monkeypatch.setenv("BONUS_GROUP", "50")
    monkeypatch.setenv("BONUS_AMOUNT", "2000")
    cfg.get_settings.cache_clear()
    try:
        assert perf.salary_for(100) == 100 * 250 + 2 * 2000   # 100//50=2
        g, a = perf.bonus_params()
        assert (g, a) == (50, 2000)
        g9, _ = perf.bonus_params("2026-09")       # 9月起按月规则 75
        assert g9 == 75
        g8, _ = perf.bonus_params("2026-08")       # 8月未命中按月规则 → 50
        assert g8 == 50
    finally:
        monkeypatch.delenv("BONUS_GROUP", raising=False)
        monkeypatch.delenv("BONUS_AMOUNT", raising=False)
        cfg.get_settings.cache_clear()
    assert perf.salary_for(100) == 100 * 250 + 1 * 3000       # 恢复默认 68/3000
    assert perf.salary_for(160, month="2026-09") == 160 * 250 + 2 * 1250  # 9月起门槛75奖1250
    assert perf.salary_for(150, month="2026-08") == 150 * 250 + 2 * 3000  # 8月仍68(150//68=2)


def test_sys_config_chain(client):
    """系统配置表驱动计算：warm_config 后 bonus_params/month_per_point 用配置值。"""
    import app.db as appdb
    from app.models import SysConfig
    from app.services import perf
    db = appdb.SessionLocal()
    db.add(SysConfig(config_month="2026-08", per_point=250, bonus_group=68,
                     bonus_amount=3000, updated_by=1))
    db.add(SysConfig(config_month="2026-09", per_point=250, bonus_group=75,
                     bonus_amount=1250, updated_by=1))
    db.commit()
    perf.clear_config_cache()
    perf.warm_config(db, "2026-09")
    assert perf.bonus_params("2026-09") == (75, 1250)
    assert perf.month_per_point(db, "2026-09") == 250
    assert perf.salary_for(160, 250, "2026-09") == 160 * 250 + 2 * 1250
    perf.warm_config(db, "2026-08")
    assert perf.bonus_params("2026-08") == (68, 3000)
    # 未配置月份 → env 默认
    assert perf.salary_for(160, 250, "2026-07") == 160 * 250 + 2 * 3000
    perf.clear_config_cache()
    db.close()
