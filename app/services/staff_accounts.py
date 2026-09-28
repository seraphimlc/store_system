# -*- coding: utf-8 -*-
"""手工新建员工：编号即身份键（规格 D19/D20）。

场景：新员工还没在任何文件里出现过，管理员先按「员工编号」开户，让他当天就能填报；
文件到达后**按编号自动接上**，不需要任何合并/关联动作。

- 编号必填：NFKC 归一 + 去首尾空白（与导入侧口径一致），长度 ≤ 32（`Person.code`）
- 编号已存在 → `CodeExists`（不覆盖、不建第二个）
- 登录名：模型优先生成拼音/罗马字，未配置或失败自动回退规则，再去重
"""
import re
import unicodedata

from app.models import Person, User

_CLEAN = re.compile(r"[^a-z0-9-]+")


class CodeExists(Exception):
    """员工编号已存在。"""


def normalize_code(code: str) -> str:
    """编号归一：NFKC（全角→半角）+ 去首尾空白。"""
    return unicodedata.normalize("NFKC", code or "").strip()


def clean_username(name: str) -> str:
    """登录名清洗（小写 a-z0-9 与连字符）。"""
    return _CLEAN.sub("", (name or "").lower()).strip("-")


def suggest_login_name(db, name: str, code: str = "") -> dict:
    """生成登录名候选（不落库）：{username, source}；source = ai | rule。"""
    from app.services import login_names
    cand = None
    if name:
        cand = login_names.ai_login_candidates([name]).get(name)
    base = clean_username(cand) if cand else None
    if not base:
        base = login_names.suggest_username(name, code)
    return {"username": login_names.unique_username(db, name, code, base=base),
            "source": "ai" if cand else "rule"}


def create_staff(db, *, code: str, name: str, username: str = "",
                 password: str = "") -> Person:
    """建 Person + User（staff）；编号重复 → CodeExists。"""
    from app.auth import hash_password
    from app.config import get_settings
    from app.services import login_names

    code = normalize_code(code)
    if not code:
        raise ValueError("员工编号必填")
    if len(code) > 32:
        raise ValueError("员工编号不能超过 32 位")
    if db.get(Person, code) is not None:
        raise CodeExists(code)
    name = (name or "").strip()
    if not name:
        raise ValueError("姓名必填")

    db.add(Person(code=code, display_name=name, first_seen_import_id=None))
    uname = clean_username(username) or suggest_login_name(db, name, code)["username"]
    uname = login_names.unique_username(db, name, code, base=uname)
    db.add(User(username=uname,
                password_hash=hash_password(password or get_settings().default_staff_password),
                display_name=name, role="staff", person_code=code,
                is_active=True, status="active", must_change_password=True))
    db.commit()
    return db.get(Person, code)
