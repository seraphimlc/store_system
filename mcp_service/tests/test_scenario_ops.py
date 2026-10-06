# -*- coding: utf-8 -*-
"""场景化重构验收（规格 docs/specs-mcp-tools-scenario.md 第六节）。

覆盖 7 项验收（第 5 项「旧名仍可调用」按用户要求**不做兼容期**，改为断言旧名已删）：
1. 每个新工具至少 1 条"能答对"的断言（临时 SQLite 造数据，断言关键字段）
2. view/action 非法值 → BAD_PARAM + 可选值提示
3. 员工越权调管理员工具 → FORBIDDEN_TOOL（无副作用）
4. tools/list：员工 6 个 / 队长 9 个 / 管理员 24 个
5. 旧工具名一律删除（不做兼容期）
6. 关键数字一致 → 由 scripts/mcp_restart.sh 对真实库跑自洽检查（本文件用临时库验证口径）
7. 审计：工具名记录正确（不是包装函数名）
"""
import asyncio
import types
from datetime import date, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (Base, DashMetric, FormalRecord, ImportFile,
                        McpAuditLog, MonthPerfRecord, PayrollAdjust,
                        PayrollPayment, PayrollPeriodRow, Person,
                        PersonDailyStat, RawRecord, ReconResult, ReconTask,
                        StoreEntity, StorePair, SysConfig, User)
from mcp_service import authz, scenario_ops, tokens

W = tokens.Actor(uid=1, role="admin", scopes=["read", "write"], token_id=1)
R = tokens.Actor(uid=1, role="admin", scopes=["read"], token_id=1)
S = tokens.Actor(uid=2, role="staff", scopes=["read"], token_id=2,
                 person_code="P001")

MONTH = "2026-09"


def fake_ctx():
    return types.SimpleNamespace(headers={})


