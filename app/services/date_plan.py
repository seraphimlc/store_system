# -*- coding: utf-8 -*-
"""日期计划（半月出勤登记）：规格 `docs/specs-date-plan.md`。

口径（与用户确认，2026-10-01）：

- **自然半月**：上半月 `1–15`（H1，截止 3 号）/ 下半月 `16–月末`（H2，截止 18 号）；
- **默认每天都出勤**：只把"不出勤"的日期落库（`available=False`）；
- **三态**：`○` 计划可出勤 / `×` 计划不出勤 / `□` 已出勤（已自报）；未登记 = `–`；
- **实际出勤以自报为准**：当天有每日填报（`staff_daily_reports`）→ 一律显示 `□`（覆盖计划值）；
- **锁定**：已过去的日期、已有自报的日期不可再改（逾期只提示，不拦）；
- 业务日 = **JST**（`daily_report.jst_today()`）。
"""
from calendar import monthrange
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Set, Tuple

from sqlalchemy.exc import IntegrityError

from app.models import Person, StaffDailyReport, StaffDatePlan, User
from app.services.daily_report import jst_today

H1 = "H1"           # 上半月 1–15
H2 = "H2"           # 下半月 16–月末
HALVES = (H1, H2)
SPLIT_DAY = 15      # ≤15 归上半月
DEADLINE_DAY = {H1: 3, H2: 18}   # 登记截止日（用户口径：3 号 / 18 号前）

STATE_ON = "on"      # ○ 计划可出勤（默认）
STATE_OFF = "off"    # × 计划不出勤
STATE_DONE = "done"  # □ 已出勤（已自报）
STATE_NONE = "none"  # – 未登记
MARKS = {STATE_ON: "○", STATE_OFF: "×", STATE_DONE: "□", STATE_NONE: "–"}

LOCK_NONE = ""
LOCK_PAST = "past"           # 日期已过去
LOCK_REPORTED = "reported"   # 当天已有自报

WD_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]


class AllLocked(Exception):
    """该半月的日期都已过去或已自报 → 没有可登记的日期。"""

    def __init__(self, key: str):
        self.key = key
        super().__init__("该半月的日期都已过去或已自报，无需再登记")


# ---------------- 半月划分 ----------------

def period_of(d: date) -> str:
    """日期 → 半月键（'YYYY-MM-H1' / 'YYYY-MM-H2'）。"""
    return "%04d-%02d-%s" % (d.year, d.month, H1 if d.day <= SPLIT_DAY else H2)


def parse_period(key: str) -> Tuple[int, int, str]:
    """'YYYY-MM-H1/H2' → (年, 月, 半月)；非法 → ValueError。"""
    parts = (key or "").strip().upper().split("-")
    if len(parts) != 3 or parts[2] not in HALVES:
        raise ValueError("半月格式应为 YYYY-MM-H1 或 YYYY-MM-H2")
    try:
        y, m = int(parts[0]), int(parts[1])
        date(y, m, 1)
    except ValueError:
        raise ValueError("半月格式应为 YYYY-MM-H1 或 YYYY-MM-H2")
    return y, m, parts[2]


def is_period(key: str) -> bool:
    try:
        parse_period(key)
        return True
    except ValueError:
        return False


def period_bounds(key: str) -> Tuple[date, date, date]:
    """(首日, 末日, 登记截止日)。"""
    y, m, half = parse_period(key)
    last = monthrange(y, m)[1]
    if half == H1:
        return date(y, m, 1), date(y, m, SPLIT_DAY), date(y, m, DEADLINE_DAY[H1])
    return date(y, m, SPLIT_DAY + 1), date(y, m, last), date(y, m, DEADLINE_DAY[H2])


def shift_period(key: str, halves: int = 0, months: int = 0) -> str:
    """前后移动若干半月（key 必须合法）。

    索引把"月"也编码进去（`(年*12+月)*2+半月`）——**只按 `年*2+半月` 编码会漏掉月份进位**
    （2026-12-H2 往后一期算成 2027-12-H1，测试抓到过）。
    """
    y, m, half = parse_period(key)
    idx = ((y * 12 + (m - 1)) * 2 + (0 if half == H1 else 1)
           + halves + months * 2)
    mi, h = divmod(idx, 2)
    y2, m0 = divmod(mi, 12)
    return "%04d-%02d-%s" % (y2, m0 + 1, HALVES[h])


