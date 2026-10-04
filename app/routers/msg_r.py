# -*- coding: utf-8 -*-
"""站内消息路由（用户 2026-10-03）。

- 员工端/队长端/管理端**同一个页面** `/messages`：收件箱 / 发件箱 / 发消息
- 员工只读（不能发）；队长只能发给**本队队员**；管理员可发给任意人 / 全体
- 未读红点在底栏（中间件注入 `request.state.unread_messages`）

规格：`docs/specs-messages.md`。
"""
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.forms import require_form_token as _dep_form_token
from app.models import User
from app.routers.auth_r import csrf_ok, require_login
from app.templating import get_templates

router = APIRouter(dependencies=[Depends(_dep_form_token)])
templates = get_templates()


def _denied():
    return RedirectResponse("/login", status_code=302)


def _user_guard(user):
    """消息模块对**三种角色**都开放（staff 只读收件箱）。

    ⚠️ **管理员账号可能没有 `person_code`**（纯管理账号）→ 不能拿它当登录判据，
    否则管理员连发件箱都进不去（收件箱为空而已）。
    """
    if user is None:
        return _denied()
    if getattr(user, "role", "") == "admin":
        return None
    if not getattr(user, "person_code", None):
        return _denied()
    return None


@router.get("/messages", response_class=HTMLResponse)
def messages_page(request: Request, tab: str = "inbox", show: str = "",
                  page: int = 1, per: int = 0,
                  msg: str = "", err: str = "",
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db)):
    """消息中心：收件箱 / 发件箱 / 发消息。"""
    g = _user_guard(user)
    if g:
        return g
    from app.services import bd_msg, paging
    can_send = user.role in ("admin", "leader")
    where = tab if tab in ("inbox", "sent", "new") else "inbox"
    if where == "sent" and can_send:
        pager = bd_msg.sent(db, user, page=page, per=per or paging.PER_DEFAULT)
        rows = pager["rows"]
    else:
        pager = bd_msg.inbox(db, user.person_code or "",
                             unread_only=(show == "unread"), page=page,
                             per=per or paging.PER_DEFAULT)
        rows = pager["rows"]
    return templates.TemplateResponse("messages.html", {
        "request": request, "current_user": user,
        "tab": where, "show": show, "rows": rows,
        "pager": pager, "page_qs": paging.qs(request.query_params),
        "can_send": can_send,
        "candidates": bd_msg.candidates(db, user) if can_send else [],
        "unread": bd_msg.unread_count(db, user.person_code or ""),
        "unread_by_kind": bd_msg.unread_by_kind(db, user.person_code),
        "msg": msg, "err": err,
    })


@router.post("/messages/send")
def messages_send(request: Request, title: str = Form(""),
                  body: str = Form(""), person: Optional[List[str]] = Form(None),
                  mode: str = Form("pick"),
                  csrf_token: str = Form(""),
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db)):
    """发消息：一个 / 多个 / 全体（**收件人范围由服务层校验，防止越界**）。"""
    g = _user_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_msg
    try:
        codes = bd_msg.recipients_for(db, user, mode=mode, codes=person or ())
        m = bd_msg.send(db, user, codes, title, body)
        db.commit()
        return RedirectResponse(
            "/messages?tab=sent&msg=%s" % quote("已发送给 %d 人" % len(codes)),
            status_code=303)
    except bd_msg.MsgError as e:
        db.rollback()
        return RedirectResponse("/messages?tab=new&err=%s" % quote(str(e)),
                                status_code=303)


@router.post("/messages/{message_id}/read")
def messages_read(request: Request, message_id: int,
                  csrf_token: str = Form(""),
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db)):
    g = _user_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_msg
    bd_msg.mark_read(db, message_id, user.person_code)
    db.commit()
    return RedirectResponse("/messages", status_code=303)


@router.get("/messages/{message_id}/go")
def messages_go(message_id: int,
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db)):
    """点开消息：**标记已读**并跳到目标页（没有目标就回消息列表）。"""
    g = _user_guard(user)
    if g:
        return g
    from app.services import bd_msg
    row = bd_msg.get_for(db, message_id, user.person_code)
    if row is None:
        return RedirectResponse("/messages", status_code=303)
    bd_msg.mark_read(db, message_id, user.person_code)
    db.commit()
    from app.models import BdMessage
    m = db.get(BdMessage, message_id)
    target = (m.url or "") if m is not None else ""
    if not target.startswith("/"):
        target = "/messages"
    return RedirectResponse(target, status_code=303)


@router.post("/messages/read-all")
def messages_read_all(request: Request, csrf_token: str = Form(""),
                      user: Optional[User] = Depends(require_login),
                      db: Session = Depends(get_db)):
    g = _user_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_msg
    n = bd_msg.mark_all_read(db, user.person_code)
    db.commit()
    return RedirectResponse("/messages?msg=%s"
                            % quote("已把 %d 条标为已读" % n), status_code=303)