def _seed(s):
    """种子覆盖全部 16 个工具的"能答对"路径（口径同 test_read_ops + 对账/店铺/找平）。"""
    now = datetime(2026, 9, 22, 10, 0, 0)
    s.add_all([
        User(id=1, username="admin", password_hash="h", role="admin",
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
                    header_row=2, data_start_row=3, parsed_rows=5, total_rows=5,
                    layout={"header_row": 2,
                            "cols": {"store_id": 1, "store_name": 2},
                            "value_map": {"visible": {"YES": "candidate"}},
                            "point_rules": [{"visible": ["YES"],
                                             "deploy": ["YES"], "points": 2}]})
    s.add(f1)
    s.add_all([
        RawRecord(id=1, import_id=1, sheet_name="S", excel_row=2,
                  store_id_raw="ST-1", store_name_local_raw="店A",
                  modified_raw="2026-09-01 10:00:00", submitter_raw="张三",
                  submitter_code="P001", visible_raw="YES", deploy_raw="YES",
                  clean_status="valid", confirm_state="auto_approved"),
        RawRecord(id=2, import_id=1, sheet_name="S", excel_row=3,
                  store_id_raw="ST-2", store_name_local_raw="店B",
                  modified_raw="2026-09-02 10:00:00", submitter_raw="张三",
                  submitter_code="P001", visible_raw="YES", deploy_raw="YES",
                  clean_status="valid", confirm_state="auto_approved"),
        RawRecord(id=3, import_id=1, sheet_name="S", excel_row=4,
                  store_id_raw="ST-3", store_name_local_raw="店C",
                  modified_raw="2026-09-03 10:00:00", submitter_raw="李四",
                  submitter_code="P002", visible_raw="YES", deploy_raw="NO",
                  clean_status="valid", confirm_state="auto_approved"),
        RawRecord(id=4, import_id=1, sheet_name="S", excel_row=5,
                  store_id_raw="ST-4", store_name_local_raw="店D",
                  modified_raw="2026-09-04 10:00:00", submitter_raw="张三",
                  submitter_code="P001", clean_status="master_late",
                  filter_reason="master_late"),
    ])
    s.add_all([
        FormalRecord(id=1, import_id=1, raw_record_id=1, person_code="P001",
                     store_id_raw="ST-1", japan_date=date(2026, 9, 1),
                     points=1),
        FormalRecord(id=2, import_id=1, raw_record_id=2, person_code="P001",
                     store_id_raw="ST-2", japan_date=date(2026, 9, 2),
                     points=2),
        FormalRecord(id=3, import_id=1, raw_record_id=3, person_code="P002",
                     store_id_raw="ST-3", japan_date=date(2026, 9, 3),
                     points=1),
    ])
    s.add_all([
        MonthPerfRecord(month=MONTH, person_code="P001", records=2, p1=1,
                        p2=1, points=3, salary=1250, per_point=250),
        MonthPerfRecord(month=MONTH, person_code="P002", records=1, p1=1,
                        p2=0, points=1, salary=250, per_point=250),
    ])
    s.add_all([
        PersonDailyStat(person_code="P001", ref_date=date(2026, 9, 1),
                        records=1, p1=1, p2=0, points=1),
        PersonDailyStat(person_code="P001", ref_date=date(2026, 9, 2),
                        records=1, p1=0, p2=1, points=2),
        PersonDailyStat(person_code="P002", ref_date=date(2026, 9, 5),
                        records=1, p1=1, p2=0, points=1),
    ])
    s.add(PayrollPeriodRow(month=MONTH, person_code="P001",
                           half1_points=1, half2_points=2, settle_points=4,
                           prev_adjust_points=0, diff_points=1,
                           half1_records=1, half1_p1=1, half1_p2=0,
                           half2_records=1, half2_p1=0, half2_p2=1,
                           half1_bonus=0, half2_bonus=0,
                           half1_amount=250, half2_amount=500,
                           settle_amount=1000, prev_adjust_amount=0,
                           diff_amount=250, adjust_points=1, adjust_amount=250))
    # 8 月找平行（与 PayrollAdjust 成对，保证自洽检查 A1 通过）
    s.add(PayrollPeriodRow(month="2026-08", person_code="P001",
                           diff_amount=-1000, diff_points=-4,
                           prev_adjust_amount=-1000,
                           adjust_amount=-1000, adjust_points=-4,
                           half1_amount=0, half2_amount=0))
    s.add(PayrollAdjust(person_code="P001", source_month="2026-08",
                        adjust_amount=-1000, settled_amount=0, remaining=-1000,
                        status="in_progress"))
    s.add(PayrollAdjust(person_code="P001", source_month=MONTH,
                        adjust_amount=250, settled_amount=0,
                        remaining=250, status="in_progress"))
    s.add(PayrollPayment(month=MONTH, person_code="P001", seq=1,
                         points=10, amount=2500))
    s.add(SysConfig(id=1, config_month="", per_point=300, bonus_group=70,
                    bonus_amount=2000, staff_visible_from="2026-08"))
    s.add_all([
        DashMetric(month=MONTH, metric="employees", value=2.0),
        DashMetric(month=MONTH, metric="records", value=3.0),
        DashMetric(month=MONTH, metric="p1", value=2.0),
        DashMetric(month=MONTH, metric="p2", value=1.0),
        DashMetric(month=MONTH, metric="total_points", value=3.0),
        DashMetric(month=MONTH, metric="total_amount", value=1500.0),
    ])
    s.add_all([
        StoreEntity(id=1, store_id_raw="ST-1", name_local="店A",
                    name_norm="店a", city="东京", master_id=1,
                    master_store_id="ST-1"),
        StoreEntity(id=2, store_id_raw="ST-2", name_local="店B",
                    name_norm="店b", city="大阪", master_id=2,
                    master_store_id="ST-2"),
    ])
    s.add(StorePair(id=1, entity_a=1, entity_b=2, kind="exact",
                    status="pending"))
    s.add(ReconTask(id=1, kind="monthly_v3", status="done", created_by=1,
                    params={"month": MONTH, "file": "9月对账.xlsx",
                            "kind": "person_points", "version": 2,
                            "current": True},
                    summary={"kind": "person_points", "compared": 2,
                             "diff_count": 1, "sys_only": [],
                             "ai_interpret": {"text": "P001 差异来自缺巡店记录",
                                              "model": "deepseek-v4-flash",
                                              "at": "2026-09-30T10:00:00"}}))
    s.add(ReconResult(task_id=1, submitter_code="P001", system_value=3,
                      report_value=5, diff=-2, status="diff",
                      family="person_points", confirmed=False,
                      note="张三 点数差异"))
    s.commit()


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    """临时库 factory + 把 scenario_ops.SessionLocal 与 actor 解析重定向。"""
    engine = create_engine(f"sqlite:///{tmp_path}/s.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    F = sessionmaker(bind=engine, expire_on_commit=False)
    s = F()
    _seed(s)
    s.close()
    monkeypatch.setattr("app.db.SessionLocal", F)
    monkeypatch.setattr(scenario_ops, "SessionLocal", F)
    monkeypatch.setattr(scenario_ops, "actor_from_ctx", lambda ctx: W)
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: W)
    yield F
    engine.dispose()


# ---------------------------------------------------------------------------
# 验收 1：每个新工具至少 1 条"能答对"的断言
# ---------------------------------------------------------------------------

def test_whoami_admin(factory):
    got = scenario_ops.visit_whoami(fake_ctx())
    assert got["ok"] is True
    d = got["data"]
    assert d["role"] == "admin" and d["role_label"] == "管理员"
    assert d["can_write"] is True


def test_my_perf_answers_own_summary(factory, monkeypatch):
    monkeypatch.setattr(scenario_ops, "actor_from_ctx", lambda ctx: S)
    got = scenario_ops.visit_my_perf(fake_ctx(), month=MONTH)
    assert got["ok"] is True
    d = got["data"]
    assert d["person_code"] == "P001"
    assert d["summary"]["points"] == 3
    assert d["summary"]["salary"] == 1250
    assert d["currency"] == "JPY"
    # view=daily 逐日明细
    got2 = scenario_ops.visit_my_perf(fake_ctx(), month=MONTH, view="daily")
    assert got2["data"]["daily"][0]["date"] == "2026-09-01"


def test_my_pay_answers_adjusts_and_payments(factory, monkeypatch):
    monkeypatch.setattr(scenario_ops, "actor_from_ctx", lambda ctx: S)
    got = scenario_ops.visit_my_pay(fake_ctx())
    assert got["ok"] is True
    d = got["data"]
    assert d["person_code"] == "P001"
    assert d["adjusts"]["count"] == 2
    assert d["adjusts"]["remaining_total"] == -750
    assert len(d["payments"]) == 1
    assert d["payments"][0]["amount"] == 2500
    # month 过滤
    got2 = scenario_ops.visit_my_pay(fake_ctx(), month="2026-08")
    assert got2["data"]["adjusts"]["count"] == 1      # 找平源月 2026-08
    got3 = scenario_ops.visit_my_pay(fake_ctx(), month="2026-09")
    assert got3["data"]["adjusts"]["count"] == 1
    assert got3["data"]["payments"][0]["month"] == "2026-09"


def test_upload_dry_run_writes_nothing(factory):
    got = scenario_ops.visit_upload(fake_ctx(), filename="a.xlsx",
                                    content_base64="bm90IGFuIHhsc3g=",  # b"not an xlsx"
                                    dry_run=True)
    assert got["ok"] is True
    assert got["data"]["dry_run"] is True
    s = factory()
    assert s.query(ImportFile).count() == 1            # 未新增
    assert s.query(FormalRecord).count() == 3
    s.close()


def test_upload_rejects_neither_source(factory):
    got = scenario_ops.visit_upload(fake_ctx(), filename="a.xlsx")
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_REQUEST"


def test_overview_summary_answers(factory):
    got = scenario_ops.visit_overview(fake_ctx(), month=MONTH)
    assert got["ok"] is True
    d = got["data"]
    assert d["formal_rows"] == 3
    assert d["points_total"] == 4
    assert d["persons"] == 2
    assert d["total_salary"] == 1500
    assert d["currency"] == "JPY"


def test_overview_months_and_ranking(factory):
    d = scenario_ops.visit_overview(fake_ctx(), view="months")["data"]
    assert "2026-09" in {m["month"] for m in d["months"]}
    d2 = scenario_ops.visit_overview(fake_ctx(), month=MONTH,
                                     view="ranking", limit=1)["data"]
    assert d2["rows"][0]["person_code"] == "P001"
    assert d2["rows"][0]["salary"] == 1250


def test_person_answers_monthly_and_daily(factory):
    got = scenario_ops.visit_person(fake_ctx(), person="P001", month=MONTH)
    assert got["ok"] is True
    d = got["data"]
    assert d["person_code"] == "P001" and d["name"] == "张三"
    assert d["summary"]["points"] == 3
    assert d["summary"]["salary"] == 1250
    assert len(d["daily"]) == 2
    # name 别名 & 默认最新月
    got2 = scenario_ops.visit_person(fake_ctx(), name="张", month=MONTH)
    assert got2["data"]["person_code"] == "P001"


def test_payroll_rows_and_trace(factory):
    d = scenario_ops.visit_payroll(fake_ctx(), month=MONTH,
                                   view="rows")["data"]
    assert d["count"] == 1
    row = d["rows"][0]
    assert row["person_code"] == "P001"
    assert row["half1_points"] == 1 and row["half2_points"] == 2
    assert row["diff_amount"] == 250
    d2 = scenario_ops.visit_payroll(fake_ctx(), month=MONTH,
                                    view="adjusts")["data"]
    assert "settlement" in d2
    assert d2["rows"]                        # 有找平/偏差行
    d3 = scenario_ops.visit_payroll(fake_ctx(), month=MONTH,
                                    view="trace")["data"]
    assert "adjusts" in d3 and "payments" in d3


def test_payroll_export_dry_run_no_ledger(factory):
    got = scenario_ops.visit_payroll_export(fake_ctx(), month=MONTH, seq=1,
                                            dry_run=True)
    assert got["ok"] is True
    d = got["data"]
    assert d["ledger"]["registered"] == 0
    assert "未登记发放" in d["ledger"]["note"]
    assert d["amount_summary"]["seq"] == 1
    assert d["amount_summary"]["total_amount"] == 250
    # dry_run 不落台账
    s = factory()
    assert s.query(PayrollPayment).count() == 1
    s.close()


def test_recon_status_diff_interpret(factory):
    d = scenario_ops.visit_recon(fake_ctx(), view="status", month=MONTH)["data"]
    assert d["count"] == 1
    assert d["tasks"][0]["id"] == 1
    d2 = scenario_ops.visit_recon(fake_ctx(), view="diff", task_id=1)["data"]
    assert d2["person_diffs"][0]["person_code"] == "P001"
    d3 = scenario_ops.visit_recon(fake_ctx(), view="interpret", task_id=1)["data"]
    assert d3["has_interpretation"] is True
    assert d3["interpretation"] == "P001 差异来自缺巡店记录"


def test_recon_export_diff(factory):
    got = scenario_ops.visit_recon_export(fake_ctx(), kind="diff", task_id=1)
    assert got["ok"] is True
    assert got["data"]["filename"] == "对账差异_任务1.xlsx"


def test_files_list_layout_report_tasks(factory):
    d = scenario_ops.visit_files(fake_ctx(), view="list", month=MONTH)["data"]
    assert d["total"] == 1
    assert d["files"][0]["judge_counts"]["valid"] == 3
    d2 = scenario_ops.visit_files(fake_ctx(), view="layout", import_id=1)["data"]
    assert d2["header_row"] == 2
    assert d2["value_map_text"]["visible"] == "YES=candidate"
    d3 = scenario_ops.visit_files(fake_ctx(), view="report", import_id=1)["data"]
    assert d3["raw_total"] == 4
    assert d3["counts"]["valid"] == 3
    d4 = scenario_ops.visit_files(fake_ctx(), view="tasks")["data"]
    assert d4["total"] >= 1


def test_rebuild_preview(factory):
    got = scenario_ops.visit_rebuild(fake_ctx(), month=MONTH,
                                     action="preview")
    assert got["ok"] is True
    d = got["data"]
    assert isinstance(d["preview_id"], int)
    assert d["raw_total"] == 4
    assert d["estimated_insert_rows"] == d["raw_by_status"]["valid"] == 3


def test_staff_list_and_set_status(factory):
    d = scenario_ops.visit_staff(fake_ctx(), view="list")["data"]
    assert d["count"] == 3
    got = scenario_ops.visit_staff(fake_ctx(), action="set_status",
                                   username="zhangsan", status="leave",
                                   confirm_text="确认改状态 zhangsan leave")
    assert got["ok"] is True
    assert got["data"]["new_status"] == "leave"
    s = factory()
    assert s.get(User, 2).status == "leave"
    s.close()


def test_config_get_and_set(factory):
    d = scenario_ops.visit_config(fake_ctx(), view="get")["data"]
    assert d["per_point"] == 300
    assert d["bonus_group"] == 70
    assert d["staff_visible_from"] == "2026-08"
    got = scenario_ops.visit_config(
        fake_ctx(), action="set", per_point=260, staff_visible_from="2026-10",
        confirm_text="确认修改配置")
    assert got["ok"] is True
    assert got["data"]["per_point"] == 260
    assert got["data"]["staff_visible_from"] == "2026-10"


def test_store_search_pairs_merge(factory):
    d = scenario_ops.visit_store(fake_ctx(), view="search", q="店A")["data"]
    assert d["total"] == 1 and d["rows"][0]["store_id_raw"] == "ST-1"
    d2 = scenario_ops.visit_store(fake_ctx(), view="pairs")["data"]
    assert d2["total"] == 1
    assert d2["pairs"][0]["kind"] == "exact"
    got = scenario_ops.visit_store(fake_ctx(), action="merge",
                                   pair_id=1, keep=1)
    assert got["ok"] is True
    assert got["data"]["merged_entity_id"] == 2
    s = factory()
    assert s.get(StoreEntity, 2).master_id == 1
    s.close()


def test_verify_integrity(factory):
    got = scenario_ops.visit_verify(fake_ctx())
    assert got["ok"] is True
    d = got["data"]
    assert "checks" in d and "summary" in d
    assert "failed" in d["summary"]


# ---------------------------------------------------------------------------
# 验收 2：非法 view/action → BAD_PARAM + 可选值提示
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("call", [
    lambda: scenario_ops.visit_overview(fake_ctx(), view="bogus"),
    lambda: scenario_ops.visit_payroll(fake_ctx(), month=MONTH, view="nope"),
    lambda: scenario_ops.visit_recon(fake_ctx(), view="wat"),
    lambda: scenario_ops.visit_files(fake_ctx(), view="zzz"),
    lambda: scenario_ops.visit_store(fake_ctx(), view="zzz"),
    lambda: scenario_ops.visit_store(fake_ctx(), action="zzz"),
    lambda: scenario_ops.visit_rebuild(fake_ctx(), month=MONTH, action="zzz"),
    lambda: scenario_ops.visit_staff(fake_ctx(), action="zzz"),
    lambda: scenario_ops.visit_config(fake_ctx(), action="zzz"),
])
def test_invalid_view_or_action_returns_bad_param(call):
    got = call()
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"
    assert got["error"]["hint"], "非法 view/action 必须给出可选值提示"


def test_invalid_recon_export_kind(factory):
    got = scenario_ops.visit_recon_export(fake_ctx(), kind="bogus", task_id=1)
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"
    assert "report" in got["error"]["hint"] and "diff" in got["error"]["hint"]


# ---------------------------------------------------------------------------
# 验收 3：员工越权调管理员工具 → FORBIDDEN_TOOL（无副作用）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("tool", [
    "visit_upload", "visit_overview", "visit_person", "visit_payroll",
    "visit_payroll_export", "visit_recon", "visit_recon_export",
    "visit_files", "visit_rebuild", "visit_staff", "visit_config",
    "visit_store", "visit_verify",
])
def test_staff_forbidden_on_all_admin_tools(tool):
    denied = authz.enforce(tool, S, {})
    assert denied is not None
    assert denied["error"]["code"] == "FORBIDDEN_TOOL"
    assert denied["error"]["retryable"] is False


