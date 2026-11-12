# -*- coding: utf-8 -*-
"""页面性能守门：这些页面都是"打开很慢"修过的，别再退回去。

用户 2026-10-17："你再检查一下所有页面，还有很多页面打开非常慢。"
线上实测（容器内直连 uvicorn，排掉网络）：

| 页面 | 修前 | 根因 |
|---|---|---|
| `/perf/export` | 9.2s | 1.7s 拉 7 万行只为算月份 + 5.4s 导出（水合 2.2 万行 RawRecord + 末尾 `ws.columns` 全量遍历） |
| `/stores` | 4.0s | 8.3 万行 ORM 全量水合（SQL 只 175ms） |
| `/perf` | 2.3s | 1,766ms 花在拉全表算月份，真业务逻辑只 24ms |
| `/tasks/export` | 1.9s | 1,102 条 SQL：循环里每个车站查 2 条 |

守门方式 = **抓真实执行的 SQL / AST 扫描**，不断言毫秒数（毫秒数在 CI 上不稳）。
"""
import ast
import glob
import io

import pytest

import app.db as appdb


# ---------------------------------------------------------------- 工具

def _sqls(db, fn):
    """执行 fn，返回 (结果, 执行过的 SQL 列表)。"""
    from sqlalchemy import event
    stmts = []

    def _rec(conn, cursor, statement, params, context, executemany):  # noqa: ANN001
        stmts.append(statement)

    engine = db.get_bind()
    event.listen(engine, "before_cursor_execute", _rec)
    try:
        out = fn()
    finally:
        event.remove(engine, "before_cursor_execute", _rec)
    assert stmts, "没抓到 SQL，守门测试本身失效了"
    return out, stmts


def _big_all_scans(path):
    """找出 `db.query(大表).all()`（且**没有任何 filter/limit**）的写法。

    没有过滤的全表 `.all()` = 把整表水合成 ORM 对象 —— 页面 2 秒起步的根因。
    带 `.filter()` 的不算（那是有界的）。
    """
    BIG = {"FormalRecord", "RawRecord", "StoreEntity", "MonthPerfRecord"}
    tree = ast.parse(io.open(path, encoding="utf-8").read())
    out = []
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "all"):
            continue
        recv = node.func.value
        models = {a.id for sub in ast.walk(recv) if isinstance(sub, ast.Call)
                  and isinstance(sub.func, ast.Attribute)
                  and sub.func.attr == "query"
                  for a in sub.args if isinstance(a, ast.Name)}
        if not (models & BIG):
            continue
        if any(isinstance(sub, ast.Call) and isinstance(sub.func, ast.Attribute)
               and sub.func.attr in ("filter", "where", "limit", "offset")
               for sub in ast.walk(recv)):
            continue
        out.append("%s:%d db.query(%s).all()"
                   % (path, node.lineno, "/".join(sorted(models & BIG))))
    return out


# ------------------------------------------------ ① 月份：全表 → SQL DISTINCT

def test_formal_months_is_one_distinct_sql(client):
    """算"有哪几个月"必须一条 `SELECT DISTINCT`，不许把 `formal_records` 拉进 Python。

    原来：`sorted({(str(r.japan_date or ""))[:7] for r in db.query(FormalRecord).all()})`
    —— 线上为 3 个月份拉 7 万行，1,766ms/页。
    """
    from datetime import date
    from app.models import FormalRecord
    from app.services import months as MO
    db = appdb.SessionLocal()
    for i, d in enumerate((date(2026, 7, 3), date(2026, 9, 30),
                           date(2026, 9, 1), None)):
        db.add(FormalRecord(import_id=1, raw_record_id=i + 1, japan_date=d,
                            person_code="P1", points=1))
    db.commit()
    mons, stmts = _sqls(db, lambda: MO.formal_months(db))
    assert mons == ["2026-07", "2026-09"], mons
    assert len(stmts) == 1, stmts
    assert "DISTINCT" in stmts[0].upper(), stmts[0]
    assert "japan_date" in stmts[0]
    db.close()


