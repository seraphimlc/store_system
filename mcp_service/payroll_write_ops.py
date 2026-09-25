# -*- coding: utf-8 -*-
"""薪资写操作：对账解读 / 找平执行取消 / 薪资找平表生成 / 人工修正 / 系统配置。

职责：只做「取 Context → 过闸门 → 调服务层 → 包错误信封」，
闸门在 guards.py（不重复实现），业务复用 app.services.*（不重复实现）。
每个工具用 mcp_service.tools._write_call 做统一包装：
  retryable=True  → 意外异常 INTERNAL（可重试；工具本身幂等）
  retryable=False → INTERNAL_WRITE（**禁止自动重试**；payroll_update 的
    adjust_delta 是增量累加，重试会重复加钱——**涉钱且非幂等**）

接线由父会话负责（server.py 里调 payroll_write_ops.register(mcp)）。
"""
from typing import Any

from mcp.server.mcpserver import Context, MCPServer

from mcp_service import guards


def _to_int(value, name: str, *, lo: int, hi: int) -> int:
    """把参数转整数并校验合理范围（非整数/越界 → BAD_PARAM）。"""
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise guards.GuardError(
            "BAD_PARAM", f"{name} 非法：{value!r}",
            f"{name} 必须是整数") from None
    if not (lo <= v <= hi):
        raise guards.GuardError(
            "BAD_PARAM", f"{name} 超出合理范围：{v}（应为 {lo}..{hi}）",
            f"{name} 应为 {lo}..{hi} 的整数")
    return v


def _to_int_any(value, name: str) -> int:
    """宽松整数转换（与路由 payroll_settle_update 同样允许任意符号）。"""
    try:
        return int(value)
    except (TypeError, ValueError):
        raise guards.GuardError(
            "BAD_PARAM", f"{name} 非法：{value!r}",
            f"{name} 必须是整数") from None


# ---------- 能力函数（每个工具一个；返回完整信封或抛 GuardError）----------

def recon_interpret(db, actor, *, task_id: int) -> dict[str, Any]:
    """visit_recon_interpret：AI 解读对账结果（写 task.summary.ai_interpret）。

    闸门：require_write；任务不存在 → NOT_FOUND。
    复用 app.services.recon.interpret_task（**可能耗时**：AI 调用最长约 600s）。
    重复解读安全（覆盖旧解读）→ retryable=True。
    """
    from app.models import ReconTask
    from app.services import recon

    guards.require_write(actor)
    t = db.get(ReconTask, task_id)
    if t is None:
        raise guards.GuardError(
            "NOT_FOUND", f"对账任务不存在：{task_id}",
            "先调用 visit_recon_status / visit_recon_diff 获取正确的 task_id")

    res = recon.interpret_task(db, task_id)   # 可能耗时（AI timeout 600s）
    return {"ok": True, "data": {
        "task_id": task_id,
        "month": (t.params or {}).get("month", ""),
        "generated": bool(res.get("ok")),
        "text": res.get("text", ""),
        "msg": res.get("msg", ""),
        "hint": "AI 解读可能耗时（最长约 10 分钟）；重复解读安全（覆盖旧解读）",
    }}


