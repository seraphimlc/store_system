# -*- coding: utf-8 -*-
"""月度分期对账偏差表（薪资找平）：
上半月(1-15)/下半月(16-月末) = 系统正式表分段点数（已分期发给现场）；
对账点数 = 该月对账文件点数（期末终算）；
上月修正 = 上月该员工对账偏差；
本月对账偏差 = 对账 − (上半月+下半月) + 上月修正（>0 少付需补，<0 多付需扣；可手改）。
"""
from datetime import date as _date

from typing import Any

from sqlalchemy.orm import Session

from app.models import FormalRecord, PayrollPeriodRow, Person, ReconResult, ReconTask


def _prev_month(month: str) -> str:
    y, m = int(month[:4]), int(month[5:7])
    if m == 1:
        return f"{y - 1}-12"
    return f"{y}-{m - 1:02d}"


def _month_bounds(month: str):
    y, m = int(month[:4]), int(month[5:7])
    st = _date(y, m, 1)
    en = _date(y + 1, 1, 1) if m == 12 else _date(y, m + 1, 1)
    return st, en


def paid_seqs(db: Session, month: str) -> set:
    """该月**已实际发放**的期号集合（来自发放台账；兼容旧的标记表）。"""
    from app.models import PayrollPaidMark, PayrollPayment
    seqs = {r.seq for r in db.query(PayrollPayment).filter(
        PayrollPayment.month == month).all()}
    # 兼容：早期只有"标记"没有台账时，把标记视作已发（无金额快照）
    seqs |= {r.half for r in db.query(PayrollPaidMark).filter(
        PayrollPaidMark.month == month).all()}
    return seqs


def paid_halves(db: Session, month: str) -> set:
    """向后兼容别名（= paid_seqs）。"""
    return paid_seqs(db, month)


def record_payment(db: Session, month: str, person_code: str, seq: int,
                   amount: int = None, points: int = None, bonus: int = None,
                   adjust_applied: int = 0, note: str = None,
                   paid_by: int = None) -> bool:
    """登记一次实际发放（台账，不可变）。金额默认取当前计算值（快照落库）。

    已登记过的 (月,人,期) 不覆盖——事实一旦记下就不该被重写；如需更正请先删除该行。

    **找平抵扣溯源**（adjust_applied 不能是空穴来风）：登记时按结转链算出
    这笔实发应抵扣多少（上月结转按 seq 顺序被本月未发的期吸收），并记下来源：
    - adjust_source_type/month/row_id/task_id → 指向**产生这笔结转的月份**的
      找平行（payroll_period_rows）与其对账任务（recon_tasks），可反查；
    - adjust_leftover → 扣完这笔后仍需递延的金额（负），与下月结转链自洽。
    """
    from app.models import PayrollPayment, PayrollPeriodRow

    row = db.query(PayrollPayment).filter(
        PayrollPayment.month == month,
        PayrollPayment.person_code == person_code,
        PayrollPayment.seq == seq).first()
    if row is not None:
        return False
    calc = db.query(PayrollPeriodRow).filter(
        PayrollPeriodRow.month == month,
        PayrollPeriodRow.person_code == person_code).first()
    if amount is None:
        amount = (calc.half1_amount if seq == 1 else calc.half2_amount) if calc else 0
    if points is None:
        points = (calc.half1_points if seq == 1 else calc.half2_points) if calc else 0
    if bonus is None:
        bonus = (calc.half1_bonus if seq == 1 else calc.half2_bonus) if calc else 0

    # ---- 溯源计算：本笔实发要吸收多少上月结转 ----
    src_type = src_month = src_row = src_task = None
    leftover = None
    pm = _prev_month(month)   # 模块级已定义：mm-1（上月），12月→y-1-12
    prev_row = db.query(PayrollPeriodRow).filter(
        PayrollPeriodRow.month == pm,
        PayrollPeriodRow.person_code == person_code).first()
    carry = prev_row.prev_adjust_amount or 0 if prev_row else 0   # 负=要扣
    if carry < 0:
        absorbed = 0
        for q in db.query(PayrollPayment).filter(
                PayrollPayment.month == month,
                PayrollPayment.person_code == person_code).all():
            absorbed += q.adjust_applied or 0                    # 已扣的（负）
        remaining = carry + absorbed                             # 还要扣的（负）
        if remaining < 0:
            paid_amt = amount or 0
            take = max(remaining, -paid_amt)                     # 本笔最多扣 paid_amt
            adjust_applied = take
            leftover = remaining - take                          # 扣完仍需递延（负）
            src_type = "carry"
            src_month = pm
            src_row = prev_row.id
            from app.models import ReconTask
            cur = None
            for t in db.query(ReconTask).filter(
                    ReconTask.kind == "monthly_v3").all():
                pp = t.params or {}
                if pp.get("month") == pm and not pp.get("replaced_by"):
                    if cur is None or t.id > cur.id:
                        cur = t
            src_task = cur.id if cur else None

    pay = PayrollPayment(month=month, person_code=person_code, seq=seq,
                         points=points or 0, amount=amount or 0,
                         bonus=bonus or 0, adjust_applied=adjust_applied or 0,
                         note=note, paid_by=paid_by,
                         adjust_source_type=src_type,
                         adjust_source_month=src_month,
                         adjust_source_row_id=src_row,
                         adjust_source_task_id=src_task,
                         adjust_leftover=leftover)
    db.add(pay)
    db.flush()                      # 拿到 payment.id 才能写关联行
    if adjust_applied:
        try:
            allocate_settlement(db, person_code, adjust_applied,
                                payment_id=pay.id, month=month)
        except Exception:  # noqa: BLE001  关联失败不影响台账登记
            db.rollback()
    db.commit()
    return True


