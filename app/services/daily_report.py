# -*- coding: utf-8 -*-
"""员工每日填报：写入/校验/查询（规格 v7 §6.1，Chunk 3）。

- 一天一条（`(person_code, report_date)` 唯一）；重复提交 → `AlreadySubmitted`
- 业务日 = **JST**（固定 +09:00，不用 zoneinfo，避免 slim 镜像缺 tzdata）
- 填报数据**不参与工资计算**；对比计算见同模块 `compare()`（Chunk 4）
"""
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy.exc import IntegrityError

from app.models import StaffDailyReport

JST = timezone(timedelta(hours=9))
MAX_CNT = 999


class AlreadySubmitted(Exception):
    """今天已经填报过了。"""

    def __init__(self, row: StaffDailyReport):
        super().__init__("today already reported")
        self.row = row


def jst_today() -> date:
    """日本时区的今天（业务日）。"""
    return datetime.now(JST).date()


def to_count(value, field="数量") -> int:
    """把表单值转成 0..999 的整数；非法 → ValueError（带可展示的文案）。"""
    s = str(value if value is not None else "").strip()
    if s == "":
        return 0
    if not s.isdigit():
        raise ValueError("%s 必须是不小于 0 的整数" % field)
    n = int(s)
    if n > MAX_CNT:
        raise ValueError("%s 不能超过 %d" % (field, MAX_CNT))
    return n


def today_report(db, person_code: str) -> Optional[StaffDailyReport]:
    return (db.query(StaffDailyReport)
            .filter(StaffDailyReport.person_code == person_code,
                    StaffDailyReport.report_date == jst_today())
            .first())


def submit_report(db, user, *, area: str = "", p1_cnt=0, p2_cnt=0,
                  client_ts: str = "") -> StaffDailyReport:
    """提交今天的填报（一天一次）。"""
    code = getattr(user, "person_code", None)
    if not code:
        raise ValueError("账号未绑定员工编号，无法填报")
    p1 = to_count(p1_cnt, "1点店铺数")
    p2 = to_count(p2_cnt, "2点店铺数")
    today = jst_today()
    if today_report(db, code) is not None:
        raise AlreadySubmitted(today_report(db, code))
    row = StaffDailyReport(person_code=code, user_id=getattr(user, "id", None),
                           report_date=today, area=(area or "").strip()[:64],
                           p1_cnt=p1, p2_cnt=p2, total_cnt=p1 + p2,
                           client_ts=(client_ts or "")[:40])
    db.add(row)
    try:
        db.commit()
    except IntegrityError:            # 并发重复提交 → 交给唯一约束兜底
        db.rollback()
        raise AlreadySubmitted(today_report(db, code))
    return row


def _row_dict(r: StaffDailyReport) -> dict:
    return {"id": r.id, "person_code": r.person_code, "date": r.report_date,
            "area": r.area or "", "p1": r.p1_cnt, "p2": r.p2_cnt,
            "total": r.total_cnt, "submitted_at": r.submitted_at,
            "source": r.source}


def my_reports(db, person_code: str, *, month: str = "", limit: int = 60) -> list:
    """我的填报历史（倒序）。month='YYYY-MM' 时只取该月。"""
    q = db.query(StaffDailyReport).filter(
        StaffDailyReport.person_code == person_code)
    if month:
        y, m = month.split("-")
        start = date(int(y), int(m), 1)
        end = date(int(y) + (1 if int(m) == 12 else 0),
                   1 if int(m) == 12 else int(m) + 1, 1)
        q = q.filter(StaffDailyReport.report_date >= start,
                     StaffDailyReport.report_date < end)
    rows = q.order_by(StaffDailyReport.report_date.desc()).limit(limit).all()
    return [_row_dict(r) for r in rows]


CHART_DAYS = 30          # 趋势窗口
CHART_MIN_FILLED = 3     # 少于这么多天就不给图（规格：太少了不给）
CHART_MIN_SPAN = 7       # 横轴最少铺开的天数（数据太少时避免一条陡线）


