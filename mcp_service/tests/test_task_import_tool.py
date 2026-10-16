# -*- coding: utf-8 -*-
"""MCP 工具 `visit_task_import`（批量导入车站任务）。

口径与网页端同一套服务层（`app/services/bd_task_import.py`）：预览只读、
确认才写、已有任务不动、认不出负责队整份拒收。
"""
import base64
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (Base, BdLine, BdStation, BdTask, BdTeam, McpAuditLog)
from app.services import bd_places
from mcp_service import scenario_ops, tokens
from tests.helpers import write_workbook

W = tokens.Actor(uid=1, role="admin", scopes=["read", "write"], token_id=1)
R = tokens.Actor(uid=1, role="admin", scopes=["read"], token_id=1)

HEAD = ["路线编号", "铁路线路", "车站／站群", "负责队", "任务来源", "状态", "备注"]


def _xlsx_b64(tmp_path, rows, name="plan.xlsx"):
    path = str(tmp_path / name)
    write_workbook(path, [("计划", [
        ["某某队 线路执行计划"], ["以下为本次新增站区"], HEAD], rows)])
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _row(station, team="小川队", source="本次新增", status="未开始"):
    return ["KN01", "テスト線", station, team, source, status, ""]


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    """临时库 + 作业域种子（线路/车站/队）+ 上传目录重定向。"""
    monkeypatch.setenv("UPLOAD_DIR", str(tmp_path / "uploads"))
    from app.config import get_settings
    get_settings.cache_clear()

    engine = create_engine(f"sqlite:///{tmp_path}/t.db",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    F = sessionmaker(bind=engine, expire_on_commit=False)
    s = F()
    from app.services import bd_tasks
    line = BdLine(name="テスト線", name_norm="テスト線", operator="テスト鉄道",
                  operator_short="テスト", kind="private", prefs="13",
                  n_station=2)
    s.add(line)
    s.flush()
    for nm in ("甲駅", "乙駅"):
        s.add(BdStation(name=nm, name_norm=bd_tasks.norm_name(nm), line="テスト線",
                        line_id=line.id, operator="テスト鉄道", pref="13",
                        source="mlit"))
    s.flush()
    bd_places.rebuild_places(s)
    s.add(BdTeam(name="小川队", code="XCD"))
    s.commit()
    s.close()

    monkeypatch.setattr("app.db.SessionLocal", F)
    monkeypatch.setattr(scenario_ops, "SessionLocal", F)
    monkeypatch.setattr(scenario_ops, "actor_from_ctx", lambda ctx: W)
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: W)
    yield F
    get_settings.cache_clear()
    engine.dispose()


def _ctx():
    return types.SimpleNamespace(headers={})


# ---------------------------------------------------------------- 权限

def test_preview_requires_write_scope(factory, tmp_path, monkeypatch):
    """只读 Token 不能调（管理员专属写工具）。"""
    monkeypatch.setattr(scenario_ops, "actor_from_ctx", lambda ctx: R)
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: R)
    got = scenario_ops.visit_task_import(
        _ctx(), content_base64=_xlsx_b64(tmp_path, [_row("甲駅")]))
    assert got["ok"] is False
    assert got["error"]["code"] == "FORBIDDEN_TOOL"


# ---------------------------------------------------------------- 参数

def test_bad_action(factory, tmp_path):
    got = scenario_ops.visit_task_import(
        _ctx(), content_base64=_xlsx_b64(tmp_path, [_row("甲駅")]), action="乱写")
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_PARAM"


def test_needs_some_source(factory):
    got = scenario_ops.visit_task_import(_ctx())
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_REQUEST"


def test_cols_without_station_is_rejected(factory, tmp_path):
    got = scenario_ops.visit_task_import(
        _ctx(), content_base64=_xlsx_b64(tmp_path, [_row("甲駅")]),
        cols={"line": 1, "team": 3})
    assert got["ok"] is False
    assert got["error"]["code"] == "BAD_REQUEST"


# ---------------------------------------------------------------- 预览

def test_preview_is_read_only_and_returns_plan(factory, tmp_path):
    got = scenario_ops.visit_task_import(
        _ctx(), filename="plan.xlsx",
        content_base64=_xlsx_b64(tmp_path, [_row("甲駅"), _row("乙駅")]))
    assert got["ok"] is True
    d = got["data"]
    assert d["applied"] is False
    assert d["blob_sha"]                      # 确认导入要靠它
    assert d["summary"]["create_stations"] == 2
    assert d["summary"]["teams"] == ["小川队"]
    assert d["layout"]["header_row"] == 3      # 自动跳过标题/说明行
    assert {r["decision"] for r in d["rows"]} == {"create"}
    # 预览绝不写库
    s = factory()
    assert s.query(BdTask).count() == 0
    s.close()


