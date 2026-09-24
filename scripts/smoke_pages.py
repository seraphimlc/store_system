# -*- coding: utf-8 -*-
"""发布前逐页渲染冒烟：管理员/员工所有页面 zh+ja 双语均 200 且无模板错误。"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))  # 项目根，可 import app
os.environ.setdefault("DATABASE_URL", "sqlite://")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.db as appdb
from app.db import Base
from app.main import create_app
from fastapi.testclient import TestClient
from app.models import User, Person
from app.auth import hash_password


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
        # 管理员
        c.post("/login", data={"username": "admin", "password": "pw123456"},
               follow_redirects=False)
        admin_pages = ["/dashboard", "/files", "/perf", "/perf?month=2026-08",
                       "/recon", "/payroll-settle", "/config", "/stores",
                       "/staff-admin", "/product", "/my/password"]
        bad = []
        for path in admin_pages:
            for lang in ("zh", "ja"):
                r = c.get(f"{path}&lang={lang}" if "?" in path
                          else f"{path}?lang={lang}")
                if r.status_code != 200:
                    bad.append((path, lang, r.status_code))
        # 员工
        c.post("/login", data={"username": "emp1", "password": "demo123"},
               follow_redirects=False)
        for lang in ("zh", "ja"):
            r = c.get(f"/my/perf?lang={lang}")
            if r.status_code != 200:
                bad.append(("/my/perf", lang, r.status_code))
        # 登录页
        for lang in ("zh", "ja"):
            r = c.get(f"/login?lang={lang}")
            if r.status_code != 200:
                bad.append(("/login", lang, r.status_code))
        if bad:
            print("FAILED:", bad)
            return 1
        print("ALL PAGES OK: admin 11 pages ×2 lang + staff + login =",
              2 * len(admin_pages) + 4, "requests all 200")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
