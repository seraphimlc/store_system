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
from app.services.daterange import month_bounds

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


class NoReport(Exception):
    """今天还没有填报记录（无法修改）。"""


def get_report(db, person_code: str, report_date) -> Optional[StaffDailyReport]:
    """按 (员工编号, 日期) 取单条填报（管理员补录/回填用）。"""
    if not person_code or report_date is None:
        return None
    return (db.query(StaffDailyReport)
            .filter(StaffDailyReport.person_code == person_code,
                    StaffDailyReport.report_date == report_date).first())


def update_today(db, user, *, area: str = "", p1_cnt=0, p2_cnt=0) -> StaffDailyReport:
    """修改**今天**已提交的填报（跨天不允许：那是补录，本期不做）。"""
    code = getattr(user, "person_code", None)
    if not code:
        raise ValueError("账号未绑定员工编号，无法填报")
    row = today_report(db, code)
    if row is None:
        raise NoReport(code)
    if is_locked(db, row.report_date):
        raise Locked(row.report_date)         # 今天的数据已对账 → 不许再改
    row.area = (area or "").strip()[:64]
    row.p1_cnt = to_count(p1_cnt, "1点店铺数")
    row.p2_cnt = to_count(p2_cnt, "2点店铺数")
    row.total_cnt = row.p1_cnt + row.p2_cnt
    db.commit()
    _refresh_materialized(db, row.report_date)      # 数据变了 → 刷新核对页物化行
    return row


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
    _refresh_materialized(db, today)  # 数据变了 → 刷新核对页物化行
    return row


class Locked(Exception):
    """该日期已对账（系统侧正式数据已覆盖）→ 自报锁定，不能再改。"""

    def __init__(self, ref_date):
        self.ref_date = ref_date
        super().__init__("该日期已有系统数据（已对账），不能再改")


def coverage_end(db):
    """正式数据覆盖的最后一天；还没有任何正式数据 → None。"""
    from app.services.daterange import formal_date_range
    return formal_date_range(db)[1]


def is_locked(db, ref_date) -> bool:
    """该日期是否已锁定（= 已对账）。

    规则（2026-09-28 用户明确）：一旦系统侧正式数据覆盖到某天（文件已导入该天，
    对比结果也已物化），"该天及之前"的自报就不能再改；之后的日期仍可补录/修改。
    """
    if ref_date is None:
        return False
    end = coverage_end(db)
    return end is not None and ref_date <= end


def save_by_admin(db, *, person_code: str, report_date, area: str = "",
                  p1_cnt=0, p2_cnt=0, user_id=None) -> StaffDailyReport:
    """管理员补录或修改**未对账**日期的自报（不存在则新建，存在则覆盖）。"""
    from app.models import Person
    code = (person_code or "").strip()
    if not code:
        raise ValueError("请选择员工")
    if report_date is None:
        raise ValueError("请选择日期")
    if db.get(Person, code) is None:
        raise ValueError("员工编号不存在")
    if is_locked(db, report_date):
        raise Locked(report_date)
    p1 = to_count(p1_cnt, "1点店铺数")
    p2 = to_count(p2_cnt, "2点店铺数")
    row = (db.query(StaffDailyReport)
           .filter(StaffDailyReport.person_code == code,
                   StaffDailyReport.report_date == report_date).first())
    if row is None:
        row = StaffDailyReport(person_code=code, user_id=user_id,
                               report_date=report_date,
                               area=(area or "").strip()[:64],
                               p1_cnt=p1, p2_cnt=p2, total_cnt=p1 + p2,
                               source="admin")
        db.add(row)
    else:
        row.area = (area or "").strip()[:64]
        row.p1_cnt = p1
        row.p2_cnt = p2
        row.total_cnt = p1 + p2
        row.source = "admin"
    try:
        db.commit()
    except IntegrityError:            # 并发补同一天 → 唯一约束兜底
        db.rollback()
        raise AlreadySubmitted(today_report(db, code))
    _refresh_materialized(db, report_date)
    return row


def _refresh_materialized(db, ref_date) -> None:
    from app.services import report_store
    """刷新覆盖该日期的已完成报告的物化行（best-effort，失败不影响填报）。

    没有这一步，员工端核对页会一直显示"报告生成那一刻"的旧数字，
    与管理端实时对比结果不一致（2026-09-28 评审发现的真实错数）。
    """
    try:
            report_store.refresh_for_date(db, ref_date)
    except Exception:  # noqa: BLE001  刷新失败不影响填报本身
        db.rollback()


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
    start, nxt = month_bounds(month)
    month_end = nxt - timedelta(days=1)
    # **没到的日子不显示**：只列到"今天"（未来日期不出现，也不标"未到"）
    last_visible = min(month_end, today)
    if last_visible < start:
        return {"month": month, "days": [], "filled": 0, "visible_days": 0,
                "p1": 0, "p2": 0, "total": 0}
    rows = {r.report_date: r for r in db.query(StaffDailyReport).filter(
        StaffDailyReport.person_code == person_code,
        StaffDailyReport.report_date >= start,
        StaffDailyReport.report_date <= last_visible).all()}
    days, filled = [], 0
    d = start
    while d <= last_visible:
        r = rows.get(d)
        future = False
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
