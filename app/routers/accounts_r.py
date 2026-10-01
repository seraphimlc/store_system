# -*- coding: utf-8 -*-
"""账号管理路由：改自己密码（员工/管理员）；管理员管理员工账号。"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from app.templating import get_templates
from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password
from app.db import get_db
from app.models import Person, User
from app.routers.auth_r import csrf_ok, require_login

from app.forms import require_form_token as _dep_form_token  # noqa: E402

router = APIRouter(dependencies=[Depends(_dep_form_token)])
templates = get_templates()

# 员工状态：展示名 + 是否可登录 + 徽标样式
STATUS_LABELS = {"active": "在岗", "leave": "请假", "disabled": "停用",
                 "resigned": "离职"}
STATUS_ALLOW = {"active", "leave", "disabled", "resigned"}


def _status_pill(status: str) -> str:
    return {"active": "pill ok", "leave": "pill run", "disabled": "pill err",
            "resigned": "pill err"}.get(status, "pill")


def _denied():
    return RedirectResponse("/login", status_code=302)


@router.get("/my/password", response_class=HTMLResponse)
def my_password_page(request: Request, user: Optional[User] = Depends(require_login),
                     msg: str = "", must: str = ""):
    if user is None:
        return _denied()
    if must == "1" and not user.must_change_password:
        return RedirectResponse("/my/perf" if user.role == "staff"
                                else "/perf", status_code=302)
    return templates.TemplateResponse("my_password.html", {
        "request": request, "current_user": user, "msg": msg,
        "must_change": user.must_change_password})


@router.post("/my/password")
def my_password_submit(request: Request, old_password: str = Form(...),
                       new_password: str = Form(...), csrf_token: str = Form(...),
                       user: Optional[User] = Depends(require_login),
                       db: Session = Depends(get_db)):
    if user is None:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    if not verify_password(old_password, user.password_hash):
        return templates.TemplateResponse("my_password.html", {
            "request": request, "current_user": user,
            "msg": "原密码不对", "must_change": user.must_change_password},
            status_code=400)
    if len(new_password) < 6:
        return templates.TemplateResponse("my_password.html", {
            "request": request, "current_user": user,
            "msg": "新密码至少 6 位", "must_change": user.must_change_password},
            status_code=400)
    user.password_hash = hash_password(new_password)
    user.must_change_password = False
    db.commit()
    # 员工改完密码直接进「每日填报」（员工每天真正要用的页面）；
    # 原来跳「我的绩效」，但员工可见起始月收紧后那里常常是空的。
    dest = "/my/report" if user.role == "staff" else "/perf"
    return RedirectResponse(dest + "?msg=密码已修改，可正常使用", status_code=303)


@router.get("/staff-admin", response_class=HTMLResponse)
def staff_admin_page(request: Request, user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db), msg: str = "",
                     err: str = "",
                     status: str = "", new_token: str = "",
                     new_token_name: str = "", new_token_uid: str = ""):
    if user is None:
        return _denied()
    if user.role != "admin":
        return RedirectResponse("/my/perf", status_code=302)
    q = db.query(User).filter(User.role == "staff")
    if status in STATUS_ALLOW:
        q = q.filter(User.status == status)
    staff = q.order_by(User.id).all()
    # 每名员工的 MCP Token（代发/吊销/状态联动提示）
    from app.services import mcp_tokens as _mt
    tokens_by_user = {u.id: [_mt.decorate(r, u)
                             for r in _mt.list_for_user(db, u.id)] for u in staff}
    return templates.TemplateResponse("staff_admin.html", {
        "request": request, "current_user": user, "staff": staff,
        "msg": msg, "err": err, "status": status,
        "labels": STATUS_LABELS, "pill": _status_pill,
        "langs": {"": "自动", "zh": "中文", "ja": "日本語"},
        "counts": {s: db.query(User).filter(User.role == "staff",
                                           User.status == s).count()
                   for s in STATUS_LABELS},
        "tokens_by_user": tokens_by_user,
        "new_token": new_token, "new_token_name": new_token_name,
        "new_token_uid": new_token_uid})


# ---------- 新建员工（编号即身份键，规格 D19/D20） ----------
@router.post("/staff-admin/create")
def staff_create(request: Request, code: str = Form(""), name: str = Form(""),
                 username: str = Form(""), password: str = Form(""),
                 csrf_token: str = Form(...),
                 user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db)):
    """手工建号：编号必填；重复只提示、不覆盖、不建第二个。"""
    from urllib.parse import quote as _q
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import staff_accounts
    try:
        staff_accounts.create_staff(db, code=code, name=name,
                                    username=username, password=password)
        return RedirectResponse("/staff-admin?msg=" + _q("已创建员工"),
                                status_code=303)
    except staff_accounts.CodeExists:
        return RedirectResponse("/staff-admin?err=" + _q("该编号已存在"),
                                status_code=303)
    except ValueError as e:  # noqa: BLE001
        return RedirectResponse("/staff-admin?err=" + _q(str(e)),
                                status_code=303)


@router.post("/staff-admin/{uid}/edit")
def staff_edit(uid: int, request: Request,
               name: str = Form(""), username: str = Form(""),
               status: str = Form(""), lang: str = Form(""),
               password: str = Form(""),
               csrf_token: str = Form(...),
               user: Optional[User] = Depends(require_login),
               db: Session = Depends(get_db)):
    """弹窗里一次提交的员工编辑：姓名 / 登录名 / 状态 / 界面语言 / 重置口令。

    **人员编号不在这里改**（身份键 + 近 20 张引用表，2026-10-01 用户决定先不做）。
    """
    from urllib.parse import quote as _q
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    target = db.get(User, uid)
    if target is None or target.role != "staff":
        raise HTTPException(404, "员工不存在")
    if status and status not in STATUS_ALLOW:
        raise HTTPException(400, f"未知状态: {status}")
    if lang not in ("", "zh", "ja"):
        raise HTTPException(400, f"未知语言: {lang}")
    from app.services import staff_accounts as SA
    try:
        res = SA.update_staff(db, target, name=name, username=username,
                              status=status, lang=lang, password=password)
    except SA.UsernameExists:
        return RedirectResponse("/staff-admin?err=" + _q(
            "该登录名已被占用，没有做任何修改"), status_code=303)
    except ValueError as e:  # noqa: BLE001
        return RedirectResponse("/staff-admin?err=" + _q(str(e)), status_code=303)
    note = "；".join(res["changed"]) if res["changed"] else "没有改动"
    return RedirectResponse("/staff-admin?msg=" + _q("已保存：" + note),
                            status_code=303)


@router.post("/staff-admin/suggest-username")
def staff_suggest_username(name: str = Form(""), code: str = Form(""),
                           user: Optional[User] = Depends(require_login),
                           db: Session = Depends(get_db)):
    """按姓名生成登录名候选（admin 专属；AI 不可用时自动回退规则）。"""
    if user is None or user.role != "admin":
        return JSONResponse({"ok": False}, status_code=403)
    from app.services import staff_accounts
    return JSONResponse(dict(ok=True,
                             **staff_accounts.suggest_login_name(db, name, code)))


@router.post("/staff-admin/{uid}/lang")
def staff_set_lang(uid: int, request: Request, lang: str = Form(""),
                   csrf_token: str = Form(...),
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db)):
    """设置员工账号的界面语言（zh/ja/空=自动）。"""
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    target = db.get(User, uid)
    if target is None or target.role != "staff":
        raise HTTPException(404, "员工不存在")
    if lang not in ("", "zh", "ja"):
        raise HTTPException(400, f"未知语言: {lang}")
    target.lang = lang
    db.commit()
    label = "自动" if not lang else "中文" if lang == "zh" else "日本語"
    return RedirectResponse(
        f"/staff-admin?msg=已设置 {target.username} 的界面语言为「{label}」",
        status_code=303)


@router.post("/staff-admin/{uid}/status")
def staff_set_status(uid: int, request: Request, new_status: str = Form(...),
                     csrf_token: str = Form(...),
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    target = db.get(User, uid)
    if target is None or target.role != "staff":
        raise HTTPException(404, "员工不存在")
    if new_status not in STATUS_ALLOW:
        raise HTTPException(400, f"未知状态: {new_status}")
    target.status = new_status
    # 停用/离职 → 禁登录；在岗/请假 → 可登录（数据永不删除）
    target.is_active = new_status in ("active", "leave")
    db.commit()
    # token 状态与员工状态联动：仅 status=='active' 且 is_active 时 token 有效
    if new_status != "active":
        note = f"，其 token 已随之失效"
    else:
        note = f"，其 token 已恢复有效（未吊销/未过期）"
    return RedirectResponse(f"/staff-admin?msg=已更新 {target.username} 为「{STATUS_LABELS[new_status]}」"
                            f"{note}&status={new_status}", status_code=303)


@router.post("/staff-admin/{uid}/reset")
def staff_reset(uid: int, request: Request, password: str = Form("demo123"),
                csrf_token: str = Form(...),
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    target = db.get(User, uid)
    if target is None or target.role != "staff":
        raise HTTPException(404, "员工不存在")
    if len(password) < 6:
        raise HTTPException(400, "口令至少 6 位")
    target.password_hash = hash_password(password)
    target.must_change_password = True  # 重置后员工须再改一次
    db.commit()
    return RedirectResponse("/staff-admin?msg=已重置口令", status_code=303)


@router.get("/staff-admin/accounts-export")
def staff_accounts_export(request: Request, active: str = "",
                          user: Optional[User] = Depends(require_login),
                          db: Session = Depends(get_db)):
    """导出员工登录名清单（发号用）。active=1 → 只导出可登录（在岗且启用）的。"""
    if user is None or user.role != "admin":
        return _denied()
    import io as _io

    from fastapi.responses import StreamingResponse

    from app.services import staff_accounts
    data, fname = staff_accounts.accounts_xlsx(
        db, only_active=(active == "1"), base_url=str(request.base_url))
    from urllib.parse import quote
    cd = ("attachment; filename=staff_accounts.xlsx; filename*=UTF-8''%s"
          % quote(fname))
    return StreamingResponse(
        _io.BytesIO(data),
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"),
        headers={"Content-Disposition": cd})


@router.post("/staff-admin/reset-all")
def staff_reset_all(request: Request, password: str = Form("demo123"),
                    csrf_token: str = Form(...),
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """批量：全部员工口令重置为指定/默认口令，并要求首次登录改密。

    用于正式发号前一次性准备（如发放 demo123 后让每人首登改成自己的密码）。
    """
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    if len(password) < 6:
        raise HTTPException(400, "口令至少 6 位")
    staff = db.query(User).filter(User.role == "staff").all()
    n = 0
    for u in staff:
        u.password_hash = hash_password(password)
        u.must_change_password = True
        n += 1
    db.commit()
    return RedirectResponse(
        f"/staff-admin?msg=已重置 {n} 名员工口令并要求首登修改", status_code=303)
