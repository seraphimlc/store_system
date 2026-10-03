# -*- coding: utf-8 -*-
"""自报 vs 系统的对比与准确率（纯查询 + 计算，不写库）。

从 daily_report 拆出：填报是"写"，对比是"读 + 算"，两者变更原因不同。
口径（规格 v7 §7）：Δ=系统−自报；准确率=1−Σ|Δ|÷Σ系统（不抵消）；漏填报单列。
"""
from datetime import date
from app.models import Person, PersonDailyStat, StaffDailyReport
from app.services.daily_report import jst_today
from app.services.daterange import formal_date_range


def points_of(p1: int, p2: int) -> int:
    """**分数（点数）= 1点店数×1 + 2点店数×2** —— 与结算侧 `perf.py` 完全同一口径。"""
    return (p1 or 0) + (p2 or 0) * 2


def p2_rate(p1: int, p2: int):
    """**比例 = 2点店数 ÷ 总店数**（与看板 `p2rate` 同一口径）；没有店 → None。"""
    total = (p1 or 0) + (p2 or 0)
    return ((p2 or 0) / total) if total else None


def _acc(abs_sum: int, sys_sum: int):
    """准确率 = 1 − Σ|Δ| ÷ Σ系统（**用绝对值之和，不抵消**）；无对照日 → None。"""
    if sys_sum > 0:
        return max(0.0, 1 - abs_sum / sys_sum)
    return 1.0 if abs_sum == 0 else 0.0


def compare(db, start, end, person_code: str = "", sort: str = "acc") -> dict:
    """区间对比：人 × 日 三类 + 区间合计 + 准确率 + 排名（程序算，模型不参与）。"""
    from app.models import Person, PersonDailyStat
    sq = (db.query(PersonDailyStat)
          .filter(PersonDailyStat.ref_date >= start, PersonDailyStat.ref_date <= end))
    rq = (db.query(StaffDailyReport)
          .filter(StaffDailyReport.report_date >= start,
                  StaffDailyReport.report_date <= end))
    if person_code:
        sq = sq.filter(PersonDailyStat.person_code == person_code)
        rq = rq.filter(StaffDailyReport.person_code == person_code)
    sys_map, rep_map = {}, {}
    for r in sq.all():
        sys_map[(r.person_code, r.ref_date)] = {
            "p1": r.p1 or 0, "p2": r.p2 or 0, "total": (r.p1 or 0) + (r.p2 or 0)}
    for r in rq.all():
        rep_map[(r.person_code, r.report_date)] = {
            "p1": r.p1_cnt or 0, "p2": r.p2_cnt or 0, "total": r.total_cnt or 0}
    names = dict(db.query(Person.code, Person.display_name).all())

    counts = {KIND_BOTH: 0, KIND_MISSING_REPORT: 0, KIND_MISSING_SYSTEM: 0}
    per, daily = {}, []
    for code, d in sorted(set(sys_map) | set(rep_map), key=lambda k: (k[0], k[1])):
        s, rp = sys_map.get((code, d)), rep_map.get((code, d))
        if s and rp:
            kind = KIND_BOTH
            d1, d2 = s["p1"] - rp["p1"], s["p2"] - rp["p2"]
            dt = s["total"] - rp["total"]
        elif s:
            kind, d1, d2, dt = KIND_MISSING_REPORT, None, None, None
        else:
            kind, d1, d2, dt = KIND_MISSING_SYSTEM, None, None, None
        counts[kind] += 1
        daily.append({
            "person_code": code, "name": names.get(code, code), "date": d, "kind": kind,
            "sys_p1": s["p1"] if s else None, "sys_p2": s["p2"] if s else None,
            "sys_total": s["total"] if s else None,
            "rep_p1": rp["p1"] if rp else None, "rep_p2": rp["p2"] if rp else None,
            "rep_total": rp["total"] if rp else None,
            # 分数（点数）与比例（2点率）：**现算，不落库**（用户 2026-10-02 要求）
            "sys_points": points_of(s["p1"], s["p2"]) if s else None,
            "rep_points": points_of(rp["p1"], rp["p2"]) if rp else None,
            "sys_rate": p2_rate(s["p1"], s["p2"]) if s else None,
            "rep_rate": p2_rate(rp["p1"], rp["p2"]) if rp else None,
            "d1": d1, "d2": d2, "dt": dt,
            # 单日准确率（规格 §7）：max(0, 1 − |Δ总| ÷ max(系统总店数, 1))；只算 both 日
            "acc": (_acc(abs(dt or 0), s["total"]) if (kind == KIND_BOTH and s)
                    else None)})
        p = per.setdefault(code, {
            "person_code": code, "name": names.get(code, code),
            "sys_p1": 0, "sys_p2": 0, "sys_total": 0,
            "rep_p1": 0, "rep_p2": 0, "rep_total": 0,
            "days_system": 0, "days_filled": 0, "days_both": 0,
            "abs_d1": 0, "abs_d2": 0, "abs_dt": 0,
            "both_sys_p1": 0, "both_sys_p2": 0, "both_sys_total": 0,
            "both_consistent": 0,
            "kinds": {KIND_BOTH: 0, KIND_MISSING_REPORT: 0, KIND_MISSING_SYSTEM: 0}})
        p["kinds"][kind] += 1
        if s:
            p["sys_p1"] += s["p1"]; p["sys_p2"] += s["p2"]
            p["sys_total"] += s["total"]; p["days_system"] += 1
        if rp:
            p["rep_p1"] += rp["p1"]; p["rep_p2"] += rp["p2"]
            p["rep_total"] += rp["total"]; p["days_filled"] += 1
        if kind == KIND_BOTH:
            p["days_both"] += 1
            p["abs_d1"] += abs(d1); p["abs_d2"] += abs(d2); p["abs_dt"] += abs(dt)
            p["both_sys_p1"] += s["p1"]; p["both_sys_p2"] += s["p2"]
            p["both_sys_total"] += s["total"]
            if dt == 0 and d1 == 0 and d2 == 0:
                p["both_consistent"] += 1

    persons = list(per.values())
    for p in persons:
        p["sys_points"] = points_of(p["sys_p1"], p["sys_p2"])
        p["rep_points"] = points_of(p["rep_p1"], p["rep_p2"])
        p["d_points"] = p["sys_points"] - p["rep_points"]
        p["sys_rate"] = p2_rate(p["sys_p1"], p["sys_p2"])
        p["rep_rate"] = p2_rate(p["rep_p1"], p["rep_p2"])
        p["acc"] = _acc(p["abs_dt"], p["both_sys_total"]) if p["days_both"] else None
        p["d_total"] = p["sys_total"] - p["rep_total"]          # >0 少报 / <0 多报
        p["d1"] = p["sys_p1"] - p["rep_p1"]
        p["d2"] = p["sys_p2"] - p["rep_p2"]
        # 应填未填 = 系统当天有数据（那天确实在干活）但员工没报的天数。
        # 不能用 days_system - days_filled：员工在系统无数据的日子也报了时会互相抵消。
        p["gaps"] = p["kinds"][KIND_MISSING_REPORT]
    if sort == "abs":
        persons.sort(key=lambda x: (-x["abs_dt"], x["acc"] if x["acc"] is not None else 1))
    else:
        persons.sort(key=lambda x: (x["acc"] if x["acc"] is not None else 1,
                                    -x["abs_dt"], -x["days_both"]))

    matched = counts[KIND_BOTH]
    consistent = sum(p["both_consistent"] for p in persons)
    summary = {
        "checkin_cnt": len(rep_map), "formal_cnt": len(sys_map), "persons": len(persons),
        "matched_cnt": matched, "consistent_cnt": consistent,
        "consistent_rate": (consistent / matched) if matched else None,
        "counts": dict(counts),
        "days_filled": sum(p["days_filled"] for p in persons),
        "days_system": sum(p["days_system"] for p in persons),
    }
    return {"start": start, "end": end, "summary": summary, "persons": persons,
            "daily": daily, "counts": counts, "sort": sort}


