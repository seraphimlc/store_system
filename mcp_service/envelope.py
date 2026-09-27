# -*- coding: utf-8 -*-
"""统一返回信封：`{ok:true,data}` / `{ok:false,error:{code,message,retryable,hint}}`。

**为什么必须有 `retryable`**：模型需要可编程判断"这个失败能不能重试"。
尤其 `INTERNAL_WRITE`（写操作可能已部分生效）**必须**标 False，
否则模型自动重试会造成重复写入或半成品数据。
"""
from typing import Any

# 错误码 → 是否可安全自动重试
RETRYABLE: dict[str, bool] = {
    # 读侧临时故障：可重试
    "INTERNAL": True,
    # 写操作可能已部分生效：禁止自动重试（最重要的一条）
    "INTERNAL_WRITE": False,
    # 需要人/文件层面修正：重试无用
    "PARSE_FAILED": False,
    "DUPLICATE_FILE": False,
    "TEMPLATE_SHEET_MISSING": False,
    "MONTH_SEALED": False,
    "MONTH_CLOSED": False,
    "PENDING_APPEALS": False,
    "NO_SOURCE_ROWS": False,
    "CONFIRM_REQUIRED": False,
    "PREVIEW_REQUIRED": False,
    "BAD_REQUEST": False,
    "BAD_MONTH": False,
    "BAD_PARAM": False,
    "NOT_FOUND": False,
    "FORBIDDEN_TOOL": False,
    "UNAUTHORIZED": False,
    "AUTH_FAILED": False,
    # 需要用户选择（不是失败，是提问）
    "NEED_FILE_KIND": False,
    "MANUAL_SETTLEMENT_FILE": False,
    "UNKNOWN_FILE": False,
}

# 兼容旧码名（同一含义的历史写法）
_ALIAS = {"MONTH_SEALED": "MONTH_CLOSED"}


def is_retryable(code: str) -> bool:
    return RETRYABLE.get(code, False)


def error(code: str, message: str, hint: str = "",
          retryable: bool | None = None, **extra: Any) -> dict[str, Any]:
    """构造失败信封。`retryable` 不传则按码表判定。"""
    body: dict[str, Any] = {
        "code": code,
        "message": message,
        "retryable": is_retryable(code) if retryable is None else bool(retryable),
        "hint": hint,
    }
    body.update(extra)          # 如 NEED_FILE_KIND 的 options / seen
    return {"ok": False, "error": body}


def ok(data: Any) -> dict[str, Any]:
    return {"ok": True, "data": data}
