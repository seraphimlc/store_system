# -*- coding: utf-8 -*-
"""迁移冒烟测试：**真的跑一遍 alembic**，并核对 ORM 与迁移建出的 schema 是否一致。

做这组测试的原因（2026-09-28 评审教训）：
本地一直用 `Base.metadata.create_all` 建表，迁移从未被执行过 —— 结果 c9d0e1f2a3b4
里 `person_code` 漏声明，`alembic upgrade head` 直接失败（线上 entrypoint 会因此起不来），
而 200 个测试全绿。**只有真跑迁移才抓得到这类问题。**

2026-10 扩容（为什么从 7 张写死的表改成全表）：
原来只对账 `NEW_TABLES`（6 张新表）+ `formal_records` = **7 / 54 张**，剩下 47 张
完全没有守卫。新表/新列（`sys_configs.staff_visible_from`、`payroll_period_rows` 分期列、
`payroll_payments`/`payroll_adjusts`/`payroll_settlement_links` 溯源列、`bd_*` 作业域、
`oauth_*`、`staff_date_plans.reported` …）能不能在迁移里建出来，全靠在人脑子里记。
更糟的是本地开发走 `create_all`：**ORM 加了列而迁移没加时，本地测试全绿、线上 500**
（线上 entrypoint 只跑 `upgrade`，不会报"少列"）。这正是本文件存在的唯一意义。
现在按 `Base.metadata.tables` 全量对账：表集（双向）/ 列名集合（双向）/ 可空性 /
唯一性列组合（双向）/ 索引列覆盖。

索引与唯一性为什么不比"名字"（踩过的坑）：
迁移与 ORM 的索引命名**本来就不一致**，例如迁移 `ix_payroll_adjusts_month` vs ORM
`ix_payroll_adjusts_source_month`；`payroll_payments`（`..._person` vs `..._person_code`）；
`payroll_settlement_links`（`ix_links_*` vs `ix_payroll_settlement_links_*`）；
`oauth_*` 迁移用「列级 UNIQUE + 普通索引」，ORM 用 `unique=True, index=True`（唯一索引）。
按名字比会一加进来就假红，所以这里改比 **"这个索引/唯一约束覆盖了哪些列"**：
既能抓到"迁移没建索引"的真漂移，又不会因为改名/换个写法误报。
"""
import os
import sqlite3
import subprocess
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# ---------------------------------------------------------------------------
# 白名单：不参与**表集**对账的表（对账双向：ORM 有→迁移必须有；迁移有→必须是 ORM 表或这里）
# ---------------------------------------------------------------------------
EXCLUDED_TABLES = {
    # alembic 自己的版本表，ORM 里本来就没有（迁移库独有）。
    "alembic_version",
    # 迁移建过、ORM 从未声明过的**历史遗留表**：从第一个 commit（7efe40e）起
    # `app/models.py` 里就没有这些模型。`app/services/importer.py` 里写明
    # "历史遗留表（模型已下线，但线上仍有 FK）"，用原生 SQL 清理。
    # 迁移历史不可回头改（已上线），drop 要走新迁移且有外键/数据风险 → 豁免。
    # ⚠️ 新增迁移建表必须同时加 ORM 模型，否则下面的表集对账会红。
    "task_runs", "clean_records", "settle_amounts", "person_stats",
    "self_report_imports", "self_report_rows", "daily_system_points",  # 初版 spec v0.4.4
    "review_flags", "confirm_tasks", "daily_confirms", "daily_activity",
    "manual_decisions",                                               # 后续迁移加的表，同样没建模型
}

