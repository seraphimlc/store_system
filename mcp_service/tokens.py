# -*- coding: utf-8 -*-
"""MCP Token 解析 → Actor。spec §5。

用 SHA-256 摘要 + 明文前缀索引：token 是 256 位高熵随机串（`secrets.token_urlsafe(32)`），
无字典攻击风险；逐行 argon2 校验是廉价 DoS 向量（v3 §6.1）。
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime

from app.models import ApiToken, User


@dataclass(frozen=True)
class Actor:
    """鉴权产物。`token_id` 供 preview 归属校验（闸门 6）。"""
    uid: int | None
    role: str
    scopes: list[str]
    token_id: int | None

    def can_write(self) -> bool:
        return self.role == "admin" and "write" in self.scopes


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def resolve(db, raw: str, bootstrap_token: str | None = None) -> Actor | None:
    """解析 Bearer 明文 → Actor；无法识别返回 None（调用方回 401）。

    bootstrap：仅当表内无任何未撤销 Token 时生效，且**只给 read scope**——
    防止未迁移的环境变量 Token 在写能力开通后直接获得写权限（spec §5.3）。
    """
    if not raw:
        return None

    row = db.query(ApiToken).filter(ApiToken.token_prefix == raw[:8]).first()
    if row is not None:
        if row.revoked_at is not None:
            return None
        if not hmac.compare_digest(row.token_hash, _digest(raw)):
            return None
        user = db.get(User, row.user_id)
        if user is None or not getattr(user, "is_active", True):
            return None
        row.last_used_at = datetime.utcnow()
        db.commit()
        scopes = [s.strip() for s in (row.scopes or "read").split(",") if s.strip()]
        return Actor(uid=user.id, role=user.role, scopes=scopes, token_id=row.id)

    if bootstrap_token and hmac.compare_digest(raw, bootstrap_token):
        has_real = db.query(ApiToken).filter(ApiToken.revoked_at.is_(None)).count() > 0
        if not has_real:
            admin = db.query(User).filter(User.role == "admin").first()
            return Actor(uid=admin.id if admin else None, role="admin",
                         scopes=["read"], token_id=None)
    return None


def issue(db, user_id: int, name: str, scopes: str = "read") -> tuple[str, ApiToken]:
    """签发 Token。返回 (明文, 行)；明文只显示一次，库里只存摘要。"""
    if scopes not in ("read", "read,write"):
        raise ValueError("scopes 只能是 read 或 read,write")
    raw = secrets.token_urlsafe(32)
    row = ApiToken(user_id=user_id, name=(name or "").strip()[:128],
                   token_prefix=raw[:8], token_hash=_digest(raw), scopes=scopes)
    db.add(row)
    db.commit()
    return raw, row


def revoke(db, token_id: int, user_id: int) -> bool:
    """吊销自己的 Token（归属校验）。"""
    row = db.get(ApiToken, token_id)
    if row is None or row.user_id != user_id or row.revoked_at is not None:
        return False
    row.revoked_at = datetime.utcnow()
    db.commit()
    return True


def list_for_user(db, user_id: int) -> list[ApiToken]:
    return (db.query(ApiToken).filter(ApiToken.user_id == user_id)
            .order_by(ApiToken.id.desc()).all())
