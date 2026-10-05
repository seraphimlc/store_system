# -*- coding: utf-8 -*-
"""闸门模块：写权限 / 封账 / 源守卫 / 确认语 / 参数 / preview / 配置就绪。

设计见 docs/specs-mcp-tools-scenario.md。
所有闸门**与会话无关**（不依赖进程内状态），因此单进程或多副本语义一致。
"""
import re
from datetime import datetime, timedelta

from sqlalchemy import func

from mcp_service.capability import MONTH_PATTERN

PREVIEW_WINDOW = timedelta(minutes=30)


class GuardError(RuntimeError):
    """带错误码的闸门拒绝：由工具层转成错误信封。"""

    def __init__(self, code: str, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.code, self.message, self.hint = code, message, hint


# ---------- 权限 ----------

def require_write(actor) -> None:
    """写工具入口：必须 admin 且 scope 含 write。"""
    if actor is None:
        raise GuardError("UNAUTHORIZED", "未认证",
                         "请在 WorkBuddy 连接器设置中重新填写 Access Token")
    if actor.role != "admin" or "write" not in actor.scopes:
        raise GuardError("FORBIDDEN_TOOL", "该操作仅限管理员且需要写权限 Token",
                         "当前 Token 无写权限；请让管理员签发 read,write 的 Token")


# ---------- 参数 ----------

def validate_month(month: str) -> str:
    if not re.fullmatch(MONTH_PATTERN, month or ""):
        raise GuardError("BAD_MONTH", f"月份格式非法：{month!r}，应为 YYYY-MM",
                         "月份必须是 YYYY-MM，例如 2026-09")
    return month


def validate_per_point(per_point) -> int:
    try:
        v = int(per_point)
    except (TypeError, ValueError):
        raise GuardError("BAD_PARAM", f"每点单价非法：{per_point!r}",
                         "每点单价必须是正整数") from None
    if v <= 0:
        raise GuardError("BAD_PARAM", f"每点单价必须为正整数，收到 {v}",
                         "每点单价必须是正整数")
    return v


def assert_confirm(text: str, expect: str) -> None:
    """确认语（**非安全边界**，仅降低误触：agent 能自己填对这句话）。"""
    if (text or "").strip() != expect:
        raise GuardError("CONFIRM_REQUIRED", "确认语不匹配",
                         f"请复述确认语：{expect}")


# ---------- 封账 ----------

def sealed_months(db, months: list[str]) -> list[str]:
    """返回命中封账的月份（支持集合，供 finalize 的多月份判定）。"""
    from app.models import SealedMonth
    if not months:
        return []
    rows = (db.query(SealedMonth.month)
            .filter(SealedMonth.month.in_(list(months))).all())
    return sorted(r[0] for r in rows)


def assert_not_sealed(db, months: list[str]) -> None:
    hit = sealed_months(db, months)
    if hit:
        raise GuardError(
            "MONTH_SEALED", f"命中封账月份：{', '.join(hit)}",
            f"{', '.join(hit)} 已封账不可写入；如需修正请走对账找平流程")


def is_sealed(db, month: str) -> bool:
    return bool(sealed_months(db, [month]))


# ---------- 源守卫 ----------

def assert_has_source(db, month: str) -> None:
    """该月 raw=0 而 formal>0 → 拒绝（重算会静默清空整月）。"""
    from app.models import FormalRecord, RawRecord
    raw_cnt = (db.query(func.count(RawRecord.id))
               .filter(RawRecord.modified_raw.like(f"{month}%")).scalar() or 0)
    formal_cnt = (db.query(func.count(FormalRecord.id))
                  .filter(FormalRecord.japan_date.like(f"{month}%")).scalar() or 0)
    if raw_cnt == 0 and formal_cnt > 0:
        raise GuardError(
            "NO_SOURCE_ROWS",
            f"该月无源记录(raw=0)但有正式表({formal_cnt})条，拒绝重算以免清空",
            "该月源记录已清理，重算会清空正式表，已拒绝。请让维护人员处理")


# ---------- 配置就绪（防写错钱）----------

def ensure_config_warmed(db, month: str | None = None) -> None:
    """涉钱操作前必须调用：冷缓存下奖金会退回 env 默认（如 68/3000），
    而 sys_configs 可能是按月配置 → 静默写错钱。"""
    from app.services import perf
    perf.warm_config(db, month)


# ---------- 预演前置 ----------

def _now() -> datetime:
    return datetime.utcnow()


def check_preview(rec, preview_id, token_id, month: str) -> None:
    """校验 preview 归属与时效（与会话无关）。"""
    if (rec is None
            or rec.id != preview_id
            or rec.tool != "visit_rebuild"
            or rec.month != month
            or rec.token_id != token_id
            or rec.created_at is None
            or _now() - rec.created_at > PREVIEW_WINDOW):
        raise GuardError(
            "PREVIEW_REQUIRED", "缺少有效的预演记录",
            f"先调用 visit_rebuild(action='preview', month='{month}')，"
            "把结果复述给用户后再用返回的 preview_id 重算（action='run'）")
