# -*- coding: utf-8 -*-
"""MCP OAuth 端点（挂在现有 web 应用上，规格：docs/specs-mcp-oauth.md §二）。

- `/.well-known/oauth-protected-resource` / `oauth-authorization-server`：发现
- `POST /oauth/register`：动态客户端注册（DCR）
- `GET/POST /oauth/authorize`：授权码 + PKCE（未登录跳登录页；已登录显示确认页）
- `POST /oauth/token`：authorization_code / refresh_token 换 token

`VISIT_OAUTH_ENABLED=0` 时 authorize/register/token 一律 404（发现端点保留，
`/my/token` 自助签发不受影响）。
"""
import json
from typing import Optional
from urllib.parse import parse_qs, quote

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.models import User
from app.routers.auth_r import csrf_ok, require_login
from app.services import oauth
from app.templating import get_templates

router = APIRouter()
templates = get_templates()


def _enabled() -> bool:
    return get_settings().visit_oauth_enabled


def _require_enabled():
    if not _enabled():
        raise HTTPException(404, "OAuth 未启用")


def _error_response(error: str, status: int = 400):
    return JSONResponse({"error": error}, status_code=status)


# ---------- 发现（RFC 9728 / RFC 8414） ----------

@router.get("/.well-known/oauth-protected-resource")
def oauth_protected_resource():
    iss = oauth.issuer()
    return {
        "resource": iss,
        "authorization_servers": [iss],
        "scopes_supported": ["read", "read,write"],
    }


@router.get("/.well-known/oauth-authorization-server")
def oauth_authorization_server():
    iss = oauth.issuer()
    return {
        "issuer": iss,
        "authorization_endpoint": f"{iss}/oauth/authorize",
        "token_endpoint": f"{iss}/oauth/token",
        "registration_endpoint": f"{iss}/oauth/register",
        "code_challenge_methods_supported": ["S256"],
        "grant_types_supported": ["authorization_code", "refresh_token"],
        "response_types_supported": ["code"],
        "token_endpoint_auth_methods_supported": ["none"],
        "scopes_supported": ["read", "read,write"],
    }


# ---------- 动态客户端注册（RFC 7591） ----------

@router.post("/oauth/register")
async def oauth_register(request: Request, db: Session = Depends(get_db)):
    _require_enabled()
    try:
        body = await request.body()
        payload = json.loads(body.decode("utf-8")) if body else {}
    except Exception:  # noqa: BLE001  json 解析失败
        return _error_response("invalid_client_metadata")
    if not isinstance(payload, dict):
        return _error_response("invalid_client_metadata")
    try:
        client = oauth.register_client(
            db, payload.get("client_name", ""), payload.get("redirect_uris", []))
    except oauth.OAuthError as exc:
        return _error_response(exc.error, exc.status)
    return {
        "client_id": client.client_id,
        "client_name": client.client_name,
        "redirect_uris": list(client.redirect_uris or []),
        "token_endpoint_auth_method": "none",
        # 公共客户端（PKCE 强制）：不发放 client_secret（规格 §五.10「若发放」）
    }


# ---------- 授权 ----------

def _validate_authorize(db, p: dict, role: str = "admin"):
    """校验授权参数；返回 (client, redirect_uri, scope, state, code_challenge)。

    失败抛 oauth.OAuthError（调用方转 JSON error 响应）。
    redirect_uri 不匹配 → invalid_request 且不发 code（规格 §五.3 / 验收 5）。
    scope 已按角色预降级（员工申请 write → read），确认页展示最终授予范围。
    """
    if p.get("response_type") != "code":
        raise oauth.OAuthError("unsupported_response_type")
    client = oauth.find_client(db, p.get("client_id", ""))
    if client is None:
        raise oauth.OAuthError("invalid_request", description="client_id 未注册")
    redirect_uri = (p.get("redirect_uri") or "").strip()
    if not oauth.redirect_ok(client, redirect_uri):
        # 精确匹配失败 → invalid_request，不发 code
        raise oauth.OAuthError("invalid_request",
                               description="redirect_uri 与注册时不匹配")
    challenge = (p.get("code_challenge") or "").strip()
    method = (p.get("code_challenge_method") or "S256").strip()
    if not challenge or method != "S256":
        # 规格 §五.1：PKCE 强制，仅 S256
        raise oauth.OAuthError("invalid_request",
                               description="必须使用 PKCE S256")
    scope = oauth.scope_for_user(role, (p.get("scope") or "read").strip())
    return client, redirect_uri, scope, (p.get("state") or ""), challenge


