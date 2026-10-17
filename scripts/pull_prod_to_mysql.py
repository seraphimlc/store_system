# -*- coding: utf-8 -*-
"""把 PostgreSQL（线上/还原副本）整库拉进本地 MySQL —— 当**测试数据**看。

用户 2026-10-17："你把线上服务器的数据拉到本地，放到本地的 mysql 里当测试数据来看吧"。

为什么要这个脚本（别再用那份旧 SQLite 快照）：
`store_settle_live.db` 是 2026-10-17 01:09 的快照，越往后越脱离线上；本脚本让你随时
用一份新的线上数据在本地 MySQL 里查（Navicat/DataGrip/`mysql` 都行）。

**怎么用**（典型两步，都在本机）：

```bash
# ① 线上 dump → 本机还原出一份 PostgreSQL 副本（不碰线上库；psql 版本要 >= 线上）
ssh store-prod "docker exec deploy-db-1 pg_dump -U store_settle -d store_settle -Fp | gzip -1" \\
    > /tmp/settle/prod_plain.sql.gz
createdb -h 127.0.0.1 -p 55432 -U postgres store_settle   # 本机 PG（可用 brew 的 postgresql@15）
gunzip -c /tmp/settle/prod_plain.sql.gz | psql -h 127.0.0.1 -p 55432 -U postgres -d store_settle -q

# ② 灌进本地 MySQL
./.venv/bin/python scripts/pull_prod_to_mysql.py \\
    --pg-uri "postgresql://postgres@127.0.0.1:55432/store_settle" \\
    --mysql-url "mysql+pymysql://root@127.0.0.1:3306/store_settle"
```

**口径与已知坑**：

- 表结构从 **ORM 模型**生成（`Base.metadata.sorted_tables`），不是从 PG 抄 DDL —— 这样
  列类型/索引/外键跟代码一致。⚠️ MySQL 不允许 `TEXT/JSON` 列有默认值 → 生成前把这类列的
  `server_default` 摘掉（17 列）。
- 数据用 `psql \\copy ... TO STDOUT CSV` 读（本机 psycopg2 是 x86_64 wheel，arm64 装不上，
  所以不走 Python 驱动）→ `NULL` 用哨兵 `\\N`，Python `csv` 解析后还原成 `None`。
- `Boolean` 列：PG 的 `t/f` → MySQL 的 `1/0`（脚本按 ORM 类型转，不猜）。
- **只读用途**：这是"拿来看/查"的副本。应用**不建议**直接连它跑（方言差异：`func.substr`、
  `Date` 列比较、`batch_alter_table` 等）；本地跑应用/测试仍用 SQLite
  （`DATABASE_URL=sqlite:///./store_settle_live.db`）。
- 数据是**生产数据**（含人名）→ 只落本机、别提交、别外传（仓库 `.gitignore` 也不含它）。
"""
import argparse
import csv
import io
import os
import subprocess
import sys

from sqlalchemy import Boolean, DateTime, JSON, Text, Time, create_engine, text

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from app.db import Base  # noqa: E402
import app.models  # noqa: E402,F401  注册全部表

NULL_SENTINEL = "\\N"
CHUNK = 1000
# `csv` 默认单字段上限 128KB，而 `raw_records.original_row` 这类 JSON 单字段能到 MB
# → 不放开就直接 `_csv.Error: field larger than field limit`（踩过）。
csv.field_size_limit(2 ** 31 - 1)


def _mysql_ready_metadata():
    """复制一份 metadata，并把它调成"MySQL 建得出来"的样子。

    做了三件事（都在**拷贝**上做，别动 `Base.metadata`）：

    1. 摘掉 `Text/JSON` 列的默认值 —— MySQL 不允许 `BLOB/TEXT/JSON` 有默认值
       （17 列，不改就 `1101 BLOB, TEXT, GEOMETRY or JSON column can't have a default value`）。
    2. `Text` → `LONGTEXT` —— MySQL 的 `TEXT` 只有 **64KB**，而 `rebuild_snapshots.payload_json`
       实测单值 **7.6MB**（`1606/1406 Data too long`）。PG 的 `text` 是无上限的，LONGTEXT 才对得上。
    3. `DateTime/Time` → 带 `fsp=6` —— 保住微秒（PG 存微秒，MySQL 默认 DATETIME 会截断到秒）。

    ⚠️ 必须是**拷贝**（`t.to_metadata(new_md)`）：直接改 `Base.metadata` 会把 `server_default`
    抹掉，迁移/ORM 对照测试就假红。（踩过：写成 `Base.metadata.to_metadata(Base.metadata)`
    → 得到空 metadata，一张表都没建，脚本还打印"建表 ✓"。）
    """
    from sqlalchemy import MetaData
    from sqlalchemy.dialects import mysql as my_types
    md = MetaData()
    for t in Base.metadata.sorted_tables:
        t.to_metadata(md)
    stripped, widened, partial = [], [], []
    for t in md.sorted_tables:
        for c in t.columns:
            if isinstance(c.type, (Text, JSON)) and c.server_default is not None:
                c.server_default = None
                c.default = None
                stripped.append("%s.%s" % (t.name, c.name))
            if isinstance(c.type, Text):
                c.type = my_types.LONGTEXT()
                widened.append("%s.%s" % (t.name, c.name))
            elif isinstance(c.type, DateTime):
                c.type = my_types.DATETIME(fsp=6)
            elif isinstance(c.type, Time):
                c.type = my_types.TIME(fsp=6)
        # 4. 去掉"部分唯一索引"（PG 特有）：MySQL 不支持 `WHERE` 条件索引，
        #    `create_all` 会退化成**全表唯一**，然后拒绝线上本来合法的数据
        #    （实测 `bd_station` 同名站在不同线路 → Duplicate entry '印旛日本医大'）。
        #    只读副本不需要唯一性，直接不建这 5 个索引。
        for idx in list(t.indexes):
            where = (idx.dialect_options.get("postgresql", {}) or {}).get("where")
            if where is not None:
                t.indexes.discard(idx)
                partial.append("%s.%s" % (t.name, idx.name))
    return md, stripped, widened, partial


