# -*- coding: utf-8 -*-
"""数据自洽检查（系统内置能力）。

**为什么内置**：找平/台账/关联这套数字必须能自证一致——否则出问题时只能靠人工
写脚本排查（这正是本项目实测踩过的：跨月债务丢失 11 人 -89,750 无人察觉）。
系统应能主动回答"我的数据自洽吗、哪一项不自洽"。

检查项：
  A1 找平表金额 == 该月差异金额（逐人）
  A2 已找平 + 剩余 == 原始金额
  A3 状态与剩余一致（0=settled）
  A4 关联（按发放）合计 == 台账抵扣额
  A5 关联（按找平）合计 == 已找平额
  A6 台账快照 vs 当前应发（**分歧提示**，非错误：计划可变、已发的钱不可改）
  A7 抵扣来源可反查（月份/找平行/对账任务存在）
  A8 递延链符号一致

返回：{"ok": bool, "checks": [{id, name, status, count, samples}], "summary": {...}}
status: pass / fail / info
"""
from typing import Any

from sqlalchemy.orm import Session

from app.models import (FormalRecord, PayrollAdjust, PayrollPayment,
                        PayrollPeriodRow, PayrollSettlementLink, ReconTask)


def _month_range(m: str) -> tuple[str, str]:
    """月份区间 [首日, 下月首日)。**不要用 "月份-32"**：SQLite 能过、PG 直接报错。"""
    y, mo = int(m[:4]), int(m[5:7])
    nxt = f"{y + 1}-01-01" if mo == 12 else f"{y}-{mo + 1:02d}-01"
    return f"{m}-01", nxt


def check_all(db: Session) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []

    def add(cid, name, bad, *, status="fail", samples=None):
        checks.append({
            "id": cid, "name": name,
            "status": "pass" if not bad else status,
            "count": len(bad) if isinstance(bad, list) else int(bad or 0),
            "samples": (samples if samples is not None else bad)[:5]
            if isinstance(bad, list) else [],
        })

    # A1 找平表金额 == 该月差异金额
    bad = []
    for a in db.query(PayrollAdjust).all():
        r = db.query(PayrollPeriodRow).filter_by(
            month=a.source_month, person_code=a.person_code).first()
        if r is None or (r.diff_amount or 0) != (a.adjust_amount or 0):
            bad.append(f"{a.source_month} {a.person_code}: 找平表 {a.adjust_amount} "
                       f"vs 找平行 {None if r is None else r.diff_amount}")
    add("A1", "找平表金额 == 该月差异金额", bad)

    # A2
    bad = [f"#{a.id} {a.source_month} {a.person_code}: "
           f"{a.settled_amount}+{a.remaining} != {a.adjust_amount}"
           for a in db.query(PayrollAdjust).all()
           if (a.settled_amount or 0) + (a.remaining or 0) != (a.adjust_amount or 0)]
    add("A2", "已找平 + 剩余 == 原始金额", bad)

    # A3
    bad = [f"#{a.id} status={a.status} remaining={a.remaining}"
           for a in db.query(PayrollAdjust).all()
           if ((a.remaining or 0) == 0) != (a.status == "settled")]
    add("A3", "状态与剩余一致（0=settled）", bad)

    # A4
    bad = []
    for p in db.query(PayrollPayment).all():
        s = sum(l.amount or 0 for l in db.query(PayrollSettlementLink).filter(
            PayrollSettlementLink.payment_id == p.id).all())
        if s != (p.adjust_applied or 0):
            bad.append(f"台账#{p.id} {p.month} seq{p.seq}: 关联合计 {s} "
                       f"vs adjust_applied {p.adjust_applied}")
    add("A4", "关联（按发放）合计 == 台账抵扣额", bad)

    # A5
    bad = []
    for a in db.query(PayrollAdjust).all():
        s = sum(l.amount or 0 for l in db.query(PayrollSettlementLink).filter(
            PayrollSettlementLink.adjust_id == a.id).all())
        if s != (a.settled_amount or 0):
            bad.append(f"找平#{a.id}: 关联合计 {s} vs 已找平 {a.settled_amount}")
    add("A5", "关联（按找平）合计 == 已找平额", bad)

    # A6（分歧提示，非错误）
    bad = []
    for p in db.query(PayrollPayment).all():
        r = db.query(PayrollPeriodRow).filter_by(
            month=p.month, person_code=p.person_code).first()
        if r is None:
            bad.append(f"台账#{p.id} 无对应找平行 {p.month} {p.person_code}")
            continue
        expect = r.half1_amount if p.seq == 1 else r.half2_amount
        if (p.amount or 0) != (expect or 0):
            bad.append(f"台账#{p.id} {p.month} seq{p.seq}: 实发 {p.amount} "
                       f"vs 当前应发 {expect}")
    add("A6", "台账快照 vs 当前应发（计划变更提示）", bad, status="info")

    # A7
    bad = []
    for p in db.query(PayrollPayment).all():
        if p.adjust_source_row_id and db.get(PayrollPeriodRow,
                                             p.adjust_source_row_id) is None:
            bad.append(f"台账#{p.id} source_row_id={p.adjust_source_row_id} 不存在")
        if p.adjust_source_month and not db.query(PayrollPeriodRow).filter_by(
                month=p.adjust_source_month, person_code=p.person_code).first():
            bad.append(f"台账#{p.id} source_month={p.adjust_source_month} 无该人找平行")
        if p.adjust_source_task_id and db.get(ReconTask,
                                              p.adjust_source_task_id) is None:
            bad.append(f"台账#{p.id} source_task_id={p.adjust_source_task_id} 不存在")
    add("A7", "抵扣来源可反查", bad)

    # A8
    bad = [f"{r.month} {r.person_code}: diff {r.diff_amount} 与结转 "
           f"{r.prev_adjust_amount} 符号不一致"
           for r in db.query(PayrollPeriodRow).all()
           if (r.diff_amount or 0) and (r.prev_adjust_amount or 0)
           and ((r.diff_amount or 0) > 0) != ((r.prev_adjust_amount or 0) > 0)]
    add("A8", "递延链符号一致", bad)

    # A9 应发表「调整列」与「差异列」一致（历史遗留列防双源）
    # 为什么要有：这两列曾被两套逻辑写（重算=diff、旧规则=全额扣），
    # 实测 9 月出现 diff=0 而 adjust=-5,173,750 的分叉，且该列被页面/报表消费。
    bad = [f"{r.month} {r.person_code}: diff {r.diff_amount} vs 调整列 {r.adjust_amount}"
           for r in db.query(PayrollPeriodRow).all()
           if (r.diff_amount or 0) != (r.adjust_amount or 0)]
    add("A9", "应发表调整列 == 差异列（防双源）", bad)

    fails = [c for c in checks if c["status"] == "fail"]
    infos = [c for c in checks if c["status"] == "info"]
    months = sorted({str(r.japan_date)[:7] for r in db.query(FormalRecord).all()
                     if r.japan_date})
    return {
        "ok": not fails,
        "checks": checks,
        "summary": {
            "passed": sum(1 for c in checks if c["status"] == "pass"),
            "failed": len(fails),
            "info": len(infos),
            "months_with_data": months,
            "scale": {
                "findiff_rows": db.query(PayrollPeriodRow).count(),
                "adjust_rows": db.query(PayrollAdjust).count(),
                "payment_rows": db.query(PayrollPayment).count(),
                "link_rows": db.query(PayrollSettlementLink).count(),
            },
        },
    }