def recon_adjust(db, actor, *, task_id: int, person_code: str,
                 action: str, confirm_text: str = None) -> dict[str, Any]:
    """visit_recon_adjust：找平执行(add)/取消(remove)（**涉钱**）。

    闸门：require_write → action 校验（add/remove 否则 BAD_PARAM）→
    确认语 `确认找平 {month} {person_code} {action}`（month 取对账任务 params）。
    复用 app.services.recon.confirm_adjust / cancel_adjust（与路由 recon_adjust
    同一链路，不重写业务规则）。
    幂等（确认重复返回已有记录不重复加钱；取消无记录视为已取消）→ retryable=True。
    """
    from app.models import ReconTask
    from app.services import recon

    guards.require_write(actor)
    if action not in ("add", "remove"):
        raise guards.GuardError(
            "BAD_PARAM", f"找平动作非法：{action!r}",
            "action 必须是 add（确认找平）或 remove（取消找平）")
    t = db.get(ReconTask, task_id)
    if t is None:
        raise guards.GuardError(
            "NOT_FOUND", f"对账任务不存在：{task_id}",
            "先调用 visit_recon_status / visit_recon_diff 获取正确的 task_id")
    month = (t.params or {}).get("month", "")
    if not month:
        raise guards.GuardError(
            "BAD_PARAM", f"对账任务 {task_id} 缺少月份参数",
            "该任务未登记月份，无法确定确认语；请核对任务数据")
    guards.assert_confirm(confirm_text,
                          f"确认找平 {month} {person_code} {action}")

    if action == "add":
        res = recon.confirm_adjust(db, task_id, person_code, actor.uid)
        if not res.get("ok"):
            raise guards.GuardError(
                "NOT_FOUND", res.get("msg") or "无法确认找平",
                "该员工在此对账任务中没有差异行；"
                "先用 visit_recon_diff(task_id=...) 确认差异")
        rec = res["record"]
        return {"ok": True, "data": {
            "task_id": task_id,
            "month": month,
            "person_code": person_code,
            "action": "add",
            "points": rec.amount,        # 调整点数（对账−系统；正=补发/负=扣回）
            "amount": (rec.amount or 0) * (rec.per_point or 0),  # 按锁存单价折算円
            "adjust_amount_written": rec.amount_adj,   # 写入薪资找平表的金额增量(円,含奖金)
            "currency": "JPY",
            "per_point": rec.per_point,
            "applied_to_month": rec.applied_to_month,
            "affected_months": [month, rec.applied_to_month],
            "idempotent": bool(res.get("msg") == "已确认过（幂等）"),
            "note": "找平已确认，下月发薪按锁存单价加减（可再调 remove 取消）",
        }}

    # action == "remove"
    rec = recon.task_adjust_map(db, task_id).get(person_code)
    if rec is None:
        return {"ok": True, "data": {
            "task_id": task_id,
            "month": month,
            "person_code": person_code,
            "action": "remove",
            "points": 0,
            "amount": 0,
            "currency": "JPY",
            "applied_to_month": "",
            "affected_months": [month],
            "note": "没有可取消的找平记录（幂等）",
        }}
    points, amount_jpy = rec.amount, (rec.amount or 0) * (rec.per_point or 0)
    recon.cancel_adjust(db, task_id, person_code)
    return {"ok": True, "data": {
        "task_id": task_id,
        "month": month,
        "person_code": person_code,
        "action": "remove",
        "points": points,
        "amount": amount_jpy,
        "currency": "JPY",
        "per_point": rec.per_point,
        "applied_to_month": rec.applied_to_month,
        "affected_months": [month, rec.applied_to_month],
        "note": "已取消找平确认（同步撤销写入薪资找平表的金额增量）",
    }}


def payroll_generate(db, actor, *, month: str) -> dict[str, Any]:
    """visit_payroll_generate：生成/更新该月薪资找平表（**涉钱**）。

    闸门：require_write → validate_month → assert_not_sealed →
    ensure_config_warmed（防用错单价，冷缓存会退回 env 默认）。
    复用 period.sync_period_table + dashboard.sync_dash_metrics；
    两者 best-effort：失败只进 data.warnings，**不返回错误**。
    幂等（重新生成保留手改偏差）→ retryable=True。
    """
    from app.services import dashboard, period

    guards.require_write(actor)
    month = guards.validate_month(month)
    guards.assert_not_sealed(db, [month])
    guards.ensure_config_warmed(db, month)

    data = {"month": month, "rows": 0, "currency": "JPY", "warnings": []}
    res = None
    try:
        res = period.sync_period_table(db, month)
    except Exception as exc:  # noqa: BLE001  best-effort
        db.rollback()
        data["warnings"].append(f"找平表生成未完成：{exc!r}")
    if res is not None:
        data["rows"] = int(res.get("rows", 0) or 0)
    try:
        dashboard.sync_dash_metrics(db, month)
    except Exception as exc:  # noqa: BLE001  best-effort
        db.rollback()
        data["warnings"].append(f"看板统计同步未完成：{exc!r}")
    if data["warnings"]:
        data["warnings"].append("请用只读工具核对当前状态")
    return {"ok": True, "data": data}