# 已知的历史漂移台账（对账项的 key → 说明）。
# 这些**不是**被断言悄悄放过的空白，而是点名登记、用 xfail 报出来的既成事实：
# 除这里登记过的之外，任何新漂移都让本测试**直接失败**（不许放宽断言换绿色）。
# 收敛办法：写一条新迁移改迁移库（本文件只抓漂移，不改迁移）；修好后删掉登记项。
KNOWN_DRIFT = {
    "列集:recon_tasks":
        "recon_tasks 迁移多一个遗留列 run_id（FK→task_runs，spec v0.4.4 残留）：ORM 从"
        "第一个 commit 起就没有它。反向（迁移多列）不影响 ORM 运行，但属既成漂移，"
        "待新迁移 drop。",
    "唯一性:sys_configs":
        "sys_configs.config_month 迁移里带 UNIQUE，ORM 里没有：8549bdd 把它从「按月生效」"
        "改成「全局单值」时删了模型上的 unique=True，却漏了配套迁移。当前 app 只在表空时"
        "插一行 config_month=''（其余走 UPDATE），未触发；但迁移库里同月插第二行会"
        "IntegrityError（create_all 的库不会）→ 真漂移，待新迁移 drop 该唯一约束。",
}


def _alembic(db_path, *args):
    env = dict(os.environ, DATABASE_URL="sqlite:///%s" % db_path)
    return subprocess.run([sys.executable, "-m", "alembic"] + list(args),
                          cwd=ROOT, env=env, capture_output=True, text=True)


def _orm_unique_col_sets(table):
    """ORM 声明的「哪些列组合是唯一的」：UniqueConstraint 与 unique=True 的 Index 都算。"""
    out = set()
    for con in table.constraints:
        if con.__class__.__name__ == "UniqueConstraint":
            out.add(frozenset(c.name for c in con.columns))
    for idx in table.indexes:
        if idx.unique:
            out.add(frozenset(c.name for c in idx.columns))
    return out


def _mig_unique_col_sets(insp, table):
    """迁移库里的唯一性列组合（口径同 `_orm_unique_col_sets`，只看列不看名字）。"""
    out = {frozenset(u["column_names"]) for u in insp.get_unique_constraints(table)}
    for idx in insp.get_indexes(table):
        if idx["unique"]:
            out.add(frozenset(idx["column_names"]))
    return out


def _orm_index_col_sets(table):
    return {frozenset(c.name for c in idx.columns) for idx in table.indexes}


def _mig_index_col_sets(insp, table):
    out = {frozenset(idx["column_names"]) for idx in insp.get_indexes(table)}
    # 唯一约束在 SQLite 由自动索引兜底（`sqlite_autoindex_*` 不出现在 get_indexes 里），
    # 也算「这组列有索引」，否则 `unique=True` 的列会被判成缺索引。
    out |= {frozenset(u["column_names"]) for u in insp.get_unique_constraints(table)}
    return out


