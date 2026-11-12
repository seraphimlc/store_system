# -*- coding: utf-8 -*-
"""只读守卫（spec §8 T3）：mode=ro URI 必须结构性拦截写操作。"""
import sqlite3

import pytest
from sqlalchemy import create_engine, text


def test_write_attempt_blocked(tmp_path):
    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    con.commit()
    con.close()

    engine = create_engine(
        f"sqlite:///file:{path}?mode=ro&uri=true",
        connect_args={"check_same_thread": False})
    try:
        with engine.connect() as c:
            with pytest.raises(Exception) as exc:
                c.execute(text("INSERT INTO t VALUES (1)"))
            assert "readonly" in str(exc.value).lower()
    finally:
        engine.dispose()


def test_read_works_in_readonly_mode(tmp_path):
    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE t (id INTEGER PRIMARY KEY);")
    con.execute("INSERT INTO t VALUES (7);")
    con.commit()
    con.close()

    engine = create_engine(
        f"sqlite:///file:{path}?mode=ro&uri=true",
        connect_args={"check_same_thread": False})
    try:
        with engine.connect() as c:
            assert c.execute(text("SELECT id FROM t")).scalar() == 7
    finally:
        engine.dispose()
