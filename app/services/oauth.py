# -*- coding: utf-8 -*-
"""MCP OAuth（SSO）服务层（规格：docs/specs-mcp-oauth.md）。

设计要点：换出来的 access token **就是 `api_tokens` 里的一行** →
Actor/授权矩阵/两阶段审计/员工状态联动全部自动继承，授权模型零改动。

本模块只做 OAuth 专属工作：
- 动态客户端注册（RFC 7591，公共客户端：PKCE 强制、不发放 secret）
- 授权码换 token：PKCE(S256) 校验 → 复用 `mcp_tokens.issue` 签 api_tokens 行
- refresh token 轮换（旧 refresh 立即置 revoked_at）
- scope 上限：员工申请 write → 降级 read（不报错，最小权限）

安全（规格 §五）：
- 库内只存 sha256 摘要，明文只在响应中出现一次；
- code 一次性 + 重放吊销已换出的 token（oauth_codes.access_token_id 关联）；
- redirect_uri 精确匹配；code 5 分钟；access 24h；refresh 90d（均可配）。
"""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse

from app.config import get_settings
from app.models import ApiToken, OAuthClient, OAuthCode, OAuthRefreshToken, User


class OAuthError(Exception):
    """OAuth 协议错误：error 字段（RFC 6749 §5.2），默认 HTTP 400。"""

    def __init__(self, error: str, status: int = 400, description: str = ""):
        super().__init__(description or error)
        self.error = error
        self.status = status
        self.description = description


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _now() -> datetime:
    return datetime.utcnow()


# ---------- 配置 ----------

def resource() -> str:
    """**受保护资源的标识**（MCP 服务地址，RFC 9728 的 resource）。

    必须与客户端连接的 MCP URL **同源**（WorkBuddy 的 SDK 用 origin 精确比对：
    端口不同即拒绝 → 表现为"点连接没反应"）。故不能复用 issuer（issuer 是 web 的
    授权服务器地址）。缺省回退 issuer。
    """
    import os
    s = get_settings()
    val = (getattr(s, "visit_oauth_resource", "") or "").strip()
    if not val:
        val = (os.environ.get("VISIT_OAUTH_RESOURCE") or "").strip()
    return (val.rstrip("/") if val else issuer())


def issuer() -> str:
    """对外 issuer：`VISIT_OAUTH_ISSUER`，缺省取 `VISIT_MCP_PUBLIC_HOST` 的 https 地址。"""
    s = get_settings()
    if (s.visit_oauth_issuer or "").strip():
        return s.visit_oauth_issuer.strip().rstrip("/")
    import os
    host = (os.environ.get("VISIT_MCP_PUBLIC_HOST") or "").strip()
    return f"https://{host}" if host else "http://localhost"


def access_ttl() -> timedelta:
    return timedelta(hours=get_settings().visit_oauth_access_hours)


def refresh_ttl() -> timedelta:
    return timedelta(days=get_settings().visit_oauth_refresh_days)


CODE_TTL = timedelta(minutes=5)


# ---------- PKCE ----------

def pkce_verify(verifier: str, challenge: str, method: str) -> bool:
    """PKCE 校验：**强制 S256**（规格 §五.1），verifier 长度 43–128。"""
    if method != "S256":
        return False
    if not verifier or not (43 <= len(verifier) <= 128):
        return False
    expected = _b64url(hashlib.sha256(verifier.encode()).digest())
    return hmac.compare_digest(expected, challenge or "")


# ---------- 客户端（DCR） ----------

def valid_redirect_uri(u: str) -> bool:
    """redirect_uri 合法性：带 scheme；http/https 必须有 host。"""
    if not isinstance(u, str) or not u.strip() or len(u) > 512:
        return False
    p = urlparse(u)
    if not p.scheme:
        return False
    if p.scheme in ("http", "https"):
        return bool(p.netloc)
    # 自定义 scheme（如 workbuddy://）允许
    return True


def register_client(db, client_name: str, redirect_uris) -> OAuthClient:
    """动态客户端注册（RFC 7591）。公共客户端：不发放 client_secret（PKCE 已强制）。

    返回新客户端行；`client_id` 高熵随机、唯一。
    """
    uris = []
    if isinstance(redirect_uris, (list, tuple)):
        uris = [u for u in redirect_uris if isinstance(u, str) and u.strip()]
    if not uris:
        raise OAuthError("invalid_client_metadata", 400,
                         "redirect_uris 必须提供至少一个 URI")
    for u in uris:
        if not valid_redirect_uri(u):
            raise OAuthError("invalid_client_metadata", 400,
                             f"redirect_uri 不合法：{u}")
    client_id = secrets.token_urlsafe(16)
    name = (client_name or "").strip()[:128]
    row = OAuthClient(client_id=client_id, client_name=name,
                      redirect_uris=uris)
    db.add(row)
    db.commit()
    return row


def find_client(db, client_id: str):
    if not client_id:
        return None
    return db.query(OAuthClient).filter(
        OAuthClient.client_id == client_id).first()


def redirect_ok(client: OAuthClient, redirect_uri: str) -> bool:
    """redirect_uri 必须与注册时**完全一致**（不做前缀/通配，规格 §五.3）。"""
    return redirect_uri in (client.redirect_uris or [])


# ---------- scope 上限 ----------

def scope_for_user(role: str, requested: str) -> str:
    """按角色给最小权限：员工申请 write → 降级 read（不报错）；管理员按请求。"""
    want = {s.strip() for s in (requested or "read").replace(" ", ",")
            .split(",") if s.strip()} & {"read", "write"}
    if "write" in want and role != "admin":
        want.discard("write")
    if not want:
        want = {"read"}
    return "read" if want == {"read"} else "read,write"


