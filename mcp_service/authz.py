# -*- coding: utf-8 -*-
"""中央授权矩阵 + 统一调用拦截（规格：docs/specs-mcp-identity.md 第二节/第五节）。

规则：
- **默认拒绝**：未登记的工具一律 `ADMIN_ONLY`（admin 全开；staff 一律 FORBIDDEN_TOOL）。
- `STAFF_ALLOWED`：员工可用的只读工具（visit_ping / visit_config_get /
  visit_product_doc / visit_my_perf / visit_my_daily / visit_my_settlement）。
- staff 调 admin 工具 → `FORBIDDEN_TOOL`（retryable=false，hint 说明「该操作仅管理员」），
  在进入业务前拦截 → **不产生任何副作用**。
- “我的”工具（visit_my_*）：无 person 参数入口；若客户端显式传入 person /
  person_code：与本人不符 → FORBIDDEN_TOOL（不是静默过滤）；相符/未传 →
  服务端强制按 `actor.person_code`（users.person_code）过滤，越权无从谈起。
- actor=None（stdio 本地模式 / 无凭据头）：读工具放行（保持现状）；
  写工具与导出工具 → UNAUTHORIZED（保持现状）。
- `dispatch()` 是统一调用入口：授权拦截 → 两阶段审计 → 执行 fn → 回填审计。

调用方约定：fn 负责业务异常 → 信封的映射（各模块既有映射不变）；
dispatch 只兜底调用方未捕获的意外异常（按 retryable 给 INTERNAL / INTERNAL_WRITE）。
"""
import time

from mcp_service import envelope

# 员工可用的只读工具（规格第二节）。未登记的工具默认 admin-only。
STAFF_ALLOWED = frozenset({
    "visit_ping", "visit_config_get", "visit_product_doc",
    "visit_my_perf", "visit_my_daily", "visit_my_settlement",
})

# “我的”系列工具：只能看本人（服务端强制过滤，不给越权留入口）
MY_TOOLS = frozenset({"visit_my_perf", "visit_my_daily", "visit_my_settlement"})

# “我的”工具里可能被客户端显式传入的 person 参数名（一律服务端校验/忽略）
_MY_PERSON_KEYS = ("person", "person_code", "person_id")

# 无身份（actor=None）时必须拒绝的工具：全部写工具 + 导出工具
# （读工具 stdio 放行是既有行为；写/导出工具历来要求身份）。
AUTH_REQUIRED_TOOLS = frozenset({
    # 写
    "visit_upload_file", "visit_finalize_file", "visit_rebuild_preview",
    "visit_rebuild_month", "visit_set_per_point", "visit_upload_recon",
    "visit_store_merge_pair", "visit_store_skip_pair",
    "visit_store_split_entity", "visit_store_apply_all",
    "visit_recon_interpret", "visit_recon_adjust", "visit_payroll_generate",
    "visit_payroll_update", "visit_payroll_mark_paid", "visit_config_set",
    "visit_staff_set_status", "visit_store_ai_run",
    # 导出（现有行为：无身份 → UNAUTHORIZED）
    "visit_export_salary", "visit_export_payroll_settle",
    "visit_export_recon_diff", "visit_export_recon_report",
    "visit_export_recon_result",
})


def require_role(tool: str) -> str:
    """工具所需角色：`STAFF_ALLOWED`（员工可用）或 `ADMIN_ONLY`（默认拒绝）。"""
    return "STAFF_ALLOWED" if tool in STAFF_ALLOWED else "ADMIN_ONLY"


def _forbidden(message: str, hint: str) -> dict:
    return envelope.error("FORBIDDEN_TOOL", message, hint, retryable=False)


def _unauthorized() -> dict:
    return envelope.error(
        "UNAUTHORIZED", "未认证",
        "请在 WorkBuddy 连接器设置中重新填写 Access Token", retryable=False)


def enforce(tool: str, actor, params: dict | None) -> dict | None:
    """授权检查：返回拒绝信封或 None（放行）。

    会原地修正 params：my 工具的显式 person 参数在相符时被规整掉
    （业务层一律按 actor.person_code 过滤，不信任客户端）。
    """
    if actor is None:
        if tool in AUTH_REQUIRED_TOOLS:
            return _unauthorized()
        return None

    # 任何角色调“我的”工具：显式 person 参数必须等于本人，否则拒绝
    if tool in MY_TOOLS:
        for k in _MY_PERSON_KEYS:
            v = (params or {}).get(k)
            if v is not None and str(v).strip():
                if not actor.person_code or str(v).strip() != actor.person_code:
                    return _forbidden(
                        "不能查询他人数据",
                        "“我的”系列工具只能查看本人数据（按账号绑定的人员编号过滤）；"
                        "如需他人数据请用管理员工具")
                params.pop(k, None)   # 相符 → 规整掉，业务只看 actor.person_code

    if actor.role == "admin":
        return None

    if actor.role == "staff":
        if tool in STAFF_ALLOWED:
            return None
        return _forbidden(
            "该操作仅限管理员",
            "该操作仅管理员，普通员工无权调用；查询个人绩效请用 visit_my_perf / "
            "visit_my_daily / visit_my_settlement")

    # 未知角色：保守拒绝
    return _forbidden("该操作仅限管理员", "未知角色，拒绝调用")


def dispatch(db, *, tool: str, actor, params: dict | None, client_info,
             fn, retryable: bool, fn_args: int) -> dict:
    """统一调用入口：授权拦截 → 两阶段审计 → 执行 fn → 回填审计。

    fn 签名：`fn(db)`（fn_args=1，读/导出）或 `fn(db, actor)`（fn_args=2，写）。
    被拒绝的调用也记一行审计（含 FORBIDDEN_TOOL / UNAUTHORIZED）。
    """
    from mcp_service import audit

    denied = enforce(tool, actor, params)
    if denied is not None:
        audit.record_denial(db, tool=tool, actor=actor, params=params,
                            denied=denied, client_info=client_info)
        return denied

    audit_id = audit.start(db, tool=tool, actor=actor, params=params,
                           client_info=client_info)
    t0 = time.monotonic()
    try:
        result = fn(db, actor) if fn_args == 2 else fn(db)
    except Exception as exc:      # noqa: BLE001  调用方未兜住的意外异常
        code = "INTERNAL" if retryable else "INTERNAL_WRITE"
        hint = ("系统内部错误，已记录；可重试" if retryable else
                "该操作可能已部分生效，**不要自动重试**；先用只读工具核对当前状态")
        result = envelope.error(code, repr(exc), hint)
    audit.finish(db, tool=tool, actor=actor, audit_id=audit_id,
                 result=result, client_info=client_info,
                 duration_ms=int((time.monotonic() - t0) * 1000))
    return result
