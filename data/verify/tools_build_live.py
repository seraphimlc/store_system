# -*- coding: utf-8 -*-
"""E2E 真实库：从零建库 → 导入 4 份真实 8 月文件 → V3 判定 → finalize →
校验 8 月对账与基准一致 → 给真实员工开账号。
用法: ./.venv/bin/python data/verify/tools_build_live.py
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./store_settle_live.db")

import app.db as appdb
from app.auth import hash_password
from app.models import (User, RawRecord, ImportFile, FormalRecord,
                        AppealRecord, ConfirmTask, Person)
from app.services.importer import upload_and_store, parse_file
from app.services import v3_flow, v3_perf

FILES = ["0726-0805.xlsx", "0806-815.xlsx", "0816-25.xlsx", "0826-0831.xlsx"]
SRC = "data/aug_input/"
DBURL = "sqlite:///./store_settle_live.db"

# 开登录账号的真实员工（code, 显示名, 用户名）
STAFF_LOGINS = [
    ("2188240606634082", "小川逸", "ogawa"),
    ("2188240606745955", "李文强", "liwenqiang"),
    ("2188240607339564", "汤静", "tangjing"),
]


def main():
    engine = appdb.reset_engine_for_tests(DBURL)
    import app.models as M
    M.Base.metadata.create_all(engine)
    db = appdb.SessionLocal()
    db.add(User(username="admin", password_hash=hash_password("demo123"),
                display_name="管理员", role="admin", is_active=True, status="active"))
    db.commit()
    admin = db.query(User).filter(User.username == "admin").first()

    for i, fn in enumerate(FILES, start=1):
        with open(SRC + fn, "rb") as f:
            imp = upload_and_store(fn, f.read(), admin.id, db)
        parse_file(imp, db)
        r = v3_flow.process_import(db, imp.id)
        n = db.query(RawRecord).filter(RawRecord.import_id == imp.id).count()
        print(f"[{i}] {fn}: raw={n} judge={r['judge']}")
    print("判定完成")

    # 员工登录账号（Person 已由判定自动建；补 User）
    for code, name, login in STAFF_LOGINS:
        if not db.query(User).filter(User.username == login).first():
            db.add(User(username=login, password_hash=hash_password("demo123"),
                        display_name=name, role="staff", person_code=code,
                        is_active=True, status="active"))
    db.commit()

    # 入正式表（真实数据无申诉 → 直接 finalize；每文件确认）
    for imp in db.query(ImportFile).order_by(ImportFile.id).all():
        res = v3_flow.finalize_import(db, imp.id)
        print(f"finalize #{imp.id}: {res}")

    # 8 月对账校验
    fr = db.query(FormalRecord).all()
    aug_f = [x for x in fr if (str(x.japan_date or "")).startswith("2026-08")]
    p1 = sum(1 for x in aug_f if x.points == 1)
    p2 = sum(1 for x in aug_f if x.points == 2)
    total_amt = sum(v3_perf.salary_for(m["points"])
                    for m in v3_perf.month_perf(db, "2026-08"))
    print("\n==== 对账校验（基准：12511 / 8231 / 4280 / 16791 / 4896750）====")
    print(f"8月 formal={len(aug_f)}  1点={p1}  2点={p2}  总点={p1+p2*2}  工资={total_amt}")
    assert len(aug_f) == 12511, "formal 数 != 12511"
    assert p1 == 8231 and p2 == 4280, "1/2点分布不符"
    assert p1 + p2 * 2 == 16791, "总点 != 16791"
    assert total_amt == 4896750, "工资 != 4896750"
    print("对账校验通过 ✓")

    # 员工端规模摘要（每人可申诉记录/正式店）
    print("\n==== 员工端规模摘要 ====")
    for code, name, login in STAFF_LOGINS:
        days = v3_flow.appeal_list(db, code)
        fcnt = db.query(FormalRecord).filter(
            FormalRecord.person_code == code).count()
        print(f"{name}: 可申诉记录={sum(len(v) for _, v in days)} 正式店={fcnt}")
    db.close()


if __name__ == "__main__":
    main()