def test_months_helpers_match_old_python_semantics(client):
    """新 SQL 与旧口径**逐值一致**（含空值/空串：两边都不产生月份）。"""
    from datetime import date
    from app.models import (FormalRecord, MonthPerfRecord, PayrollPeriodRow,
                            PersonDailyStat)
    from app.services import months as MO
    db = appdb.SessionLocal()
    for i, d in enumerate((date(2026, 8, 1), date(2026, 8, 31),
                           date(2026, 10, 2), None)):
        db.add(FormalRecord(import_id=1, raw_record_id=i + 1, japan_date=d,
                            person_code="P1", points=1))
    for mo in ("2026-08", "2026-10"):
        db.add(MonthPerfRecord(month=mo, person_code="P1"))
        db.add(PayrollPeriodRow(month=mo, person_code="P1"))
    for i, d in enumerate((date(2026, 6, 30), date(2026, 10, 1))):
        db.add(PersonDailyStat(person_code="P1", ref_date=d, points=1))
    db.commit()
    assert MO.formal_months(db) == sorted(
        {(str(r.japan_date or ""))[:7] for r in db.query(FormalRecord).all()
         if r.japan_date})
    assert MO.perf_months(db) == sorted({r.month for r in
                                         db.query(MonthPerfRecord).all()})
    assert MO.payroll_months(db) == sorted({r.month for r in
                                            db.query(PayrollPeriodRow).all()})
    # 日统计带 since 过滤：口径也是"前缀比较"
    assert MO.stat_months(db) == sorted(
        {(str(r.ref_date or ""))[:7] for r in db.query(PersonDailyStat).all()
         if r.ref_date})
    assert MO.stat_months(db, since="2026-07") == ["2026-10"]
    # 发薪结算页 = 日统计 ∪ 发薪行（两边都只看 since 之后）
    assert MO.settle_months(db, since="2026-07") == ["2026-08", "2026-10"]
    db.close()


def test_routers_never_scan_big_tables_wholesale():
    """路由里不许出现"没有任何过滤"的大表 `.all()`（页面慢的根因都在这几行）。"""
    bad = []
    for path in sorted(glob.glob("app/routers/*.py")):
        bad += _big_all_scans(path)
    assert not bad, "路由里整表拉大表（页面会慢到几秒）：\n  " + "\n  ".join(bad)


def test_guard_itself_catches_the_old_bug(tmp_path):
    """守门测试自身要有效：拿"旧写法"试一下必须被抓到。"""
    p = tmp_path / "fake_router.py"
    p.write_text(
        "def refresh(db):\n"
        "    months = sorted({(str(r.japan_date or ''))[:7]\n"
        "                     for r in db.query(FormalRecord).all()})\n"
        "    q = db.query(StoreEntity).filter(StoreEntity.city == 'x').all()\n"
        "    return months, q\n", encoding="utf-8")
    hits = _big_all_scans(str(p))
    assert len(hits) == 1 and "FormalRecord" in hits[0], hits


# ------------------------------------------------ ② 店铺目录：不许全量水合

def test_store_catalog_sql_selects_only_display_columns(client):
    """`/stores` 只取模板用到的列 —— 别再把 8 万行 ORM 全套列水合（4.0s→0.5s）。"""
    from app.models import StoreEntity
    from app.services import store_master
    db = appdb.SessionLocal()
    db.add(StoreEntity(store_id_raw="S1", name_local="甲店", name_norm="甲店",
                       city="東京", master_id=1))
    db.commit()
    cat, stmts = _sqls(db, lambda: store_master.store_catalog(db, page=1, per=5))
    assert cat["total"] == 1
    sql = " ".join(stmts)
    assert "store_entities.master_id" in sql and "store_entities.name_local" in sql
    for unwanted in ("store_entities.note", "store_entities.created_at",
                     "store_entities.master_store_id", "store_entities.raw_data_id",
                     "store_entities.master_raw_data_id"):
        assert unwanted not in sql, "又把 %s 拉进来了（全量水合 = 4 秒）" % unwanted
    db.close()