def _copy_out(pg_uri: str, table: str, cols):
    """`psql \\copy` 把一张表读成 CSV 行（生成器），NULL 用哨兵。"""
    collist = ", ".join('"%s"' % c for c in cols)
    sql = ("\\copy (select %s from \"%s\") to stdout with (format csv, null '%s')"
           % (collist, table, NULL_SENTINEL))
    p = subprocess.Popen(["psql", pg_uri, "-q", "-c", sql],
                         stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    reader = csv.reader(io.TextIOWrapper(p.stdout, encoding="utf-8", newline=""))
    for row in reader:
        yield row
    p.stdout.close()
    err = p.stderr.read().decode("utf-8", "replace").strip()
    rc = p.wait()
    if rc != 0:
        raise RuntimeError("psql \\copy %s 失败：%s" % (table, err[:500]))


def _convert(table, row):
    """按 ORM 列类型转成**字典**（键 = 列名）。

    ⚠️ 必须用字典，**不能用元组**：SQLAlchemy 2.0 + pymysql 的 `executemany` 喂元组会**串列**
    （实测 `bd_ai_summary`：语句编译出 9 列，实际只发 `days, model, tokens, created_by,
    created_at` 5 列、且值是列默认值 → `Field 'summary_date' doesn't have a default value`）。
    元组单行 `execute()` 也一样报 `List argument must consist only of tuples or dictionaries`。
    字典实测正常 ✓。
    """
    out = {}
    for col, v in zip(table.columns, row):
        if v == NULL_SENTINEL:
            out[col.name] = None
        elif isinstance(col.type, Boolean):
            out[col.name] = 1 if str(v).lower() in ("t", "true", "1") else 0
        else:
            out[col.name] = v
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--pg-uri", required=True,
                    help='如 postgresql://postgres@127.0.0.1:55432/store_settle')
    ap.add_argument("--mysql-url", default=os.environ.get(
        "MYSQL_URL", "mysql+pymysql://root@127.0.0.1:3306/store_settle"))
    ap.add_argument("--only", default="", help="只拉这些表（逗号分隔）")
    ap.add_argument("--keep", action="store_true", help="不清空已有数据（默认先 TRUNCATE）")
    ap.add_argument("--no-create", action="store_true", help="不建表（库结构已就绪时）")
    a = ap.parse_args()

    md, stripped, widened, partial = _mysql_ready_metadata()
    my = create_engine(a.mysql_url)
    if not a.no_create:
        md.create_all(my)
        print("MySQL 建表 ✓（摘默认值 %d 列；Text→LONGTEXT %d 列；去掉部分唯一索引 %d 个）"
              % (len(stripped), len(widened), len(partial)))

    only = {x.strip() for x in a.only.split(",") if x.strip()}
    tables = [t for t in md.sorted_tables if not only or t.name in only]
    total = 0
    for t in tables:
        cols = [c.name for c in t.columns]
        exists = my.connect().execute(text(
            "select count(*) from information_schema.tables where table_schema="
            "database() and table_name=:n"), {"n": t.name}).scalar()
        if not exists:
            print("  %-32s ⚠️ MySQL 里没这张表，跳过" % t.name)
            continue
        with my.begin() as con:
            if not a.keep:
                con.execute(text("set foreign_key_checks=0"))
                con.execute(text("truncate table `%s`" % t.name))
                con.execute(text("set foreign_key_checks=1"))
        ins = t.insert()
        n = 0
        buf = []
        with my.begin() as con:
            con.execute(text("set foreign_key_checks=0"))
            for row in _copy_out(a.pg_uri, t.name, cols):
                buf.append(_convert(t, row))
                if len(buf) >= CHUNK:
                    con.execute(ins, buf)
                    n += len(buf)
                    buf = []
            if buf:
                con.execute(ins, buf)
                n += len(buf)
            con.execute(text("set foreign_key_checks=1"))
        total += n
        print("  %-32s %7d 行" % (t.name, n))
    print("合计 %d 行 → %s" % (total, a.mysql_url))


if __name__ == "__main__":
    main()
