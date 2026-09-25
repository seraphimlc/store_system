# -*- coding: utf-8 -*-
"""read_ops：9 个只读工具的聚合口径与错误信封（临时 SQLite，绝不碰真实库）。

覆盖要求：每个工具至少 1 条正常断言 + 1 条错误/空态断言（BAD_MONTH / NOT_FOUND / 空月），
另有：只读证据（mode=ro 连接上全部能力函数可跑通）、注册清单、错误信封映射。
"""
import asyncio
import datetime as _dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (Base, DashMetric, FormalRecord, ImportFile,
                        MonthPerfRecord, PayrollPeriodRow, Person,
                        PersonDailyStat, RawRecord, StoreEntity, SysConfig,
                        User)
from mcp_service import capability, read_ops

EXPECTED_TOOLS = {
    "visit_file_list", "visit_file_report", "visit_perf_ranking",
    "visit_dashboard", "visit_payroll_rows", "visit_person_detail",
    "visit_config_get", "visit_staff_list", "visit_store_search",
    "visit_list_tasks", "visit_list_months"}


@pytest.fixture()
def db(tmp_path):
    """临时 SQLite：Base.metadata.create_all + 少量种子数据（覆盖全部 9 个工具）。"""
    path = tmp_path / "r.db"
    eng = create_engine(f"sqlite:///{path}",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    now = _dt.datetime(2026, 9, 22, 10, 0, 0)
    s.add_all([
        User(id=1, username="admin", password_hash="h-admin", role="admin",
             display_name="管理员", status="active", is_active=True,
             created_at=now),
        User(id=2, username="zhangsan", password_hash="h2", role="staff",
             display_name="张三", status="active", is_active=True,
             person_code="P001", created_at=now),
        User(id=3, username="lisi", password_hash="h3", role="staff",
             display_name="李四", status="leave", is_active=True,
             person_code="P002", created_at=now),
    ])
    s.add_all([
        Person(code="P001", display_name="张三"),
        Person(code="P002", display_name="李四"),
    ])
    f1 = ImportFile(id=1, file_name="2026-09巡店.xlsx", file_sha256="a" * 64,
                    file_size=10, stored_path="/tmp/a.xlsx", uploaded_by=1,
                    uploaded_at=now, status="parsed", format="wide",
                    parsed_rows=5, total_rows=5)
    f2 = ImportFile(id=2, file_name="2026-08巡店.xlsx", file_sha256="b" * 64,
                    file_size=10, stored_path="/tmp/b.xlsx", uploaded_by=1,
                    uploaded_at=now, status="parsed", format="wide",
                    parsed_rows=2, total_rows=2)
    f3 = ImportFile(id=3, file_name="2026-10空.xlsx", file_sha256="c" * 64,
                    file_size=10, stored_path="/tmp/c.xlsx", uploaded_by=1,
                    uploaded_at=now, status="parsed", format="wide",
                    parsed_rows=0, total_rows=0)
    s.add_all([f1, f2, f3])
    # f1 判定：3 valid + 1 master_late + 1 cross_file_dup（2026-09）
    s.add_all([
        RawRecord(id=1, import_id=1, sheet_name="S", excel_row=2,
                  store_id_raw="ST-1", store_name_local_raw="店A",
                  modified_raw="2026-09-01 10:00:00", submitter_raw="张三",
                  submitter_code="P001", clean_status="valid"),
        RawRecord(id=2, import_id=1, sheet_name="S", excel_row=3,
                  store_id_raw="ST-2", store_name_local_raw="店B",
                  modified_raw="2026-09-02 10:00:00", submitter_raw="张三",
                  submitter_code="P001", clean_status="valid"),
        RawRecord(id=3, import_id=1, sheet_name="S", excel_row=4,
                  store_id_raw="ST-3", store_name_local_raw="店C",
                  modified_raw="2026-09-03 10:00:00", submitter_raw="李四",
                  submitter_code="P002", clean_status="valid"),
        RawRecord(id=4, import_id=1, sheet_name="S", excel_row=5,
                  store_id_raw="ST-4", store_name_local_raw="店D",
                  modified_raw="2026-09-04 10:00:00", submitter_raw="张三",
                  submitter_code="P001", clean_status="master_late",
                  filter_reason="master_late"),
        RawRecord(id=5, import_id=1, sheet_name="S", excel_row=6,
                  store_id_raw="ST-5", store_name_local_raw="店E",
                  modified_raw="2026-09-05 10:00:00", submitter_raw="张三",
                  submitter_code="P001", clean_status="cross_file_dup",
                  filter_reason="cross_file_dup"),
        # f2 判定：2 valid（2026-08）
        RawRecord(id=6, import_id=2, sheet_name="S", excel_row=2,
                  store_id_raw="ST-6", store_name_local_raw="店F",
                  modified_raw="2026-08-01 10:00:00", submitter_raw="张三",
                  submitter_code="P001", clean_status="valid"),
        RawRecord(id=7, import_id=2, sheet_name="S", excel_row=3,
                  store_id_raw="ST-7", store_name_local_raw="店G",
                  modified_raw="2026-08-02 10:00:00", submitter_raw="李四",
                  submitter_code="P002", clean_status="valid"),
    ])
    # f1 → 3 条正式记录（2026-09）
    s.add_all([
        FormalRecord(id=1, import_id=1, raw_record_id=1, person_code="P001",
                     store_id_raw="ST-1", japan_date=_dt.date(2026, 9, 1),
                     points=1),
        FormalRecord(id=2, import_id=1, raw_record_id=2, person_code="P001",
                     store_id_raw="ST-2", japan_date=_dt.date(2026, 9, 2),
                     points=2),
        FormalRecord(id=3, import_id=1, raw_record_id=3, person_code="P002",
                     store_id_raw="ST-3", japan_date=_dt.date(2026, 9, 3),
                     points=1),
    ])
    # 月绩效物化（2026-09）
    s.add_all([
        MonthPerfRecord(month="2026-09", person_code="P001", records=2, p1=1,
                        p2=1, points=3, salary=1250, per_point=250),
        MonthPerfRecord(month="2026-09", person_code="P002", records=1, p1=1,
                        p2=0, points=1, salary=250, per_point=250),
    ])
    # 人×日统计（2026-09）
    s.add_all([
        PersonDailyStat(person_code="P001", ref_date=_dt.date(2026, 9, 1),
                        records=1, p1=1, p2=0, points=1),
        PersonDailyStat(person_code="P001", ref_date=_dt.date(2026, 9, 2),
                        records=1, p1=0, p2=1, points=2),
        PersonDailyStat(person_code="P002", ref_date=_dt.date(2026, 9, 5),
                        records=1, p1=1, p2=0, points=1),
    ])
    # 薪资找平（2026-09，1 行）
    s.add_all([
        PayrollPeriodRow(month="2026-09", person_code="P001",
                         half1_points=1, half2_points=2, settle_points=4,
                         prev_adjust_points=0, diff_points=1,
                         half1_records=1, half1_p1=1, half1_p2=0,
                         half2_records=1, half2_p1=0, half2_p2=1,
                         half1_bonus=0, half2_bonus=0,
                         half1_amount=250, half2_amount=500,
                         settle_amount=1000, prev_adjust_amount=0,
                         diff_amount=250, adjust_points=1, adjust_amount=250),
    ])
    # 系统配置（全局单值）
    s.add(SysConfig(id=1, config_month="", per_point=300, bonus_group=70,
                    bonus_amount=2000, staff_visible_from="2026-08"))
    # 看板物化（2026-09）：>=5 条数值指标 + top_staff payload
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
    # 店铺主档
    s.add_all([
        StoreEntity(id=1, store_id_raw="ST-1", name_local="店A",
                    name_norm="店a", city="东京", master_id=1,
                    master_store_id="ST-1"),
        StoreEntity(id=2, store_id_raw="ST-2", name_local="店B",
                    name_norm="店b", city="大阪", master_id=2,
                    master_store_id="ST-2"),
    ])
    s.commit()
    yield s, path
    s.close()
    eng.dispose()


# ---------------- visit_file_list ----------------

def test_file_list_basic(db):
    s, _ = db
    got = read_ops.file_list(s)
    assert got["total"] == 3
    by_id = {f["file_id"]: f for f in got["files"]}
    f1 = by_id[1]
    assert f1["file_name"] == "2026-09巡店.xlsx"
    assert f1["status"] == "parsed"
    assert f1["parsed_rows"] == 5
    assert f1["months"] == ["2026-09"]
    assert f1["judge_counts"]["valid"] == 3
    assert f1["judge_counts"]["master_late"] == 1
    assert f1["judge_counts"]["cross_file_dup"] == 1
    assert f1["formal_rows"] == 3


def test_file_list_month_filter(db):
    s, _ = db
    got = read_ops.file_list(s, month="2026-08")
    assert got["total"] == 1
    assert got["files"][0]["file_id"] == 2
    assert got["files"][0]["months"] == ["2026-08"]


def test_file_list_empty_month_is_ok(db):
    s, _ = db
    got = read_ops.file_list(s, month="2026-10")
    assert got["total"] == 0
    assert got["files"] == []
    assert "hint" in got


def test_file_list_bad_month_raises(db):
    s, _ = db
    with pytest.raises(capability.BadMonth):
        read_ops.file_list(s, month="2026-9")


# ---------------- visit_file_report ----------------

def test_file_report_counts_and_samples(db):
    s, _ = db
    got = read_ops.file_report(s, 1)
    assert got["raw_total"] == 5
    assert got["counts"] == {"valid": 3, "master_late": 1, "cross_file_dup": 1}
    assert got["formal_rows"] == 3
    assert got["pending_appeals"] == 0
    assert len(got["samples"]["valid"]) == 3
    assert got["samples"]["valid"][0]["in_formal"] is True
    assert got["samples"]["master_late"][0]["status"] == "master_late"
    assert got["samples"]["cross_file_dup"][0]["filter_reason"] == "cross_file_dup"


def test_file_report_bucket_filter(db):
    s, _ = db
    got = read_ops.file_report(s, 1, bucket="master_late")
    assert got["bucket"] == "master_late"
    assert got["counts"]["valid"] == 3            # counts 恒为全部分布
    assert len(got["samples"]["master_late"]) == 1
    assert got["samples"]["valid"] == []


def test_file_report_not_found(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.file_report(d, 999))
    assert got["ok"] is False
    assert got["error"]["code"] == "NOT_FOUND"


def test_file_report_empty_file(db):
    s, _ = db
    got = read_ops.file_report(s, 3)
    assert got["raw_total"] == 0
    assert got["counts"] == {}
    assert "hint" in got


# ---------------- visit_perf_ranking ----------------

def test_perf_ranking_top_n(db):
    s, _ = db
    got = read_ops.perf_ranking(s, "2026-09")
    assert got["count"] == 2
    assert got["currency"] == "JPY"
    first = got["rows"][0]
    assert first["person_code"] == "P001"      # 点数 3 最高
    assert first["points"] == 3
    assert first["p1"] == 1 and first["p2"] == 1
    assert first["salary"] == 1250


def test_perf_ranking_limit(db):
    s, _ = db
    got = read_ops.perf_ranking(s, "2026-09", limit=1)
    assert got["limit"] == 1
    assert got["count"] == 1
    assert got["rows"][0]["person_code"] == "P001"


def test_perf_ranking_empty_month(db):
    s, _ = db
    got = read_ops.perf_ranking(s, "2026-08")
    assert got["count"] == 0
    assert got["rows"] == []
    assert "hint" in got


def test_perf_ranking_bad_month(db):
    s, _ = db
    with pytest.raises(capability.BadMonth):
        read_ops.perf_ranking(s, "2026-9")


def test_perf_ranking_bad_limit(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.perf_ranking(d, "2026-09", limit=0))
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"


# ---------------- visit_dashboard ----------------

def test_dashboard_materialized(db):
    s, _ = db
    got = read_ops.dashboard_metrics(s, "2026-09")
    assert got["source"] == "dash_metrics"
    assert got["metrics"]["employees"] == 2
    assert got["metrics"]["records"] == 3
    assert got["metrics"]["points"] == 3
    assert got["metrics"]["amount"] == 1500
    assert "company_summary" not in got          # 已合并进 metrics，不再单独返回
    assert got["top_staff"][0]["code"] == "P001"
    assert got["currency"] == "JPY"


def test_dashboard_top_param(db):
    s, _ = db
    got0 = read_ops.dashboard_metrics(s, "2026-09", top=0)
    assert "top_staff" not in got0               # top=0 不返回排行
    got1 = read_ops.dashboard_metrics(s, "2026-09", top=1)
    assert len(got1["top_staff"]) == 1
    assert got1["top_staff"][0]["code"] == "P001"


def test_dashboard_bad_top(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.dashboard_metrics(d, "2026-09", top=-1))
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"


def test_dashboard_realtime_fallback_empty(db):
    s, _ = db
    got = read_ops.dashboard_metrics(s, "2026-10")
    assert got["source"] == "realtime"
    assert got["metrics"]["records"] == 0
    assert got["metrics"]["employees"] == 0
    assert "hint" in got


def test_dashboard_bad_month(db):
    s, _ = db
    with pytest.raises(capability.BadMonth):
        read_ops.dashboard_metrics(s, "２０２６-09")


# ---------------- visit_payroll_rows ----------------

def test_payroll_rows_basic(db):
    s, _ = db
    got = read_ops.payroll_rows(s, "2026-09")
    assert got["count"] == 1
    assert got["currency"] == "JPY"
    row = got["rows"][0]
    assert row["person_code"] == "P001"
    assert row["name"] == "张三"
    assert row["half1_points"] == 1
    assert row["half2_points"] == 2
    assert row["half1_records"] == 1
    assert row["half2_p2"] == 1
    assert row["settle_points"] == 4
    assert row["diff_points"] == 1
    assert row["diff_amount"] == 250
    assert row["adjust_amount"] == 250
    assert row["carry_amount"] == 0


def test_payroll_rows_person_filter(db):
    s, _ = db
    got = read_ops.payroll_rows(s, "2026-09", person="P001")
    assert got["count"] == 1
    empty = read_ops.payroll_rows(s, "2026-09", person="李四")
    assert empty["count"] == 0
    assert "hint" in empty


def test_payroll_rows_empty_month(db):
    s, _ = db
    got = read_ops.payroll_rows(s, "2026-08")
    assert got["count"] == 0
    assert got["rows"] == []
    assert "hint" in got


def test_payroll_rows_bad_month(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.payroll_rows(d, "2026-13"))
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_MONTH"


# ---------------- visit_person_detail ----------------

def test_person_detail_basic(db):
    s, _ = db
    got = read_ops.person_detail(s, "2026-09", "P001")
    assert got["person_code"] == "P001"
    assert got["name"] == "张三"
    assert len(got["daily"]) == 2
    assert got["daily"][0]["date"] == "2026-09-01"
    assert got["daily"][0]["points"] == 1
    assert got["summary"]["points"] == 3
    assert got["summary"]["salary"] == 1250
    assert got["currency"] == "JPY"


def test_person_detail_by_name(db):
    s, _ = db
    got = read_ops.person_detail(s, "2026-09", "张")
    assert got["person_code"] == "P001"


def test_person_detail_not_found(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.person_detail(d, "2026-09", "不存在"))
    assert got["ok"] is False
    assert got["error"]["code"] == "NOT_FOUND"


def test_person_detail_bad_month(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.person_detail(d, "2026-9", "P001"))
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_MONTH"


# ---------------- visit_config_get ----------------

def test_config_get(db):
    s, _ = db
    got = read_ops.config_get(s)
    assert got["per_point"] == 300
    assert got["bonus_group"] == 70
    assert got["bonus_amount"] == 2000
    assert got["staff_visible_from"] == "2026-08"
    assert got["currency"] == "JPY"


def test_config_get_defaults_without_sysconfig(tmp_path):
    """空态：无 SysConfig → 回退 env/默认（per_point=250、env 奖金、env 可见起始月）。"""
    eng = create_engine(f"sqlite:///{tmp_path}/c.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    s = sessionmaker(bind=eng, expire_on_commit=False)()
    try:
        got = read_ops.config_get(s)
        from app.config import get_settings
        assert got["per_point"] == 250          # PER_POINT 默认，无环境覆盖
        assert got["bonus_group"] == get_settings().bonus_group
        assert got["bonus_amount"] == get_settings().bonus_amount
        assert got["staff_visible_from"] == get_settings().staff_visible_from
    finally:
        s.close()
        eng.dispose()


# ---------------- visit_staff_list ----------------

def test_staff_list_basic(db):
    s, _ = db
    got = read_ops.staff_list(s)
    assert got["count"] == 3
    zhangsan = next(r for r in got["rows"] if r["username"] == "zhangsan")
    assert zhangsan["role"] == "staff"
    assert zhangsan["status"] == "active"
    assert zhangsan["bound_person"] is True
    assert zhangsan["person_code"] == "P001"


def test_staff_list_status_filter(db):
    s, _ = db
    got = read_ops.staff_list(s, status="leave")
    assert got["count"] == 1
    assert got["rows"][0]["username"] == "lisi"


def test_staff_list_never_exposes_password_hash(db):
    s, _ = db
    got = read_ops.staff_list(s)
    for r in got["rows"]:
        assert "password_hash" not in r
        assert "hash" not in r


def test_staff_list_bad_status(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.staff_list(d, status="bogus"))
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"


def test_staff_list_empty(db):
    s, _ = db
    got = read_ops.staff_list(s, status="resigned")
    assert got["count"] == 0
    assert got["rows"] == []
    assert "hint" in got


# ---------------- visit_store_search ----------------

def test_store_search_by_name(db):
    s, _ = db
    got = read_ops.store_search(s, "店A")
    assert got["total"] == 1
    row = got["rows"][0]
    assert row["store_id_raw"] == "ST-1"
    assert row["name_local"] == "店A"
    assert row["is_master"] is True


def test_store_search_by_city_and_id(db):
    s, _ = db
    assert read_ops.store_search(s, "大阪")["rows"][0]["store_id_raw"] == "ST-2"
    assert read_ops.store_search(s, "ST-2")["rows"][0]["store_id_raw"] == "ST-2"


def test_store_search_empty(db):
    s, _ = db
    got = read_ops.store_search(s, "不存在店")
    assert got["total"] == 0
    assert "hint" in got
    got2 = read_ops.store_search(s, "")
    assert got2["total"] == 0
    assert "hint" in got2


def test_store_search_bad_limit(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.store_search(d, "店A", limit=0))
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"


# ---------------- 错误信封映射 ----------------

def test_envelope_mapping(db):
    s, _ = db
    assert read_ops._call(s, lambda d: read_ops.file_list(d, month="2026-9")) \
        ["error"]["code"] == "BAD_MONTH"
    assert read_ops._call(s, lambda d: read_ops.file_report(d, 999)) \
        ["error"]["code"] == "NOT_FOUND"
    assert read_ops._call(s, lambda d: read_ops.store_search(d, "x", limit=0)) \
        ["error"]["code"] == "BAD_PARAM"
    boom = read_ops._call(
        s, lambda d: (_ for _ in ()).throw(RuntimeError("boom")))
    assert boom["ok"] is False
    assert boom["error"]["code"] == "INTERNAL"


def test_envelope_empty_is_ok_not_error(db):
    s, _ = db
    got = read_ops._call(s, lambda d: read_ops.file_list(d, month="2026-10"))
    assert got["ok"] is True
    assert got["data"]["total"] == 0
    assert "hint" in got["data"]


# ---------------- 注册清单 ----------------

def test_register_exposes_nine_read_tools():
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer(name="t", version="0.0.0")
    read_ops.register(mcp)
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    for t in tools:
        assert t.description, f"{t.name} 缺少中文描述"


# ---------------- 只读证据 ----------------

def test_all_capabilities_work_on_readonly_connection(db):
    """结构性只读证据：9 个能力函数在 mode=ro 连接上全部可跑通（无任何写操作）。"""
    s, path = db
    s.close()
    s.bind.dispose()
    eng = create_engine(f"sqlite:///file:{path}?mode=ro&uri=true",
                        connect_args={"check_same_thread": False})
    ro = sessionmaker(bind=eng)()
    try:
        assert read_ops.file_list(ro)["total"] == 3
        assert read_ops.file_report(ro, 1)["raw_total"] == 5
        assert read_ops.perf_ranking(ro, "2026-09")["count"] == 2
        assert read_ops.dashboard_metrics(ro, "2026-09")["source"] == "dash_metrics"
        assert read_ops.payroll_rows(ro, "2026-09")["count"] == 1
        assert read_ops.person_detail(ro, "2026-09", "P001")["person_code"] == "P001"
        assert read_ops.config_get(ro)["per_point"] == 300
        assert read_ops.staff_list(ro)["count"] == 3
        assert read_ops.store_search(ro, "店A")["total"] == 1
    finally:
        ro.close()
        eng.dispose()