# ---------- 授权码 ----------

def create_code(db, client: OAuthClient, user: User, redirect_uri: str,
                code_challenge: str, scope: str) -> str:
    """签发授权码（5 分钟有效）。返回明文；库内只存 sha256。"""
    raw = secrets.token_urlsafe(32)
    now = _now()
    row = OAuthCode(code_hash=_digest(raw), client_id=client.id,
                    user_id=user.id, redirect_uri=redirect_uri,
                    code_challenge=code_challenge,
                    code_challenge_method="S256", scope=scope,
                    expires_at=now + CODE_TTL, created_at=now)
    db.add(row)
    db.commit()
    return raw


def consume_code(db, code_raw: str, client: OAuthClient,
                 redirect_uri: str, verifier: str):
    """校验并消费授权码 → (user, scope)。

    失败一律 `invalid_grant`；**重放**（used_at 已有）时吊销该 code 换出的
    access token（规格 §五.2）。
    """
    row = db.query(OAuthCode).filter(
        OAuthCode.code_hash == _digest(code_raw)).first()
    if row is None or row.client_id != client.id:
        raise OAuthError("invalid_grant", 400, "authorization code 无效")
    if row.redirect_uri != redirect_uri:
        raise OAuthError("invalid_grant", 400, "redirect_uri 与授权时不一致")
    now = _now()
    if row.used_at is not None:
        # 重放：吊销第一次换出的 token
        if row.access_token_id is not None:
            tok = db.get(ApiToken, row.access_token_id)
            if tok is not None and tok.revoked_at is None:
                tok.revoked_at = now
                db.commit()
        raise OAuthError("invalid_grant", 400, "authorization code 已使用（重放）")
    if row.expires_at < now:
        raise OAuthError("invalid_grant", 400, "authorization code 已过期")
    user = db.get(User, row.user_id)
    if user is None or user.status != "active" or not user.is_active:
        raise OAuthError("invalid_grant", 400, "账号不可用")
    if not pkce_verify(verifier, row.code_challenge, row.code_challenge_method):
        raise OAuthError("invalid_grant", 400, "PKCE 校验失败（verifier 不匹配）")
    row.used_at = now
    db.commit()
    return user, row.scope


# ---------- token 签发 ----------

def issue_access_token(db, user: User, scope: str,
                       name: str = "OAuth") -> tuple:
    """签 access token（= api_tokens 一行，24 小时）。返回 (明文, 行)。"""
    from app.services import mcp_tokens
    return mcp_tokens.issue(db, user.id, name, scope,
                            days=get_settings().visit_oauth_access_hours / 24)


def issue_refresh(db, client: OAuthClient, user: User, scope: str) -> str:
    """签 refresh token（90 天，轮换制）。返回明文；库内只存 sha256。"""
    raw = secrets.token_urlsafe(48)
    row = OAuthRefreshToken(
        token_hash=_digest(raw), client_id=client.id, user_id=user.id,
        scope=scope, expires_at=_now() + refresh_ttl())
    db.add(row)
    db.commit()
    return raw


def exchange_code(db, *, code: str, client_id: str, redirect_uri: str,
                  code_verifier: str) -> tuple:
    """authorization_code 换 token → (access_raw, refresh_raw, scope)。

    成功后把换出的 access token 行 id 记回 code（供重放吊销）。
    """
    client = find_client(db, client_id)
    if client is None:
        raise OAuthError("invalid_client", 400, "client_id 未注册")
    user, scope = consume_code(db, code, client, redirect_uri, code_verifier)
    at_raw, at_row = issue_access_token(db, user, scope)
    row = db.query(OAuthCode).filter(
        OAuthCode.code_hash == _digest(code)).first()
    if row is not None:
        row.access_token_id = at_row.id
    client.last_used_at = _now()
    db.commit()
    rt_raw = issue_refresh(db, client, user, scope)
    return at_raw, rt_raw, scope


def exchange_refresh(db, *, refresh_token: str, client_id: str) -> tuple:
    """refresh_token 换新 token（**轮换**：旧 refresh 置 revoked_at）。

    返回 (access_raw, refresh_raw, scope)。旧 refresh 再用 → invalid_grant。
    """
    client = find_client(db, client_id)
    if client is None:
        raise OAuthError("invalid_client", 400, "client_id 未注册")
    row = db.query(OAuthRefreshToken).filter(
        OAuthRefreshToken.token_hash == _digest(refresh_token)).first()
    now = _now()
    if row is None or row.client_id != client.id:
        raise OAuthError("invalid_grant", 400, "refresh token 无效")
    if row.revoked_at is not None or row.expires_at < now:
        raise OAuthError("invalid_grant", 400, "refresh token 已失效（轮换后旧 token 不可再用）")
    user = db.get(User, row.user_id)
    if user is None or user.status != "active" or not user.is_active:
        raise OAuthError("invalid_grant", 400, "账号不可用")
    scope = row.scope
    # 轮换：旧 refresh 立即失效
    row.revoked_at = now
    row.last_used_at = now
    client.last_used_at = now
    db.commit()
    at_raw, _ = issue_access_token(db, user, scope, name="OAuth refresh")
    rt_raw = issue_refresh(db, client, user, scope)
    return at_raw, rt_raw, scope


def client_info(client: OAuthClient) -> dict:
    """客户端信息（授权确认页展示用）。"""
    return {"client_id": client.client_id, "client_name": client.client_name,
            "redirect_uris": list(client.redirect_uris or [])}
