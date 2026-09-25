# -*- coding: utf-8 -*-
"""P0–P2 验收测试（docs/workbuddy-mcp-feedback.md 验收表逐项）。

A12 交付了实现但未写完测试（上下文耗尽），父会话补齐。
覆盖：参数互斥 / 错误信封 retryable / is_overwrite / list_tasks / list_months /
dry_run 零写入 / annotations / kind 一致性。
"""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import (Base, FormalRecord, ImportFile, Person, ReconTask,
                        SealedMonth)
from mcp_service import guards, read_ops, recon_write_ops, tokens, write_ops

W = tokens.Actor(uid=1, role="admin", scopes=["read", "write"], token_id=1)


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/f.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add(Person(code="P001", display_name="甲"))
    s.add(ImportFile(id=1, file_name="9月.xlsx", file_sha256="x", file_size=1,
                     stored_path="/tmp/x.xlsx", uploaded_by=1, status="parsed",
                     parsed_sheets=[], ignored_sheets=[], warnings=[], errors=[]))
    s.add(ReconTask(id=1, kind="monthly_v3", status="done", created_by=1,
                    params={"month": "2026-09", "kind": "daily_records",
                            "version": 2, "current": True},
                    summary={}, created_at=datetime.utcnow()))
    s.add(ReconTask(id=2, kind="monthly_v3", status="done", created_by=1,
                    params={"month": "2026-09", "kind": "daily_records",
                            "version": 1, "current": True, "replaced_by": 1},
                    summary={}, created_at=datetime.utcnow() - timedelta(days=1)))
    s.commit()
    yield s
    s.close()
    eng.dispose()


# ---------- P0-1 参数互斥 ----------

def test_upload_rejects_neither_source(db):
    with pytest.raises(guards.GuardError) as e:
        write_ops.upload_file(db, W, filename="a.xlsx")
    assert e.value.code == "BAD_PARAM"
    assert "content_base64 或 path" in e.value.hint


def test_upload_rejects_both_sources(db):
    with pytest.raises(guards.GuardError) as e:
        write_ops.upload_file(db, W, filename="a.xlsx", content=b"x" * 4,
                              path="/tmp/x.xlsx")
    assert e.value.code == "BAD_PARAM"
    assert "不可同时提供" in e.value.message


def test_recon_upload_rejects_both_sources(db):
    with pytest.raises(guards.GuardError) as e:
        recon_write_ops.upload_recon(db, W, month="2026-09",
                                     filename="a.xlsx", content=b"x" * 4,
                                     path="/tmp/x.xlsx")
    assert e.value.code == "BAD_PARAM"


# ---------- P0-5 信封 retryable ----------

def test_error_envelope_has_retryable(db):
    """失败信封含 retryable；INTERNAL_WRITE 类必须不可重试。"""
    from mcp_service import envelope
    e = envelope.error("INTERNAL_WRITE", "写异常", "可能已部分生效")
    assert e["error"]["retryable"] is False
    assert e["error"]["hint"] == "可能已部分生效"
    e2 = envelope.error("INTERNAL", "读异常", "")
    assert e2["error"]["retryable"] is True


# ---------- P0-3 is_overwrite ----------

def test_upload_recon_returns_is_overwrite(db, monkeypatch):
    """对账重传 → is_overwrite=true + replaced_previous_ids。"""
    from app.services import recon
    monkeypatch.setattr(recon, "submit_task",
                        lambda d, m, fn, c, uid, sync=True: (
                            type("T", (), {"id": 9, "status": "done"}), [1]))
    from mcp_service import recon_ops
    monkeypatch.setattr(recon_ops, "_recon_status", lambda d, m: {"tasks": []})
    res = recon_write_ops.upload_recon(db, W, month="2026-09",
                                       filename="a.xlsx", content=b"x" * 4)
    assert res["data"]["is_overwrite"] is True
    assert res["data"]["replaced_previous_ids"] == [1]


# ---------- P2-10 list_tasks ----------

def test_list_tasks_filters_and_replaced(db):
    data = read_ops.list_tasks(db, kind="recon", month="2026-09")
    by_id = {t["id"]: t for t in data["tasks"]}
    assert by_id[1]["is_previous"] is False
    assert by_id[1]["version"] == 2
    assert by_id[2]["is_previous"] is True
    assert by_id[1]["replaced_previous_ids"] == [2]
    # kind 过滤：daily_records = ImportFile（fixture 里 1 条）；recon = 对账任务
    data2 = read_ops.list_tasks(db, kind="daily_records")
    assert data2["total"] == 1
    assert data2["tasks"][0]["kind"] == "daily_records"


# ---------- P2-11 list_months ----------

def test_list_months_covers_all_sources(db):
    data = read_ops.list_months(db)
    by = {m["month"]: m for m in data["months"]}
    assert "2026-09" in by
    assert by["2026-09"]["recon_tasks"] == 1


# ---------- P2-12 dry_run 零写入 ----------

def test_upload_dry_run_writes_nothing(db, monkeypatch):
    """dry_run=true：识别+预估，调用前后 imports/raw/formal 行数不变。"""
    from sqlalchemy import text
    before = {
        t: db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
        for t in ("imports", "raw_records", "formal_records", "recon_tasks")
    }
    res = write_ops.upload_file(db, W, filename="a.xlsx",
                                content=b"not an xlsx", dry_run=True)
    assert res["ok"] is True
    assert res["data"]["dry_run"] is True
    after = {
        t: db.execute(text(f"SELECT COUNT(*) FROM {t}")).scalar()
        for t in ("imports", "raw_records", "formal_records", "recon_tasks")
    }
    assert before == after


# ---------- P1-6 annotations ----------

def test_tools_have_annotations():
    import asyncio
    from mcp.server.mcpserver import MCPServer
    from mcp_service import read_ops, recon_ops

    mcp = MCPServer(name="t", version="0.0.0")
    read_ops.register(mcp)
    recon_ops.register(mcp)
    tools = asyncio.run(mcp.list_tools())
    by = {t.name: t for t in tools}
    for name in ("visit_dashboard", "visit_file_list"):
        assert by[name].annotations is not None, name
        assert by[name].annotations.read_only_hint is True, name
        assert by[name].annotations.title, name


# ---------- P1-7 kind 一致性 ----------

def test_recon_response_kind_is_consistent(db):
    """对账任务快照 kind == "recon"（P1-7：不再暴露内部文件 kind）。"""
    from mcp_service import recon_ops
    got = recon_ops._recon_status(db, "2026-09")   # 能力层直调（不需要 ctx）
    by = {t["id"]: t for t in got["tasks"]}
    assert by[1]["kind"] == "recon"
