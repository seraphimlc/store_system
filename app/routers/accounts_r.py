# -*- coding: utf-8 -*-
"""账号管理路由：改自己密码（员工/管理员）；管理员管理员工账号。"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.templating import get_templates
from sqlalchemy.orm import Session

from app.auth import hash_password, verify_password
from app.db import get_db
from app.models import Person, User
from app.routers.auth_r import csrf_ok, require_login

router = APIRouter()
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
    dest = "/my/perf" if user.role == "staff" else "/perf"
    return RedirectResponse(dest + "?msg=密码已修改，可正常使用", status_code=303)


@router.get("/staff-admin", response_class=HTMLResponse)
def staff_admin_page(request: Request, user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db), msg: str = "",
                     status: str = ""):
    if user is None:
        return _denied()
    if user.role != "admin":
        return RedirectResponse("/my/perf", status_code=302)
    q = db.query(User).filter(User.role == "staff")
    if status in STATUS_ALLOW:
        q = q.filter(User.status == status)
    staff = q.order_by(User.id).all()
    return templates.TemplateResponse("staff_admin.html", {
        "request": request, "current_user": user, "staff": staff,
        "msg": msg, "status": status,
        "labels": STATUS_LABELS, "pill": _status_pill,
        "langs": {"": "自动", "zh": "中文", "ja": "日本語"},
        "counts": {s: db.query(User).filter(User.role == "staff",
                                           User.status == s).count()
                   for s in STATUS_LABELS}})


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
    return RedirectResponse(f"/staff-admin?msg=已更新 {target.username} 为「{STATUS_LABELS[new_status]}」"
                            f"&status={new_status}", status_code=303)


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
