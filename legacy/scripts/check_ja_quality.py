# -*- coding: utf-8 -*-
"""抽查 ja 渲染的翻译覆盖率（粗略：统计可见中文标签残留）。"""
import os
import sys
import re
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

import app.db as appdb
from app.db import Base
from app.main import create_app
from app.models import User, Person
from app.auth import hash_password

CN = re.compile(r'[\u4e00-\u9fff]')


def main():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool, future=True)
    appdb._ENGINE = engine
    appdb.SessionLocal = sessionmaker(bind=engine, future=True,
                                      expire_on_commit=False)
    Base.metadata.create_all(engine)
    app = create_app()
    with TestClient(app) as c:
        db = appdb.SessionLocal()
        db.add(User(username="admin", password_hash=hash_password("pw123456"),
                    display_name="管理员", role="admin", is_active=True))
        db.add(User(username="emp1", password_hash=hash_password("demo123"),
                    display_name="员工甲", role="staff", person_code="P1",
                    is_active=True, status="active"))
        db.add(Person(code="P1", display_name="员工甲"))
        db.commit()
        db.close()
        c.post("/login", data={"username": "admin", "password": "pw123456"},
               follow_redirects=False)
        for path in ("/perf", "/recon", "/payroll-settle", "/config",
                     "/staff-admin", "/dashboard"):
            r = c.get(f"{path}?lang=ja")
            # 去掉 script/style 与属性值后再数中文
            body = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', r.text, flags=re.S)
            body = re.sub(r'<[^>]+>', ' ', body)
            # 去掉数据性中文（如店名/员工名/日期）
            body = re.sub(r'员工甲|管理员|2026-\d\d|店\d+|甲\(' , '', body)
            n = len(CN.findall(body))
            print(f"{path}: ja 页面可见中文残留 ≈ {n} 字符")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
