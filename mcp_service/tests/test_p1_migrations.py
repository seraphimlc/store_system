# -*- coding: utf-8 -*-
"""P1 迁移落地：四表存在 + 2026-08 已预置封账（spec §11）。

本地 SQLite 走 create_all/ALTER（AGENTS.md：本地手动、线上 entrypoint 自动 upgrade）；
本测试只断言最终 schema 状态，不依赖本地 alembic 版本链（旧迁移含 PG 专用 now()，
在 SQLite 上无法整体 upgrade——仓库既有状况）。
"""
import sqlite3
from pathlib import Path

DB = Path(__file__).resolve().parent.parent.parent / "store_settle_live.db"


def _ro():
    return sqlite3.connect(f"file:{DB}?mode=ro", uri=True)


def test_four_tables_exist():
    con = _ro()
    try:
        names = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    finally:
        con.close()
    assert {"api_tokens", "mcp_audit_log", "sealed_months",
            "rebuild_snapshots"} <= names


def test_sealed_month_preseeded():
    con = _ro()
    try:
        rows = con.execute("SELECT month FROM sealed_months").fetchall()
    finally:
        con.close()
    assert ("2026-08",) in rows


def test_token_prefix_unique_index_exists():
    con = _ro()
    try:
        idx = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='index'").fetchall()}
    finally:
        con.close()
    assert any("token_prefix" in name for name in idx)


def test_migration_files_are_chained():
    """两个迁移文件存在且 revision/down_revision 串链正确（生产路径）。"""
    import re
    d = Path(__file__).resolve().parent.parent.parent / "migrations" / "versions"
    f1 = d / "a1b2c3d4e5f7_workbuddy_api_tokens.py"
    f2 = d / "b2c3d4e5f6a8_workbuddy_audit_seal_snapshot.py"
    assert f1.exists() and f2.exists()
    t1 = f1.read_text(encoding="utf-8")
    t2 = f2.read_text(encoding="utf-8")
    assert re.search(r'revision\s*=\s*"a1b2c3d4e5f7"', t1)
    assert re.search(r'down_revision\s*=\s*"f6c7d8e9f0a1"', t1)
    assert re.search(r'revision\s*=\s*"b2c3d4e5f6a8"', t2)
    assert re.search(r'down_revision\s*=\s*"a1b2c3d4e5f7"', t2)
    assert "2026-08" in t2          # 预置封账
