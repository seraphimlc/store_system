# -*- coding: utf-8 -*-
"""P1 写能力层：finalize_file / rebuild_preview / rebuild_month / set_per_point。

设计见 docs/specs-mcp-tools-scenario.md。
场景化重构（49→16）后本文件**不再注册工具**（注册统一在 scenario_ops.py）：
- rebuild 经 visit_rebuild(action='preview'|'run') 暴露；
- finalize 的能力（出正式表）已并入 visit_upload 的自动链路；
- set_per_point 的能力已并入 visit_config(action=set) 的 per_point 参数。
本文件只保留能力函数：f(db, actor, ...) → 完整信封或抛 GuardError。
业务复用 app.services.*（不重写判重/工资/找平/对账规则）。
"""
import json
from datetime import date as _date
from datetime import timedelta
from typing import Any

from mcp_service import guards


# ---------- 适配层 ----------

class _PreviewRec:
    """guards.check_preview 引用 `rec.month`，而 McpAuditLog 没有 month 列——
    月份存在审计行的 params_json 里。这里包一层：month 从 params_json 解析，
    其余字段委托给 McpAuditLog 行对象（spec §6.2 前置 1：同月校验）。
    """

    __slots__ = ("_row",)

    def __init__(self, row):
        self._row = row

    @property
    def month(self):
        try:
            return (json.loads(self._row.params_json or "{}") or {}).get("month")
        except Exception:  # noqa: BLE001
            return None

    def __getattr__(self, name):
        return getattr(self._row, name)


def _month_edges(month: str):
    """YYYY-MM → (该月首日, 下月首日)。"""
    y, m0 = int(month[:4]), int(month[5:7])
    if m0 == 12:
        return _date(y, m0, 1), _date(y + 1, 1, 1)
    return _date(y, m0, 1), _date(y, m0 + 1, 1)


def _sub_ids() -> set:
    """重算时排除的从档店精确名单（与 flow.rebuild_month 同一口径）。"""
    import os
    ids = set(os.environ.get(
        "DEDUP_SUB_STORES",
        "0101047092026031200555097|0202047092026032480084411|"
        "0101047092026081903348200|0101047092026060970019734").split("|"))
    if ids == {""}:
        return set()
    return ids


# ---------- 能力函数（每个工具一个；返回完整信封或抛 GuardError）----------

def finalize_file(db, actor, *, file_id: int) -> dict[str, Any]:
    """出正式表（原 visit_finalize_file；现为 visit_upload 自动链路的能力）。原子先删后插，幂等可安全重试。

    封账月份集合 = 该文件 raw 月份 ∪ 该文件 FormalRecord 月份
    （write_ops._file_months 现成实现；并集保证「raw 已清理、formal 保留」
    的封账月不漏判，spec §6.2）。文件粒度 pending 申诉 → PENDING_APPEALS。
    """
    from app.models import AppealRecord, ImportFile
    from app.services import flow
    from mcp_service.write_ops import _file_months

    guards.require_write(actor)

    if db.get(ImportFile, file_id) is None:
        raise guards.GuardError(
            "NOT_FOUND", f"文件不存在：{file_id}",
            "先调用 visit_files(view='list', month=...) 获取正确 id")

    pend = db.query(AppealRecord).filter(
        AppealRecord.import_id == file_id,
        AppealRecord.status == "pending").count()
    if pend:
        raise guards.GuardError(
            "PENDING_APPEALS",
            f"仍有 {pend} 条申诉未处理，处理后再入正式表",
            "存在未决申诉，申诉功能未开放，请联系维护人员处理")

    months = sorted(_file_months(db, file_id))
    guards.assert_not_sealed(db, months)

    res = flow.auto_finalize_pipeline(db, file_id, actor.uid)
    if not res.get("ok"):
        msg = res.get("msg") or ""
        if "申诉" in msg:
            raise guards.GuardError(
                "PENDING_APPEALS", msg,
                "存在未决申诉，申诉功能未开放，请联系维护人员处理")
        raise guards.GuardError("INTERNAL", msg,
                                "系统内部错误，已记录；可重试")

    return {"ok": True, "data": {
        "file_id": file_id,
        "affected_months": months,
        "formal_rows": res.get("added", 0),
        "hint": "stats/period/看板为 best-effort 各自 commit，"
                "若失败部分同步可能未完成，请用只读工具核对",
    }}


