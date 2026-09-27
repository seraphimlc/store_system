# -*- coding: utf-8 -*-
"""MCP Token 服务适配层：**只调用** `mcp_service/tokens.py` 的
`issue` / `revoke` / `list_for_user`，不改动该模块（安全核心由另一批次负责）。

本机主 venv 为 Python 3.9.6（`mcp_service/tokens.py` 使用 PEP 604 语法 `int | None`，
Python ≥3.10 才可导入；且生产 web 容器不打包 `mcp_service/`），因此这里 try/except 兼容：
- 能导入（解释器 ≥3.10 且 mcp_service 随代码存在）→ 直接调用原函数；
- 否则回退到下方**等价实现**（同一 ORM 模型、同一语义：sha256 摘要 + 明文前缀索引 +
  归属校验），保证 web 侧行为一致。

`issue` 的 `days` 参数同样兼容：新签名支持 `days=` 直接传；旧签名抛 TypeError 时
签发后自行补写 `expires_at`。
"""
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional

from app.models import ApiToken

try:
    from mcp_service.tokens import issue as _issue_real          # noqa: F401
    from mcp_service.tokens import revoke as _revoke_real        # noqa: F401
    from mcp_service.tokens import list_for_user as _list_real   # noqa: F401
    _REAL = True
except Exception:  # noqa: BLE001 — py3.9 语法不兼容 / 生产容器无 mcp_service
    _REAL = False


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def _issue_fallback(db, user_id: int, name: str, scopes: str,
                    days: Optional[int] = None):
    if scopes not in ("read", "read,write"):
        raise ValueError("scopes 只能是 read 或 read,write")
    raw = secrets.token_urlsafe(32)
    row = ApiToken(user_id=user_id, name=(name or "").strip()[:128],
                   token_prefix=raw[:8], token_hash=_digest(raw), scopes=scopes)
    if days:
        row.expires_at = datetime.utcnow() + timedelta(days=days)
    db.add(row)
    db.commit()
    return raw, row


def _revoke_fallback(db, token_id: int, user_id: int) -> bool:
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.utcnow()
    db.commit()
    return True


def _list_fallback(db, user_id: int):
    return (db.query(ApiToken).filter(ApiToken.user_id == user_id)
            .order_by(ApiToken.id.desc()).all())


def _apply_expiry(db, row, days: Optional[int]):
    """若真实现未写有效期（旧签名/未实现 days），签发后自行补写。"""
    if days and getattr(row, "expires_at", None) is None:
        row.expires_at = datetime.utcnow() + timedelta(days=days)
        db.commit()


def issue(db, user_id: int, name: str, scopes: str = "read",
          days: Optional[int] = None):
    """签发 Token → (明文, 行)。明文只返回这一次，库内只存 sha256 + 前缀。

    `days`：90 → 90 天；None → 永久（仅管理员可签，页面层已限制）。
    """
    if _REAL:
        try:
            raw, row = _issue_real(db, user_id, name, scopes, days=days)
        except TypeError:
            # 旧签名无 days 参数
            raw, row = _issue_real(db, user_id, name, scopes)
        _apply_expiry(db, row, days)
        return raw, row
    return _issue_fallback(db, user_id, name, scopes, days)


def revoke(db, token_id: int, user_id: int) -> bool:
    """吊销（归属校验）：只能吊销自己的 Token；已吊销返回 False。"""
    if _REAL:
        return _revoke_real(db, token_id, user_id)
    return _revoke_fallback(db, token_id, user_id)


def list_for_user(db, user_id: int):
    """某用户的全部 Token（新在前）。"""
    if _REAL:
        return _list_real(db, user_id)
    return _list_fallback(db, user_id)


def token_status(row, user) -> str:
    """Token 对绑定用户的有效性：active / revoked / expired / user_inactive。

    规则（spec §三）：存在 ∧ 未吊销 ∧ (未过期 ∨ 永久) ∧ 绑定用户
    `status == 'active'` 且 `is_active`，才有效。
    """
    if row.revoked_at is not None:
        return "revoked"
    if row.expires_at is not None and row.expires_at < datetime.utcnow():
        return "expired"
    if user is None or user.status != "active" or not user.is_active:
        return "user_inactive"
    return "active"


# 页面展示：状态 → 文案 / 徽标样式
TOKEN_STATUS_LABELS = {"active": "有效", "revoked": "已吊销",
                       "expired": "已过期", "user_inactive": "随员工状态失效"}
TOKEN_STATUS_PILL = {"active": "pill ok", "revoked": "pill err",
                     "expired": "pill warn", "user_inactive": "pill run"}


def decorate(row, user) -> dict:
    """页面用包装：row + status + label + pill。"""
    st = token_status(row, user)
    return {"row": row, "status": st,
            "label": TOKEN_STATUS_LABELS[st], "pill": TOKEN_STATUS_PILL[st]}