def mark_paid(db: Session, month: str, half: int, marked_by: int = None,
              unmark: bool = False, person_code: str = None) -> int:
    """登记/取消「该期已发薪」。**写入发放台账**（含金额快照）。返回影响人数。

    - person_code 为空 → 该月**全部人**的这一期（整期发薪的常见场景）
    - unmark=True → 删除台账行（用于更正误登记）
    """
    from app.models import PayrollPaidMark, PayrollPayment, PayrollPeriodRow
    if half not in (1, 2):
        raise ValueError("half 只能是 1（上半月）或 2（下半月）")

    q = db.query(PayrollPeriodRow).filter(PayrollPeriodRow.month == month)
    if person_code:
        q = q.filter(PayrollPeriodRow.person_code == person_code)
    codes = [r.person_code for r in q.all()]

    if unmark:
        n = db.query(PayrollPayment).filter(
            PayrollPayment.month == month, PayrollPayment.seq == half)
        if person_code:
            n = n.filter(PayrollPayment.person_code == person_code)
        n = n.delete(synchronize_session=False)
        db.query(PayrollPaidMark).filter(
            PayrollPaidMark.month == month, PayrollPaidMark.half == half).delete(
            synchronize_session=False)
        db.commit()
        return n

    n = 0
    for code in codes:
        if record_payment(db, month, code, half, paid_by=marked_by):
            n += 1
    # 兼容旧标记表（历史代码/页面可能读它）
    if db.query(PayrollPaidMark).filter(
            PayrollPaidMark.month == month,
            PayrollPaidMark.half == half).first() is None:
        db.add(PayrollPaidMark(month=month, half=half, marked_by=marked_by))
        db.commit()
    return n


def _now_utc():
    from datetime import datetime as _dt
    return _dt.utcnow()


def sync_adjusts(db: Session, month: str) -> int:
    """为该月找平差异 upsert **找平行**（进度保留，不重置已找平金额）。

    每笔差异一行：adjust_amount=该月 diff；settled_amount 保留历史回收；
    remaining=adjust_amount−settled_amount；归零 → status=settled + settled_at。
    """
    from app.models import PayrollAdjust, PayrollPeriodRow
    task = None
    for t in db.query(ReconTask).filter(ReconTask.kind == "monthly_v3").all():
        pp = t.params or {}
        if pp.get("month") == month and not pp.get("replaced_by"):
            if task is None or t.id > task:
                task = t.id
    n = 0
    for r in db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == month).all():
        diff = r.diff_amount or 0
        row = db.query(PayrollAdjust).filter(
            PayrollAdjust.source_month == month,
            PayrollAdjust.person_code == r.person_code).first()
        if row is None:
            if diff == 0:
                continue
            db.add(PayrollAdjust(source_month=month, person_code=r.person_code,
                                 source_task_id=task, source_row_id=r.id,
                                 adjust_amount=diff, settled_amount=0,
                                 remaining=diff, status="in_progress",
                                 updated_at=_now_utc()))
        else:
            row.adjust_amount = diff
            row.source_task_id = task or row.source_task_id
            row.source_row_id = r.id
            row.remaining = diff - (row.settled_amount or 0)
            if row.remaining == 0:
                if row.status != "settled":
                    row.status, row.settled_at = "settled", _now_utc()
            else:
                row.status, row.settled_at = "in_progress", None
            row.updated_at = _now_utc()
        n += 1
    db.commit()
    return n


