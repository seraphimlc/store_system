# -*- coding: utf-8 -*-
"""aug8: 全量导入四文件(含7月行) + 按「店名trim×结算月」窗口 judge，统计8月口径。
用法: DATABASE_URL=sqlite:///./store_settle_aug8.db ./.venv/bin/python <this>
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
os.environ.setdefault("DATABASE_URL", "sqlite:///./store_settle_aug8.db")
import app.db as appdb
from app.auth import hash_password
from app.models import User, RawRecord, ImportFile
from app.services.importer import upload_and_store, parse_file
from app.services import v3_flow

FILES = ["0726-0805.xlsx", "0806-815.xlsx", "0816-25.xlsx", "0826-0831.xlsx"]
SRC = "data/aug_input/"
DBURL = "sqlite:///./store_settle_aug8.db"


def main():
    engine = appdb.reset_engine_for_tests(DBURL)
    import app.models as M
    M.Base.metadata.create_all(engine)
    db = appdb.SessionLocal()
    db.add(User(username="admin", password_hash=hash_password("demo123"),
                display_name="管理员", role="admin", is_active=True, status="active"))
    db.commit()
    admin = db.query(User).first()
    for i, fn in enumerate(FILES, start=1):
        with open(SRC + fn, "rb") as f:
            imp = upload_and_store(fn, f.read(), admin.id, db)
        parse_file(imp, db)
        r = v3_flow.process_import(db, imp.id)
        n = db.query(RawRecord).filter(RawRecord.import_id == imp.id).count()
        print(f"[{i}] {fn}: raw={n} judge={r['judge']} appealable={r['appealable']}")
    from collections import Counter
    rows = db.query(RawRecord).all()
    aug = [r for r in rows if (r.modified_raw or "").startswith("2026-08")]
    c8 = Counter(r.clean_status for r in aug)
    print("\n8月 clean_status:", dict(c8), "total", len(aug))
    print("8月 valid:", sum(1 for r in aug if r.clean_status == "valid"))
    db.close()


if __name__ == "__main__":
    main()
