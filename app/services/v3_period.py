# -*- coding: utf-8 -*-
"""月度分期对账偏差表（薪资找平）：
上半月(1-15)/下半月(16-月末) = 系统正式表分段点数（已分期发给现场）；
对账点数 = 该月对账文件点数（期末终算）；
上月修正 = 上月该员工对账偏差；
本月对账偏差 = 对账 − (上半月+下半月) + 上月修正（>0 少付需补，<0 多付需扣；可手改）。
"""
from datetime import date as _date

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
    from app.services import v3_perf
    if per_point is None:
        per_point = v3_perf.month_per_point(db, month)
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
        carry_in[r.person_code] = r.prev_adjust_amount or 0   # 上月结转
        prev[r.person_code] = (r.diff_points or 0) - (r.adjust_points or 0)
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
        from app.services import v3_perf
        g, amt = v3_perf.bonus_params(month)
        settle_amt = v3_perf.salary_for(sp, per_point, month)
        b1 = (h1 // g) * amt
        h1_rem = h1 % g
        b2 = ((h1_rem + h2) // g) * amt
        h1_amt = h1 * per_point + b1
        h2_amt = h2 * per_point + b2
        # 金额差（含奖金）= 对账金额 − 系统已发金额（负=系统多发→扣款；正=系统少发→补款）
        diff_amt = settle_amt - (h1_amt + h2_amt)
        # 下月结转 = 本月金额差 + 本月扣剩余额（负=下月继续扣；正=下月补发）
        #   （上月结转先在本月两期工资里扣：本月两期+上月结转<0 的部分才递延）
        left_this = carry_in.get(code, 0) + h1_amt + h2_amt
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