def test_staff_forbidden_no_side_effect(factory, monkeypatch):
    """员工调写工具被拦在业务前：表行数不变。"""
    monkeypatch.setattr(scenario_ops, "actor_from_ctx", lambda ctx: S)
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: S)
    got = scenario_ops.visit_upload(fake_ctx(), filename="a.xlsx",
                                    content_base64="eA==", dry_run=False)
    assert got["ok"] is False and got["error"]["code"] == "FORBIDDEN_TOOL"
    got2 = scenario_ops.visit_overview(fake_ctx(), month=MONTH)
    assert got2["ok"] is False and got2["error"]["code"] == "FORBIDDEN_TOOL"
    s = factory()
    assert s.query(ImportFile).count() == 1
    assert s.query(McpAuditLog).filter_by(
        tool="visit_upload", error_code="FORBIDDEN_TOOL").count() == 1
    s.close()


# ---------------------------------------------------------------------------
# 验收 4：tools/list 员工 6 个 / 队长 9 个 / 管理员 24 个
# ---------------------------------------------------------------------------

def _registered_names():
    from mcp.server.mcpserver import MCPServer
    from mcp_service import tools as tools_mod
    mcp = MCPServer(name="t", version="0.0.0")
    tools_mod.register(mcp)
    return {t.name for t in asyncio.run(mcp.list_tools())}