def allocate_settlement(db: Session, person_code: str, absorbed: int,
                        payment_id: int = None, month: str = None) -> list:
    """把一笔发放吸收的找平额按 **FIFO**（先欠的先还）冲最早的未结清找平行。

    absorbed 与 remaining 同号（负=扣回/正=补发）。返回分配明细（可追溯）。
    payment_id 给出时，同时写 **关联行**（payroll_settlement_links），实现双向可查：
    - 一笔发放 → 冲了哪几笔找平（可能多条，跨月）
    - 一笔找平 → 被哪几期发放回收的（可能多条，跨期）
    """
    from app.models import PayrollAdjust, PayrollSettlementLink
    if not absorbed:
        return []
    alloc = []
    rest = absorbed
    rows = db.query(PayrollAdjust).filter(
        PayrollAdjust.person_code == person_code,
        PayrollAdjust.status == "in_progress").order_by(
        PayrollAdjust.source_month, PayrollAdjust.id).all()
    for a in rows:
        if rest == 0:
            break
        rem = a.remaining or 0
        if rem == 0 or (rem > 0) != (rest > 0):
            continue                      # 方向不同 → 不是同一笔的回收
        take = rest if abs(rest) <= abs(rem) else rem
        a.settled_amount = (a.settled_amount or 0) + take
        a.remaining = rem - take
        rest -= take
        if a.remaining == 0:
            a.status, a.settled_at = "settled", _now_utc()
        a.updated_at = _now_utc()
        if payment_id is not None:
            db.add(PayrollSettlementLink(
                adjust_id=a.id, payment_id=payment_id, month=month,
                person_code=person_code, amount=take))
        alloc.append({"adjust_id": a.id, "source_month": a.source_month,
                      "amount": take})
    db.commit()
    return alloc


def settlement_trace(db: Session, month: str = None, person: str = None,
                     adjust_id: int = None, payment_id: int = None) -> dict:
    """找平↔薪资 **双向轨迹查询**（用户要求的专用查询口）。

    - 无过滤 → 全部找平的汇总 + 明细
    - month → 该**源月**的找平（也可命中该月发放的关联）
    - person → 该人所有找平（支持工号精确/姓名包含）
    - adjust_id → 单笔找平完整轨迹（原始/已找平/剩余/结清 + 被哪几期回收）
    - payment_id → 单笔发放冲了哪几笔找平
    """
    from app.models import (PayrollAdjust, PayrollPayment, PayrollSettlementLink,
                            Person)
    names = {p.code: p.display_name for p in db.query(Person).all()}

    def _person_match(code: str, key: str) -> bool:
        return code == key or key in (names.get(code) or "")

    q = db.query(PayrollAdjust)
    if adjust_id is not None:
        q = q.filter(PayrollAdjust.id == adjust_id)
    if month:
        q = q.filter(PayrollAdjust.source_month == month)
    adjusts = q.order_by(PayrollAdjust.source_month, PayrollAdjust.id).all()
    if person:
        key = person.strip()
        adjusts = [a for a in adjusts if _person_match(a.person_code, key)]

    out_adjusts = []
    for a in adjusts:
        rec = adjust_payment_links(db, a.id)
        out_adjusts.append({
            "adjust_id": a.id, "source_month": a.source_month,
            "person_code": a.person_code,
            "name": names.get(a.person_code, a.person_code),
            "adjust_amount": a.adjust_amount or 0,
            "settled_amount": a.settled_amount or 0,
            "remaining": a.remaining or 0,
            "status": a.status,
            "settled_at": str(a.settled_at) if a.settled_at else None,
            "source_task_id": a.source_task_id,
            "source_row_id": a.source_row_id,
            "recovered_by": rec,
        })

    out_payments = []
    if payment_id is not None:
        for p_ in db.query(PayrollPayment).filter(
                PayrollPayment.id == payment_id).all():
            out_payments.append({
                "payment_id": p_.id, "month": p_.month, "seq": p_.seq,
                "person_code": p_.person_code,
                "name": names.get(p_.person_code, p_.person_code),
                "amount": p_.amount or 0, "points": p_.points or 0,
                "bonus": p_.bonus or 0,
                "adjust_applied": p_.adjust_applied or 0,
                "adjust_leftover": p_.adjust_leftover,
                "adjusts": payment_adjust_links(db, p_.id),
            })
    elif month:
        for p_ in db.query(PayrollPayment).filter(
                PayrollPayment.month == month).all():
            links = payment_adjust_links(db, p_.id)
            if links:
                out_payments.append({
                    "payment_id": p_.id, "month": p_.month, "seq": p_.seq,
                    "person_code": p_.person_code,
                    "name": names.get(p_.person_code, p_.person_code),
                    "amount": p_.amount or 0, "adjust_applied": p_.adjust_applied or 0,
                    "adjusts": links,
                })

    data = {
        "currency": "JPY",
        "filters": {"month": month, "person": person,
                    "adjust_id": adjust_id, "payment_id": payment_id},
        "adjusts": out_adjusts,
        "payments": out_payments,
        "summary": {
            "adjust_count": len(out_adjusts),
            "settled": sum(1 for x in out_adjusts if x["status"] == "settled"),
            "in_progress": sum(1 for x in out_adjusts
                               if x["status"] != "settled"),
            "total_adjust_amount": sum(x["adjust_amount"] for x in out_adjusts),
            "total_settled": sum(x["settled_amount"] for x in out_adjusts),
            "total_remaining": sum(x["remaining"] for x in out_adjusts),
        },
    }
    if not out_adjusts and not out_payments:
        data["hint"] = "无匹配的找平/回收记录（合法结果，不是错误）"
    return data


