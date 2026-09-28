# -*- coding: utf-8 -*-
"""登录/登出/仪表盘 路由。登录 POST 豁免 CSRF；其余页面见各 chunk。"""
from typing import Optional

from fastapi import APIRouter, Depends, Form, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.templating import get_templates
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import SESSION_COOKIE, new_session_token, read_session_token, verify_password
from app.config import get_settings
from app.db import get_db
from app.models import ImportFile, User

from app.forms import require_form_token as _dep_form_token  # noqa: E402

router = APIRouter(dependencies=[Depends(_dep_form_token)])
templates = get_templates()


def _safe_next(next_url: str) -> str:
    """登录回跳白名单：仅允许站内相对路径（拒绝 //、空白、外部 URL）。

    OAuth 授权流程（/oauth/authorize?...）经 `/login?next=` 回跳（规格 §五.7）。
    """
    n = (next_url or "").strip()
    if not n or n.startswith("//") or "\\" in n \
            or any(ch.isspace() for ch in n):
        return "/"
    if not n.startswith("/"):
        return "/"
    return n


def _set_session(response, uid: int):
    token = new_session_token(uid)
    response.set_cookie(SESSION_COOKIE, token, httponly=True, samesite="lax",
                        max_age=3600 * get_settings().session_ttl_hours)


def current_user(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    data = read_session_token(request.cookies.get(SESSION_COOKIE))
    if not data:
        return None
    return db.get(User, data["uid"])


def require_login(request: Request, db: Session = Depends(get_db)) -> Optional[User]:
    user = current_user(request, db)
    if user is None or not user.can_login:
        return None
    request.state.user = user
    data = read_session_token(request.cookies.get(SESSION_COOKIE))
    request.state.csrf = (data or {}).get("csrf", "")
    return user


def csrf_ok(request: Request, posted: str) -> bool:
    data = read_session_token(request.cookies.get(SESSION_COOKIE)) or {}
    return bool(data) and data.get("csrf") == posted


@router.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = Query("")):
    if read_session_token(request.cookies.get(SESSION_COOKIE)):
        return RedirectResponse(_safe_next(next), status_code=302)
    return templates.TemplateResponse("login.html",
                                      {"request": request, "error": None,
                                       "next": next})


@router.post("/login")
def login_submit(username: str = Form(...), password: str = Form(...),
                 next: str = Form(""),
                 request: Request = None, db: Session = Depends(get_db)):
    user = db.execute(select(User).where(User.username == username)).scalars().first()
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse("login.html",
                                          {"request": request,
                                           "error": "用户名或密码错误",
                                           "next": next},
                                          status_code=401)
    if not user.can_login:
        return templates.TemplateResponse("login.html",
                                          {"request": request,
                                           "error": "该账号当前不可登录（停用/离职），请联系管理员",
                                           "next": next},
                                          status_code=401)
    resp = RedirectResponse(_safe_next(next), status_code=302)
    _set_session(resp, user.id)
    return resp


@router.post("/logout")
def logout(request: Request, csrf_token: str = Form(...)):
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    resp = RedirectResponse("/login", status_code=302)
    resp.delete_cookie(SESSION_COOKIE)
    return resp


@router.get("/", response_class=HTMLResponse)
def root_redirect(request: Request):
    data = read_session_token(request.cookies.get(SESSION_COOKIE))
    if data:
        from app.db import SessionLocal
        from app.models import User
        s = SessionLocal()
        try:
            u = s.get(User, data.get("uid"))
            if u is not None and u.role == "staff":
                return RedirectResponse("/my/perf", status_code=302)
        finally:
            s.close()
    # 首页 = 数据看板（管理员）
    return RedirectResponse("/dashboard", status_code=302)
