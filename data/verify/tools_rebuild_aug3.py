# -*- coding: utf-8 -*-
"""aug3: 只导入各文件 modified=2026-08 的行（7 月行视为上月已结算）。
目标：验证"按月窗口判重"下 V3 是否对齐基准 12511/533。
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

import os
os.environ.setdefault("DATABASE_URL", "sqlite:///./store_settle_aug5.db")
import sqlite3, json
import app.db as appdb
from app.auth import hash_password
from app.models import User, RawRecord, ImportFile
from app.services.importer import upload_and_store, parse_file
from app.services import v3_flow

FILES = ["0726-0805.xlsx", "0806-815.xlsx", "0816-25.xlsx", "0826-0831.xlsx"]
SRC = "data/aug_input/"


def main():
    engine = appdb.reset_engine_for_tests("sqlite:///./store_settle_aug5.db")
    import app.models as M
    M.Base.metadata.create_all(engine)
    db = appdb.SessionLocal()
    db.add(User(username="admin", password_hash=hash_password("demo123"),
                display_name="管理员", role="admin", is_active=True, status="active"))
    db.commit()
    admin = db.query(User).first()
    for i, fn in enumerate(FILES, start=1):
        p = SRC + fn
        with open(p, "rb") as f:
            imp = upload_and_store(fn, f.read(), admin.id, db)
        parse_file(imp, db)
        # 剔除 7 月（及更早）行：8 月结算窗口
        n_before = db.query(RawRecord).filter(RawRecord.import_id == imp.id).count()
        deleted = db.query(RawRecord).filter(
            RawRecord.import_id == imp.id,
            RawRecord.modified_raw < "2026-08-01").delete()
        db.commit()
        # 注意 parse 已建 persons；raw 剔除不影响。import 记 parsed_rows 旧值无妨
        imp2 = db.get(ImportFile, imp.id)
        # 记录剔除后行数再 process
        # sync 只对该文件 raw 建实体 → 剔除后再 sync
        r = v3_flow.process_import(db, imp.id)
        n_after = db.query(RawRecord).filter(RawRecord.import_id == imp.id).count()
        print(f"[{i}] {fn}: before={n_before} del={deleted} after={n_after} "
              f"judge={r['judge']} tasks={r['tasks']}")
    # 全库 8 月口径
    from collections import Counter
    rows = db.query(RawRecord).all()
    aug = [r for r in rows if (r.modified_raw or "").startswith("2026-08")]
    print("\n8月 raw:", len(aug))
    c8 = Counter(r.clean_status for r in aug)
    print("8月 clean_status:", dict(c8))
    v8 = [r for r in aug if r.clean_status == "valid"]
    print("8月 valid:", len(v8))
    db.close()


if __name__ == "__main__":
    main()
