# -*- coding: utf-8 -*-
"""工具冗余清理（docs/workbuddy-mcp-tool-cleanup.md）验收测试。

覆盖：
- month_salary 的 sort_by（points/amount）与 limit（0=全部）；
- dashboard 响应不再含 company_summary 键（已合并进 metrics）；
- perf_ranking / upload_recon 描述首句含 DEPRECATED 且指向替代工具；
- 互斥指引关键词存在于各工具描述（什么时候用我）。
"""
import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (Base, DashMetric, MonthPerfRecord, Person,
                        ReconTask)
from mcp_service import capability, read_ops


@pytest.fixture()
def db(tmp_path):
    """月绩效两条（P001 点数高、P002 金额高），用于 sort_by 断言。"""
    eng = create_engine(f"sqlite:///{tmp_path}/c.db",
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


@pytest.fixture()
def dash_db(tmp_path):
    """看板物化数据：metrics 齐 + top_staff payload。"""
    eng = create_engine(f"sqlite:///{tmp_path}/d.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add_all([
        DashMetric(month="2026-09", metric="employees", value=2.0),
        DashMetric(month="2026-09", metric="records", value=3.0),
        DashMetric(month="2026-09", metric="p1", value=2.0),
        DashMetric(month="2026-09", metric="p2", value=1.0),
        DashMetric(month="2026-09", metric="total_points", value=3.0),
        DashMetric(month="2026-09", metric="total_amount", value=1500.0),
        DashMetric(month="2026-09", metric="top_staff", value=0.0,
                   payload='[{"code": "P001", "name": "张三", "points": 3}]'),
    ])
    s.commit()
    yield s
    s.close()
    eng.dispose()


# ---------------- month_salary：sort_by / limit ----------------

def test_month_salary_default_sorts_by_points(db):
    got = capability.month_salary(db, "2026-09")
    codes = [r["person_code"] for r in got["rows"]]
    assert codes == ["P001", "P002"]          # 点数 14 > 5（默认 points 降序）
    assert got["rows"][0]["points"] == 14


def test_month_salary_sort_by_amount(db):
    got = capability.month_salary(db, "2026-09", sort_by="amount")
    codes = [r["person_code"] for r in got["rows"]]
    assert codes == ["P002", "P001"]          # 金额 1250 > 1000（amount 降序）
    assert got["rows"][0]["salary"] == 1250


def test_month_salary_limit_truncates(db):
    got = capability.month_salary(db, "2026-09", limit=1)
    assert len(got["rows"]) == 1
    assert got["rows"][0]["person_code"] == "P001"
    assert got["persons"] == 1                # 汇总口径随返回行（与 person 过滤一致）


def test_month_salary_limit_zero_means_all(db):
    got = capability.month_salary(db, "2026-09", limit=0)
    assert len(got["rows"]) == 2


def test_month_salary_limit_with_sort_by(db):
    got = capability.month_salary(db, "2026-09", sort_by="amount", limit=1)
    assert got["rows"][0]["person_code"] == "P002"


def test_month_salary_bad_sort_by(db):
    with pytest.raises(capability.BadParam):
        capability.month_salary(db, "2026-09", sort_by="xxx")


def test_month_salary_bad_limit(db):
    with pytest.raises(capability.BadParam):
        capability.month_salary(db, "2026-09", limit=-1)


# ---------------- dashboard：无 company_summary 键 ----------------

def test_dashboard_has_no_company_summary_key(dash_db):
    got = read_ops.dashboard_metrics(dash_db, "2026-09")
    assert "company_summary" not in got
    # metrics 为主结构且字段全集保留（数值与合并前的 company_summary 全等）
    assert got["metrics"]["records"] == 3
    assert got["metrics"]["points"] == 3
    assert got["metrics"]["amount"] == 1500
    assert got["metrics"]["employees"] == 2
    assert "top_staff" in got                 # 默认 top=8 仍返回排行


# ---------------- 注册清单与描述 ----------------

def _registered_descriptions() -> dict[str, str]:
    from mcp.server.mcpserver import MCPServer
    from mcp_service import tools as tools_mod

    mcp = MCPServer(name="t", version="0.0.0")
    tools_mod.register(mcp)                   # 全量注册（唯一入口 = 16 个）
    tools = asyncio.run(mcp.list_tools())
    return {t.name: (t.description or "") for t in tools}


def test_registered_tools_are_16_scenario_tools():
    """场景化重构验收：列表只有 16 个新工具，旧名一律删除（不做兼容期）。"""
    descs = _registered_descriptions()
    assert len(descs) == 16
    expect = {
        "visit_whoami", "visit_my_perf", "visit_my_pay",
        "visit_upload", "visit_overview", "visit_person", "visit_payroll",
        "visit_payroll_export", "visit_recon", "visit_recon_export",
        "visit_files", "visit_rebuild", "visit_staff", "visit_config",
        "visit_store", "visit_verify",
    }
    assert set(descs) == expect
    # 旧名已删（用户明确：不做兼容期）
    for old in ("visit_ping", "visit_upload_file", "visit_month_salary",
                "visit_month_summary", "visit_perf_ranking",
                "visit_upload_recon", "visit_rebuild_preview",
                "visit_rebuild_month", "visit_export_salary",
                "visit_verify_integrity", "visit_product_doc"):
        assert old not in descs, old


def test_mutual_exclusion_guidance_in_descriptions():
    descs = _registered_descriptions()
    expect = {
        "visit_overview": "公司级总览",
        "visit_person": "某个人",
        "visit_payroll": "薪资找平",
        "visit_my_perf": "我自己",
        "visit_upload": "统一上传入口",
        "visit_recon": "对账",
    }
    for name, keyword in expect.items():
        assert keyword in descs[name], f"{name} 描述缺少关键词 {keyword!r}"
        assert "什么时候用我" in descs[name], f"{name} 描述缺少「什么时候用我」"


def test_upload_and_payroll_export_descriptions_mention_dry_run():
    descs = _registered_descriptions()
    assert "dry_run" in descs["visit_upload"]
    assert "dry_run" in descs["visit_payroll_export"]
    assert "未登记发放" in descs["visit_payroll_export"]


def test_payroll_rows_exposes_paid_state(db):
    """find pay rows carry paid_half flags from the ledger (export=paid fact)."""
    from datetime import date
    from app.models import (PayrollPayment, PayrollPeriodRow)
    db.add(PayrollPeriodRow(month="2026-09", person_code="P001",
                            half1_amount=1000, half2_amount=500,
                            half1_points=4, half2_points=2,
                            diff_amount=0, adjust_amount=0))
    db.add(PayrollPayment(month="2026-09", person_code="P001", seq=1,
                          points=4, amount=1000))
    db.commit()
    from mcp_service import read_ops
    data = read_ops.payroll_rows(db, "2026-09")
    row = next(x for x in data["rows"] if x["person_code"] == "P001")
    assert row["paid_half1"] is True and row["paid_half2"] is False
    assert data["paid_summary"]["half1_persons"] == 1
