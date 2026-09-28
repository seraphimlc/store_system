# -*- coding: utf-8 -*-
"""管理员代发/吊销 MCP Token（/staff-admin/*）。

2026-09-28 用户明确：**员工不需要感知 MCP 操作**，因此
- 员工端自助签发页 `/my/token` **已删除**（员工走 OAuth SSO，换出的 access token
  同样是 `api_tokens` 一行，不需要自己管凭证）；
- 审计页 `/mcp-audit` **已删除**（调用日志只供我方离线统计，数据仍在 `mcp_audit_log` 表）；
- 保留管理员代发/吊销：OAuth 异常时的按人应急通道。

明文 token 只在签发后的那个响应页显示一次；写操作沿用 `csrf_ok` + 登录校验。
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

from app.forms import require_form_token as _dep_form_token  # noqa: E402

router = APIRouter(dependencies=[Depends(_dep_form_token)])
templates = get_templates()

SCOPES_ALLOW = {"read", "read,write"}
PER_PAGE = 50


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
