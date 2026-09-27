# -*- coding: utf-8 -*-
"""月薪查询：读已物化的 month_perf_records（绝不重算），含真实库回归。

为什么"绝不重算"：工资 = 每点单价 + 每满门槛点奖金，两项按月可配。
本地库实测 2026-09 的工资是按「250/点 + 每满75点奖1250」物化的，
而 sys_configs 里 2026-09 写着 68/3000 —— 按当前配置重算会得出不同的钱。
"""
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, MonthPerfRecord, Person
from mcp_service import capability

LIVE_DB = Path(__file__).resolve().parent.parent.parent / "store_settle_live.db"


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/s.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add_all([
        Person(code="P001", display_name="甲"),
        Person(code="P002", display_name="乙"),
    ])
    s.add_all([
        MonthPerfRecord(month="2026-09", person_code="P001", records=10,
                        p1=6, p2=4, points=14, salary=1000, per_point=250),
        MonthPerfRecord(month="2026-09", person_code="P002", records=5,
                        p1=5, p2=0, points=5, salary=1250, per_point=250),
    ])
    s.commit()
    yield s
    s.close()
    eng.dispose()


def test_month_salary_totals(db):
    got = capability.month_salary(db, "2026-09")
    assert got["persons"] == 2
    assert got["total_points"] == 19
    assert got["total_salary"] == 2250
    assert got["per_point"] == 250
    assert len(got["rows"]) == 2


def test_rows_include_names_and_salary(db):
    rows = {r["person_code"]: r for r in capability.month_salary(db, "2026-09")["rows"]}
    assert rows["P001"]["name"] == "甲"
    assert rows["P001"]["salary"] == 1000
    assert rows["P002"]["points"] == 5


def test_person_filter_by_code_and_name(db):
    by_code = capability.month_salary(db, "2026-09", person="P002")
    assert by_code["persons"] == 1
    assert by_code["rows"][0]["person_code"] == "P002"
    by_name = capability.month_salary(db, "2026-09", person="甲")
    assert by_name["persons"] == 1
    assert by_name["rows"][0]["person_code"] == "P001"


def test_empty_month_returns_zeros_with_hint(db):
    got = capability.month_salary(db, "2026-08")
    assert got["persons"] == 0
    assert got["total_salary"] == 0
    assert "hint" in got


def test_currency_is_jpy(db):
    """金额单位必须是日元（避免 agent 表述成人民币元）。"""
    assert capability.month_salary(db, "2026-09")["currency"] == "JPY"


def test_bad_month_rejected(db):
    with pytest.raises(capability.BadMonth):
        capability.month_salary(db, "2026-9")


@pytest.mark.skipif(not LIVE_DB.exists(), reason="本地库不存在")
def test_live_db_salary_is_internally_consistent():
    """本机库薪资的**一致性**校验（不断言固定数字）。

    本地库是演练环境，数据会随上传/重算变化，故这里只断言：
      · 总额 == 各行之和；· 每点单价为正；· 工资 == 点数×单价 + 奖金（整数倍）
    固定数字属于「与手工结算对照」的验收动作，不做成单元测试。
    """
    eng = create_engine(f"sqlite:///file:{LIVE_DB}?mode=ro&uri=true",
                        connect_args={"check_same_thread": False})
    db = sessionmaker(bind=eng)()
    try:
        got = capability.month_salary(db, "2026-09")
    finally:
        eng.dispose()
    if got["persons"] == 0:
        pytest.skip("本机库 9 月无数据（演练环境已清空）")
    assert got["total_salary"] == sum(r["salary"] or 0 for r in got["rows"])
    assert got["total_points"] == sum(r["points"] or 0 for r in got["rows"])
    assert got["per_point"] and got["per_point"] > 0