def rebuild_preview(db, actor, *, month: str) -> dict[str, Any]:
    """visit_rebuild(action='preview')：只读估算，**不调 rebuild_month**。

    一致性（构造上成立）：
      raw_total == Σ(raw_by_status 各桶含 other)；
      Σ(点数×count) == valid 行点数合计（点数口径与 finalize/rebuild 同源）。
    """
    from app.models import FormalRecord, ImportFile, McpAuditLog, RawRecord
    from app.services import flow

    guards.validate_month(month)
    guards.assert_not_sealed(db, [month])

    # —— 正式表现状（已结算口径，与 raw 估算不做等值断言）——
    lo, hi = _month_edges(month)
    frs = db.query(FormalRecord).filter(
        FormalRecord.japan_date >= lo,
        FormalRecord.japan_date < hi).all()
    formal_rows_now = len(frs)
    formal_points_now = sum(f.points or 0 for f in frs)

    # —— 该月 raw 判定分布（六键 + other 残差桶）——
    raws = db.query(RawRecord).filter(
        RawRecord.modified_raw.like(month + "%")).all()
    raw_total = len(raws)
    buckets = {"valid": 0, "cross_file_dup": 0, "master_late": 0,
               "from_sub": 0, "no_ref": 0, "blank": 0, "other": 0}
    for rr in raws:
        cs = rr.clean_status
        if cs in ("valid", "cross_file_dup", "master_late",
                  "from_sub", "no_ref"):
            buckets[cs] += 1
        elif cs in ("visible_blank", "blank"):
            buckets["blank"] += 1
        else:                                   # NULL / 历史遗留值
            buckets["other"] += 1

    # —— valid raw 点数分布（与 finalize/rebuild 同一口径：_formal_for_raw）——
    rules_by_imp = {i.id: ((i.layout or {}).get("point_rules"))
                    for i in db.query(ImportFile).all()}
    sub_ids = _sub_ids()
    by_points: dict[int, int] = {}
    valid_points_total = 0
    dedup_sub_hits = 0
    for rr in raws:
        if rr.clean_status != "valid":
            continue
        p = flow._formal_for_raw(rr, point_rules=rules_by_imp.get(rr.import_id))
        pts = p.points or 0
        by_points[pts] = by_points.get(pts, 0) + 1
        valid_points_total += pts
        if rr.store_id_raw in sub_ids:
            dedup_sub_hits += 1

    persons = sorted({rr.submitter_code for rr in raws if rr.submitter_code})

    # —— 先写一行审计拿 preview_id（ok=None；两阶段回填由审计中间件负责）——
    row = McpAuditLog(tool="visit_rebuild",
                      params_json=json.dumps({"month": month},
                                             ensure_ascii=False),
                      token_id=actor.token_id, user_id=actor.uid, ok=None)
    db.add(row)
    db.commit()
    expires_at = row.created_at + timedelta(minutes=30)

    return {"ok": True, "data": {
        "month": month,
        "formal_rows_now": formal_rows_now,
        "formal_points_now": formal_points_now,
        "raw_total": raw_total,
        "raw_by_status": buckets,
        "raw_by_points": by_points,
        "estimated_insert_rows": buckets["valid"],
        "dedup_sub_hits": dedup_sub_hits,
        "affected_persons": persons,
        "preview_id": row.id,
        "expires_at": expires_at.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "notes": [
            "估算基于当前 clean_status，重判可能改变分类；"
            "estimated_insert_rows 为入表上限（=valid），不是承诺结果",
        ],
    }}


def rebuild_month(db, actor, *, month: str, preview_id: int,
                  confirm_text: str) -> dict[str, Any]:
    """visit_rebuild(action='run')：月度重算（前置 preview + 确认语，全部与会话无关）。

    闸门顺序：require_write → validate_month → assert_confirm →
    check_preview（同一 token_id/同月/30 分钟窗）→ assert_has_source →
    assert_not_sealed → ensure_config_warmed。
    月粒度 pending 申诉由 flow.rebuild_month 拒绝，此处捕获映射 PENDING_APPEALS。
    成功后补 sync_period_table / sync_dash_metrics（best-effort）：
    失败只进 data.warnings，**绝不**返回 INTERNAL_WRITE（spec §6.2 评审修正）。
    """
    from app.models import McpAuditLog
    from app.services import dashboard, flow, period

    guards.require_write(actor)
    guards.validate_month(month)
    guards.assert_confirm(confirm_text, f"确认重算 {month}")
    rec = db.get(McpAuditLog, preview_id) if preview_id else None
    guards.check_preview(_PreviewRec(rec) if rec is not None else None,
                         preview_id, actor.token_id, month)
    guards.assert_has_source(db, month)
    guards.assert_not_sealed(db, [month])
    guards.ensure_config_warmed(db, month)

    res = flow.rebuild_month(db, month, actor.uid)
    if not res.get("ok"):
        msg = res.get("msg") or ""
        if "申诉" in msg:
            raise guards.GuardError(
                "PENDING_APPEALS", msg,
                "存在未决申诉，申诉功能未开放，请联系维护人员处理")
        raise guards.GuardError(
            "INTERNAL_WRITE", msg,
            "重算可能已部分生效，**不要自动重试**；先用只读工具核对当前状态")

    data = {
        "month": month,
        "files": res.get("files", []),
        "formal_before": res.get("formal_before"),
        "formal_after": res.get("formal_after"),
        "points_before": res.get("points_before"),
        "points_after": res.get("points_after"),
        "hint": "重算会改写 clean_status、翻 from_sub、重建 stats；"
                "M+1 递延结转会陈旧；快照只能回灌正式表",
    }
    warnings = []
    try:
        period.sync_period_table(db, month)
    except Exception as exc:  # noqa: BLE001  best-effort
        warnings.append(f"找平表同步未完成：{exc!r}")
    try:
        dashboard.sync_dash_metrics(db, month)
    except Exception as exc:  # noqa: BLE001  best-effort
        warnings.append(f"看板同步未完成：{exc!r}")
    if warnings:
        data["warnings"] = warnings + ["请用只读工具核对当前状态"]
    return {"ok": True, "data": data}


def set_per_point(db, actor, *, month: str, per_point,
                  confirm_text: str) -> dict[str, Any]:
    """改单价（原 visit_set_per_point；现为 visit_config(action=set) 的能力）。幂等可重试；先 ensure_config_warmed 防写错钱。"""
    from app.services import perf

    guards.require_write(actor)
    guards.validate_month(month)
    pp = guards.validate_per_point(per_point)
    guards.assert_confirm(confirm_text, f"确认改单价 {month} {pp}")
    guards.assert_not_sealed(db, [month])
    guards.ensure_config_warmed(db, month)

    res = perf.set_month_per_point(db, month, pp)
    return {"ok": True, "data": {
        "month": month,
        "per_point": pp,
        "rows": res.get("rows", 0),
        "hint": "只重算目标月，M+1 递延结转会陈旧，如需对齐请对下月重跑或人工核对",
    }}