def payment_adjust_links(db: Session, payment_id: int) -> list:
    """一笔发放 → 冲了哪几笔找平（含找平源月/原始金额/本次冲抵额）。"""
    from app.models import PayrollAdjust, PayrollSettlementLink
    out = []
    for lk in db.query(PayrollSettlementLink).filter(
            PayrollSettlementLink.payment_id == payment_id).all():
        a = db.get(PayrollAdjust, lk.adjust_id)
        out.append({"adjust_id": lk.adjust_id,
                    "source_month": a.source_month if a else None,
                    "adjust_amount": (a.adjust_amount if a else None),
                    "amount": lk.amount,
                    "adjust_status": (a.status if a else None)})
    return out


def adjust_payment_links(db: Session, adjust_id: int) -> list:
    """一笔找平 → 从哪几期薪资里回收的（含发放月/期号/金额）。"""
    from app.models import PayrollPayment, PayrollSettlementLink
    out = []
    for lk in db.query(PayrollSettlementLink).filter(
            PayrollSettlementLink.adjust_id == adjust_id).order_by(
            PayrollSettlementLink.month, PayrollSettlementLink.id).all():
        p = db.get(PayrollPayment, lk.payment_id)
        out.append({"payment_id": lk.payment_id, "month": lk.month,
                    "seq": (p.seq if p else None),
                    "payment_amount": (p.amount if p else None),
                    "amount": lk.amount})
    return out