def payroll_update(db, actor, *, month: str, person_code: str,
                   half1_amount=None, half2_amount=None, adjust_delta=None,
                   confirm_text: str = None) -> dict[str, Any]:
    """visit_payroll_update：人工修正某人两期金额/找平增量（**涉钱**）。

    闸门：require_write → validate_month → assert_not_sealed →
    ensure_config_warmed → 确认语 `确认修正 {month} {person_code}`。
    复用 period.set_period_values（与路由 payroll_settle_update 同一逻辑，
    **不重写业务规则**）：half1/half2_amount 直接赋值、adjust_delta 增量累加。
    **非幂等**（adjust_delta 重试会重复加钱）→ retryable=False（INTERNAL_WRITE）。
    """
    from app.models import PayrollPeriodRow
    from app.services import period

    guards.require_write(actor)
    month = guards.validate_month(month)
    guards.assert_not_sealed(db, [month])
    guards.ensure_config_warmed(db, month)
    guards.assert_confirm(confirm_text, f"确认修正 {month} {person_code}")

    h1 = _to_int_any(half1_amount if half1_amount is not None else 0,
                     "half1_amount")
    h2 = _to_int_any(half2_amount if half2_amount is not None else 0,
                     "half2_amount")
    adj = _to_int_any(adjust_delta if adjust_delta is not None else 0,
                      "adjust_delta")
    res = period.set_period_values(db, month, person_code, h1, h2,
                                   user_id=actor.uid, adjust_delta=adj)
    if not res.get("ok"):
        raise guards.GuardError(
            "NOT_FOUND", res.get("msg") or "该月此员工无对账行",
            "先调用 visit_payroll_generate(month=...) 生成该月薪资找平表")

    row = (db.query(PayrollPeriodRow)
           .filter(PayrollPeriodRow.month == month,
                   PayrollPeriodRow.person_code == person_code).first())
    return {"ok": True, "data": {
        "month": month,
        "person_code": person_code,
        "half1_amount": row.half1_amount if row else h1,
        "half2_amount": row.half2_amount if row else h2,
        "adjust_amount": row.adjust_amount if row else 0,
        "currency": "JPY",
        "hint": "修正会影响发薪与递延（两期实发与下月结转）；"
                "adjust_delta 为增量累加，重试会重复加钱",
    }}


def config_set(db, actor, *, per_point=None, bonus_group=None,
               bonus_amount=None, confirm_text: str = None) -> dict[str, Any]:
    """visit_config_set：保存系统配置（**涉钱**：影响所有月份工资口径）。

    闸门：require_write → 参数校验（正整数，复用 guards.validate_per_point 思路；
    bonus 范围与路由 sys_config_save 一致）→ 确认语 `确认修改配置`。
    复用路由 sys_config_save 逻辑：写/更新 sys_configs（全局单值，最新一条生效）
    + perf.clear_config_cache()。参数留空 = 保留当前值。
    幂等（覆盖式写入）→ retryable=True。
    """
    from app.models import SysConfig
    from app.services import perf

    guards.require_write(actor)
    guards.assert_confirm(confirm_text, "确认修改配置")

    row = db.query(SysConfig).order_by(SysConfig.id.desc()).first()
    pp = (guards.validate_per_point(per_point)
          if per_point is not None else (row.per_point if row else 250))
    bg = _to_int(bonus_group if bonus_group is not None
                 else (row.bonus_group if row else 68),
                 "bonus_group", lo=1, hi=1000)
    ba = _to_int(bonus_amount if bonus_amount is not None
                 else (row.bonus_amount if row else 3000),
                 "bonus_amount", lo=0, hi=1000000)

    if row is None:
        db.add(SysConfig(config_month="", per_point=pp, bonus_group=bg,
                         bonus_amount=ba, updated_by=actor.uid))
    else:
        row.per_point, row.bonus_group, row.bonus_amount = pp, bg, ba
        row.updated_by = actor.uid
    db.commit()
    perf.clear_config_cache()
    return {"ok": True, "data": {
        "per_point": pp,
        "bonus_group": bg,
        "bonus_amount": ba,
        "currency": "JPY",
        "hint": "该配置为全局单值（最新一条生效），会影响所有月份工资口径；"
                "如需按月不同请用 env 的 BONUS_*_SCHEDULE",
    }}


# ---------- 注册 ----------

def _next_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    return f"{y + 1}-01" if m == 12 else f"{y}-{m + 1:02d}"


