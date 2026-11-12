# -*- coding: utf-8 -*-
"""表单一次性提交令牌的接线层：模板全局函数 + 路由依赖 + 客户端防双击。

- `form_token()`：Jinja 全局函数，模板每个 POST 表单调用一次；**同一请求复用同一个 token**
  （一个页面有多个表单也能各自提交）
- `require_form_token`：路由级依赖，校验并作废令牌；未登录/非表单/机器端点自动跳过
  （未登录时不拦截，交给各路由自己的守卫，保持原有的 302 跳登录行为）
- 客户端防双击：`base.html` 里统一处理（提交后禁用按钮，4 秒或校验失败后恢复）
"""
from contextvars import ContextVar
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.db import get_db

# 每请求上下文：{"uid": int|None, "token": str|None}（由 main.py 的中间件设置）
CTX: ContextVar[Optional[dict]] = ContextVar("form_ctx", default=None)

# 机器端点 / 无需令牌的路径（登录页本身没有会话）
#: 只读端点也豁免一次性令牌：它们不改数据，但会**消耗**掉页面表单共用的那个 token
#: （2026-10-06 审计：AI 派工建议与车站池表单共用 _ft → 点完 AI 再点「分配给该队」必 400）
EXEMPT_PREFIXES = ("/login", "/logout", "/oauth/", "/.well-known/",
                   "/tasks/ai-suggest",
                   "/healthz", "/static", "/favicon")


def reset_ctx(uid=None) -> None:
    CTX.set({"uid": uid, "token": None})


def form_token() -> str:
    """Jinja 全局：返回本请求的表单令牌（懒发放，同请求复用）。"""
    ctx = CTX.get()
    if ctx is None:
        return ""                            # 非请求上下文（直接渲染模板，如单测）
    if not ctx.get("token"):
        from app.db import SessionLocal
        from app.services import form_tokens
        db = SessionLocal()
        try:
            ctx["token"] = form_tokens.issue(db, ctx.get("uid"))
        finally:
            db.close()
    return ctx["token"]


def _is_form_post(request: Request) -> bool:
    if request.method != "POST":
        return False
    if any(request.url.path.startswith(p) for p in EXEMPT_PREFIXES):
        return False
    ctype = (request.headers.get("content-type") or "").lower()
    return (ctype.startswith("application/x-www-form-urlencoded")
            or ctype.startswith("multipart/form-data"))


def _uid_of(request: Request):
    from app.auth import SESSION_COOKIE, read_session_token
    data = read_session_token(request.cookies.get(SESSION_COOKIE))
    return (data or {}).get("uid")


async def require_form_token(request: Request,
                             db: Session = Depends(get_db)) -> None:
    """依赖：表单 POST 必须带有效的一次性令牌（未登录跳过，交给路由守卫）。"""
    if not _is_form_post(request):
        return
    uid = _uid_of(request)
    if uid is None:
        return                               # 未登录：不在这里拦截（保持 302 跳登录）
    form = await request.form()              # 与端点共用解析结果（Starlette 会缓存）
    token = str(form.get("_ft") or "")
    from app.services import form_tokens
    if not form_tokens.consume(db, token, uid):
        raise HTTPException(400, "表单已提交过或已过期，请刷新页面重试")