def settlement_status(db: Session, month: str) -> dict[str, Any]:
    """某结算月（对账源月）的找平**结清状态**：这笔差异扣/补到哪一步了。

    依据：找平链（payroll_period_rows.prev_adjust_amount 逐月结转的债务）。
    - diff_amount：源月原始差异（负=应扣/正=应补）
    - chain：源月及其后各月的结转债务（prev_adjust_amount）
    - remaining：最新一期的债务（≠0 = 还没找平完）
    - recovered：diff_amount − remaining（该源月差异已实际回收的金额）
    - status：remaining==0 → settled（已结清）；否则 in_progress

    说明：当多个月份差异交错时，recovered 口径为"该源月差异的回收进度"近似，
    事件级证据以 payroll_payments 的 adjust_applied/adjust_source_* 为准（逐笔可查）。
    """
    from app.models import PayrollAdjust, PayrollPeriodRow, Person

    # 优先读**找平表**（进度是存储事实，非推导）；无行时回退链条推导
    adj_rows = db.query(PayrollAdjust).filter(
        PayrollAdjust.source_month == month).order_by(
        PayrollAdjust.person_code).all()
    if adj_rows:
        names = {p.code: p.display_name for p in db.query(Person).all()}
        out = [{
            "adjust_id": a.id,
            "person_code": a.person_code,
            "name": names.get(a.person_code, a.person_code),
            "adjust_amount": a.adjust_amount or 0,
            "settled_amount": a.settled_amount or 0,
            "remaining": a.remaining or 0,
            "status": a.status,
            "settled_at": str(a.settled_at) if a.settled_at else None,
            "source_task_id": a.source_task_id,
            "source": "payroll_adjusts",          # 来自找平表
            # 反向可查：这笔找平从哪几期薪资里回收的
            "recovered_by": adjust_payment_links(db, a.id),
        } for a in adj_rows]
        data = {"month": month, "currency": "JPY", "rows": out,
                "count": len(out), "source": "payroll_adjusts",
                "settled_count": sum(1 for x in out if x["status"] == "settled")}
        return data

    def _next_month(m: str) -> str:
        y, mm = int(m[:4]), int(m[5:7])
        return f"{y + 1}-01" if mm == 12 else f"{y}-{mm + 1:02d}"

    names = {p.code: p.display_name for p in db.query(Person).all()}
    months = sorted({r.month for r in db.query(PayrollPeriodRow).all()})
    out = []
    for r in db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == month).order_by(
            PayrollPeriodRow.person_code).all():
        diff = r.diff_amount or 0
        chain = [{"month": month, "debt": r.prev_adjust_amount or 0}]
        cur = r.prev_adjust_amount or 0
        m = _next_month(month)
        while m in months and cur != 0:
            nxt = db.query(PayrollPeriodRow).filter(
                PayrollPeriodRow.month == m,
                PayrollPeriodRow.person_code == r.person_code).first()
            if nxt is None:
                break
            cur = nxt.prev_adjust_amount or 0
            chain.append({"month": m, "debt": cur})
            m = _next_month(m)
        out.append({
            "person_code": r.person_code,
            "name": names.get(r.person_code, r.person_code),
            "diff_amount": diff,
            "chain": chain,
            "remaining": cur if cur != 0 else 0,
            "recovered": (diff or 0) - (cur if cur != 0 else 0),
            "status": "settled" if cur == 0 else "in_progress",
        })
    data = {"month": month, "currency": "JPY", "rows": out, "count": len(out)}
    if not out:
        data["hint"] = "该月无找平数据（合法结果，不是错误）"
    return data


def register_exported_half(db: Session, month: str, half: int,
                            paid_by: int = None) -> int:
    """**导出 = 发放事实**：系统无发薪反馈（导出 Excel 后离线按表发放），
    所以「导出发薪表」就是事实触发点——把该期全部人的金额快照写入台账。

    - 同 (月,人,期) 已存在不覆盖（第一次导出即事实，更正需显式 unmark）
    - 返回新登记人数；幂等
    """
    return mark_paid(db, month, half, marked_by=paid_by)


def month_has_recon(db: Session, month: str) -> bool:
    """该月是否有「当前」对账任务（供页面标注『待对账』）。"""
    return bool(_current_recon(db, month))


def _current_recon(db: Session, month: str) -> dict:
    """该月「当前」对账任务的 人→对账点数（无则空）。"""
    out = {}
    cur = None
    for t in db.query(ReconTask).filter(
            ReconTask.kind == "monthly_v3").all():
        p = t.params or {}
        if p.get("month") != month:
            continue
        if p.get("replaced_by"):
            continue
        if cur is None or t.id > cur.id:
            cur = t
    if cur is None:
        return out
    from app.models import ReconDataRow
    for r in db.query(ReconDataRow).filter(
            ReconDataRow.task_id == cur.id).all():
        out[r.person_code] = out.get(r.person_code, 0) + (r.points or 0)
    return out


def _half_stats_from_stats(db: Session, month: str) -> dict:
    """按期聚合人×日统计（供 sync 落库快照用）。"""
    from app.models import PersonDailyStat
    st, en = _month_bounds(month)
    out = {}
    for r in db.query(PersonDailyStat).filter(
            PersonDailyStat.ref_date >= st,
            PersonDailyStat.ref_date < en).all():
        if not r.person_code:
            continue
        d = out.setdefault(r.person_code,
                           {"h1": [0, 0, 0], "h2": [0, 0, 0]})
        key = "h1" if r.ref_date.day <= 15 else "h2"
        d[key][0] += r.records or 0
        d[key][1] += r.p1 or 0
        d[key][2] += r.p2 or 0
    return out


