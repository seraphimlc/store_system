# -*- coding: utf-8 -*-
"""员工每日填报 + 区间对比分析报告（规格 v7）。

阶段 1：数据层（两张新表的读写与约束）。后续阶段在此文件继续追加。
"""
from datetime import date

import pytest
from sqlalchemy.exc import IntegrityError

import app.db as appdb
from app.models import Person, StaffDailyReport, StaffReportAnalysis


def _person(db, code="P1", name="甲"):
    if db.get(Person, code) is None:
        db.add(Person(code=code, display_name=name))
        db.commit()
    return code


def test_daily_report_roundtrip(client):
    """填报一条：区域/1点/2点/总数落库，submitted_at 自动填。"""
    db = appdb.SessionLocal()
    code = _person(db)
    db.add(StaffDailyReport(person_code=code, report_date=date(2026, 9, 20),
                            area="渋谷", p1_cnt=3, p2_cnt=2, total_cnt=5))
    db.commit()
    r = db.query(StaffDailyReport).one()
    assert (r.area, r.p1_cnt, r.p2_cnt, r.total_cnt) == ("渋谷", 3, 2, 5)
    assert r.submitted_at is not None
    assert r.source == "web"


def test_daily_report_unique_per_person_day(client):
    """一天一条：同人同日重复插入被唯一约束拦住。"""
    db = appdb.SessionLocal()
    code = _person(db)
    db.add(StaffDailyReport(person_code=code, report_date=date(2026, 9, 20),
                            p1_cnt=1, p2_cnt=0, total_cnt=1))
    db.commit()
    db.add(StaffDailyReport(person_code=code, report_date=date(2026, 9, 20),
                            p1_cnt=9, p2_cnt=9, total_cnt=18))
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
    assert db.query(StaffDailyReport).count() == 1


def test_analysis_roundtrip(client):
    """报告记录：summary/payload 是 JSON，status 默认 pending，tokens 默认 0。"""
    db = appdb.SessionLocal()
    db.add(StaffReportAnalysis(period_start=date(2026, 9, 16),
                               period_end=date(2026, 9, 30),
                               summary={"total": 2},
                               payload={"by_lang": {"zh": {"overall_comment": "ok"}}}))
    db.commit()
    a = db.query(StaffReportAnalysis).one()
    assert a.status == "pending"
    assert a.summary == {"total": 2}
    assert a.payload["by_lang"]["zh"]["overall_comment"] == "ok"
    assert a.ai_tokens == 0 and a.finished_at is None
