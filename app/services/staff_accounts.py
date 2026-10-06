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
                 password: str = "", role: str = "staff") -> Person:
    """建 Person + User（staff / leader）；编号重复 → CodeExists。

    `role`：`staff` 队员 / `leader` 队长 —— **不允许建 admin**（管理员账号不从这里出）。
    """
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
                display_name=name,
                role=("leader" if role == "leader" else "staff"),
                person_code=code,
                is_active=True, status="active", must_change_password=True))
    db.commit()
    return db.get(Person, code)


def accounts_xlsx(db, *, only_active: bool = False, base_url: str = ""):
    """导出员工账号清单（发号用）：登录名 / 姓名 / 编号 / 状态 / 是否需首登改密 / 初始口令。

    `初始口令` 只在"待首登改密"时写出（= 系统默认口令），否则留空 —— 说明本人已自设密码。
    返回 (xlsx 字节, 文件名)；用 write_only 流式写。
    """
    import io

    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    from app.config import get_settings
    from app.models import User

    # ⚠️ 2026-10-06：原来只导 `role == 'staff'` → **队长不出现在账号表里**
    #    （管理端「员工管理」页早就是 ("staff","leader")，导出漏了；用户口径"队长不也是staff嘛"）
    q = db.query(User).filter(User.role.in_(("staff", "leader")))
    if only_active:
        q = q.filter(User.status == "active", User.is_active.is_(True))
    rows = q.order_by(User.status, User.person_code).all()
    settings = get_settings()
    default_pw = settings.default_staff_password
    # 公开地址优先取配置（应用在 nginx 后面看不到 https，request.base_url 会是 http://）
    url = (settings.visit_oauth_issuer or base_url or "").rstrip("/") + "/login"

    wb = Workbook(write_only=True)
    ws = wb.create_sheet("员工登录名")
    head = ["登录名", "姓名", "员工编号", "在职状态", "账号启用",
            "首次登录需改密", "初始口令", "界面语言", "登录地址"]
    cells = []
    for i, h in enumerate(head, 1):
        c = WriteOnlyCell(ws, value=h)
        c.font = Font(bold=True)
        cells.append(c)
        ws.column_dimensions[c.column_letter].width = [14, 14, 20, 10, 10, 14, 12, 10, 34][i - 1]
    ws.append(cells)
    for u in rows:
        must = bool(u.must_change_password)
        ws.append([u.username, u.display_name, u.person_code or "",
                   u.status, "是" if u.is_active else "否",
                   "是" if must else "", default_pw if must else "",
                   u.lang or "自动", url])

    ws2 = wb.create_sheet("使用说明")
    for line in (["项目", "说明"],
                 ["登录地址", url],
                 ["初始口令", "%s（仅用于首次登录）" % default_pw],
                 ["首次登录", "系统强制要求改为自己的密码，改完才能进入其它页面"],
                 ["账号状态", "「非在岗」（离职等）的账号无法登录，不必分发"],
                 ["忘记密码", "请联系管理员在员工管理页重置"]):
        ws2.append(line)

    bio = io.BytesIO()
    wb.save(bio)
    from datetime import date
    return bio.getvalue(), "员工登录名_%s%s.xlsx" % (
        date.today().isoformat(), "_仅在岗" if only_active else "")


# ---------------- 编辑员工（弹窗一次提交） ----------------

class UsernameExists(Exception):
    """登录名已被占用。"""


def update_staff(db, user, *, name: str = None, username: str = None,
                 status: str = None, lang: str = None,
                 password: str = None, role: str = None) -> dict:
    """编辑员工（弹窗一次提交）：**姓名 / 登录名 / 状态 / 界面语言 / 重置口令 / 角色**。

    只传需要改的字段（None = 不改）。返回改动说明，用于页面提示。

    `role` 只允许 `staff`（队员）/ `leader`（队长）——**不允许改成 admin**，
    避免误把普通员工提成管理员（规格 `docs/specs-team-management.md` Q4）。

    ⚠️ **人员编号不支持在这里改**（2026-10-01 用户决定）：编号是身份键，改它要级联改
    `persons` + 近 20 张引用表（计划/自报/绩效/工资/分析/对账），风险远大于收益，先不做。
    """
    from app.auth import hash_password
    from app.models import Person

    changed = []
    if name is not None and name.strip() and name.strip() != (user.display_name or ""):
        nm = name.strip()
        user.display_name = nm
        p = db.get(Person, user.person_code) if user.person_code else None
        if p is not None:
            p.display_name = nm
        changed.append("姓名 → %s" % nm)
    if username is not None and (username or "").strip():
        uname = clean_username(username)
        if not uname:
            raise ValueError("登录名只能用字母、数字和连字符")
        if uname != user.username:
            if db.query(User).filter(User.username == uname).first() is not None:
                raise UsernameExists(uname)      # 明确拒绝，不自动加后缀
            changed.append("登录名 %s → %s" % (user.username, uname))
            user.username = uname
    if status is not None and status != user.status:
        user.status = status
        user.is_active = status in ("active", "leave")
        changed.append("状态 → %s" % status)
    if lang is not None and lang != (user.lang or ""):
        user.lang = lang
        changed.append("语言 → %s" % (lang or "自动"))
    if role in ("staff", "leader") and role != user.role:
        user.role = role
        changed.append("角色 → %s" % ("队长" if role == "leader" else "队员"))
    if password:
        user.password_hash = hash_password(password)
        user.must_change_password = True
        changed.append("口令已重置（首登须改密）")
    db.commit()
    return {"changed": changed}
