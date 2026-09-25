# -*- coding: utf-8 -*-
"""对账只读 MCP 工具测试：visit_recon_status / visit_recon_diff / visit_recon_adjust_state。

临时 SQLite（Base.metadata.create_all）造数据，绝不碰真实库。
- 业务层函数直接传临时 session（f(db, ...) 可复用范式）；
- 工具信封层用 monkeypatch 把 recon_ops.SessionLocal 重定向到临时库 factory，
  断言 ok 信封 / BAD_MONTH / NOT_FOUND / BAD_PARAM / INTERNAL / 空态 hint。
"""
import asyncio
import types
from datetime import date

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (AdjustRecord, Base, MonthPerfRecord,
                        PayrollPeriodRow, Person, ReconDataRow, ReconDayRow,
                        ReconResult, ReconTask, User)
from mcp_service import recon_ops


def fake_ctx():
    """无 Authorization 头（stdio 风格）→ actor_from_ctx 直接返回 None。"""
    return types.SimpleNamespace(headers={})


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    """临时库 session factory；并把 recon_ops.SessionLocal 重定向过去。"""
    # 口径确定性：清掉进程级缓存（config 的 lru_cache 与 perf 的 _CONFIG_CACHE
    # 都是全局的，跨测试模块会残留，导致断言漂移）
    from app.config import get_settings
    from app.services import perf as _perf
    get_settings.cache_clear()
    _perf.clear_config_cache()
    engine = create_engine(f"sqlite:///{tmp_path}/r.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    F = sessionmaker(bind=engine, expire_on_commit=False)
    db = F()
    # —— 基础数据 ——
    db.add(User(id=1, username="admin", password_hash="x", role="admin"))
    db.add(Person(code="P001", display_name="甲"))
    db.add(Person(code="P002", display_name="乙"))
    db.add(Person(code="P003", display_name="丙"))
    # —— 对账任务：2026-09 当前版(task1) + 上一版(task2)；2026-10 pending(task3) ——
    db.add_all([
        ReconTask(id=1, kind="monthly_v3", status="done", created_by=1,
                  params={"month": "2026-09", "file": "9月对账.xlsx",
                          "kind": "person_points", "version": 2,
                          "current": True},
                  summary={"kind": "person_points", "compared": 2,
                           "diff_count": 2, "sys_only": ["P003"]}),
        ReconTask(id=2, kind="monthly_v3", status="done", created_by=1,
                  params={"month": "2026-09", "file": "9月旧版.xlsx",
                          "kind": "person_points", "version": 1,
                          "replaced": True, "replaced_by": 1},
                  summary={"kind": "person_points", "compared": 2,
                           "diff_count": 1, "sys_only": []}),
        ReconTask(id=3, kind="monthly_v3", status="pending", created_by=1,
                  params={"month": "2026-10", "file": "10月.xlsx",
                          "kind": "person_points", "version": 1,
                          "current": True},
                  summary={"kind": "person_points", "compared": 0,
                           "diff_count": 0, "sys_only": []}),
    ])
    # —— task1 差异行：P001 人月差异(系统100 vs 对账80)；P002 日级问题行 ——
    db.add_all([
        ReconResult(task_id=1, submitter_code="P001", system_value=100,
                    report_value=80, diff=20, status="diff",
                    family="person_points", confirmed=False,
                    note="甲 点数差异"),
        ReconDayRow(task_id=1, ref_date=date(2026, 9, 1), person_code="P002",
                    sys_points=2, rep_points=0, sys_count=1, rep_count=0,
                    diff=2, side="local_only", note="乙"),
        ReconDataRow(task_id=1, ref_date=date(2026, 9, 1), person_code="P001",
                     person_name="甲", points=80, cnt=40),
        ReconDataRow(task_id=1, ref_date=date(2026, 9, 2), person_code="P002",
                     person_name="乙", points=2, cnt=1),
    ])
    # —— 找平与偏差：P001 已确认 -20 点 × 250 = -5000 円 ——
    db.add(AdjustRecord(month="2026-09", applied_to_month="2026-10",
                        person_code="P001", amount=-20, per_point=250,
                        amount_adj=-5000,
                        reason="对账#1 2026-09 差异 +20 点 · 当时单价 250円/点",
                        source_task_id=1, source_points_diff=20, created_by=1))
    db.add(MonthPerfRecord(month="2026-09", person_code="P001",
                           per_point=250, diff_amount=-5000))
    db.add(MonthPerfRecord(month="2026-09", person_code="P002",
                           per_point=250, diff_amount=0))
    db.add(PayrollPeriodRow(month="2026-09", person_code="P001",
                            diff_amount=-5000, adjust_amount=-5000))
    db.commit()
    db.close()
    monkeypatch.setattr(recon_ops, "SessionLocal", F)
    yield F
    engine.dispose()


# ---------------- visit_recon_status ----------------

def test_status_lists_tasks(factory):
    got = recon_ops.visit_recon_status(fake_ctx())
    assert got["ok"] is True
    data = got["data"]
    assert data["count"] == 3
    assert data["currency"] == "JPY"
    by_id = {t["id"]: t for t in data["tasks"]}
    t1 = by_id[1]
    assert t1["month"] == "2026-09"
    assert t1["status"] == "done"
    assert t1["is_previous"] is False
    assert t1["diff_persons"] == 2        # P001(人月差异) ∪ P002(日级问题行)
    # 与 salary_for 同源推导（不硬编码金额：奖金门槛/奖额按月可配）
    from app.services import perf as _perf
    expect_diff = (_perf.salary_for(80, month="2026-09")
                   - _perf.salary_for(100, month="2026-09"))
    assert t1["diff_amount"] == expect_diff
    assert by_id[2]["is_previous"] is True
    assert by_id[2]["replaced_by"] == 1
    assert by_id[2]["version"] == 1
    assert by_id[3]["month"] == "2026-10"
    assert by_id[3]["status"] == "pending"


def test_status_month_filter(factory):
    data = recon_ops.visit_recon_status(fake_ctx(), month="2026-09")["data"]
    assert data["count"] == 2
    assert {t["id"] for t in data["tasks"]} == {1, 2}


def test_status_empty_month_ok_with_hint(factory):
    got = recon_ops.visit_recon_status(fake_ctx(), month="2026-11")
    assert got["ok"] is True
    data = got["data"]
    assert data["count"] == 0
    assert data["tasks"] == []
    assert "hint" in data


def test_status_bad_month(factory):
    got = recon_ops.visit_recon_status(fake_ctx(), month="2026-9")
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_MONTH"


def test_status_fullwidth_month_rejected(factory):
    """不用 \\d：全角会放行并静默返回 0 行（capability.MONTH_PATTERN 唯一关口）。"""
    got = recon_ops.visit_recon_status(fake_ctx(), month="２０２６-09")
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_MONTH"


# ---------------- visit_recon_diff ----------------

def test_diff_by_task_id(factory):
    got = recon_ops.visit_recon_diff(fake_ctx(), task_id=1)
    assert got["ok"] is True
    data = got["data"]
    assert data["task"]["id"] == 1
    assert data["task"]["month"] == "2026-09"
    assert data["task"]["diff_count"] == 2
    assert data["currency"] == "JPY"
    pd = {r["person_code"]: r for r in data["person_diffs"]}
    assert set(pd) == {"P001"}
    assert pd["P001"]["system_value"] == 100
    assert pd["P001"]["report_value"] == 80
    assert pd["P001"]["diff"] == 20
    # 与 salary_for 同源推导（奖金门槛/奖额按月可配，不硬编码金额）
    from app.services import perf as _perf
    expect_diff = (_perf.salary_for(80, month="2026-09")
                   - _perf.salary_for(100, month="2026-09"))
    assert pd["P001"]["adjust_amount"] == expect_diff
    assert pd["P001"]["adjusted"] is True
    assert len(data["day_diffs"]) == 1
    d = data["day_diffs"][0]
    assert d["person_code"] == "P002"
    assert d["date"] == "2026-09-01"
    assert d["sys_points"] == 2 and d["rep_points"] == 0
    assert d["side"] == "local_only"
    assert [x["person_code"] for x in data["system_only"]] == ["P003"]
    totals = {r["person_code"]: r for r in data["report_totals"]}
    assert totals["P001"]["points"] == 80 and totals["P001"]["cnt"] == 40
    assert totals["P002"]["points"] == 2 and totals["P002"]["name"] == "乙"


def test_diff_by_month_resolves_current_task(factory):
    got = recon_ops.visit_recon_diff(fake_ctx(), month="2026-09")
    assert got["ok"] is True
    data = got["data"]
    # 旧版 task2 被 replaced_by 排除，取最新当前版 task1
    assert data["task"]["id"] == 1
    assert data["person_diffs"][0]["person_code"] == "P001"


def test_diff_task_not_found(factory):
    got = recon_ops.visit_recon_diff(fake_ctx(), task_id=999)
    assert got["ok"] is False
    assert got["error"]["code"] == "NOT_FOUND"
    assert "hint" in got["error"]


def test_diff_month_no_task_ok_with_hint(factory):
    got = recon_ops.visit_recon_diff(fake_ctx(), month="2026-11")
    assert got["ok"] is True
    data = got["data"]
    assert data["task"] is None
    assert data["person_diffs"] == [] and data["day_diffs"] == []
    assert "hint" in data


def test_diff_neither_param_rejected(factory):
    got = recon_ops.visit_recon_diff(fake_ctx())
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"


def test_diff_bad_month(factory):
    got = recon_ops.visit_recon_diff(fake_ctx(), month="2026-13")
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_MONTH"


# ---------------- visit_recon_adjust_state ----------------

def test_adjust_state_confirmed_and_balances(factory):
    got = recon_ops.visit_recon_adjust_state(fake_ctx(), month="2026-09")
    assert got["ok"] is True
    data = got["data"]
    assert data["currency"] == "JPY"
    rows = {r["person_code"]: r for r in data["rows"]}
    assert set(rows) == {"P001", "P002"}
    p1 = rows["P001"]
    assert p1["confirmed"] is True
    assert p1["adjust_points"] == -20
    # 同源推导（单价与奖金按月可配，不硬编码金额）
    from app.services import perf as _perf
    expect_diff = (_perf.salary_for(80, month="2026-09")
                   - _perf.salary_for(100, month="2026-09"))
    assert p1["adjust_amount"] == p1["amount_adj"]
    # -20 点 × 该月单价 == 与 salary_for 差额一致（两者应同源）
    assert p1["adjust_amount"] == expect_diff
    assert p1["applied_to_month"] == "2026-10"
    assert p1["source_task_id"] == 1
    assert p1["source_points_diff"] == 20
    assert p1["perf_diff_amount"] == expect_diff
    assert p1["payroll_diff_amount"] == expect_diff
    assert p1["payroll_adjust_amount"] == expect_diff
    p2 = rows["P002"]
    assert p2["confirmed"] is False
    assert p2["perf_diff_amount"] == 0
    assert data["summary"]["persons"] == 2
    assert data["summary"]["confirmed"] == 1
    assert data["summary"]["total_adjust_amount"] == expect_diff


def test_adjust_state_empty_month_ok_with_hint(factory):
    got = recon_ops.visit_recon_adjust_state(fake_ctx(), month="2026-11")
    assert got["ok"] is True
    data = got["data"]
    assert data["rows"] == []
    assert data["summary"]["persons"] == 0
    assert "hint" in data


def test_adjust_state_bad_month(factory):
    got = recon_ops.visit_recon_adjust_state(fake_ctx(), month="2026-9")
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_MONTH"


def test_adjust_state_missing_month_rejected(factory):
    got = recon_ops.visit_recon_adjust_state(fake_ctx(), month="")
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_MONTH"


# ---------------- 只读守卫 / 信封 ----------------

def test_tools_are_read_only(factory):
    """工具调用前后各表行数不变（不得有 commit/写）。"""
    models = (ReconTask, ReconResult, ReconDayRow, ReconDataRow,
              AdjustRecord, MonthPerfRecord, PayrollPeriodRow)

    def counts(db):
        return {m.__tablename__: db.query(m).count() for m in models}

    db = factory()
    before = counts(db)
    db.close()
    recon_ops.visit_recon_status(fake_ctx())
    recon_ops.visit_recon_diff(fake_ctx(), task_id=1)
    recon_ops.visit_recon_diff(fake_ctx(), month="2026-09")
    recon_ops.visit_recon_adjust_state(fake_ctx(), month="2026-09")
    db = factory()
    after = counts(db)
    db.close()
    assert after == before


def test_unexpected_exception_maps_to_internal(factory, monkeypatch):
    def boom(db):
        raise RuntimeError("boom")

    monkeypatch.setattr(recon_ops, "_recon_status", boom)
    got = recon_ops.visit_recon_status(fake_ctx())
    assert got["ok"] is False
    assert got["error"]["code"] == "INTERNAL"
    assert "boom" in got["error"]["message"]


def test_register_exposes_five_tools():
    """register(mcp) 注册全部 4 个工具，且描述为中文、写明只读与日元。"""
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer(name="test-recon", version="0")
    recon_ops.register(mcp)
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"visit_recon_status", "visit_recon_diff",
                     "visit_recon_adjust_state", "visit_recon_settlement",
                     "visit_settlement_trace"}
    desc = {t.name: t.description for t in tools}
    for name in names:
        assert "只读" in desc[name]
        assert ("JPY" in desc[name]) or ("日元" in desc[name])
    assert "month" in desc["visit_recon_adjust_state"]