def period_label(key: str) -> str:
    """中文标签（管理端/导出用）：'2026-10 上半月（1–15）'。"""
    start, end, _ = period_bounds(key)
    half = "上半月" if (key or "").strip().upper().endswith(H1) else "下半月"
    return "%s %s（%d–%d）" % (key[:7], half, start.day, end.day)


def current_period(today=None) -> str:
    return period_of(today or jst_today())


def period_options(today=None, back: int = 2, fwd: int = 1) -> List[dict]:
    """可选半月列表（默认含上两期、本期、下一期）：本期可提前登记下一期。"""
    today = today or jst_today()
    cur = current_period(today)
    out = []
    for i in range(-back, fwd + 1):
        key = shift_period(cur, halves=i)
        s, e, dl = period_bounds(key)
        out.append({"key": key, "start": s, "end": e, "deadline": dl,
                    "half": H1 if key.endswith(H1) else H2,
                    "current": key == cur,
                    "overdue": today > dl,
                    "label": period_label(key)})
    return out


def period_days(key: str) -> List[date]:
    start, end, _ = period_bounds(key)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ---------------- 三态与锁定 ----------------

def cell_state(plan_available: Optional[bool], reported: bool) -> str:
    """三态口径（唯一来源）：**已自报 > 计划**；没有计划行 = 未登记。"""
    if reported:
        return STATE_DONE
    if plan_available is None:
        return STATE_NONE
    return STATE_ON if plan_available else STATE_OFF


def lock_of(d: date, today: date, reported: bool) -> str:
    """锁定原因：已自报 → reported；已过去 → past；否则可改（''）。"""
    if reported:
        return LOCK_REPORTED
    if d < today:
        return LOCK_PAST
    return LOCK_NONE


def _reported_dates(db, person_code: str, start: date, end: date) -> Set[date]:
    rows = (db.query(StaffDailyReport.report_date)
            .filter(StaffDailyReport.person_code == person_code,
                    StaffDailyReport.report_date >= start,
                    StaffDailyReport.report_date <= end).all())
    return {r[0] for r in rows}


def _plan_map(db, person_code: str, start: date, end: date) -> Dict[date, bool]:
    rows = (db.query(StaffDatePlan.plan_date, StaffDatePlan.available)
            .filter(StaffDatePlan.person_code == person_code,
                    StaffDatePlan.plan_date >= start,
                    StaffDatePlan.plan_date <= end).all())
    return {d: bool(a) for d, a in rows}


def is_locked(db, person_code: str, d: date, today=None) -> Optional[str]:
    """该日期是否锁定（返回原因 'reported'/'past'，未锁定 → None）。"""
    today = today or jst_today()
    rep = (db.query(StaffDailyReport.id)
           .filter(StaffDailyReport.person_code == person_code,
                   StaffDailyReport.report_date == d).first()) is not None
    return lock_of(d, today, rep) or None


def is_submitted(db, person_code: str, key: str) -> bool:
    """该半月是否登记过（有任意一行计划即算登记）。"""
    start, end, _ = period_bounds(key)
    return (db.query(StaffDatePlan.id)
            .filter(StaffDatePlan.person_code == person_code,
                    StaffDatePlan.plan_date >= start,
                    StaffDatePlan.plan_date <= end).first()) is not None


# ---------------- 员工端视图与提交 ----------------

