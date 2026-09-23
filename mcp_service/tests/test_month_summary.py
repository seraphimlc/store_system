# -*- coding: utf-8 -*-
"""visit_month_summary 的聚合口径（spec §5.4）。

用临时 SQLite 造数据，绝不碰真实库。
注意 fixture 用本地 create_engine 而非 app.db.SessionLocal——app/db.py 的
引擎在 import 时全局缓存（计划坑 1），monkeypatch env 对已缓存的引擎无效。
"""
import sqlite3

import pytest
from sqlalchemy import create_engine

from mcp_service import capability

SCHEMA = """
CREATE TABLE formal_records (
    id INTEGER PRIMARY KEY, import_id INTEGER NOT NULL,
    raw_record_id INTEGER NOT NULL, person_code VARCHAR(32),
    store_id_raw TEXT NOT NULL, japan_date DATE, points INTEGER NOT NULL,
    created_at DATETIME NOT NULL);
"""


@pytest.fixture()
def conn(tmp_path):
    path = tmp_path / "t.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA + """
        INSERT INTO formal_records VALUES
            (1,1,1,'P001','st-a','2026-09-01',1,'2026-09-22 00:00:00'),
            (2,1,2,'P001','st-b','2026-09-02',2,'2026-09-22 00:00:00'),
            (3,1,3,'P002','st-c','2026-09-03',1,'2026-09-22 00:00:00'),
            (4,1,4,'P003','st-d','2026-10-01',1,'2026-09-22 00:00:00');
        CREATE TABLE persons (id INTEGER PRIMARY KEY, code VARCHAR(32));
        INSERT INTO persons VALUES (1,'P001'),(2,'P002'),(3,'P003'),(4,'P999');
    """)
    con.commit()
    con.close()
    engine = create_engine(
        f"sqlite:///file:{path}?mode=ro&uri=true",
        connect_args={"check_same_thread": False})
    yield engine.connect()
    engine.dispose()


def test_summary_counts(conn):
    got = capability.month_summary(conn, "2026-09")
    assert got == {"month": "2026-09", "formal_rows": 3, "points_total": 4,
                   "p1_count": 2, "p2_count": 1, "persons": 2}


def test_persons_is_distinct_person_code_not_persons_table(conn):
    """persons 表有 4 行，本月只有 2 人——必须返回 2。"""
    assert capability.month_summary(conn, "2026-09")["persons"] == 2


def test_october_row_excluded(conn):
    """范围过滤必须排除次月第一天。"""
    assert capability.month_summary(conn, "2026-09")["formal_rows"] == 3


def test_empty_month_returns_zeros_not_error(conn):
    got = capability.month_summary(conn, "2026-08")
    assert got == {"month": "2026-08", "formal_rows": 0, "points_total": 0,
                   "p1_count": 0, "p2_count": 0, "persons": 0}


def test_bad_month_raises(conn):
    with pytest.raises(capability.BadMonth):
        capability.month_summary(conn, "2026-9")


def test_fullwidth_month_rejected(conn):
    """不用 \\d：全角会放行并静默返回 0 行（spec §5.4）。"""
    with pytest.raises(capability.BadMonth):
        capability.month_summary(conn, "２０２６-09")


def test_month_too_long_rejected(conn):
    with pytest.raises(capability.BadMonth):
        capability.month_summary(conn, "2026-13")


def test_month_with_day_rejected(conn):
    with pytest.raises(capability.BadMonth):
        capability.month_summary(conn, "2026-09-01")


def test_year_boundary_rolls_over(tmp_path):
    """12 月 -> 次年 1 月的边界必须正确（_next_month 的逻辑）。"""
    path = tmp_path / "y.db"
    con = sqlite3.connect(path)
    con.executescript(SCHEMA + """
        INSERT INTO formal_records VALUES
            (5,1,5,'P004','st-e','2026-12-31',2,'2026-12-31 00:00:00'),
            (6,1,6,'P005','st-f','2027-01-01',1,'2027-01-01 00:00:00');
    """)
    con.commit()
    con.close()
    engine = create_engine(
        f"sqlite:///file:{path}?mode=ro&uri=true",
        connect_args={"check_same_thread": False})
    try:
        got = capability.month_summary(engine.connect(), "2026-12")
        assert got["formal_rows"] == 1
        assert got["points_total"] == 2
    finally:
        engine.dispose()