def test_preview_rejects_file_without_team_column(factory, tmp_path):
    """没有「负责队」列 → 拒收 + 可操作的提示（不猜队名）。"""
    path = str(tmp_path / "no_team.xlsx")
    write_workbook(path, [("计划", [["线路", "车站", "担当", "状态"]],
                           [["テスト線", "甲駅", "某人", "未开始"]])])
    with open(path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode()
    got = scenario_ops.visit_task_import(_ctx(), content_base64=b64)
    assert got["ok"] is False
    assert got["error"]["code"] == "TASK_IMPORT_REJECTED"
    assert "负责队" in got["error"]["message"]
    assert got["error"]["retryable"] is False


# ---------------------------------------------------------------- 确认导入

def test_apply_creates_tasks_for_the_file_team(factory, tmp_path):
    b64 = _xlsx_b64(tmp_path, [_row("甲駅"), _row("乙駅")])
    pre = scenario_ops.visit_task_import(_ctx(), content_base64=b64)
    got = scenario_ops.visit_task_import(_ctx(), action="apply",
                                         blob_sha=pre["data"]["blob_sha"])
    assert got["ok"] is True
    assert got["data"]["created"] == 2
    assert got["data"]["applied"] is True
    s = factory()
    tasks = s.query(BdTask).all()
    team = s.query(BdTeam).filter(BdTeam.name == "小川队").first()
    assert len(tasks) == 2
    assert all(t.team_id == team.id for t in tasks)
    s.close()


def test_apply_twice_never_touches_existing(factory, tmp_path):
    """重复导入不会改已有任务的归属（用户红线）。"""
    b64 = _xlsx_b64(tmp_path, [_row("甲駅"), _row("乙駅")])
    pre = scenario_ops.visit_task_import(_ctx(), content_base64=b64)
    scenario_ops.visit_task_import(_ctx(), action="apply",
                                   blob_sha=pre["data"]["blob_sha"])
    s = factory()
    before = {t.id: t.team_id for t in s.query(BdTask).all()}
    s.close()

    got = scenario_ops.visit_task_import(_ctx(), action="apply",
                                         blob_sha=pre["data"]["blob_sha"])
    assert got["ok"] is True
    assert got["data"]["created"] == 0
    assert got["data"]["existing_untouched"] == 2
    s = factory()
    assert {t.id: t.team_id for t in s.query(BdTask).all()} == before
    s.close()


def test_apply_refuses_unmatched_then_skips(factory, tmp_path):
    """对不上的行没跳过 → 拒收且一条不写；放进 skip_rows → 其余照导。"""
    b64 = _xlsx_b64(tmp_path, [_row("甲駅"), _row("不存在的駅")])
    pre = scenario_ops.visit_task_import(_ctx(), content_base64=b64)
    assert pre["data"]["summary"]["bad"] == 1
    bad_row = [r for r in pre["data"]["rows"] if r["decision"] == "bad"][0]["row"]

    got = scenario_ops.visit_task_import(_ctx(), action="apply",
                                         blob_sha=pre["data"]["blob_sha"])
    assert got["ok"] is False
    assert got["error"]["code"] == "TASK_IMPORT_REJECTED"
    s = factory()
    assert s.query(BdTask).count() == 0
    s.close()

    got = scenario_ops.visit_task_import(_ctx(), action="apply",
                                         blob_sha=pre["data"]["blob_sha"],
                                         skip_rows=[bad_row])
    assert got["ok"] is True
    assert got["data"]["created"] == 1
    s = factory()
    assert s.query(BdTask).count() == 1
    s.close()


def test_apply_with_unknown_blob_sha(factory):
    got = scenario_ops.visit_task_import(_ctx(), action="apply",
                                         blob_sha="0" * 64)
    assert got["ok"] is False
    assert got["error"]["code"] == "NOT_FOUND"


def test_audit_row_records_tool_name(factory, tmp_path):
    """审计里记的是工具名（不是包装函数名）。"""
    scenario_ops.visit_task_import(
        _ctx(), content_base64=_xlsx_b64(tmp_path, [_row("甲駅")]))
    s = factory()
    names = {a.tool for a in s.query(McpAuditLog).all()}
    assert "visit_task_import" in names
    s.close()