def chart_series(db, person_code: str, *, days: int = CHART_DAYS,
                 today=None, min_span: int = CHART_MIN_SPAN) -> dict:
    """最近 N 天（含今天）的自报序列：缺的天标 filled=False。

    **横轴自适应**：固定 30 天会把"刚开始填报的人"全挤到右边，所以窗口右端固定为今天、
    左端取「第一个有填报的日子」与「今天-(min_span-1)」中更早者，且不超过 N 天上限。
    这样稀疏数据也能铺满图宽；中间的缺口仍然断开显示。
    """
    today = today or jst_today()
    hard_start = today - timedelta(days=days - 1)
    rows = {r.report_date: r for r in db.query(StaffDailyReport).filter(
        StaffDailyReport.person_code == person_code,
        StaffDailyReport.report_date >= hard_start,
        StaffDailyReport.report_date <= today).all()}
    if rows:
        start = min(min(rows), today - timedelta(days=min_span - 1))
        start = max(start, hard_start)
    else:
        start = max(today - timedelta(days=min_span - 1), hard_start)
    pts, d = [], start
    while d <= today:
        r = rows.get(d)
        pts.append({"date": d, "filled": r is not None,
                    "p1": r.p1_cnt if r else 0, "p2": r.p2_cnt if r else 0,
                    "total": r.total_cnt if r else 0})
        d += timedelta(days=1)
    filled = sum(1 for x in pts if x["filled"])
    vals = [x[k] for x in pts for k in ("p1", "p2")]
    return {"days": pts, "filled": filled, "start": start, "end": today,
            "span_days": len(pts), "window_days": days,
            "p1": sum(x["p1"] for x in pts), "p2": sum(x["p2"] for x in pts),
            "total": sum(x["total"] for x in pts),
            "max": max(vals) if vals else 0,
            "min_filled": CHART_MIN_FILLED,
            "show": filled >= CHART_MIN_FILLED}


_Y_STEPS = (2, 4, 6, 8, 10, 12, 16, 20, 24, 30, 40, 50, 60, 80,
            100, 150, 200, 300, 500, 1000)


def _nice_ceiling(v: int) -> int:
    """把最大值抬到"好看的整数刻度上限"（例：7→8、9→10、30→30）。"""
    v = max(1, int(v or 0))
    for c in _Y_STEPS:
        if c >= v:
            return c
    return v


def _smooth(pts, tension: float = 0.32):
    """把折线点转成平滑曲线路径（Cardinal 样条 → 三次贝塞尔），控制点夹在绘图区内。"""
    if len(pts) < 2:
        return ""
    d = "M%.1f,%.1f" % pts[0]
    for i in range(len(pts) - 1):
        p0 = pts[i - 1] if i > 0 else pts[i]
        p1, p2 = pts[i], pts[i + 1]
        p3 = pts[i + 2] if i + 2 < len(pts) else p2
        c1 = (p1[0] + (p2[0] - p0[0]) * tension / 2,
              p1[1] + (p2[1] - p0[1]) * tension / 2)
        c2 = (p2[0] - (p3[0] - p1[0]) * tension / 2,
              p2[1] - (p3[1] - p1[1]) * tension / 2)
        d += " C%.1f,%.1f %.1f,%.1f %.1f,%.1f" % (c1[0], c1[1], c2[0], c2[1],
                                                  p2[0], p2[1])
    return d