def plan_days(db, person_code: str, key: str, today=None) -> dict:
    """员工端逐日视图：三态 + 能否修改 + 锁定原因 + 统计。

    员工端与管理端显示口径有**一处有意差别**：管理端把"没登记"显示成 `–`
    （不能把没登记当成可出勤），而员工端是"默认每天都出勤"，**可改且未登记**的日子
    按默认值显示 `○`（否则员工会以为那天是空白的）。三态判定本身同为 `cell_state()`。
    """
    today = today or jst_today()
    start, end, deadline = period_bounds(key)
    plans = _plan_map(db, person_code, start, end)
    reps = _reported_dates(db, person_code, start, end)
    days = []
    d = start
    while d <= end:
        rep = d in reps
        state = cell_state(plans.get(d), rep)
        lock = lock_of(d, today, rep)
        editable = lock == LOCK_NONE
        off = (d in plans) and not plans[d]
        mark = MARKS[STATE_ON] if (editable and state == STATE_NONE) else MARKS[state]
        days.append({"date": d, "wd": d.weekday(), "state": state, "mark": mark,
                     "reported": rep, "off": off, "available": not off,
                     "editable": editable, "lock": lock})
        d += timedelta(days=1)
    stamps = (db.query(StaffDatePlan.updated_at, StaffDatePlan.created_at)
              .filter(StaffDatePlan.person_code == person_code,
                      StaffDatePlan.plan_date >= start,
                      StaffDatePlan.plan_date <= end).all())
    last = max([(r[0] or r[1]) for r in stamps], default=None)
    return {"key": key, "start": start, "end": end, "deadline": deadline,
            "label": period_label(key), "today": today,
            "overdue": today > deadline,
            "submitted": bool(plans), "last_at": last,
            "days": days,
            "off_cnt": sum(1 for x in days if x["off"]),
            "free_cnt": sum(1 for x in days if not x["off"]),
            "done_cnt": sum(1 for x in days if x["reported"]),
            "editable_cnt": sum(1 for x in days if x["editable"])}


