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
    return {"id": r.id, "date": r.report_date, "area": r.area or "",
            "p1": r.p1_cnt, "p2": r.p2_cnt, "total": r.total_cnt,
            "submitted_at": r.submitted_at}


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


def chart_series(db, person_code: str, *, days: int = CHART_DAYS,
                 today=None) -> dict:
    """最近 N 天（含今天）的自报序列：缺的天标 filled=False。

    `show` = 是否值得画（已填报天数 >= CHART_MIN_FILLED）。
    """
    today = today or jst_today()
    start = today - timedelta(days=days - 1)
    rows = {r.report_date: r for r in db.query(StaffDailyReport).filter(
        StaffDailyReport.person_code == person_code,
        StaffDailyReport.report_date >= start,
        StaffDailyReport.report_date <= today).all()}
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
            "p1": sum(x["p1"] for x in pts), "p2": sum(x["p2"] for x in pts),
            "total": sum(x["total"] for x in pts),
            "max": max(vals) if vals else 0,
            "min_filled": CHART_MIN_FILLED,
            "show": filled >= CHART_MIN_FILLED}


def chart_geometry(series: dict, *, width: int = 320, height: int = 120,
                   pad_x: int = 10, pad_y: int = 12) -> dict:
    """把序列转成 SVG 几何：两条线各自分段（**缺数据处断开**）+ 每个有数据日的点。"""
    days = series["days"]
    n = len(days)
    mx = max(1, series["max"] or 1)
    step = (width - 2 * pad_x) / max(n - 1, 1)
    span = height - 2 * pad_y

    def xy(i, v):
        return (pad_x + i * step, height - pad_y - (v / mx) * span)

    out = {"width": width, "height": height, "pad_x": pad_x, "pad_y": pad_y,
           "max": mx, "grid": pad_y, "base": height - pad_y,
           "x_first": str(days[0]["date"]) if days else "",
           "x_last": str(days[-1]["date"]) if days else ""}
    for key in ("p1", "p2"):
        segs, dots, cur = [], [], []
        for i, d in enumerate(days):
            if d["filled"]:
                x, y = xy(i, d[key])
                cur.append("%.1f,%.1f" % (x, y))
                dots.append({"x": "%.1f" % x, "y": "%.1f" % y, "v": d[key]})
            else:
                if len(cur) > 1:
                    segs.append(" ".join(cur))
                cur = []
        if len(cur) > 1:
            segs.append(" ".join(cur))
        out["segments_" + key] = segs
        out["dots_" + key] = dots
    return out


def month_days(db, person_code: str, month: str = "", *,
               today=None) -> dict:
    """**本月逐日视图**：1 号到「今天或月末」，**缺填报的日子留空行**。

    员工要能看到"哪天没有数据"，所以这里不做"只列有记录的天"。
    返回：{month, days:[{date, wd, empty, area, p1, p2, total}], filled,
          visible_days, p1, p2, total}
    """
    today = today or jst_today()
    if not month:
        month = today.strftime("%Y-%m")
    y, m = (int(x) for x in month.split("-"))
    start = date(y, m, 1)
    nxt = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    last_visible = min(nxt - timedelta(days=1), today)
    rows = {}
    if last_visible >= start:
        rows = {r.report_date: r for r in db.query(StaffDailyReport).filter(
            StaffDailyReport.person_code == person_code,
            StaffDailyReport.report_date >= start,
            StaffDailyReport.report_date <= last_visible).all()}
    days, filled = [], 0
    d = start
    while d <= last_visible:
        r = rows.get(d)
        if r is not None:
            filled += 1
            days.append({"date": d, "wd": d.weekday(), "empty": False,
                         "area": r.area or "", "p1": r.p1_cnt, "p2": r.p2_cnt,
                         "total": r.total_cnt, "submitted_at": r.submitted_at})
        else:
            days.append({"date": d, "wd": d.weekday(), "empty": True,
                         "area": "", "p1": 0, "p2": 0, "total": 0,
                         "submitted_at": None})
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
