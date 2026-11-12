# -*- coding: utf-8 -*-
"""写工具四道闸门与信封（spec §6/§7）。

重点覆盖：
- finalize 的封账月份集合 = raw ∪ formal（**raw 已清理时仍能拦住封账月**，评审修正的核心）
- rebuild 的 preview/confirm/源守卫/封账 前置，以及补同步失败只进 warnings
- set_per_point 强制 warm_config（防写错钱）与参数校验
"""
from datetime import date, datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (AppealRecord, Base, FormalRecord, ImportFile,
                        McpAuditLog, RawRecord, SealedMonth)
from mcp_service import guards, tokens, write_tools

W = tokens.Actor(uid=1, role="admin", scopes=["read", "write"], token_id=1)
R = tokens.Actor(uid=1, role="admin", scopes=["read"], token_id=1)


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/w.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add(SealedMonth(month="2026-08", note="8月封账"))
    s.add(ImportFile(id=1, file_name="9月.xlsx", file_sha256="x", file_size=1,
                     stored_path="/tmp/x.xlsx", uploaded_by=1,
                     status="parsed", parsed_sheets=[], ignored_sheets=[],
                     warnings=[], errors=[]))
    s.add(RawRecord(id=1, import_id=1, sheet_name="s", excel_row=2,
                    store_id_raw="0101", store_name_local_raw="店A",
                    store_name_en_raw="", modified_raw="2026-09-01 10:00:00",
                    submitter_raw="甲(1)", submitter_code="P001",
                    record_id_raw="r1", visible_raw="YES", deploy_raw="YES",
                    original_row=[], clean_status="valid",
                    confirm_state="auto_approved"))
    s.commit()
    yield s
    s.close()
    eng.dispose()


# ---------- 权限 ----------

def test_all_write_tools_require_write_scope(db):
    # 注意：rebuild_preview 是**只读预演**，不要求写权限（刻意不含在此）
    for call in (
        lambda: write_tools.finalize_file(db, R, file_id=1),
        lambda: write_tools.rebuild_month(db, R, month="2026-09", preview_id=0,
                                          confirm_text="确认重算 2026-09"),
        lambda: write_tools.set_per_point(db, R, month="2026-09", per_point=250,
                                          confirm_text="确认改单价 2026-09 250"),
    ):
        with pytest.raises(guards.GuardError) as e:
            call()
        assert e.value.code == "FORBIDDEN_TOOL"


# ---------- finalize ----------

def test_finalize_not_found(db):
    with pytest.raises(guards.GuardError) as e:
        write_tools.finalize_file(db, W, file_id=999)
    assert e.value.code == "NOT_FOUND"


def test_finalize_pending_appeal_refused(db):
    db.add(AppealRecord(import_id=1, raw_record_id=1, submitter_code="P001",
                        reason="master_late", status="pending"))
    db.commit()
    with pytest.raises(guards.GuardError) as e:
        write_tools.finalize_file(db, W, file_id=1)
    assert e.value.code == "PENDING_APPEALS"


def test_finalize_refuses_sealed_month_even_when_raw_cleared(db):
    """**关键**：raw 已被清理、formal 仍在封账月 → 必须拒绝。

    只按 raw 推导月份会漏判，导致删掉该文件跨月的正式表（毁封账数据）。
    """
    db.add(FormalRecord(import_id=1, raw_record_id=1, person_code="P001",
                        store_id_raw="0101", japan_date=date(2026, 8, 5),
                        points=1))
    db.query(RawRecord).delete()          # 模拟 raw 已被清理
    db.commit()

    with pytest.raises(guards.GuardError) as e:
        write_tools.finalize_file(db, W, file_id=1)
    assert e.value.code == "MONTH_SEALED"
    assert "2026-08" in e.value.message


def test_finalize_ok_for_unsealed_month(db, monkeypatch):
    from app.services import flow
    monkeypatch.setattr(flow, "auto_finalize_pipeline",
                        lambda d, fid, uid: {"ok": True, "added": 42})
    res = write_tools.finalize_file(db, W, file_id=1)
    assert res["ok"] is True
    assert res["data"]["formal_rows"] == 42
    assert res["data"]["affected_months"] == ["2026-09"]


# ---------- rebuild_preview ----------

def test_preview_returns_preview_id_and_buckets(db):
    res = write_tools.rebuild_preview(db, W, month="2026-09")
    assert res["ok"] is True
    d = res["data"]
    assert isinstance(d["preview_id"], int)
    assert d["raw_total"] == 1
    assert sum(d["raw_by_status"].values()) == d["raw_total"]   # 含 other 残差桶
    assert d["estimated_insert_rows"] == d["raw_by_status"]["valid"]
    row = db.get(McpAuditLog, d["preview_id"])
    assert row.tool == "visit_rebuild"       # 场景化后 preview 归入 visit_rebuild(action='preview')
    assert "2026-09" in (row.params_json or "")     # 月份存在 params_json（无 month 列）