def test_alembic_upgrade_head_on_fresh_db(tmp_path):
    """全新库上 `alembic upgrade head` 必须成功，ORM 的全部表都建出来，版本到 head。"""
    from app.db import Base
    import app.models  # noqa: F401  注册全部表

    db = str(tmp_path / "fresh.db")
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, "迁移失败：\n%s\n%s" % (r.stdout[-3000:], r.stderr[-3000:])
    con = sqlite3.connect(db)
    tables = {row[0] for row in con.execute(
        "select name from sqlite_master where type='table'")}
    head = con.execute("select version_num from alembic_version").fetchone()[0]
    con.close()
    # 不再写死表名：ORM 有几张就要求迁移建几张（新增模型忘记写迁移 → 这里直接红）
    missing = sorted(set(Base.metadata.tables) - tables)
    assert not missing, "迁移后缺表：%s" % missing
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
    """迁移建出的表结构必须与 ORM 一致：表集 / 列名 / 可空性 / 唯一性 / 索引覆盖。

    判据（为什么这么定）：
    - 表集与列名集合**双向**比：ORM 有而迁移没有 = 线上必挂（最要命）；迁移有而 ORM
      没有 = 遗留/多列，登记在 `EXCLUDED_TABLES` / `KNOWN_DRIFT` 里，其余也报出来。
    - 索引只查「ORM 有、迁移没建」一个方向：迁移多建索引只是多占点空间，不改语义
      （`mcp_audit_log` 就是迁移多两个索引），拿它判失败是假红。
    - 索引/唯一性按**列组合**比而不是按名字比（迁移与 ORM 命名口径本来就不一致，见模块
      docstring）。唯一性必须双向比：迁移多一个 UNIQUE 会让"本地能写、线上 IntegrityError"。
    """
    from sqlalchemy import create_engine, inspect

    from app.db import Base
    import app.models  # noqa: F401  注册全部表

    db = str(tmp_path / "cmp.db")
    r = _alembic(db, "upgrade", "head")
    assert r.returncode == 0, r.stderr[-2000:]
    insp = inspect(create_engine("sqlite:///%s" % db))

    mig_tables = set(insp.get_table_names())
    orm_tables = set(Base.metadata.tables)
    problems = []          # [(key, 说明)]，key 用于与 KNOWN_DRIFT 台账对账

    # 1) 表集：ORM 有必须迁移有；迁移有必须是 ORM 表或白名单
    for t in sorted(orm_tables - mig_tables):
        problems.append(("缺表:" + t, "%s：ORM 有、迁移没建" % t))
    for t in sorted(mig_tables - orm_tables - EXCLUDED_TABLES):
        problems.append(("多表:" + t,
                         "%s：迁移建了、ORM 里没有（新迁移建表要同时加模型；"
                         "确属遗留请加进 EXCLUDED_TABLES 并写明原因）" % t))

    # 2) 逐表：列名 / 可空性 / 唯一性 / 索引覆盖（覆盖 Base.metadata 里全部表）
    for t in sorted(orm_tables & mig_tables):
        orm_tbl = Base.metadata.tables[t]
        mig_cols = {c["name"]: c["nullable"] for c in insp.get_columns(t)}
        orm_cols = {c.name: c.nullable for c in orm_tbl.columns}
        if set(mig_cols) != set(orm_cols):
            problems.append(("列集:" + t, "%s 列不同：迁移多 %s / ORM 多 %s" % (
                t, sorted(set(mig_cols) - set(orm_cols)),
                sorted(set(orm_cols) - set(mig_cols)))))
        else:
            for name, nullable in sorted(orm_cols.items()):
                if mig_cols[name] != nullable:
                    problems.append(("可空性:%s.%s" % (t, name),
                                     "%s.%s 可空性不同：迁移=%s ORM=%s" % (
                                         t, name, mig_cols[name], nullable)))
        orm_uniq, mig_uniq = _orm_unique_col_sets(orm_tbl), _mig_unique_col_sets(insp, t)
        if orm_uniq != mig_uniq:
            problems.append(("唯一性:" + t, "%s 唯一性（列组合）不同：迁移多 %s / ORM 多 %s" % (
                t, sorted(sorted(c) for c in mig_uniq - orm_uniq),
                sorted(sorted(c) for c in orm_uniq - mig_uniq))))
        # 索引覆盖也要查（历史坑：`formal_records` 的 ix_formal_japan_date 曾因迁移文件名
        # 撞车整条漏掉；ORM 声明了而迁移没建 → 线上静默性能退化）
        missing_idx = _orm_index_col_sets(orm_tbl) - _mig_index_col_sets(insp, t)
        if missing_idx:
            problems.append(("缺索引:" + t, "%s 缺索引（ORM 有、迁移没建）：列 %s" % (
                t, sorted(sorted(c) for c in missing_idx))))

    unknown = [(k, m) for k, m in problems if k not in KNOWN_DRIFT]
    known = [(k, m) for k, m in problems if k in KNOWN_DRIFT]
    if unknown:
        msg = "迁移与 ORM 出现**新的**漂移（未登记，请补迁移而不是放宽断言）：\n  " + \
              "\n  ".join(m for _, m in unknown)
        if known:
            msg += "\n已登记的历史漂移（KNOWN_DRIFT）：\n  " + "\n  ".join(m for _, m in known)
        pytest.fail(msg)
    if known:
        # 只登记过的既成漂移 → 报 xfail（不静默通过，也不让整条守门测试常年红掉被无视）。
        pytest.xfail("已知历史漂移，登记在 KNOWN_DRIFT，待新迁移收敛：\n  " +
                     "\n  ".join(m for _, m in known))