def payroll_mark_paid(db, actor, *, month: str, half, unmark: bool = False,
                      person_code: str = None) -> dict:
    """标记/取消「某月某期已实际发薪」（找平吸收额度只算未发薪的期）。

    为什么重要：上月对账差异要在本月两期工资里扣/补，但**已发薪的期改不了**。
    不标记的话，系统会以为结转已被吸收，实际漏扣（实测：9月上半月已发、下半月为0时，
    8月结转的 -59,000 被误判为已处理）。标记后重算，未吸收部分正确递延下月。
    """
    from app.services import period

    guards.require_write(actor)
    month = guards.validate_month(month)
    try:
        h = int(half)
    except (TypeError, ValueError):
        raise guards.GuardError("BAD_PARAM", f"half 非法：{half!r}",
                                "half=1 表示上半月，half=2 表示下半月") from None
    if h not in (1, 2):
        raise guards.GuardError("BAD_PARAM", f"half 只能是 1 或 2，收到 {h}",
                                "half=1 表示上半月，half=2 表示下半月")

    affected = period.mark_paid(db, month, h, marked_by=actor.uid,
                                unmark=unmark, person_code=person_code)
    period.sync_period_table(db, month)          # 标记后立即重算吸收
    nxt = _next_month(month)
    from app.models import PayrollPeriodRow
    if db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == nxt).first() is not None:
        period.sync_period_table(db, nxt)        # 下月结转随之变化
    db.commit()
    return {"ok": True, "data": {
        "month": month, "half": h,
        "action": "取消标记" if unmark else "标记已发薪",
        "affected_persons": affected,
        "paid_halves": sorted(period.paid_halves(db, month)),
        "note": "已重算本月及下月找平：吸收额度只使用未发薪的期",
    }}