def test_tools_list_admin_24():
    names = _registered_names()
    assert len(names) == 24


def test_tools_list_staff_6():
    """员工 tools/list 裁剪 = STAFF_ALLOWED（6 个：3 结算域 + 3 作业域）。"""
    assert set(authz.STAFF_ALLOWED) == {
        "visit_whoami", "visit_my_perf", "visit_my_pay",
        "visit_my_tasks", "visit_self_report", "visit_task_report"}
    names = _registered_names()
    assert set(authz.STAFF_ALLOWED) <= names


def test_leader_tier_allowed():
    """**队长档**（2026-10-06 新增）：员工能用的 + 本队任务管理（3 个）。"""
    assert set(authz.LEADER_ALLOWED) == set(authz.STAFF_ALLOWED) | {
        "visit_team_tasks", "visit_task_assign", "visit_task_confirm"}
    assert authz.require_role("visit_team_tasks") == "LEADER_ALLOWED"
    assert authz.require_role("visit_upload") == "ADMIN_ONLY"


def test_leader_cannot_call_admin_tools():
    """队长调管理员工具 / 员工调队长工具 → FORBIDDEN_TOOL（进入业务前拦截）。"""
    class _A:
        role = "leader"
        person_code = "P1"
        uid = 1
    class _S:
        role = "staff"
        person_code = "P2"
        uid = 2
    assert authz.enforce("visit_task_confirm", _A(), {}) is None
    assert authz.enforce("visit_team_tasks", _A(), {}) is None
    d = authz.enforce("visit_upload", _A(), {})
    assert d and d["error"]["code"] == "FORBIDDEN_TOOL"
    d2 = authz.enforce("visit_team_tasks", _S(), {})
    assert d2 and d2["error"]["code"] == "FORBIDDEN_TOOL"
    assert authz.enforce("visit_my_tasks", _S(), {}) is None