def test_settlement_trace_filters(factory):
    """四维过滤：全部 / 源月 / 人 / 单笔找平 / 单笔发放，双向明细齐全。"""
    from app.models import PayrollAdjust, PayrollPayment, PayrollPeriodRow
    from app.services import period
    db = factory()
    db.add(PayrollPeriodRow(month="2026-08", person_code="P001",
                            diff_amount=-10000, prev_adjust_amount=-10000))
    db.commit()
    period.sync_adjusts(db, "2026-08")
    a = db.query(PayrollAdjust).filter_by(person_code="P001").first()
    period.record_payment(db, "2026-09", "P001", 2, amount=5000, points=20)
    pay = db.query(PayrollPayment).filter_by(person_code="P001").first()

    got = recon_ops._settlement_trace(db)
    assert got["summary"]["adjust_count"] >= 1
    got = recon_ops._settlement_trace(db, month="2026-08")
    assert all(x["source_month"] == "2026-08" for x in got["adjusts"])
    got = recon_ops._settlement_trace(db, person="P001")
    assert got["adjusts"][0]["person_code"] == "P001"
    assert got["adjusts"][0]["recovered_by"][0]["payment_id"] == pay.id
    got = recon_ops._settlement_trace(db, adjust_id=a.id)
    assert len(got["adjusts"]) == 1 and got["adjusts"][0]["adjust_id"] == a.id
    got = recon_ops._settlement_trace(db, payment_id=pay.id)
    assert got["payments"][0]["payment_id"] == pay.id
    assert got["payments"][0]["adjusts"][0]["adjust_id"] == a.id
