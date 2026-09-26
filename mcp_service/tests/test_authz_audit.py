# -*- coding: utf-8 -*-
"""身份认证与授权验收（规格 docs/specs-mcp-identity.md 第五节）。

安全核心由子代理实现但未带测试（上下文耗尽），此文件补齐**最关键**的验收：
- 员工越权必须被拦住（且无副作用）
- 员工只能用"我的"工具、只能看本人
- token 状态跟随员工状态（休假即失效）
- 读写全记审计（含被拒绝的调用）
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import ApiToken, Base, McpAuditLog, User
from mcp_service import authz, tokens


def _staff(**kw):
    base = dict(uid=1, role="staff", scopes=["read"], token_id=1,
                person_code="P001")
    base.update(kw)
    return tokens.Actor(**base)


def _admin(**kw):
    base = dict(uid=2, role="admin", scopes=["read", "write"], token_id=2,
                person_code=None)
    base.update(kw)
    return tokens.Actor(**base)


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/a.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add(User(id=1, username="emp1", password_hash="x", role="staff",
               person_code="P001", status="active", is_active=True))
    s.add(User(id=2, username="admin", password_hash="x", role="admin",
               status="active", is_active=True))
    s.commit()
    yield s
    s.close()
    eng.dispose()


# ---------- 1) 员工越权被拦住 ----------

@pytest.mark.parametrize("tool", [
    "visit_upload_file", "visit_rebuild_month", "visit_month_salary",
    "visit_export_salary", "visit_set_per_point", "visit_staff_set_status",
    "visit_store_merge_pair", "visit_config_set",
])
def test_staff_forbidden_on_admin_tools(tool):
    denied = authz.enforce(tool, _staff(), {})
    assert denied is not None
    assert denied["error"]["code"] == "FORBIDDEN_TOOL"
    assert denied["error"]["retryable"] is False


def test_unknown_tool_defaults_to_admin_only():
    """未登记的工具默认拒绝员工（安全的失败方向）。"""
    assert authz.require_role("visit_some_brand_new_tool") == "ADMIN_ONLY"
    assert authz.enforce("visit_some_brand_new_tool", _staff(), {}) is not None


def test_staff_allowed_tools():
    for tool in ("visit_ping", "visit_my_perf", "visit_my_daily",
                 "visit_my_settlement"):
        assert authz.enforce(tool, _staff(), {}) is None, tool


# ---------- 2) "我的"工具只能看本人 ----------

def test_my_tool_rejects_other_person():
    denied = authz.enforce("visit_my_perf", _staff(), {"person": "P999"})
    assert denied is not None and denied["error"]["code"] == "FORBIDDEN_TOOL"


def test_my_tool_normalizes_own_person():
    params = {"person": "P001"}
    assert authz.enforce("visit_my_perf", _staff(), params) is None
    assert "person" not in params          # 相符 → 规整掉，业务只看 actor


def test_my_tool_without_person_ok():
    assert authz.enforce("visit_my_settlement", _staff(), {}) is None


# ---------- 3) 管理员不受限 ----------

def test_admin_allowed_everywhere():
    for tool in ("visit_upload_file", "visit_rebuild_month",
                 "visit_month_salary", "visit_my_perf", "visit_config_set"):
        assert authz.enforce(tool, _admin(), {}) is None, tool


def test_unknown_role_denied():
    a = tokens.Actor(uid=9, role="guest", scopes=[], token_id=None)
    assert authz.enforce("visit_month_salary", a, {}) is not None


# ---------- 4) 无身份：写/导出拒绝，读放行（既有行为） ----------

def test_unauthenticated_write_denied_read_allowed():
    assert authz.enforce("visit_upload_file", None, {})["error"]["code"] == "UNAUTHORIZED"
    assert authz.enforce("visit_export_salary", None, {})["error"]["code"] == "UNAUTHORIZED"
    assert authz.enforce("visit_ping", None, {}) is None


# ---------- 5) token 状态跟随员工状态 ----------

def _issue(db, uid=1, days=90):
    raw, row = tokens.issue(db, uid, "t", "read", days)
    return raw, row


def test_resolve_ok_then_revoked(db):
    raw, row = _issue(db)
    actor, reason = tokens.resolve_with_reason(db, raw)
    assert actor is not None and actor.person_code == "P001"
    tokens.revoke(db, row.id, 1)
    actor2, reason2 = tokens.resolve_with_reason(db, raw)
    assert actor2 is None and reason2 == "revoked"


def test_resolve_expired(db):
    raw, row = _issue(db)
    row.expires_at = datetime.utcnow() - timedelta(days=1)
    db.commit()
    actor, reason = tokens.resolve_with_reason(db, raw)
    assert actor is None and reason == "expired"


def test_resolve_follows_user_status_leave(db):
    """员工休假 → token 立即失效（用户明确要求）。"""
    raw, row = _issue(db)
    u = db.get(User, 1)
    u.status = "leave"                    # is_active 仍为 True
    db.commit()
    actor, reason = tokens.resolve_with_reason(db, raw)
    assert actor is None and reason == "user_status:leave"
    u.status = "active"                   # 复工 → 恢复
    db.commit()
    assert tokens.resolve_with_reason(db, raw)[0] is not None


def test_resolve_follows_user_disabled(db):
    raw, row = _issue(db)
    u = db.get(User, 1)
    u.status = "disabled"
    u.is_active = False
    db.commit()
    actor, reason = tokens.resolve_with_reason(db, raw)
    assert actor is None and reason == "user_status:disabled"


def test_permanent_token_has_no_expiry(db):
    raw, row = _issue(db, days=None)
    assert row.expires_at is None
    assert tokens.resolve_with_reason(db, raw)[0] is not None


# ---------- 6) 读写全记审计（含被拒绝） ----------

def test_dispatch_records_success_audit(db):
    res = authz.dispatch(db, tool="visit_ping", actor=_admin(), params={},
                         client_info="t", fn=lambda d: {"ok": True, "data": {}},
                         retryable=True, fn_args=1)
    assert res["ok"] is True
    rows = db.query(McpAuditLog).filter(McpAuditLog.tool == "visit_ping").all()
    assert len(rows) == 1
    assert rows[0].ok is True and rows[0].user_id == 2
    assert rows[0].token_id == 2
    assert rows[0].params_json is not None      # 参数摘要已落库


def test_dispatch_records_denial_audit(db):
    """被拒绝的调用也必须留痕（谁试图越权是最该记的）。"""
    res = authz.dispatch(db, tool="visit_upload_file", actor=_staff(), params={},
                         client_info="t", fn=lambda d, a: {"ok": True},
                         retryable=False, fn_args=2)
    assert res["error"]["code"] == "FORBIDDEN_TOOL"
    rows = db.query(McpAuditLog).filter(
        McpAuditLog.tool == "visit_upload_file").all()
    assert len(rows) == 1
    assert rows[0].ok is False
    assert rows[0].error_code == "FORBIDDEN_TOOL"


def test_audit_redacts_base64(db):
    from mcp_service import audit
    out = audit.redact_params({"content_base64": "x" * 500, "month": "2026-09"})
    s = str(out)
    assert "x" * 100 not in s             # 不记大段 base64
    assert "2026-09" in s
