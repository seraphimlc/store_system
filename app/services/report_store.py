# -*- coding: utf-8 -*-
"""对比结果的物化存储（人×区间 / 人×日）：落表、失效刷新、员工端只读快照。

从 report_ai 拆出：AI 生成与"对比结果的持久化"是两件事，改一个不该动另一个。
**读接口只读**（不写库）——写只发生在报告生成、员工填报/修改、月度统计重算这三处。
"""
from sqlalchemy.exc import IntegrityError

from app.models import StaffReportAnalysis
from app.services.daterange import formal_date_range


def materialize(db, analysis_id: int, res: dict) -> int:
    """把对比结果落成物化表（幂等：先删后插）。返回写入行数。

    员工端核对页从此**只读物化表**，不再实时跑 compare()。
    """
    from sqlalchemy.exc import IntegrityError

    from app.models import StaffReportCompareDay as _D
    from app.models import StaffReportComparePerson as _P
    db.query(_D).filter(_D.analysis_id == analysis_id).delete(
        synchronize_session=False)
    db.query(_P).filter(_P.analysis_id == analysis_id).delete(
        synchronize_session=False)
    for p in res["persons"]:
        db.add(_P(analysis_id=analysis_id, person_code=p["person_code"],
                  name=p["name"] or "", sys_p1=p["sys_p1"], sys_p2=p["sys_p2"],
                  sys_total=p["sys_total"], rep_p1=p["rep_p1"],
                  rep_p2=p["rep_p2"], rep_total=p["rep_total"], d1=p["d1"],
                  d2=p["d2"], d_total=p["d_total"], acc=p["acc"],
                  days_filled=p["days_filled"],
                  days_system=p["days_system"], days_both=p["days_both"],
                  gaps=p["gaps"], abs_dt=p["abs_dt"]))
    for r in res["daily"]:
        db.add(_D(analysis_id=analysis_id, person_code=r["person_code"],
                  ref_date=r["date"], kind=r["kind"], sys_p1=r["sys_p1"],
                  sys_p2=r["sys_p2"], sys_total=r["sys_total"],
                  rep_p1=r["rep_p1"], rep_p2=r["rep_p2"],
                  rep_total=r["rep_total"], d1=r["d1"], d2=r["d2"], dt=r["dt"]))
    try:
        db.commit()
    except IntegrityError:      # 并发重复落表 → 不是错误（唯一键就是干这个的）
        db.rollback()
        return 0
    return len(res["persons"]) + len(res["daily"])


def refresh_for_date(db, ref_date) -> int:
    """**数据变化后刷新物化行**：覆盖该日期的已完成报告重算并落表。

    触发点：员工填报/修改（`daily_report`）、系统侧统计重算（`perf.sync_month_stats`）。
    不刷新的话，员工端核对页会一直显示报告生成那一刻的旧数字（与管理端实时对比不一致）。
    返回刷新了几份报告。
    """
    from app.services import report_compare
    rows = (db.query(StaffReportAnalysis)
            .filter(StaffReportAnalysis.status == "done",
                    StaffReportAnalysis.period_start <= ref_date,
                    StaffReportAnalysis.period_end >= ref_date).all())
    for a in rows:
        res = report_compare.compare(db, a.period_start, a.period_end)
        if res["persons"]:
            materialize(db, a.id, res)
    return len(rows)


def _person_row(p) -> dict:
    return {"person_code": p.person_code, "name": p.name, "sys_p1": p.sys_p1,
            "sys_p2": p.sys_p2, "sys_total": p.sys_total, "rep_p1": p.rep_p1,
            "rep_p2": p.rep_p2, "rep_total": p.rep_total, "d1": p.d1,
            "d2": p.d2, "d_total": p.d_total, "acc": p.acc,
            "days_filled": p.days_filled,
            "days_system": p.days_system, "days_both": p.days_both,
            "gaps": p.gaps, "abs_dt": p.abs_dt}


def employee_snapshot(db, analysis, person_code: str) -> dict:
    """员工端**只读**物化结果：{person, days}。

    物化行在报告生成时写入（`run_analysis`）或由 `scripts/backfill_compare.py` 补写；
    这里**绝不写库**（读接口不能有副作用，并发下会撞唯一键）。
    """
    from app.models import StaffReportCompareDay as _D
    from app.models import StaffReportComparePerson as _P
    if analysis is None:
        return {"person": None, "days": []}
    p = (db.query(_P).filter(_P.analysis_id == analysis.id,
                             _P.person_code == person_code).first())
    days = (db.query(_D).filter(_D.analysis_id == analysis.id,
                                _D.person_code == person_code)
            .order_by(_D.ref_date).all())
    return {"person": _person_row(p) if p is not None else None,
            "days": [{"date": d.ref_date, "kind": d.kind, "sys_total": d.sys_total,
                      "sys_p1": d.sys_p1, "sys_p2": d.sys_p2,
                      "rep_total": d.rep_total, "rep_p1": d.rep_p1,
                      "rep_p2": d.rep_p2, "dt": d.dt} for d in days]}


def file_coverage(db):
    """已导入文件在正式表里的日期覆盖范围 (min, max)；没有任何正式记录 → (None, None)。

    这是"离线数据"的边界：区间超出它就没有系统侧数字可比。
    """
    return formal_date_range(db)


def available_periods(db, person_code: str = "", *, limit: int = 12) -> list:
    """员工端可看的核对区间：同区间取最新一份，且**整段落在已导入文件的覆盖范围内**。

    数据源 = 物化表（`staff_report_compare_person`，报告生成时写入）。
    **只读**：老报告若没有物化行，用 `scripts/backfill_compare.py` 补（不在读路径里写库）。
    """
    from app.models import StaffReportAnalysis as _A
    from app.models import StaffReportComparePerson as _P
    cov_start, cov_end = file_coverage(db)
    if cov_start is None:
        return []
    q = (db.query(_A).join(_P, _P.analysis_id == _A.id)
         .filter(_A.status == "done"))
    if person_code:
        q = q.filter(_P.person_code == person_code)
    seen, out = set(), []
    for a in q.order_by(_A.id.desc()).all():
        if a.period_start < cov_start or a.period_end > cov_end:
            continue                      # 跨出文件覆盖范围 → 不列（没有离线数据可比）
        key = (a.period_start, a.period_end)
        if key in seen:                   # 同区间重复生成过 → 只留最新
            continue
        seen.add(key)
        out.append(a)
        if len(out) >= limit:
            break
    return out


def import_period(db, import_id: int):
    """**该文件**在正式表里的日期范围；没有正式记录时返回 (None, None)。

    刻意不回退全局覆盖范围：入表 0 条的文件不该触发生成报告。
    """
    return formal_date_range(db, import_id)