def _parse_dates(values: Iterable, start: date, end: date) -> Set[date]:
    """表单多值 → 日期集合（去重）；越界/非法一律报错，不静默丢。"""
    out: Set[date] = set()
    for v in values or ():
        s = str(v or "").strip()
        if not s:
            continue
        try:
            d = datetime.strptime(s, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError("日期格式应为 YYYY-MM-DD：%s" % s[:20])
        if d < start or d > end:
            raise ValueError("日期超出该半月范围：%s" % d)
        out.add(d)
    return out


def save_plan(db, user, key: str, unavailable: Iterable = (), today=None,
              source: str = "web") -> dict:
    """提交/修改某半月的出勤计划。

    **只写未锁定的日期**：已过去、已自报的日期既不新增也不改写（用户明确"历史事实不覆盖"）。
    表单只提交"不出勤"的日期集合，其余日期落 `available=True`（默认每天都出勤）。
    """
    code = getattr(user, "person_code", None)
    if not code:
        raise ValueError("账号未绑定员工编号，无法登记出勤计划")
    today = today or jst_today()
    start, end, _ = period_bounds(key)
    off = _parse_dates(unavailable, start, end)
    reps = _reported_dates(db, code, start, end)
    existing = {r.plan_date: r for r in db.query(StaffDatePlan).filter(
        StaffDatePlan.person_code == code,
        StaffDatePlan.plan_date >= start,
        StaffDatePlan.plan_date <= end).all()}
    written = skipped = 0
    for d in period_days(key):
        if lock_of(d, today, d in reps):
            skipped += 1
            continue
        val = d not in off
        row = existing.get(d)
        if row is None:
            db.add(StaffDatePlan(person_code=code, plan_date=d,
                                 available=val, source=source))
        else:
            row.available = val
            row.source = source
        written += 1
    if not written:
        db.rollback()
        raise AllLocked(key)
    try:
        db.commit()
    except IntegrityError:          # 并发双击 → 唯一约束兜底
        db.rollback()
        raise ValueError("保存冲突，请刷新页面重试")
    return {"key": key, "written": written, "skipped": skipped,
            "off_cnt": len([d for d in period_days(key)
                            if d in off and not lock_of(d, today, d in reps)])}


# ---------------- 管理端矩阵与导出 ----------------

def _matrix_people(db) -> List[dict]:
    """矩阵里的"员工"= 有账号的在岗/请假员工 ∪ 本期有登记记录的人。

    停用/离职员工的账号进不来（`can_login=False`），但**只要本期登记过就仍然显示**，
    否则他们的登记会被静默吞掉（数据在库里却没人看得见）。
    """
    users = db.query(User).filter(User.role == "staff").all()
    names = dict(db.query(Person.code, Person.display_name).all())
    cand: Dict[str, dict] = {}
    for u in users:
        code = (u.person_code or "").strip()
        if not code:
            continue
        e = cand.setdefault(code, {"person_code": code, "name": "",
                                   "can_login": False, "user_id": u.id})
        e["can_login"] = e["can_login"] or bool(u.can_login)
        if not e["name"]:
            e["name"] = names.get(code) or u.display_name or code
    return cand


def admin_matrix(db, key: str, today=None) -> dict:
    """管理端矩阵：行=员工，列=日期（三态），另给未提交标记与按日可出勤小计。"""
    today = today or jst_today()
    start, end, deadline = period_bounds(key)
    days = period_days(key)
    plan_rows = (db.query(StaffDatePlan.person_code, StaffDatePlan.plan_date,
                          StaffDatePlan.available)
                 .filter(StaffDatePlan.plan_date >= start,
                         StaffDatePlan.plan_date <= end).all())
    plan_map = {(c, d): bool(a) for c, d, a in plan_rows}
    rep_rows = (db.query(StaffDailyReport.person_code, StaffDailyReport.report_date)
                .filter(StaffDailyReport.report_date >= start,
                        StaffDailyReport.report_date <= end).all())
    rep_set = {(c, d) for c, d in rep_rows}
    with_plan = {c for c, _ in plan_map}

    cand = _matrix_people(db)
    for code in sorted(with_plan - set(cand)):
        cand[code] = {"person_code": code, "name": "", "can_login": False,
                      "user_id": None}
    codes = sorted((c for c, e in cand.items()
                    if e["can_login"] or c in with_plan),
                   key=lambda c: (cand[c]["name"] or c, c))

    rows = []
    col_free = {d: 0 for d in days}
    col_none = {d: 0 for d in days}
    for code in codes:
        states = {d: cell_state(plan_map.get((code, d)), (code, d) in rep_set)
                  for d in days}
        for d in days:
            if states[d] in (STATE_ON, STATE_DONE):
                col_free[d] += 1
            elif states[d] == STATE_NONE:
                col_none[d] += 1
        rows.append({
            "person_code": code, "name": cand[code]["name"] or code,
            "states": states,
            "marks": {d: MARKS[s] for d, s in states.items()},
            "submitted": code in with_plan,
            "on_cnt": sum(1 for d in days if states[d] == STATE_ON),
            "off_cnt": sum(1 for d in days if states[d] == STATE_OFF),
            "done_cnt": sum(1 for d in days if states[d] == STATE_DONE),
        })
    past = {d: d < today for d in days}
    # 按日小计只算"今天及以后"：已过去的日子不构成可用人力
    free_cnt = {d: (0 if past[d] else col_free[d]) for d in days}
    none_cnt = {d: col_none[d] for d in days}
    return {"key": key, "start": start, "end": end, "deadline": deadline,
            "label": period_label(key), "today": today, "overdue": today > deadline,
            "days": days, "past": past, "rows": rows,
            "free_cnt": free_cnt, "none_cnt": none_cnt,
            "unsubmitted": [r["person_code"] for r in rows if not r["submitted"]],
            "total": len(rows)}


def plan_xlsx(db, key: str, today=None):
    """管理端导出（员工 × 日期，符号与页面一致）。返回 (xlsx 字节, 文件名)。

    用 `Workbook(write_only=True)` 流式写（与 report_export 同一写法）。
    """
    import io

    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    m = admin_matrix(db, key, today)
    days = m["days"]
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=(key[:31] or "plan"))
    head = []
    for i, h in enumerate(["员工编号", "姓名", "登记"] +
                          [d.strftime("%m-%d") for d in days], 1):
        c = WriteOnlyCell(ws, value=h)
        c.font = Font(bold=True)
        head.append(c)
        ws.column_dimensions[c.column_letter].width = 12 if i <= 3 else 7
    ws.append(head)
    for r in m["rows"]:
        ws.append([r["person_code"], r["name"],
                   "已登记" if r["submitted"] else "未登记"] +
                  [r["marks"][d] for d in days])
    ws.append([""] * len(head))
    ws.append(["可出勤人数（今天及以后）", ""] +
              [(m["free_cnt"][d] if not m["past"][d] else "") for d in days])
    ws.append(["未登记人数", ""] + [m["none_cnt"][d] for d in days])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue(), "date_plan_%s.xlsx" % key
