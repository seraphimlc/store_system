# -*- coding: utf-8 -*-
"""一次性提交令牌：发放 / 校验 / 清理（防重复提交的服务端机制）。

用法：
- 渲染：模板里每个 POST 表单放 `{{ form_token() }}`（同一请求复用同一个 token）
- 提交：路由级依赖 `Depends(require_form_token)` 校验并**立即作废**
- 豁免：未登录请求（交给各路由自己的守卫）、非表单请求、机器端点（/oauth/* 等）

语义：token 只能用一次；用过/过期/不属于本人 → 拒绝。
"""
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func

from app.models import FormToken

TTL_HOURS = 2          # 表单从渲染到提交的有效期
KEEP_HOURS = 6         # 清理：超过这个时间的记录直接删（含已用过的）


class FormTokenError(Exception):
    """令牌缺失/已用/过期。"""


def issue(db, user_id=None) -> str:
    """发放一个新令牌（渲染表单时调用）。顺手概率性清理旧记录。"""
    token = secrets.token_urlsafe(24)
    db.add(FormToken(token=token, user_id=user_id))
    db.commit()
    if secrets.randbelow(20) == 0:          # ~5% 概率触发清理，避免每次渲染都扫表
        try:
            purge(db)
        except Exception:  # noqa: BLE001  清理失败不影响发放
            db.rollback()
    return token


def consume(db, token: str, user_id=None) -> bool:
    """校验并作废；返回是否本次有效（**同一个 token 只会有一次 True**）。"""
    if not token:
        return False
    row = db.get(FormToken, token)
    if row is None or row.used_at is not None:
        return False
    if row.created_at and row.created_at < datetime.utcnow() - timedelta(hours=TTL_HOURS):
        return False
    if row.user_id is not None and user_id is not None and row.user_id != user_id:
        return False                        # 不属于本人 → 拒绝
    row.used_at = datetime.utcnow()
    db.commit()
    return True


def purge(db, keep_hours: int = KEEP_HOURS) -> int:
    """删除过期记录（含已用过的）。"""
    from app.db import SessionLocal
    own = db is None
    if own:
        db = SessionLocal()
    try:
        cut = datetime.utcnow() - timedelta(hours=keep_hours)
        n = db.query(FormToken).filter(FormToken.created_at < cut).delete(
            synchronize_session=False)
        db.commit()
        return n
    finally:
        if own:
            db.close()


def stats(db) -> dict:
    total = db.query(func.count(FormToken.token)).scalar() or 0
    used = db.query(func.count(FormToken.token)).filter(
        FormToken.used_at.isnot(None)).scalar() or 0
    return {"total": total, "used": used, "unused": total - used}