def test_store_catalog_lists_real_child_not_the_master(client):
    """主档的展开列表 = 它的分身；**不能把自己列进去、把真分身漏掉**。

    旧实现 `members.sort(key=id)` 后取 `members[1:]`：当某个分身的 id 比主档还小时，
    最小的那个被丢掉、主档自己被当成"分身"显示（线上 659 组受影响）。
    """
    from app.models import StoreEntity
    from app.services import store_master
    db = appdb.SessionLocal()
    child = StoreEntity(store_id_raw="S_OLD", name_local="鮨屋のうおきん 銀座店",
                        name_norm="鮨屋のうおきん 銀座店", city="東京", master_id=0)
    db.add(child)
    db.flush()
    master = StoreEntity(store_id_raw="S_NEW", name_local="鮨屋のうおきん 銀座店",
                         name_norm="鮨屋のうおきん 銀座店", city="東京",
                         master_id=child.id)          # 先挂上，再改成"自己是主档"
    db.add(master)
    db.flush()
    master.master_id = master.id
    child.master_id = master.id
    db.commit()
    assert child.id < master.id, "本测试要的就是'分身 id 更小'"
    cat = store_master.store_catalog(db, q="鮨屋", page=1, per=5)
    assert cat["total"] == 1, cat["total"]
    row = cat["rows"][0]
    assert row["master"].id == master.id
    assert [c.id for c in row["children"]] == [child.id], (
        "分身没显示对：children=%s" % [c.id for c in row["children"]])
    assert master.id not in [c.id for c in row["children"]], "主档把自己列成分身了"
    db.close()


# ------------------------------------------------ ③ 车站池：常数条 SQL

def test_list_unassigned_places_sql_is_constant(client):
    """未分配车站池：一圈 SQL 与车站数**无关**（原来每站 2 条：1,569 站 → 1,102 条）。"""
    from app.models import BdTask
    from app.services import bd_tasks
    from tests_web.test_team_task import _place_fixture
    db = appdb.SessionLocal()
    _line, places = _place_fixture(
        db, names=tuple("站%02d" % i for i in range(12)),
        seqs=tuple(range(1, 13)))
    db.add(BdTask(place_id=places[2].id, station_id=None, source_type="station",
                  state="unassigned", pct=0))
    db.commit()
    pool, stmts = _sqls(db, lambda: bd_tasks.list_unassigned_places(
        db, all_rows=True))
    assert len(pool["rows"]) == 12
    assert len(stmts) <= 6, ("车站池 SQL 条数随车站数增长：%d 条\n%s"
                             % (len(stmts), "\n".join(stmts)))
    assert [r["has_task"] for r in pool["rows"]].count(True) == 1
    assert [r["seq"] for r in pool["rows"]] == list(range(1, 13))
    assert all(r["line_label"] for r in pool["rows"])
    db.close()


# ------------------------------------------------ ④ 发薪表导出

def test_payroll_workbook_sql_never_pulls_raw_wide_columns(client):
    """导出不许把 RawRecord 整行（含 `original_row` 大 JSON）水合成对象。"""
    from app.services import report
    db = appdb.SessionLocal()
    _data, stmts = _sqls(db, lambda: report.build_payroll_workbook(
        db, "2026-10", "half1"))
    bad = [s for s in stmts if "original_row" in s]
    assert not bad, "又把 original_row（大 JSON）拉出来了：%s" % bad[:1]
    db.close()


def test_payroll_workbook_column_widths_are_max_len_plus_2(client):
    """列宽口径不变：每列 = 该列最长值 + 2（改成随写随统计，结果必须一致）。"""
    import io as _io
    from openpyxl import load_workbook
    from app.services import report
    db = appdb.SessionLocal()
    data, _fname = report.build_payroll_workbook(db, "2026-10", "half1")
    wb = load_workbook(_io.BytesIO(data))
    assert wb.worksheets, "没生成 sheet"
    for ws in wb.worksheets:
        for col, dim in ws.column_dimensions.items():
            longest = max([len(str(c.value or ""))
                           for c in ws[col]] or [0])
            assert dim.width == longest + 2, (
                "%s!%s 列宽 %s ≠ 最长 %d + 2" % (ws.title, col, dim.width, longest))
    db.close()