def test_preview_refuses_sealed_month(db):
    with pytest.raises(guards.GuardError) as e:
        write_tools.rebuild_preview(db, W, month="2026-08")
    assert e.value.code == "MONTH_SEALED"


# ---------- rebuild_month ----------

def _preview_id(db):
    return write_tools.rebuild_preview(db, W, month="2026-09")["data"]["preview_id"]


def test_rebuild_requires_valid_preview(db):
    with pytest.raises(guards.GuardError) as e:
        write_tools.rebuild_month(db, W, month="2026-09", preview_id=999,
                                  confirm_text="确认重算 2026-09")
    assert e.value.code == "PREVIEW_REQUIRED"


def test_rebuild_requires_confirm(db):
    pid = _preview_id(db)
    with pytest.raises(guards.GuardError) as e:
        write_tools.rebuild_month(db, W, month="2026-09", preview_id=pid,
                                  confirm_text="重算吧")
    assert e.value.code == "CONFIRM_REQUIRED"


def test_rebuild_source_guard_blocks_empty_month(db):
    """raw=0 且 formal>0 → NO_SOURCE_ROWS（且正式表不变）。"""
    db.add(FormalRecord(import_id=1, raw_record_id=1, person_code="P001",
                        store_id_raw="0101", japan_date=date(2026, 9, 5), points=1))
    db.commit()
    pid = _preview_id(db)
    db.query(RawRecord).delete()
    db.commit()
    before = db.query(FormalRecord).count()
    with pytest.raises(guards.GuardError) as e:
        write_tools.rebuild_month(db, W, month="2026-09", preview_id=pid,
                                  confirm_text="确认重算 2026-09")
    assert e.value.code == "NO_SOURCE_ROWS"
    assert db.query(FormalRecord).count() == before


def test_rebuild_ok_with_warnings_on_sync_failure(db, monkeypatch):
    """补同步失败 → ok:true + warnings（**绝不** INTERNAL_WRITE）。"""
    from app.services import flow
    monkeypatch.setattr(flow, "rebuild_month",
                        lambda d, m, actor_id=None: {"ok": True, "formal_after": 10,
                                                     "points_after": 12})
    from app.services import period
    monkeypatch.setattr(period, "sync_period_table",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("sync down")))
    pid = _preview_id(db)
    res = write_tools.rebuild_month(db, W, month="2026-09", preview_id=pid,
                                    confirm_text="确认重算 2026-09")
    assert res["ok"] is True
    assert res["data"]["warnings"]


# ---------- set_per_point ----------

def test_set_per_point_requires_confirm(db):
    with pytest.raises(guards.GuardError) as e:
        write_tools.set_per_point(db, W, month="2026-09", per_point=250,
                                  confirm_text="改吧")
    assert e.value.code == "CONFIRM_REQUIRED"


def test_set_per_point_rejects_bad_value(db):
    with pytest.raises(guards.GuardError) as e:
        write_tools.set_per_point(db, W, month="2026-09", per_point=0,
                                  confirm_text="确认改单价 2026-09 0")
    assert e.value.code == "BAD_PARAM"


def test_set_per_point_refuses_sealed_month(db):
    with pytest.raises(guards.GuardError) as e:
        write_tools.set_per_point(db, W, month="2026-08", per_point=250,
                                  confirm_text="确认改单价 2026-08 250")
    assert e.value.code == "MONTH_SEALED"


def test_set_per_point_warms_config_before_writing(db, monkeypatch):
    """必须 warm_config：冷缓存下奖金会退回 env 默认 → 写错钱。"""
    called = {}
    monkeypatch.setattr(write_tools.guards, "ensure_config_warmed",
                        lambda d, m: called.setdefault("month", m))
    from app.services import perf
    monkeypatch.setattr(perf, "set_month_per_point",
                        lambda d, m, pp: {"ok": True, "per_point": pp})
    res = write_tools.set_per_point(db, W, month="2026-09", per_point=260,
                                    confirm_text="确认改单价 2026-09 260")
    assert called.get("month") == "2026-09"
    assert res["data"]["per_point"] == 260


def test_no_register_in_write_tools():
    """场景化重构：write_tools 不再注册工具（能力函数供 scenario_ops 调用）。"""
    assert not hasattr(write_tools, "register")