def test_old_tool_names_deleted_no_compat():
    """验收 5（按用户要求改为）：旧名一律删除，不做兼容期。"""
    names = _registered_names()
    for old in ("visit_ping", "visit_upload_file", "visit_upload_recon",
                "visit_month_salary", "visit_month_summary",
                "visit_rebuild_preview", "visit_rebuild_month",
                "visit_finalize_file", "visit_set_per_point",
                "visit_export_salary", "visit_export_payroll_settle",
                "visit_export_recon_diff", "visit_export_recon_report",
                "visit_export_recon_result", "visit_verify_integrity",
                "visit_product_doc", "visit_my_daily", "visit_my_settlement",
                "visit_staff_set_status", "visit_staff_list",
                "visit_config_set", "visit_config_get",
                "visit_store_merge_pair", "visit_store_skip_pair",
                "visit_store_split_entity", "visit_store_apply_all",
                "visit_store_ai_run", "visit_recon_interpret",
                "visit_recon_adjust", "visit_payroll_generate",
                "visit_payroll_update", "visit_payroll_mark_paid",
                "visit_recon_status", "visit_recon_diff",
                "visit_recon_adjust_state", "visit_recon_settlement",
                "visit_settlement_trace", "visit_perf_ranking",
                "visit_dashboard", "visit_payroll_rows",
                "visit_person_detail", "visit_file_list",
                "visit_file_report", "visit_file_layout",
                "visit_list_tasks", "visit_list_months",
                "visit_store_search"):
        assert old not in names, old


