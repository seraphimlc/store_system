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
    """建员工账号并绑定人员编号（staff）。

    **编号是身份键**（规格 D19/D20）：先按编号查，已有账号就复用，
    绝不因为"登录名不同"给同一个人开出第二个账号
    （历史坑：本函数原先只按 username 查重，导致同一编号出现两条账号）。
    """
    from app.auth import hash_password
    from app.db import SessionLocal
    from app.models import Person, User
    db = SessionLocal()
    try:
        person = db.query(Person).filter(Person.code == person_code).first()
        if person is None:
            print(f"人员编号不存在: {person_code}", file=sys.stderr)
            sys.exit(1)
        by_code = (db.query(User)
                   .filter(User.role == "staff", User.person_code == person_code)
                   .first())
        if by_code is not None:
            print(f"该编号已有员工账号 {by_code.username}（{person_code}），"
                  f"不重复创建")
            return by_code
        user = db.query(User).filter(User.username == username).first()
        if user is None:
            u = User(username=username, password_hash=hash_password(password),
                     display_name=person.display_name or person_code,
                     role="staff", person_code=person_code, is_active=True,
                     must_change_password=True)
            db.add(u)
            print(f"创建员工 {username}（{person_code}）")
            db.commit()
            return u
        # 登录名被占用：如果是别人的编号，拒绝（避免把编号挂到别人账号上）
        if user.person_code and user.person_code != person_code:
            print(f"登录名 {username} 已被编号 {user.person_code} 占用，"
                  f"请换一个登录名", file=sys.stderr)
            sys.exit(2)
        user.role = "staff"
        user.person_code = person_code
        print(f"更新 {username} 为员工（{person_code}）")
        db.commit()
        return user
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
