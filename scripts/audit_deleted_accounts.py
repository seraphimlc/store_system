# -*- coding: utf-8 -*-
"""只读审计：某批「疑似废账号」有没有留下任何引用 / 相关人员的数据是否完好。

用途：清理重复账号**前**先跑一遍确认"直接删"安全；清理后再跑一遍留证。
只读，不写库。

用法（在生产容器里）：
    docker cp scripts/audit_deleted_accounts.py deploy-web-1:/tmp/
    docker exec deploy-web-1 python /tmp/audit_deleted_accounts.py \\
        --ids 92,93,94,95,96,97 --names luo-zijie,ogawa-itsuru

不带参数时审计 2026-10-24 清理的那 6 个账号（留档用）。

判据：
1. 所有表的 `user_id` / `created_by` / `updated_by` / `actor` 列，按 **id 和用户名** 两种写法
   各查一次（统一 cast 成文本比较，避免 int/varchar 混用漏查）→ 合计必须为 0；
2. 绩效/工资/任务/出勤都是**按 `person_code` 归属**，不按账号 → 列出每人现存数据条数，
   证明"删登录账号"没有、也不可能碰到这些数据。
"""
import argparse
import os

from sqlalchemy import create_engine, inspect, text

DEFAULT_IDS = "92,93,94,95,96,97"
DEFAULT_NAMES = "luo-zijie,chen-jiayi,ogawa-itsuru,chen-weifeng,tang-jing,gan-zijie"

REF_COLS = ("user_id", "created_by", "updated_by", "actor")
PERSON_TABLES = (
    ("month_perf_records", "person_code"),
    ("formal_records", "person_code"),
    ("person_daily_stats", "person_code"),
    ("payroll_period_rows", "person_code"),
    ("payroll_adjusts", "person_code"),
    ("bd_task_assign", "person_code"),
    ("bd_team_member", "person_code"),
    ("staff_date_plans", "person_code"),
)


def _connect():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL 未设置（app/db.py 会缓存它，别让它静默连空库）")
    return create_engine(url)


def audit_refs(conn, ids, names):
    """所有引用列按 id + 用户名 各查一次，返回 (总引用数, 明细行)。"""
    insp = inspect(conn)
    refs = []
    for t in insp.get_table_names():
        if t == "users":
            continue
        for c in insp.get_columns(t):
            if c["name"] in REF_COLS:
                refs.append((t, c["name"]))
    vals = [str(v) for v in list(ids) + list(names)]
    inlist = ",".join("'%s'" % v.replace("'", "''") for v in vals)
    total, rows = 0, []
    for t, col in refs:
        try:
            n = conn.execute(text(
                f'SELECT count(*) FROM "{t}" WHERE "{col}"::text IN ({inlist})')
            ).scalar()
        except Exception as e:  # noqa: BLE001
            rows.append(f"  ! {t}.{col} 无法比较（{type(e).__name__}），请人工核对")
            continue
        if n:
            total += n
            rows.append(f"  {t}.{col}: {n}")
    return total, rows


def person_data(conn, codes):
    """每个 person_code 在绩效/工资/任务/出勤表里的现存条数。"""
    out = {}
    for code in codes:
        parts = {}
        for t, col in PERSON_TABLES:
            try:
                n = conn.execute(text(
                    f'SELECT count(*) FROM "{t}" WHERE "{col}" = :c'),
                    {"c": code}).scalar()
            except Exception:  # noqa: BLE001
                n = None
            if n:
                parts[t] = n
        out[code] = parts
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="清理账号前的只读引用审计")
    ap.add_argument("--ids", default=DEFAULT_IDS, help="账号 id，逗号分隔")
    ap.add_argument("--names", default=DEFAULT_NAMES, help="登录名，逗号分隔")
    ap.add_argument("--codes", default="",
                    help="顺带体检的 person_code（默认=库里所有编号），逗号分隔")
    a = ap.parse_args(argv)
    ids = [x.strip() for x in a.ids.split(",") if x.strip()]
    names = [x.strip() for x in a.names.split(",") if x.strip()]
    codes = [x.strip() for x in a.codes.split(",") if x.strip()]

    eng = _connect()
    with eng.connect() as conn:
        print("== 1) 对这批账号的任何引用（id + 用户名）==")
        total, rows = audit_refs(conn, ids, names)
        for r in rows:
            print(r)
        print(f"  合计引用 = {total}"
              + ("  → 可以安全删除" if total == 0 else "  → ⚠️ 有引用，改用停用"))

        if not codes:
            codes = sorted(r[0] for r in conn.execute(text(
                "SELECT DISTINCT person_code FROM users "
                "WHERE person_code IS NOT NULL AND person_code <> ''")).fetchall())
        print("\n== 2) 人员维度数据（按 person_code，删账号不影响）==")
        for code, parts in person_data(conn, codes).items():
            print(f"  {code}  " + (" ".join(f"{k}={v}" for k, v in parts.items())
                                    or "（无数据）"))

        print("\n== 3) 账号现状 ==")
        dup = conn.execute(text(
            "SELECT person_code, count(*) FROM users "
            "WHERE coalesce(person_code,'') <> '' GROUP BY person_code "
            "HAVING count(*) > 1")).fetchall()
        for code, n in dup:
            print(f"  ⚠️ 重复编号 {code}: {n} 条")
        print(f"  重复编号数 = {len(dup)}；users 总数 = "
              f"{conn.execute(text('SELECT count(*) FROM users')).scalar()}")


if __name__ == "__main__":
    main()
