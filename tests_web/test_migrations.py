# -*- coding: utf-8 -*-
"""迁移冒烟测试：**真的跑一遍 alembic**，并核对 ORM 与迁移建出的 schema 是否一致。

做这组测试的原因（2026-09-28 评审教训）：
本地一直用 `Base.metadata.create_all` 建表，迁移从未被执行过 —— 结果 c9d0e1f2a3b4
里 `person_code` 漏声明，`alembic upgrade head` 直接失败（线上 entrypoint 会因此起不来），
而 200 个测试全绿。**只有真跑迁移才抓得到这类问题。**
"""
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
NEW_TABLES = ("staff_daily_reports", "staff_report_analyses",
              "staff_report_compare_person", "staff_report_compare_day",
              "form_tokens")


def _alembic(db_path, *args):
    env = dict(os.environ, DATABASE_URL="sqlite:///%s" % db_path)
    return subprocess.run([sys.executable, "-m", "alembic"] + list(args),
                          cwd=ROOT, env=env, capture_output=True, text=True)


def test_alembic_upgrade_head_on_fresh_db(tmp_path):
    """全新库上 `alembic upgrade head` 必须成功，且新表齐全、版本到 head。"""
    db = str(tmp_path / "fresh.db")
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, "迁移失败：\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
    con = sqlite3.connect(db)
    tables = {row[0] for row in con.execute(
        "select name from sqlite_master where type='table'")}
    for t in NEW_TABLES:
        assert t in tables, "迁移后缺表：%s" % t
    head = con.execute("select version_num from alembic_version").fetchone()[0]
    con.close()
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    expected = ScriptDirectory.from_config(Config(os.path.join(ROOT, "alembic.ini"))).get_current_head()
    assert head == expected, "版本未到 head：%s != %s" % (head, expected)


def test_alembic_has_single_head():
    """单一 head（多 head 会让 `alembic upgrade head` 报错）。"""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    heads = ScriptDirectory.from_config(
        Config(os.path.join(ROOT, "alembic.ini"))).get_heads()
    assert len(heads) == 1, "存在多个 head：%s" % heads


def test_migration_schema_matches_orm(tmp_path):
    """迁移建出的表结构必须与 ORM 一致（列名 / 可空性），防止两边悄悄漂移。"""
    from sqlalchemy import create_engine, inspect

    from app.db import Base
    import app.models  # noqa: F401  注册全部表

    db = str(tmp_path / "cmp.db")
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    insp = inspect(create_engine("sqlite:///%s" % db))
    problems = []
    for table in NEW_TABLES:
        mig_cols = {c["name"]: c["nullable"] for c in insp.get_columns(table)}
        orm_cols = {c.name: c.nullable for c in Base.metadata.tables[table].columns}
        if set(mig_cols) != set(orm_cols):
            problems.append("%s 列不同：迁移多 %s / ORM 多 %s" % (
                table, sorted(set(mig_cols) - set(orm_cols)),
                sorted(set(orm_cols) - set(mig_cols))))
            continue
        for name, nullable in orm_cols.items():
            if mig_cols[name] != nullable:
                problems.append("%s.%s 可空性不同：迁移=%s ORM=%s" % (
                    table, name, mig_cols[name], nullable))
    assert not problems, "迁移与 ORM 不一致：\n  " + "\n  ".join(problems)