# ---------------------------------------------------------------------------
# 验收 7：审计工具名记录正确（不是包装函数名）
# ---------------------------------------------------------------------------

def test_audit_records_scenario_tool_names(factory):
    scenario_ops.visit_overview(fake_ctx(), month=MONTH)
    scenario_ops.visit_files(fake_ctx(), view="list", month=MONTH)
    s = factory()
    rows = s.query(McpAuditLog).order_by(McpAuditLog.id).all()
    tools = [r.tool for r in rows]
    assert "visit_overview" in tools
    assert "visit_files" in tools
    # 绝不记录包装函数名（_t_* 之类）
    assert not any(t.startswith("_t_") for t in tools)
    s.close()


def test_rebuild_preview_audit_row_uses_scenario_name(factory):
    got = scenario_ops.visit_rebuild(fake_ctx(), month=MONTH,
                                     action="preview")
    assert got["ok"] is True
    s = factory()
    row = s.get(McpAuditLog, got["data"]["preview_id"])
    assert row.tool == "visit_rebuild"
    assert "2026-09" in (row.params_json or "")
    s.close()


# ---------------------------------------------------------------------------
# 验收 6（口径一致性，真实库自洽由 mcp_restart.sh 执行）
# ---------------------------------------------------------------------------

def test_key_numbers_match_read_ops_aggregation(factory):
    """临时库口径：overview 数字与 read_ops/capability 同源一致。"""
    from mcp_service import capability, read_ops
    s = factory()
    ms = capability.month_summary(s, MONTH)
    sal = capability.month_salary(s, MONTH)
    ov = scenario_ops._overview_summary(s, MONTH)
    assert ov["formal_rows"] == ms["formal_rows"] == 3
    assert ov["points_total"] == ms["points_total"] == 4
    assert ov["persons"] == ms["persons"] == 2
    assert ov["total_salary"] == sal["total_salary"] == 1500
    s.close()