def chart_geometry(series: dict, *, width: int = 360, height: int = 176,
                   pad_l: int = 30, pad_r: int = 12, pad_t: int = 14,
                   pad_b: int = 26) -> dict:
    """把序列转成 SVG 几何（供模板直接渲染）：

    - 纵轴：好看的上限 + 3 条刻度线（0 / 中 / 顶）与数值标签
    - 横轴：最多 4 个日期标签（MM-DD）
    - 两条曲线：**按连续段**平滑连线（缺数据处断开）+ 段内面积填充
    - 每个有数据的日子一个圆点（`<title>` 里带数值，鼠标悬停可见）
    - "今天"竖虚线
    """
    days = series["days"]
    n = len(days)
    if not n:
        return {"width": width, "height": height, "empty": True}
    top = _nice_ceiling(series["max"])
    plot_w = width - pad_l - pad_r
    plot_h = height - pad_t - pad_b
    step_x = plot_w / max(n - 1, 1)

    def x_of(i):
        return pad_l + i * step_x

    def y_of(v):
        return pad_t + plot_h - (min(max(v, 0), top) / top) * plot_h

    geo = {"width": width, "height": height, "empty": False,
           "pad_l": pad_l, "pad_r": pad_r, "pad_t": pad_t, "pad_b": pad_b,
           "plot_w": plot_w, "plot_h": plot_h, "top": top,
           "top_y": round(y_of(top), 1),
           "mid_y": round(y_of(top / 2), 1), "mid_v": top // 2,
           "base_y": round(y_of(0), 1), "span_days": n,
           "today_x": round(x_of(n - 1), 1),
           "ticks": [{"y": round(y_of(top), 1), "v": top},
                     {"y": round(y_of(top / 2), 1), "v": top // 2},
                     {"y": round(y_of(0), 1), "v": 0}],
           "x_ticks": []}
    idxs = sorted({0, n // 3, (2 * n) // 3, n - 1})
    for i in idxs:
        geo["x_ticks"].append({"x": round(x_of(i), 1),
                               "label": str(days[i]["date"])[5:]})
    for key in ("p1", "p2"):
        paths, areas, dots = [], [], []
        run = []
        for i, d in enumerate(days):
            if d["filled"]:
                run.append((round(x_of(i), 1), round(y_of(d[key]), 1)))
                dots.append({"x": round(x_of(i), 1), "y": round(y_of(d[key]), 1),
                             "v": d[key], "date": str(d["date"])})
            else:
                if run:
                    paths.append(_smooth(run))
                    areas.append(_smooth(run) + " L%.1f,%.1f L%.1f,%.1f Z"
                                 % (run[-1][0], geo["base_y"], run[0][0],
                                    geo["base_y"]))
                run = []
        if run:
            paths.append(_smooth(run))
            areas.append(_smooth(run) + " L%.1f,%.1f L%.1f,%.1f Z"
                         % (run[-1][0], geo["base_y"], run[0][0], geo["base_y"]))
        geo["paths_" + key] = [p for p in paths if p]
        geo["areas_" + key] = [a for a in areas if a]
        geo["dots_" + key] = dots
    return geo


def month_days(db, person_code: str, month: str = "", *,
               today=None) -> dict:
    """**本月逐日视图**：1 号到「今天或月末」，**缺填报的日子留空行**。

    员工要能看到"哪天没有数据"，所以这里不做"只列有记录的天"。
    返回：{month, days:[{date, wd, empty, future, area, p1, p2, total}], filled,
          visible_days(整月天数), p1, p2, total}
    """
    today = today or jst_today()
    if not month:
        month = today.strftime("%Y-%m")
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    month_end = nxt - timedelta(days=1)
    if start > today:
        # 未开始的月份：不列一屏"未到"，直接空态
        return {"month": month, "days": [], "filled": 0, "visible_days": 0,
                "p1": 0, "p2": 0, "total": 0}
    rows = {r.report_date: r for r in db.query(StaffDailyReport).filter(
        StaffDailyReport.person_code == person_code,
        StaffDailyReport.report_date >= start,
        StaffDailyReport.report_date <= month_end).all()}
    days, filled = [], 0
    d = start
    while d <= month_end:                     # **整月**列出：未来日子标 future（未到）
        r = rows.get(d)
        future = d > today
        if r is not None:
            filled += 1
            days.append({"date": d, "wd": d.weekday(), "empty": False,
                         "future": future, "area": r.area or "", "p1": r.p1_cnt,
                         "p2": r.p2_cnt, "total": r.total_cnt,
                         "submitted_at": r.submitted_at})
        else:
            days.append({"date": d, "wd": d.weekday(), "empty": True,
                         "future": future, "area": "", "p1": 0, "p2": 0,
                         "total": 0, "submitted_at": None})
        d += timedelta(days=1)
    return {"month": month, "days": days, "filled": filled,
            "visible_days": len(days),
            "p1": sum(x["p1"] for x in days),
            "p2": sum(x["p2"] for x in days),
            "total": sum(x["total"] for x in days)}


def my_months(db, person_code: str) -> list:
    """我填报过的月份（倒序），用于表单默认月份。"""
    rows = (db.query(StaffDailyReport.report_date)
            .filter(StaffDailyReport.person_code == person_code).all())
    return sorted({str(r[0])[:7] for r in rows if r[0]}, reverse=True)


# ---------- 对比与准确率（规格 v7 §7；纯读 person_daily_stats） ----------

KIND_BOTH = "both"                    # 两侧都有
KIND_MISSING_REPORT = "missing_report"   # 系统有、员工没报 → 漏填报
KIND_MISSING_SYSTEM = "missing_system"   # 员工报了、系统没有 → 自报无系统记录


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
            "d1": d1, "d2": d2, "dt": dt})
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
        p["acc"] = _acc(p["abs_dt"], p["both_sys_total"]) if p["days_both"] else None
        p["acc1"] = _acc(p["abs_d1"], p["both_sys_p1"]) if p["days_both"] else None
        p["acc2"] = _acc(p["abs_d2"], p["both_sys_p2"]) if p["days_both"] else None
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


def suggest_period(db):
    """默认对比区间 = **最近一次上传的文件**在正式表里的日期范围（无则全量/本月）。"""
    from sqlalchemy import func

    from app.models import FormalRecord, ImportFile
    rng = db.query(func.min(FormalRecord.japan_date),
                   func.max(FormalRecord.japan_date))
    imp = db.query(ImportFile).order_by(ImportFile.id.desc()).first()
    for q in ((rng.filter(FormalRecord.import_id == imp.id) if imp is not None else None),
              rng):
        if q is None:
            continue
        row = q.first()
        if row and row[0] and row[1]:
            return row[0], row[1]
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
