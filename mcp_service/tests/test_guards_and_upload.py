# -*- coding: utf-8 -*-
"""闸门与上传编排（spec §7 闸门 / §6.2 上传链路）。

上传用 mock 验证**编排顺序**（require_write → 上传 → 解析 → 封账 → 判定 → 入表），
真实文件的端到端由用户在 WorkBuddy 里实测。
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, SealedMonth, User
from mcp_service import guards, tokens, write_ops

ADMIN_W = tokens.Actor(uid=1, role="admin", scopes=["read", "write"], token_id=1)
ADMIN_R = tokens.Actor(uid=1, role="admin", scopes=["read"], token_id=1)
STAFF_W = tokens.Actor(uid=2, role="staff", scopes=["read", "write"], token_id=2)


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/w.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add(User(username="admin", password_hash="x", role="admin", is_active=True))
    s.add(SealedMonth(month="2026-08", note="8月封账"))
    s.commit()
    yield s
    s.close()
    eng.dispose()


# ---------- 权限闸门 ----------

def test_require_write_allows_admin_with_write():
    guards.require_write(ADMIN_W)


def test_require_write_rejects_read_only_token():
    with pytest.raises(guards.GuardError) as e:
        guards.require_write(ADMIN_R)
    assert e.value.code == "FORBIDDEN_TOOL"


def test_require_write_rejects_staff():
    with pytest.raises(guards.GuardError) as e:
        guards.require_write(STAFF_W)
    assert e.value.code == "FORBIDDEN_TOOL"


def test_require_write_rejects_none():
    with pytest.raises(guards.GuardError) as e:
        guards.require_write(None)
    assert e.value.code == "UNAUTHORIZED"


# ---------- 参数闸门 ----------

def test_validate_month_and_per_point():
    assert guards.validate_month("2026-09") == "2026-09"
    assert guards.validate_per_point("250") == 250
    for bad in ("2026-9", "２０２６-09", ""):
        with pytest.raises(guards.GuardError) as e:
            guards.validate_month(bad)
        assert e.value.code == "BAD_MONTH"
    for bad in (0, -5, "abc", None):
        with pytest.raises(guards.GuardError) as e:
            guards.validate_per_point(bad)
        assert e.value.code == "BAD_PARAM"


def test_assert_confirm():
    guards.assert_confirm("确认重算 2026-09", "确认重算 2026-09")
    with pytest.raises(guards.GuardError) as e:
        guards.assert_confirm("重算一下", "确认重算 2026-09")
    assert e.value.code == "CONFIRM_REQUIRED"


# ---------- 封账闸门 ----------

def test_sealed_months_supports_set(db):
    assert guards.sealed_months(db, ["2026-08", "2026-09"]) == ["2026-08"]
    assert guards.sealed_months(db, ["2026-09"]) == []


def test_assert_not_sealed_lists_hits(db):
    with pytest.raises(guards.GuardError) as e:
        guards.assert_not_sealed(db, ["2026-07", "2026-08"])
    assert e.value.code == "MONTH_SEALED"
    assert "2026-08" in e.value.message


# ---------- 源守卫 ----------

def test_assert_has_source_ok_when_raw_exists(db):
    guards.assert_has_source(db, "2026-09")      # 空库：raw=0 且 formal=0 → 放行


# ---------- 上传编排（mock）----------

def _mock_upload(monkeypatch, months=("2026-09",), dup=False, parse_failed=False):
    calls = []

    class _Imp:
        id = 7
        file_name = "9月巡店.xlsx"
        parsed_rows = 120
        format = "宽表"
        status = "parsed"
        errors = []

    class _Dup(Exception):
        pass

    class _UpErr(Exception):
        pass

    def upload_and_store(filename, content, user_id, db):
        calls.append(("upload", filename, len(content), user_id))
        if dup:
            raise _Dup("该文件已存在（同内容 sha256）")
        return _Imp()

    def parse_file(imp, db):
        calls.append(("parse", imp.id))
        if parse_failed:
            imp.status = "failed"
            imp.errors = ["表头无法识别"]

    def process_import(db, fid):
        calls.append(("process", fid))
        return {"judge": {"valid": 100, "blank": 20}}

    def auto_finalize_pipeline(db, fid, uid):
        calls.append(("finalize", fid, uid))
        return {"ok": True, "added": 100}

    from app.services import flow, importer
    monkeypatch.setattr(importer, "upload_and_store", upload_and_store)
    monkeypatch.setattr(importer, "parse_file", parse_file)
    monkeypatch.setattr(importer, "DuplicateUpload", _Dup)
    monkeypatch.setattr(importer, "UploadError", _UpErr)
    monkeypatch.setattr(flow, "process_import", process_import)
    monkeypatch.setattr(flow, "auto_finalize_pipeline", auto_finalize_pipeline)
    monkeypatch.setattr(write_ops, "_file_months", lambda db, fid: set(months))
    return calls


def test_upload_full_chain_order(db, monkeypatch):
    calls = _mock_upload(monkeypatch)
    res = write_ops.upload_file(db, ADMIN_W, filename="9月巡店.xlsx",
                                content=b"x" * 10)
    assert res["ok"] is True
    assert res["data"]["formal_added"] == 100
    assert res["data"]["judge"]["valid"] == 100
    assert [c[0] for c in calls] == ["upload", "parse", "process", "finalize"]


def test_upload_rejects_read_only_token(db, monkeypatch):
    _mock_upload(monkeypatch)
    with pytest.raises(guards.GuardError) as e:
        write_ops.upload_file(db, ADMIN_R, filename="a.xlsx", content=b"x")
    assert e.value.code == "FORBIDDEN_TOOL"


def test_upload_duplicate_maps_to_error(db, monkeypatch):
    _mock_upload(monkeypatch, dup=True)
    with pytest.raises(guards.GuardError) as e:
        write_ops.upload_file(db, ADMIN_W, filename="a.xlsx", content=b"x")
    assert e.value.code == "DUPLICATE_FILE"


def test_upload_parse_failure_returns_envelope(db, monkeypatch):
    _mock_upload(monkeypatch, parse_failed=True)
    res = write_ops.upload_file(db, ADMIN_W, filename="a.xlsx", content=b"x")
    assert res["ok"] is False
    assert res["error"]["code"] == "PARSE_FAILED"


def test_upload_refuses_sealed_month_before_finalize(db, monkeypatch):
    """文件涉及封账月 → 拒绝且**不进入判定/入表**（spec §7 闸门 3）。"""
    calls = _mock_upload(monkeypatch, months=("2026-08", "2026-09"))
    with pytest.raises(guards.GuardError) as e:
        write_ops.upload_file(db, ADMIN_W, filename="a.xlsx", content=b"x")
    assert e.value.code == "MONTH_SEALED"
    assert "2026-08" in e.value.message
    assert ("process", 7) not in calls and ("finalize", 7, 1) not in calls


def test_upload_path_requires_local_flag(db, monkeypatch):
    """远端部署默认禁止本地路径（任意文件读取风险）。"""
    _mock_upload(monkeypatch)
    monkeypatch.delenv("VISIT_MCP_ALLOW_LOCAL_PATH", raising=False)
    with pytest.raises(guards.GuardError) as e:
        write_ops.upload_file(db, ADMIN_W, path="/etc/hosts")
    assert e.value.code == "FORBIDDEN_TOOL"


def test_decode_base64_rejects_garbage():
    with pytest.raises(guards.GuardError) as e:
        write_ops.decode_base64("not-base64!!")
    assert e.value.code == "BAD_PARAM"


def test_decode_base64_roundtrip():
    import base64
    raw = b"hello xlsx"
    assert write_ops.decode_base64(base64.b64encode(raw).decode()) == raw