def register(mcp: MCPServer) -> None:
    """父会话在 server.py 里调用（与 mcp_service.tools.register 并列）。"""
    from mcp_service.tools import _write_call

    @mcp.tool(
        name="visit_recon_interpret",
        description=(
            "用 AI 解读某对账任务的结果并写入任务 summary.ai_interpret（覆盖旧解读）。"
            "**可能耗时**：AI 调用最长约 10 分钟（timeout 600s），期间请勿重复并发调用；"
            "重复解读安全（幂等）。任务不存在返回 NOT_FOUND；"
            "未配置 AI 模型时 generated=false 并给出 msg（不是错误）。"
            "参数：task_id。需要写权限 Token。"
        ),
    )
    def visit_recon_interpret(ctx: Context, task_id: int) -> dict[str, Any]:
        def run(db, actor):
            return recon_interpret(db, actor, task_id=task_id)

        return _write_call(ctx, "visit_recon_interpret",
                           {"task_id": task_id}, run, retryable=True)

    @mcp.tool(
        name="visit_recon_adjust",
        description=(
            "对账差异找平（**涉钱**）：action=add 确认找平（记入下月工资，正=补发/负=扣回），"
            "action=remove 取消找平（同步撤销写入薪资找平表的金额增量）。"
            "confirm_text 必须原文：`确认找平 {month} {person_code} {action}`"
            "（month 为该对账任务的月份）。"
            "幂等可安全重试：重复 add 返回已有记录、不会重复加钱；remove 无记录视为已取消。"
            "任务不存在 NOT_FOUND；员工在该任务无差异行 NOT_FOUND。"
            "参数：task_id、person_code、action、confirm_text。需要写权限 Token。"
        ),
    )
    def visit_recon_adjust(ctx: Context, task_id: int, person_code: str,
                           action: str,
                           confirm_text: str = None) -> dict[str, Any]:
        def run(db, actor):
            return recon_adjust(db, actor, task_id=task_id,
                                person_code=person_code, action=action,
                                confirm_text=confirm_text)

        return _write_call(ctx, "visit_recon_adjust",
                           {"task_id": task_id, "person_code": person_code,
                            "action": action},
                           run, retryable=True)

    @mcp.tool(
        name="visit_payroll_generate",
        description=(
            "生成/更新某月的薪资找平表（**涉钱**）：同步该月分期对账偏差表，"
            "并刷新看板统计。封账月份拒绝（MONTH_SEALED）；生成前强制加载系统配置"
            "（防用错单价）。同步失败只进 data.warnings（不返回错误）。"
            "幂等可安全重试（重新生成保留手改偏差）。"
            "参数：month（YYYY-MM）。需要写权限 Token。"
        ),
    )
    def visit_payroll_generate(ctx: Context, month: str) -> dict[str, Any]:
        def run(db, actor):
            return payroll_generate(db, actor, month=month)

        return _write_call(ctx, "visit_payroll_generate",
                           {"month": month}, run, retryable=True)

    @mcp.tool(
        name="visit_payroll_update",
        description=(
            "人工修正某人某月的两期金额与找平增量（**涉钱**，会影响发薪与递延："
            "两期实发与下月结转）。confirm_text 必须原文：`确认修正 {month} {person_code}`。"
            "half1_amount/half2_amount 直接赋值，adjust_delta 为**增量累加**。"
            "**非幂等**：意外失败返回 INTERNAL_WRITE，**不要自动重试**，"
            "先用只读工具核对当前状态。该月该员工无找平行 NOT_FOUND。"
            "参数：month、person_code、half1_amount、half2_amount、adjust_delta、"
            "confirm_text。需要写权限 Token。"
        ),
    )
    def visit_payroll_update(ctx: Context, month: str, person_code: str,
                             half1_amount: int | None = None,
                             half2_amount: int | None = None,
                             adjust_delta: int | None = None,
                             confirm_text: str = None) -> dict[str, Any]:
        def run(db, actor):
            return payroll_update(db, actor, month=month,
                                  person_code=person_code,
                                  half1_amount=half1_amount,
                                  half2_amount=half2_amount,
                                  adjust_delta=adjust_delta,
                                  confirm_text=confirm_text)

        return _write_call(ctx, "visit_payroll_update",
                           {"month": month, "person_code": person_code,
                            "half1_amount": half1_amount,
                            "half2_amount": half2_amount,
                            "adjust_delta": adjust_delta},
                           run, retryable=False)

    @mcp.tool(
        name="visit_payroll_mark_paid",
        description=(
            "标记/取消「某结算月的某期（上半月/下半月）已实际发薪」。"
            "**发薪后请及时标记**：找平的「上月结转」只能从未发薪的期里扣/补，"
            "已发薪的期改不了；不标记会让系统误以为结转已处理而漏扣。"
            "标记后系统自动重算本月与下月的找平。**写入发放台账（含金额快照）**，"
            "台账是历史事实、不会被后续重算改写。参数：month（YYYY-MM）；"
            "half（1=上半月，2=下半月）；person_code（可选，留空=该期全部人，"
            "整期发薪用）；unmark（true=取消登记/更正）。需要写权限。"
        ),
    )
    def visit_payroll_mark_paid(ctx: Context, month: str, half: int,
                                unmark: bool = False,
                                person_code: str = None) -> dict[str, Any]:
        return _write_call(ctx, "visit_payroll_mark_paid",
                           {"month": month, "half": half, "unmark": unmark,
                            "person_code": person_code},
                           lambda db, actor: payroll_mark_paid(
                               db, actor, month=month, half=half, unmark=unmark,
                               person_code=person_code),
                           retryable=True)

    @mcp.tool(
        name="visit_config_set",
        description=(
            "保存系统配置（**涉钱**）：每点单价 per_point（円/点）、奖金门槛 "
            "bonus_group（点）、奖额 bonus_amount（円）。confirm_text 必须原文："
            "`确认修改配置`。**该配置为全局单值（最新一条生效），会影响所有月份工资口径；"
            "如需按月不同请用 env 的 BONUS_*_SCHEDULE**。参数留空表示保留当前值。"
            "幂等可安全重试（覆盖式写入）。需要写权限 Token。"
        ),
    )
    def visit_config_set(ctx: Context, per_point: int | None = None,
                         bonus_group: int | None = None,
                         bonus_amount: int | None = None,
                         confirm_text: str = None) -> dict[str, Any]:
        def run(db, actor):
            return config_set(db, actor, per_point=per_point,
                              bonus_group=bonus_group,
                              bonus_amount=bonus_amount,
                              confirm_text=confirm_text)

        return _write_call(ctx, "visit_config_set",
                           {"per_point": per_point, "bonus_group": bonus_group,
                            "bonus_amount": bonus_amount},
                           run, retryable=True)
