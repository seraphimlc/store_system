# -*- coding: utf-8 -*-
"""清理「同一员工编号多个账号」（2026-01 导入自动开户 bug 的存量数据）。

背景：`importer.ensure_staff_user` 原来判重只查 `role == "staff"`，
队长（`leader`）账号判不到 → 每次导入都给同一编号再开一个队员号
（线上实测 6 位队长各多 1 个罗马字账号：`ogawa` + `ogawa-itsuru`）。

清理口径（保守）：
- 同一 `person_code` 下保留**最早创建**的账号（created_at 最早，其次 id 最小）；
- 其余账号只有在 **①`must_change_password=true`**（从未完成首登改密）
  且 **②零引用**（form_tokens / api_tokens / oauth / 日报 / 各表 created_by 等都不指向它）
  时才删除；否则跳过并打印原因。
- 默认**干跑**，加 `--apply` 才真正删除。

用法：
    DATABASE_URL="sqlite:///./store_settle_live.db" ./.venv/bin/python scripts/dedupe_staff_accounts.py
    DATABASE_URL=... ./.venv/bin/python scripts/dedupe_staff_accounts.py --apply
"""
import os
import sys
from collections import defaultdict

from sqlalchemy import bindparam, create_engine, inspect, text

USER_COLS = ("user_id", "created_by", "updated_by")


def _is_int_col(col) -> bool:
    try:
        import sqlalchemy as sa
        return isinstance(col["type"], (sa.Integer, sa.BigInteger, sa.SmallInteger))
    except Exception:  # noqa: BLE001
        return "INT" in str(col["type"]).upper()


def main() -> int:
    apply = "--apply" in sys.argv
    url = os.environ.get("DATABASE_URL") or "sqlite:///./store_settle_live.db"
    eng = create_engine(url)
    insp = inspect(eng)
    tables = insp.get_table_names()

    # 枚举所有引用用户的列
    refs = []  # (table, column, is_int)
    for t in tables:
        if t == "users":
            continue
        for c in insp.get_columns(t):
            if c["name"] in USER_COLS:
                refs.append((t, c["name"], _is_int_col(c)))

    with eng.connect() as conn:
        rows = conn.execute(text(
            "SELECT id, username, role, person_code, must_change_password, "
            "created_at, status FROM users "
            "WHERE coalesce(person_code,'') <> '' ORDER BY person_code, id"
        )).fetchall()
        groups = defaultdict(list)
        for r in rows:
            groups[r[3]].append(r)

        dup = {k: v for k, v in groups.items() if len(v) > 1}
        print(f"重复编号 {len(dup)} 个（库: {url}）")
        to_delete = []
        for code, users in sorted(dup.items()):
            users = sorted(users, key=lambda u: (str(u[5] or ""), u[0]))
            keep, others = users[0], users[1:]
            print(f"\n[{code}] 保留 id={keep[0]} {keep[1]} ({keep[2]})")
            for u in others:
                uid, uname, role, _, must, created, status = u
                reasons = []
                if not must:
                    reasons.append("已不是首登改密（可能本人已用）")
                n_ref = 0
                for t, col, is_int in refs:
                    val = uid if is_int else uname
                    try:
                        n = conn.execute(text(
                            f'SELECT count(*) FROM "{t}" WHERE "{col}" = :v'),
                            {"v": val}).scalar()
                    except Exception:  # noqa: BLE001
                        continue
                    n_ref += int(n or 0)
                if n_ref:
                    reasons.append(f"有 {n_ref} 条引用")
                flag = "可删" if not reasons else "跳过（" + "；".join(reasons) + "）"
                print(f"  - id={uid} {uname} ({role}, {status}, 建于 {created}) → {flag}")
                if not reasons:
                    to_delete.append(uid)

        if not to_delete:
            print("\n没有可安全删除的账号。")
            return 0
        print(f"\n共 {len(to_delete)} 个待删：{to_delete}")
        if not apply:
            print("干跑模式：未改动。确认后加 --apply 执行。")
            return 0
        conn.execute(text("DELETE FROM users WHERE id IN :ids").bindparams(
            bindparam("ids", expanding=True)), {"ids": tuple(to_delete)})
        conn.commit()
        print(f"已删除 {len(to_delete)} 个账号。")
        left = conn.execute(text(
            "SELECT person_code, count(*) FROM users "
            "WHERE coalesce(person_code,'') <> '' "
            "GROUP BY person_code HAVING count(*) > 1")).fetchall()
        print("删除后仍重复的编号：", left or "无")
    return 0


if __name__ == "__main__":
    sys.exit(main())