def missing_summary(db, start, end, *, limit: int = 60) -> list:
    """漏填汇总：每人 应填天数/实填天数/漏填天数 + 漏填日期（规格 §7「漏填标记」）。

    应填 = 该区间系统侧有记录的天数；漏填 = 系统有数据但员工没报的天数。
    """
    res = compare(db, start, end)
    dates = {}
    for r in res["daily"]:
        if r["kind"] == KIND_MISSING_REPORT:
            dates.setdefault(r["person_code"], []).append(r["date"])
    out = []
    for p in res["persons"]:
        out.append({"person_code": p["person_code"], "name": p["name"],
                    "days_system": p["days_system"], "days_filled": p["days_filled"],
                    "gaps": p["gaps"], "dates": dates.get(p["person_code"], [])})
    out.sort(key=lambda x: (-x["gaps"], x["person_code"]))
    return out[:limit]


def suggest_period(db):
    """默认对比区间 = **最近一次上传的文件**在正式表里的日期范围（无则全量/本月）。"""
    from app.models import ImportFile
    imp = db.query(ImportFile).order_by(ImportFile.id.desc()).first()
    for rng in ((formal_date_range(db, imp.id) if imp is not None else (None, None)),
                formal_date_range(db)):
        if rng[0] and rng[1]:
            return rng[0], rng[1]
    today = jst_today()
    return today.replace(day=1), today


def list_reports(db, *, start=None, end=None, person_code: str = "",
                 page: int = 1, per: int = 50) -> dict:
    """管理端填报列表（分页）。"""
    q = db.query(StaffDailyReport)
    if start:
        q = q.filter(StaffDailyReport.report_date >= start)
    if end:
        q = q.filter(StaffDailyReport.report_date <= end)
    if person_code:
        q = q.filter(StaffDailyReport.person_code == person_code)
    total = q.count()
    page = max(1, int(page or 1))
    rows = (q.order_by(StaffDailyReport.report_date.desc(),
                       StaffDailyReport.person_code)
            .offset((page - 1) * per).limit(per).all())
    return {"rows": [_row_dict(r) for r in rows], "total": total,
            "page": page, "per": per, "pages": max(1, (total + per - 1) // per)}


KIND_BOTH = "both"                    # 两侧都有


KIND_MISSING_REPORT = "missing_report"   # 系统有、员工没报 → 漏填报


KIND_MISSING_SYSTEM = "missing_system"   # 员工报了、系统没有 → 自报无系统记录


def _row_dict(r: StaffDailyReport) -> dict:
    """自报一行（列表/导出共用）：**分数与比例现算，不落库**。"""
    p1, p2 = r.p1_cnt or 0, r.p2_cnt or 0
    return {"id": r.id, "person_code": r.person_code, "date": r.report_date,
            "area": r.area or "", "p1": p1, "p2": p2,
            "total": r.total_cnt, "submitted_at": r.submitted_at,
            "source": r.source,
            "points": points_of(p1, p2), "rate": p2_rate(p1, p2)}