def half_stats_map(db: Session, month: str) -> dict:
    """按期店数（读 payroll_period_rows 落库快照，不实时聚合）：

    {code: {h1: (有效店,p1,p2), h2: (有效店,p1,p2)}}——发薪表期视图取本数据；
    快照在 sync_period_table 时固化，人×日表后续变化不影响已生成的分期数字。
    """
    out = {}
    for r in db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == month).all():
        out[r.person_code] = {
            "h1": (r.half1_records, r.half1_p1, r.half1_p2),
            "h2": (r.half2_records, r.half2_p1, r.half2_p2),
        }
    return out


def sync_period_table(db: Session, month: str, per_point: int = None) -> dict:
    """为该月生成/更新分期对账偏差表（幂等；手改过的偏差保留）。

    分段点数取自 person_daily_stats（人×日结算点，由正式表同步），
    与对账本地侧同源；上半月=1-15，下半月=16-月末。
    per_point：该月点数单价（默认取月绩效表锁存值，再退全局 250）。
    """
    from app.models import PersonDailyStat
    from app.services import perf
    if per_point is None:
        per_point = perf.month_per_point(db, month)
    st, en = _month_bounds(month)
    # 员工集合：该月统计表 ∪ 对账文件
    codes = {r.person_code for r in db.query(PersonDailyStat).filter(
        PersonDailyStat.ref_date >= st, PersonDailyStat.ref_date < en).all()
        if r.person_code}
    codes |= set(_current_recon(db, month).keys())
    # 分期店数快照（与分期点数同源同步固化）
    hstat = _half_stats_from_stats(db, month)
    half1 = {}
    half2 = {}
    for r in db.query(PersonDailyStat).filter(
            PersonDailyStat.ref_date >= st,
            PersonDailyStat.ref_date < en).all():
        if not r.person_code or not r.points:
            continue
        (half1 if r.ref_date.day <= 15 else half2)[r.person_code] = \
            (half1 if r.ref_date.day <= 15 else half2).get(
                r.person_code, 0) + r.points
    settle = _current_recon(db, month)
    has_recon = bool(settle)           # 该月是否有当前对账任务（无则差异记 0，不产生全额扣）
    paid = paid_seqs(db, month)        # 已发放的期（吸收额度只用**未发放**的期）
    # 结转链（金额，正=补/负=扣）：
    #   本月行存「下月要扣/补的余额」= −本月金额差 + 本月扣剩余额
    #   - 上月结转(上月 prev_adjust_amount)在本月两期工资里扣/补，
    #     本月两期吸收后仍为负的余额才继续递延；
    #   - 本月新产生的金额差(系统多发为正)下月开始扣 → 结转取负号。
    # 找平金额自动=金额差(无点击操作)：adjust_amount 由 sync 自动写，页面只读。
    prev = {}
    carry_in = {}
    pm = _prev_month(month)
    for r in db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == pm).all():
        carry_in[r.person_code] = r.prev_adjust_amount or 0   # 上月结转（兜底）
        prev[r.person_code] = (r.diff_points or 0) - (r.adjust_points or 0)
    # **找平表为准**：未结清余额按人汇总（source_month < 本月）。
    # 为什么：某人某月没有找平行时（实测 8→9 月有 11 人如此），
    # 从"上月找平行"读结转会**丢债**（-89,750 收不回来）；
    # 找平表是跨月的债务台账，不会因中间某月没数据而丢失。
    from app.models import PayrollAdjust
    prior_debt = {}
    for a_ in db.query(PayrollAdjust).filter(
            PayrollAdjust.source_month < month,
            PayrollAdjust.status == "in_progress").all():
        prior_debt[a_.person_code] = (prior_debt.get(a_.person_code, 0)
                                      + (a_.remaining or 0))
    for code_, debt in prior_debt.items():
        if debt:
            carry_in[code_] = debt
    names = {p.code: p.display_name for p in db.query(Person).all()}
    existing = {r.person_code: r for r in db.query(PayrollPeriodRow).filter(
        PayrollPeriodRow.month == month).all()}
    for code in sorted(codes):
        h1 = half1.get(code, 0)
        h2 = half2.get(code, 0)
        sp = settle.get(code, 0)
        pa = prev.get(code, 0)
        calc = sp - (h1 + h2) + pa
        # 金额：对账金额=工资规则(该月单价)×对账点数；分期金额=各期点数×单价 + 奖金
        # 奖金：每满 bonus_group 点发 bonus_amount 円，整月滚动、不跨月。
        # 上半月奖金 = h1÷门槛×奖额（余点带向下半月）；
        # 下半月奖金 = (h1余 + h2)÷门槛×奖额 − 上半月已发（当月累计滚动）
        from app.services import perf
        g, amt = perf.bonus_params(month)
        settle_amt = perf.salary_for(sp, per_point, month)
        b1 = (h1 // g) * amt
        h1_rem = h1 % g
        b2 = ((h1_rem + h2) // g) * amt
        h1_amt = h1 * per_point + b1
        h2_amt = h2 * per_point + b2
        # 金额差（含奖金）= 对账金额 − 系统已发金额（负=系统多发→扣款；正=系统少发→补款）
        # **无对账任务时记 0**：否则 sp=0 → 差异 = −全月工资 → 找平显示"全额扣"并结转下月
        # （实测：7/9 月无对账任务时，金额差 = 整月工资，会误导页面并可能误扣下月发薪）
        diff_amt = (settle_amt - (h1_amt + h2_amt)) if has_recon else 0
        # 下月结转 = 本月金额差 + 本月扣剩余额（负=下月继续扣；正=下月补发）
        #   （上月结转先在本月两期工资里扣：本月两期+上月结转<0 的部分才递延）
        # 可吸收额度 = **未发薪的期**的金额（已发薪的期改不了，不能算作可扣）
        # 否则会出现「上半月已发、下半月为 0 → 结转被判定已吸收，实际漏扣」。
        capacity = 0
        if 1 not in paid:
            capacity += h1_amt
        if 2 not in paid:
            capacity += h2_amt
        left_this = carry_in.get(code, 0) + capacity
        prev_amt = diff_amt + (left_this if left_this < 0 else 0)
        sh1 = hstat.get(code, {"h1": (0, 0, 0), "h2": (0, 0, 0)})["h1"]
        sh2 = hstat.get(code, {"h1": (0, 0, 0), "h2": (0, 0, 0)})["h2"]
        row = existing.get(code)
        if row is None:
            db.add(PayrollPeriodRow(
                month=month, person_code=code,
                half1_points=h1, half2_points=h2, settle_points=sp,
                prev_adjust_points=pa, diff_points=calc,
                half1_records=sh1[0], half1_p1=sh1[1], half1_p2=sh1[2],
                half2_records=sh2[0], half2_p1=sh2[1], half2_p2=sh2[2],
                half1_bonus=b1, half2_bonus=b2,
                half1_amount=h1_amt, half2_amount=h2_amt,
                settle_amount=settle_amt,
                prev_adjust_amount=prev_amt,  # 下月结转余额(链式:负扣/正补)
                diff_amount=diff_amt,   # 金额差(含奖金)=系统已发−对账金额
                adjust_amount=diff_amt,  # 找平自动=金额差(无点击操作,页面只读)
                updated_at=__import__("datetime").datetime.utcnow()))
        else:
            # 偏差两列=系统参考值：每次生成/更新自动按公式刷新；
            # 找平(金额)=金额差：自动写表（无人工调整，页面只读）。
            row.half1_points = h1
            row.half2_points = h2
            row.half1_records = sh1[0]
            row.half1_p1 = sh1[1]
            row.half1_p2 = sh1[2]
            row.half2_records = sh2[0]
            row.half2_p1 = sh2[1]
            row.half2_p2 = sh2[2]
            row.settle_points = sp
            row.prev_adjust_points = pa
            row.settle_amount = settle_amt
            row.prev_adjust_amount = prev_amt  # 下月结转余额(链式:负扣/正补)
            row.half1_bonus = b1
            row.half2_bonus = b2
            row.half1_amount = h1_amt
            row.half2_amount = h2_amt
            row.diff_points = calc
            row.diff_amount = diff_amt   # 金额差(含奖金)=系统已发−对账金额
            row.adjust_amount = diff_amt  # 找平自动=金额差(无点击,只读)
            row.updated_at = __import__("datetime").datetime.utcnow()
    db.commit()
    # 对账/偏差写回月绩效表（月绩效页直接可见"对账"与"偏差金额"）
    from app.models import MonthPerfRecord
    prs = db.query(PayrollPeriodRow).filter(
        PayrollPeriodRow.month == month).all()
    mpf = {r.person_code: r for r in db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).all()}
    for pr in prs:
        m = mpf.get(pr.person_code)
        if m is None:
            continue
        m.settle_points = pr.settle_points
        m.settle_amount = pr.settle_amount
        m.diff_points = pr.diff_points
        m.diff_amount = pr.diff_amount
    db.commit()
    # 同步**找平表**（进度落库：谁欠多少、已还多少、是否结清）
    try:
        sync_adjusts(db, month)
    except Exception:  # noqa: BLE001  找平表同步失败不影响主流程
        db.rollback()
    return {"rows": len(codes), "month": month, "settle": len(settle)}


def period_rows(db: Session, month: str):
    """该月偏差表行（含姓名），按人排序。"""
    names = {p.code: p.display_name for p in db.query(Person).all()}
    # 「上月修正」= 本月要扣/补的上月结转（上月行 prev_adjust_amount）
    pm = _prev_month(month)
    carry = {r.person_code: r.prev_adjust_amount for r in
             db.query(PayrollPeriodRow).filter(
                 PayrollPeriodRow.month == pm).all()}
    carry_pt = {r.person_code: r.prev_adjust_points for r in
                db.query(PayrollPeriodRow).filter(
                    PayrollPeriodRow.month == pm).all()}
    out = []
    for r in db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == month).order_by(
            PayrollPeriodRow.person_code).all():
        out.append({
            "code": r.person_code, "name": names.get(r.person_code,
                                                     r.person_code),
            "half1": r.half1_points, "half2": r.half2_points,
            "settle": r.settle_points, "prev": carry_pt.get(r.person_code, 0),
            "diff": r.diff_points,
            "half1_bonus": r.half1_bonus, "half2_bonus": r.half2_bonus,
            "half1_amt": r.half1_amount, "half2_amt": r.half2_amount,
            "settle_amt": r.settle_amount,
            "prev_amt": carry.get(r.person_code, 0),   # 上月结转(本月要扣/补)
            "diff_amt": r.diff_amount,
            "adj": r.adjust_points, "adj_amt": r.adjust_amount,
            "updated_by": r.updated_by, "updated_at": r.updated_at,
        })
    return out


