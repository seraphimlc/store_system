# -*- coding: utf-8 -*-
"""命令行工具：create-admin（幂等）。

用法:  ./.venv/bin/python -m app.cli create-admin
账号/密码来自环境变量 ADMIN_USERNAME/ADMIN_PASSWORD（默认 admin / 空则提示）。
"""
import os
import sys


def create_admin():
    from sqlalchemy import select

    from app.auth import hash_password
    from app.config import get_settings
    from app.db import SessionLocal
    from app.models import User

    s = get_settings()
    username = s.admin_username
    password = s.admin_password
    if not password:
        password = os.environ.get("ADMIN_PASSWORD", "")
    if not password:
        print("未设置密码：请 export ADMIN_PASSWORD=... 后重试", file=sys.stderr)
        sys.exit(1)
    db = SessionLocal()
    try:
        user = db.execute(select(User).where(User.username == username)).scalars().first()
        if user is None:
            db.add(User(username=username, password_hash=hash_password(password),
                        display_name="管理员", role="admin", is_active=True))
            print(f"创建管理员 {username}")
        else:
            user.password_hash = hash_password(password)
            user.is_active = True
            print(f"更新管理员 {username} 密码")
        db.commit()
    finally:
        db.close()


def create_employee(username: str, person_code: str, password: str = "demo123"):
    """建员工账号并绑定人员编号（staff）。"""
    from sqlalchemy import select
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.models import Person, User
    db = SessionLocal()
    try:
        person = db.query(Person).filter(Person.code == person_code).first()
        if person is None:
            print(f"人员编号不存在: {person_code}", file=sys.stderr)
            sys.exit(1)
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            db.add(User(username=username, password_hash=hash_password(password),
                        display_name=person.display_name or person_code,
                        role="staff", person_code=person_code, is_active=True,
                        must_change_password=True))
            print(f"创建员工 {username}（{person_code}）")
        else:
            user.role = "staff"
            user.person_code = person_code
            print(f"更新 {username} 为员工（{person_code}）")
        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "create-admin":
        create_admin()
    elif cmd == "create-employee":
        if len(sys.argv) >= 4:
            create_employee(sys.argv[2], sys.argv[3],
                            sys.argv[4] if len(sys.argv) > 4 else "demo123")
        else:
            print("用法: create-employee <用户名> <人员编号> [口令]")
            sys.exit(1)
    else:
        print(__doc__)
        sys.exit(1)
