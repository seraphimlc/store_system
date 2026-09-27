# -*- coding: utf-8 -*-
"""MCP Token 自助签发（/my/token）+ 管理员代发/吊销（/staff-admin/*）+ MCP 调用审计（/mcp-audit）。

写操作（生成/吊销）沿用项目既有 `csrf_ok` / `request.state.csrf` 模式 + 登录校验；
明文 token 只在签发后的那个响应页显示一次（URL 经 JS history.replaceState 清除）。
"""
from datetime import datetime, timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ApiToken, McpAuditLog, User
from app.routers.accounts_r import _denied
from app.routers.auth_r import csrf_ok, require_login
from app.services import mcp_tokens
from app.templating import get_templates

router = APIRouter()
templates = get_templates()

SCOPES_ALLOW = {"read", "read,write"}
PER_PAGE = 50


# ---------- /my/token：登录用户自助 ----------
@router.get("/my/token", response_class=HTMLResponse)
def my_token_page(request: Request, user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db),
                  issued: str = "", name: str = "", token: str = "",
                  msg: str = ""):
    if user is None:
        return _denied()
    tokens = [mcp_tokens.decorate(row, user)
              for row in mcp_tokens.list_for_user(db, user.id)]
    return templates.TemplateResponse("my_token.html", {
        "request": request, "current_user": user,
        "tokens": tokens, "issued": bool(issued), "new_token": token,
        "new_token_name": name, "msg": msg,
        "is_admin": user.role == "admin",
        "page_state": {"page": "my_token", "token_count": len(tokens)}})


@router.post("/my/token/issue")
def my_token_issue(request: Request, name: str = Form(""),
                   scope: str = Form("read"), days: str = Form("90"),
                   csrf_token: str = Form(...),
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db)):
    if user is None:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    if scope not in SCOPES_ALLOW:
        raise HTTPException(400, "权限只能是只读或读写")
    if days == "permanent" and user.role != "admin":
        days = "90"  # 员工强制 90 天（永久仅管理员）
    if days not in ("90", "permanent"):
        raise HTTPException(400, "有效期只能是 90 天或永久")
    n_days = 90 if days == "90" else None
    raw, row = mcp_tokens.issue(db, user.id, name, scope, n_days)
    q = (f"?issued=1&name={quote((name or '').strip()[:128])}&token={raw}")
    return RedirectResponse("/my/token" + q, status_code=303)


@router.post("/my/token/{tid}/revoke")
def my_token_revoke(tid: int, request: Request, csrf_token: str = Form(...),
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    if user is None:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    if not mcp_tokens.revoke(db, tid, user.id):
        raise HTTPException(404, "Token 不存在或不属于你")
    return RedirectResponse("/my/token?msg=已吊销", status_code=303)


# ---------- 管理员：代发/吊销任意员工 token ----------
@router.post("/staff-admin/{uid}/tokens/issue")
def staff_admin_tokens_issue(uid: int, request: Request, name: str = Form(""),
                             scope: str = Form("read"), days: str = Form("90"),
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
    if scope not in SCOPES_ALLOW:
        raise HTTPException(400, "权限只能是只读或读写")
    if days not in ("90", "permanent"):
        raise HTTPException(400, "有效期只能是 90 天或永久")
    n_days = 90 if days == "90" else None
    raw, row = mcp_tokens.issue(db, uid, name, scope, n_days)
    q = (f"?new_token={raw}&new_token_name={quote((name or '').strip()[:128])}"
         f"&new_token_uid={uid}")
    return RedirectResponse("/staff-admin" + q, status_code=303)


@router.post("/staff-admin/{uid}/tokens/{tid}/revoke")
def staff_admin_tokens_revoke(uid: int, tid: int, request: Request,
                              csrf_token: str = Form(...),
                              user: Optional[User] = Depends(require_login),
                              db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    tok = db.get(ApiToken, tid)
    if tok is None or tok.user_id != uid:
        raise HTTPException(404, "Token 不存在")
    mcp_tokens.revoke(db, tid, uid)
    return RedirectResponse("/staff-admin?msg=已吊销", status_code=303)


# ---------- /mcp-audit：管理员审计 ----------
@router.get("/mcp-audit", response_class=HTMLResponse)
def mcp_audit_page(request: Request,
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db),
                   user_id: str = Query(""), tool: str = Query(""),
                   from_: str = Query("", alias="from"),
                   to: str = Query("", alias="to"),
                   page: int = Query(1, ge=1)):
    if user is None:
        return _denied()
    if user.role != "admin":
        return RedirectResponse("/my/perf", status_code=302)
    q = db.query(McpAuditLog)
    if user_id.strip().isdigit():
        q = q.filter(McpAuditLog.user_id == int(user_id))
    if tool.strip():
        q = q.filter(McpAuditLog.tool.like("%" + tool.strip() + "%"))
    for field, fmt in (("from_", "%Y-%m-%d"), ("to", "%Y-%m-%d")):
        v = locals()[field].strip()
        if not v:
            continue
        try:
            dt = datetime.strptime(v, fmt)
        except ValueError:
            raise HTTPException(400, "时间格式应为 YYYY-MM-DD")
        if field == "from_":
            q = q.filter(McpAuditLog.created_at >= dt)
        else:
            q = q.filter(McpAuditLog.created_at
                         <= dt + timedelta(days=1) - timedelta(seconds=1))
    total = q.count()
    pages = max(1, (total + PER_PAGE - 1) // PER_PAGE)
    page = min(page, pages)
    rows = (q.order_by(McpAuditLog.id.desc())
             .offset((page - 1) * PER_PAGE).limit(PER_PAGE).all())
    users = db.query(User).order_by(User.id).all()
    user_map = {u.id: u for u in users}
    return templates.TemplateResponse("mcp_audit.html", {
        "request": request, "current_user": user, "rows": rows,
        "users": users, "user_map": user_map,
        "f_user_id": user_id, "f_tool": tool, "f_from": from_, "f_to": to,
        "page": page, "pages": pages, "total": total,
        "page_state": {"page": "mcp_audit", "total": total, "page": page}})