def set_period_diff(db: Session, month: str, person_code: str,
                    diff: int, user_id: int) -> dict:
    """（兼容旧调用）把 diff 作为找平增量。"""
    return set_period_values(db, month, person_code, 0, 0,
                             user_id=user_id, adjust_delta=diff)


def set_period_values(db: Session, month: str, person_code: str,
                      half1_amount: int = 0, half2_amount: int = 0,
                      diff_points=None, user_id: int = 0,
                      adjust_delta: int = 0) -> dict:
    """保存「找平」增量（人工执行，单位为金額）：

    - 输入框语义 = 剩余未找平金额(金额差−已找平金额)，保存的是本次增量 → 累计制：
      金额差+58,250、第一次存+58,250后 输入框自动归 0；再填 5,000 → 总执行 +63,250。
    - 找平按金额修正（含奖金差异），不按点数；点数列是参考值。
    """
    row = db.query(PayrollPeriodRow).filter(
        PayrollPeriodRow.month == month,
        PayrollPeriodRow.person_code == person_code).first()
    if row is None:
        return {"ok": False, "msg": "该月此员工无对账行"}
    row.half1_amount = int(half1_amount or 0)
    row.half2_amount = int(half2_amount or 0)
    row.adjust_amount = (row.adjust_amount or 0) + int(adjust_delta or 0)
    row.updated_by = user_id
    row.updated_at = __import__("datetime").datetime.utcnow()
    db.commit()
    return {"ok": True, "msg": "找平已保存"}


def carry_map(db: Session, month: str) -> dict:
    """该月应结转的「上月未扣完余额」→ {code: [余量点(参考), 余额金额]}。

    余额金额(链式) = 上月(carry + half1_amt + half2_amt)，两期工资扣不完的负余额
    才递延到本月（发薪表"找平金额"列与两期扣减取自本函数）。
    找平金额(金额差)自动写表、页面只读；本函数返回上月结转余额(正补/负扣)。
    """
    pm = _prev_month(month)
    if not month:
        return {}
    out = {}
    for r in db.query(PayrollPeriodRow).filter(
            PayrollPeriodRow.month == pm).all():
        remain_pt = (r.diff_points or 0) - (r.adjust_points or 0)
        carry_amt = r.prev_adjust_amount or 0
        if carry_amt:
            out[r.person_code] = [remain_pt, carry_amt]
    return out
