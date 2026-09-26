# -*- coding: utf-8 -*-
"""MCP Token 解析 → Actor。spec §5。

用 SHA-256 摘要 + 明文前缀索引：token 是 256 位高熵随机串（`secrets.token_urlsafe(32)`），
无字典攻击风险；逐行 argon2 校验是廉价 DoS 向量（v3 §6.1）。

身份认证（specs-mcp-identity.md）：
- 校验链：存在 ∧ `revoked_at IS NULL` ∧ (`expires_at` 为空或未过期) ∧
  绑定用户 `is_active` ∧ 用户 `status == "active"`。
- 失效原因可区分（revoked / expired / user_inactive / user_status:<leave|disabled|resigned>），
  对外统一 UNAUTHORIZED + hint 带原因；原因同时留给审计。
- 签发默认 90 天（days=90）；`days=None` = 永久（管理员可签）。
"""
import hashlib
import hmac
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta

from app.models import ApiToken, User


@dataclass(frozen=True)
class Actor:
    """鉴权产物。`token_id` 供 preview 归属校验（闸门 6）；
    `person_code` 为绑定用户的人员编号（“我的”系列工具强制过滤依据）。"""
    uid: int | None
    role: str
    scopes: list[str]
    token_id: int | None
    person_code: str | None = None

    def can_write(self) -> bool:
        return self.role == "admin" and "write" in self.scopes


def _digest(raw: str) -> str:
    return hashlib.sha256(raw.encode()).hexdigest()


def static_tokens() -> dict[str, list[str]]:
    """测试期固定 Token（不依赖数据库，重启不变）。

    由 env 提供：`VISIT_MCP_TOKEN`（read,write）/ `VISIT_MCP_READ_TOKEN`（read）。
    `VISIT_MCP_STATIC_TOKENS=0` 可整体关闭（生产应关闭，改用 api_tokens）。
    本地测试环境默认开启（spec：静态 token 本地继续可用）。
    """
    import os
    if os.environ.get("VISIT_MCP_STATIC_TOKENS", "1") == "0":
        return {}
    out: dict[str, list[str]] = {}
    rw = (os.environ.get("VISIT_MCP_TOKEN") or "").strip()
    ro = (os.environ.get("VISIT_MCP_READ_TOKEN") or "").strip()
    if rw:
        out[rw] = ["read", "write"]
    if ro:
        out[ro] = ["read"]
    return out


def _user_actor(admin: User | None, scopes: list[str]) -> Actor:
    return Actor(uid=admin.id if admin is not None else None, role="admin",
                 scopes=scopes, token_id=None,
                 person_code=admin.person_code if admin is not None else None)


def resolve_with_reason(db, raw: str,
                       bootstrap_token: str | None = None) -> tuple:
    """解析 Bearer 明文 → (Actor | None, reason | None)。

    reason 用于区分失效原因（revoked / expired / user_inactive /
    user_status:<leave|disabled|resigned>）；None 表示不可识别/无身份。
    静态 token（本地测试）恒定有效，不随用户状态变化。
    """
    if not raw:
        return None, None

    # 测试期固定 Token 优先（恒定有效，便于联调）
    for tok, scopes in static_tokens().items():
        if hmac.compare_digest(raw, tok):
            admin = db.query(User).filter(User.role == "admin").first()
            return _user_actor(admin, scopes), None

    row = db.query(ApiToken).filter(ApiToken.token_prefix == raw[:8]).first()
    if row is not None:
        if row.revoked_at is not None:
            return None, "revoked"
        if not hmac.compare_digest(row.token_hash, _digest(raw)):
            return None, None
        if row.expires_at is not None and row.expires_at < datetime.utcnow():
            return None, "expired"
        user = db.get(User, row.user_id)
        if user is None:
            return None, "user_inactive"
        # 状态优先于 is_active（leave 也是 is_active=True，需给出 user_status:leave）
        if user.status != "active":
            return None, f"user_status:{user.status}"
        if not user.is_active:
            return None, "user_inactive"
        try:                      # 只读部署下记账失败不应影响鉴权（读工具仍可用）
            row.last_used_at = datetime.utcnow()
            db.commit()
        except Exception:         # noqa: BLE001
            db.rollback()
        scopes = [s.strip() for s in (row.scopes or "read").split(",") if s.strip()]
        return Actor(uid=user.id, role=user.role, scopes=scopes,
                     token_id=row.id, person_code=user.person_code), None

    if bootstrap_token and hmac.compare_digest(raw, bootstrap_token):
        has_real = db.query(ApiToken).filter(ApiToken.revoked_at.is_(None)).count() > 0
        if not has_real:
            admin = db.query(User).filter(User.role == "admin").first()
            return _user_actor(admin, ["read"]), None
    return None, None


def resolve(db, raw: str, bootstrap_token: str | None = None) -> Actor | None:
    """解析 Bearer 明文 → Actor；无法识别返回 None（调用方回 401）。

    bootstrap：仅当表内无任何未撤销 Token 时生效，且**只给 read scope**——
    防止未迁移的环境变量 Token 在写能力开通后直接获得写权限（spec §5.3）。
    """
    actor, _reason = resolve_with_reason(db, raw, bootstrap_token=bootstrap_token)
    return actor


def issue(db, user_id: int, name: str, scopes: str = "read",
          days: int | None = None) -> tuple[str, ApiToken]:
    """签发 Token。返回 (明文, 行)；明文只显示一次，库里只存摘要。

    `days`：正整数=有效天数（默认签发建议 90 天）；`None`=永久（expires_at=NULL）。
    """
    if scopes not in ("read", "read,write"):
        raise ValueError("scopes 只能是 read 或 read,write")
    raw = secrets.token_urlsafe(32)
    expires_at = None
    if days is not None:
        expires_at = datetime.utcnow() + timedelta(days=int(days))
    row = ApiToken(user_id=user_id, name=(name or "").strip()[:128],
                   token_prefix=raw[:8], token_hash=_digest(raw), scopes=scopes,
                   expires_at=expires_at)
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