@router.get("/oauth/authorize", response_class=HTMLResponse)
def oauth_authorize_page(request: Request,
                         user: Optional[User] = Depends(require_login),
                         db: Session = Depends(get_db)):
    _require_enabled()
    p = {k: v for k, v in request.query_params.items()}
    try:
        client, redirect_uri, scope, state, challenge = _validate_authorize(
            db, p, role=user.role if user else "admin")
    except oauth.OAuthError as exc:
        return _error_response(exc.error, exc.status)
    if user is None:
        # 未登录 → 跳现有登录页并带回跳（规格 §五.7）。next 用站内相对路径
        # （含 query，URL 编码进 ?next=），登录 POST 白名单校验后原样回跳。
        next_url = request.url.path
        if request.url.query:
            next_url += "?" + request.url.query
        return RedirectResponse(f"/login?next={quote(next_url, safe='')}",
                                status_code=302)
    return _consent_page(request, db, client, redirect_uri, scope, state,
                         challenge, p)


@router.post("/oauth/authorize")
async def oauth_authorize_submit(request: Request,
                                 user: Optional[User] = Depends(require_login),
                                 db: Session = Depends(get_db)):
    _require_enabled()
    form = await request.form()
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if not csrf_ok(request, form.get("csrf_token") or ""):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    p = {k: v for k, v in form.items()}
    try:
        client, redirect_uri, scope, state, challenge = _validate_authorize(
            db, p, role=user.role)
    except oauth.OAuthError as exc:
        return _error_response(exc.error, exc.status)
    if (form.get("decision") or "") == "deny":
        # 拒绝 → error=access_denied（state 原样回传，规格 §五.6）
        return _redirect_with(redirect_uri, error="access_denied", state=state)
    code = oauth.create_code(db, client, user, redirect_uri, challenge, scope)
    return _redirect_with(redirect_uri, code=code, state=state)


def _redirect_with(redirect_uri: str, **params):
    sep = "&" if "?" in redirect_uri else "?"
    qs = "&".join(f"{k}={v}" for k, v in params.items() if v != "")
    return RedirectResponse(redirect_uri + sep + qs, status_code=302)


def _consent_page(request, db, client, redirect_uri, scope, state, challenge,
                  p):
    """授权确认页：展示客户端名 + 申请 scope（规格 §五.6）；带 CSRF。"""
    scope_display = "只读（read）" if scope == "read" else "读写（read, write）"
    return templates.TemplateResponse("oauth_consent.html", {
        "request": request, "current_user": getattr(request.state, "user", None),
        "csrf": getattr(request.state, "csrf", ""),
        "client": oauth.client_info(client),
        "scope": scope, "scope_display": scope_display,
        "redirect_uri": redirect_uri, "state": state,
        "challenge": challenge, "response_type": p.get("response_type", "code"),
        "client_id": client.client_id,
    })


# ---------- token ----------

async def _token_payload(request: Request) -> dict:
    body = await request.body()
    ctype = (request.headers.get("content-type") or "").lower()
    if "application/json" in ctype:
        try:
            data = json.loads(body.decode("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:  # noqa: BLE001
            return {}
    try:
        return {k: v[0] for k, v in parse_qs(body.decode("utf-8")).items()}
    except Exception:  # noqa: BLE001
        return {}


@router.post("/oauth/token")
async def oauth_token(request: Request, db: Session = Depends(get_db)):
    _require_enabled()
    p = await _token_payload(request)
    grant_type = p.get("grant_type", "")
    try:
        if grant_type == "authorization_code":
            at, rt, scope = oauth.exchange_code(
                db, code=p.get("code", ""), client_id=p.get("client_id", ""),
                redirect_uri=p.get("redirect_uri", ""),
                code_verifier=p.get("code_verifier", ""))
        elif grant_type == "refresh_token":
            at, rt, scope = oauth.exchange_refresh(
                db, refresh_token=p.get("refresh_token", ""),
                client_id=p.get("client_id", ""))
        else:
            return _error_response("unsupported_grant_type")
    except oauth.OAuthError as exc:
        return JSONResponse({"error": exc.error}, status_code=exc.status)
    hours = get_settings().visit_oauth_access_hours
    return {
        "access_token": at,
        "token_type": "Bearer",
        "expires_in": int(hours * 3600),
        "refresh_token": rt,
        "scope": scope,
    }
