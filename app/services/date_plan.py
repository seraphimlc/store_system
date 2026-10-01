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
# 导出用的单字星期（日本七曜，与"排班计划"参考表头一致）
WD_SHORT = ["月", "火", "水", "木", "金", "土", "日"]

SOURCE_WEB = "web"           # 员工登记的整期计划行
SOURCE_ADMIN = "admin"       # 管理员写入
SOURCE_REPORT = "report"     # 由每日填报写透的行（**不算"已登记"**）

# 填报窗口：提前 7 天开放（用户口径 2026-10-01）；窗口结束 = 登记截止日
OPEN_DAYS_BEFORE = 7


class AllLocked(Exception):
    """该半月的日期都已过去或已自报 → 没有可登记的日期。"""

    def __init__(self, key: str):
        self.key = key
        super().__init__("该半月的日期都已过去或已自报，无需再登记")


class WindowClosed(Exception):
    """填报窗口未开放或已结束（提前 7 天开放，到截止日为止）。"""

    def __init__(self, key: str, state: str):
        self.key = key
        self.state = state          # before / closed
        super().__init__("填报期未开放或已结束")


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
    """可选半月列表（默认含上两期、本期、下一期）：可提前登记下一期。"""
    today = today or jst_today()
    cur = current_period(today)
    out = []
    for i in range(-back, fwd + 1):
        key = shift_period(cur, halves=i)
        s, e, dl = period_bounds(key)
        o, c = period_window(key)
        out.append({"key": key, "start": s, "end": e, "deadline": dl,
                    "open_at": o, "close_at": c,
                    "half": H1 if key.endswith(H1) else H2,
                    "current": key == cur,
                    "window_state": window_state(key, today),
                    "overdue": today > dl,
                    "label": period_label(key)})
    return out


def period_days(key: str) -> List[date]:
    start, end, _ = period_bounds(key)
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


# ---------------- 填报窗口（提前 7 天开放，到截止日为止） ----------------

def period_window(key: str) -> Tuple[date, date]:
    """填报窗口 = (**期首 − 7 天**, **登记截止日**)。

    用户口径（2026-10-01）："每个周期的计划表提前 7 天开放填报入口，一直到 3/18 号"
    —— 即 H1（1–15）窗口 = 上月 25 日 ~ 当月 3 日；H2（16–月末）窗口 = 当月 9 日 ~ 18 日。
    窗口结束 = 截止日 → 之后不再接受登记（未登记的一律按"默认全部出勤"处理）。
    """
    start, _end, deadline = period_bounds(key)
    return start - timedelta(days=OPEN_DAYS_BEFORE), deadline


def window_state(key: str, today=None) -> str:
    """填报窗口状态：`before`（未开放）/ `open`（开放中）/ `closed`（已结束）。"""
    today = today or jst_today()
    open_at, close_at = period_window(key)
    if today < open_at:
        return "before"
    return "open" if today <= close_at else "closed"


def is_window_open(key: str, today=None) -> bool:
    return window_state(key, today) == "open"


def open_period(today=None) -> str:
    """当前**可填报**的半月（窗口开着的那一期）；没有 → ""。

    窗口互不重叠（3 日关 → 9 日开；18 日关 → 25 日开），所以最多一期可填；
    4–8 号、19–24 号是"没有可填报期"的间隙。
    """
    today = today or jst_today()
    cur = current_period(today)
    for key in (cur, shift_period(cur, halves=1)):
        if is_window_open(key, today):
            return key
    return ""


def default_period(today=None) -> str:
    """页面默认展示的半月：优先**正在填报的那一期**（管理员关心的"未来两周"），否则本期。"""
    today = today or jst_today()
    return open_period(today) or current_period(today)


def next_window(today=None) -> Optional[dict]:
    """下一个填报窗口（用于"下个填报期 X 开放"提示）；没有可预见的 → None。"""
    today = today or jst_today()
    cur = current_period(today)
    for i in range(0, 3):
        key = shift_period(cur, halves=i)
        st = window_state(key, today)
        if st == "open":
            return None                      # 当前正开着 → 不需要提示"下一个"
        if st == "before":
            open_at, close_at = period_window(key)
            return {"key": key, "open_at": open_at, "close_at": close_at,
                    "label": period_label(key)}
    return None


# ---------------- 三态与锁定 ----------------

def cell_state(plan_available: Optional[bool], reported: bool,
               assumed: bool = False, past: bool = False) -> str:
    """格状态口径（**唯一来源**）：**已自报 > 已过去 > 计划值 > 默认出勤 > 未登记**。

    - `reported=True` → **□** 已出勤（已自报；事实，覆盖一切）；
    - `past=True`（日期 < 今天）→ **×** 没自报就是**没出勤**（用户 2026-10-01 明确：
      "过去的日期里，有自报显示方框，没自报显示叉"，计划值不参与 —— 预报不改变既成事实）；
    - 今天及以后：有计划行 → ○ / ×；没计划行 → **默认出勤 ○**（`assumed`，窗口关闭后）
      或 **–** 未登记（窗口未关）。
    """
    if reported:
        return STATE_DONE
    if past:
        return STATE_OFF
    if plan_available is None:
        return STATE_ON if assumed else STATE_NONE
    return STATE_ON if plan_available else STATE_OFF


def assumed_default(d: date, today: date, deadline: date) -> bool:
    """该日期是否走"未登记 → 默认全部出勤"：今天及以后 + 已过登记截止日。"""
    return d >= today and today > deadline


def lock_of(d: date, today: date, reported: bool) -> str:
    """锁定原因：已自报 → reported；已过去 → past；否则可改（''）。"""
    if reported:
        return LOCK_REPORTED
    if d < today:
        return LOCK_PAST
    return LOCK_NONE


def _plan_map(db, person_code: str, start: date, end: date) -> Dict[date, tuple]:
    """该人某区间的计划行 → `{日期: (available, reported)}`。

    **只读 `staff_date_plans` 这一张表**（用户 2026-10-01 口径："避免关联查询"）：
    "已自报"由每日填报**写透**到本表 `reported` 列（见 `mark_reported`），
    渲染时不再查 `staff_daily_reports`。
    """
    rows = (db.query(StaffDatePlan.plan_date, StaffDatePlan.available,
                     StaffDatePlan.reported)
            .filter(StaffDatePlan.person_code == person_code,
                    StaffDatePlan.plan_date >= start,
                    StaffDatePlan.plan_date <= end).all())
    return {d: (bool(a), bool(r)) for d, a, r in rows}


def _row_of(db, person_code: str, d: date):
    return (db.query(StaffDatePlan)
            .filter(StaffDatePlan.person_code == person_code,
                    StaffDatePlan.plan_date == d).first())


def is_locked(db, person_code: str, d: date, today=None) -> Optional[str]:
    """该日期是否锁定（返回原因 'reported'/'past'，未锁定 → None）。单表判定。"""
    today = today or jst_today()
    row = _row_of(db, person_code, d)
    return lock_of(d, today, bool(row and row.reported)) or None


def is_submitted(db, person_code: str, key: str) -> bool:
    """该半月是否**登记过整期计划**。

    注意：`source='report'` 的行（员工只自报、没登记计划时写透进来的）**不算登记**，
    否则"只自报没登记"的人会被误判成已登记、连催办弹窗都不弹（2026-10-01 评审）。
    """
    start, end, _ = period_bounds(key)
    return (db.query(StaffDatePlan.id)
            .filter(StaffDatePlan.person_code == person_code,
                    StaffDatePlan.plan_date >= start,
                    StaffDatePlan.plan_date <= end,
                    StaffDatePlan.source != SOURCE_REPORT).first()) is not None


# ---------------- 自报写透（单表渲染的关键） ----------------

def mark_reported(db, person_code: str, ref_date: date, flag: bool = True,
                  commit: bool = True) -> bool:
    """把"当天已自报出勤"**写进计划表**（每日填报的提交/修改/删除时调用）。

    - 已有计划行 → 只改 `reported`（保留 `available`，删自报后能回到原计划值）；
    - 没有计划行（没登记就自报）→ 插一行 `source='report'`（不算"已登记"）；
    - `flag=False`（删除自报）→ 置回 False。

    这样管理端矩阵 / 员工页**只读计划表**就能显示 □，不用关联 `staff_daily_reports`。
    """
    if not person_code or ref_date is None:
        return False
    row = _row_of(db, person_code, ref_date)
    if row is None:
        if not flag:
            return False
        db.add(StaffDatePlan(person_code=person_code, plan_date=ref_date,
                             available=True, reported=True,
                             source=SOURCE_REPORT))
    else:
        if bool(row.reported) == bool(flag):
            return False
        row.reported = bool(flag)
    if commit:
        db.commit()
    return True


def rebuild_reported(db, person_code: Optional[str] = None,
                     start: Optional[date] = None, end: Optional[date] = None,
                     commit: bool = True) -> int:
    """按 `staff_daily_reports` **重建** `reported`（写路径修复工具，不在渲染路径上）。

    用于：迁移回填、清理脚本批量删自报之后。返回改动行数。
    """
    q = db.query(StaffDailyReport.person_code, StaffDailyReport.report_date)
    if person_code:
        q = q.filter(StaffDailyReport.person_code == person_code)
    if start is not None:
        q = q.filter(StaffDailyReport.report_date >= start)
    if end is not None:
        q = q.filter(StaffDailyReport.report_date <= end)
    truth = {(c, d) for c, d in q.all()}
    rows = db.query(StaffDatePlan)
    if person_code:
        rows = rows.filter(StaffDatePlan.person_code == person_code)
    if start is not None:
        rows = rows.filter(StaffDatePlan.plan_date >= start)
    if end is not None:
        rows = rows.filter(StaffDatePlan.plan_date <= end)
    changed = 0
    for row in rows.all():
        want = (row.person_code, row.plan_date) in truth
        if bool(row.reported) != want:
            row.reported = want
            changed += 1
    if commit and changed:
        db.commit()
    return changed


# ---------------- 待填报判定与员工落点 ----------------

def needs_plan(db, person_code: Optional[str], today=None) -> dict:
    """**现在是否还需要填报出勤计划**：当期填报窗口开着 且 还没登记。

    返回 {} = 不需要（没窗口／已登记）；否则给出该期信息（窗口起止）。
    用于：员工登录落点、员工端弹窗提示（用户 2026-10-01 要求）。
    窗口一关（截止日过了）就不再催办 —— 那一期已按"默认全部出勤"处理。
    """
    if not person_code:
        return {}
    today = today or jst_today()
    key = open_period(today)
    if not key or is_submitted(db, person_code, key):
        return {}
    start, end, deadline = period_bounds(key)
    open_at, close_at = period_window(key)
    return {"key": key, "start": start, "end": end, "deadline": deadline,
            "open_at": open_at, "close_at": close_at,
            "overdue": today > deadline, "label": period_label(key)}


def staff_home(db, user) -> str:
    """**员工登录后的第一个页面**（单一来源）：

    - 本期出勤计划**没登记** → `/my/plan`（先去填计划）；
    - 否则 → `/my/report`（每日自报）；
    - 没有绑定员工编号（用不了这两个页面）→ `/my/perf`（保持历史行为）。
    """
    code = getattr(user, "person_code", None)
    if not code:
        return "/my/perf"
    return "/my/plan" if needs_plan(db, code) else "/my/report"


# ---------------- 员工端视图与提交 ----------------

def plan_days(db, person_code: str, key: str, today=None) -> dict:
    """员工端逐日视图：三态 + 能否修改 + 锁定原因 + 统计。

    员工端与管理端显示口径有**一处有意差别**：管理端把"没登记"显示成 `–`
    （不能把没登记当成可出勤），而员工端是"默认每天都出勤"，**可改且未登记**的日子
    按默认值显示 `○`（否则员工会以为那天是空白的）。三态判定本身同为 `cell_state()`。
    """
    today = today or jst_today()
    start, end, deadline = period_bounds(key)
    open_at, close_at = period_window(key)
    wstate = window_state(key, today)
    window_open = wstate == "open"
    plans = _plan_map(db, person_code, start, end)
    days = []
    d = start
    while d <= end:
        avail, rep = plans.get(d, (None, False))
        asm = assumed_default(d, today, deadline)
        past = d < today
        state = cell_state(avail, rep, assumed=asm, past=past)
        lock = lock_of(d, today, rep)
        editable = window_open and lock == LOCK_NONE     # 窗口没开/已关 → 一律不可改
        off = (avail is False)
        # 未登记且可改 → 显示"默认可出勤"的 ○（员工要看到默认值，不是空白）
        mark = MARKS[STATE_ON] if (editable and state == STATE_NONE) else MARKS[state]
        days.append({"date": d, "wd": d.weekday(), "state": state, "mark": mark,
                     "reported": rep, "off": off, "available": not off,
                     "past": past, "assumed": asm and (d not in plans),
                     "editable": editable, "lock": lock})
        d += timedelta(days=1)
    stamps = (db.query(StaffDatePlan.updated_at, StaffDatePlan.created_at)
              .filter(StaffDatePlan.person_code == person_code,
                      StaffDatePlan.plan_date >= start,
                      StaffDatePlan.plan_date <= end).all())
    last = max([(r[0] or r[1]) for r in stamps], default=None)
    return {"key": key, "start": start, "end": end, "deadline": deadline,
            "label": period_label(key), "today": today,
            "open_at": open_at, "close_at": close_at,
            "window_state": wstate, "window_open": window_open,
            "overdue": today > deadline, "default_all": today > deadline,
            "submitted": bool(plans), "last_at": last,
            "days": days,
            "off_cnt": sum(1 for x in days if x["off"]),
            "free_cnt": sum(1 for x in days if not x["off"]),
            "done_cnt": sum(1 for x in days if x["reported"]),
            "assumed_cnt": sum(1 for x in days if x["assumed"]),
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
    **窗口校验**：填报窗口 = 期首前 7 天 ~ 登记截止日；窗口没开或已关 → `WindowClosed`。
    """
    code = getattr(user, "person_code", None)
    if not code:
        raise ValueError("账号未绑定员工编号，无法登记出勤计划")
    today = today or jst_today()
    start, end, _ = period_bounds(key)
    wstate = window_state(key, today)
    if wstate != "open":
        raise WindowClosed(key, wstate)
    off = _parse_dates(unavailable, start, end)
    existing = {r.plan_date: r for r in db.query(StaffDatePlan).filter(
        StaffDatePlan.person_code == code,
        StaffDatePlan.plan_date >= start,
        StaffDatePlan.plan_date <= end).all()}
    written = skipped = 0
    for d in period_days(key):
        row = existing.get(d)
        # 锁定判定只看本行（reported 已由每日填报写透）：已自报 / 已过去 → 不动
        if lock_of(d, today, bool(row and row.reported)):
            skipped += 1
            continue
        val = d not in off
        if row is None:
            db.add(StaffDatePlan(person_code=code, plan_date=d,
                                 available=val, source=source))
        else:
            row.available = val
            row.source = source        # 只自报没登记的行 → 这次算正式登记
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
                            if d in off
                            and not lock_of(d, today, bool(existing.get(d)
                                                          and existing[d].reported))])}


# ---------------- 管理端矩阵与导出 ----------------

SHORT_CODE_LEN = 5      # 页面/导出只显示编号后 5 位（用户 2026-10-01："取后5位就行"）


def short_code(code: Optional[str], n: int = SHORT_CODE_LEN) -> str:
    """员工编号的短展示：**后 n 位**（编号比 n 短就整条返回，不补零）。"""
    s = (code or "").strip()
    return s[-n:] if len(s) > n else s


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
    """管理端矩阵：行=员工，列=日期（三态），另给未提交标记与按日可出勤小计。

    **只查一张表**（`staff_date_plans`）拿全部格子状态：`reported` 已由每日填报写透，
    所以这里**不查 `staff_daily_reports`、不做关联**（用户 2026-10-01 明确要求）。
    """
    today = today or jst_today()
    start, end, deadline = period_bounds(key)
    days = period_days(key)
    plan_rows = (db.query(StaffDatePlan.person_code, StaffDatePlan.plan_date,
                          StaffDatePlan.available, StaffDatePlan.reported)
                 .filter(StaffDatePlan.plan_date >= start,
                         StaffDatePlan.plan_date <= end).all())
    plan_map = {(c, d): (bool(a), bool(r)) for c, d, a, r in plan_rows}
    with_plan = {c for c, _ in plan_map}          # 有行 = 登记过（含只自报的行，下面再判）

    cand = _matrix_people(db)
    for code in sorted(with_plan - set(cand)):
        cand[code] = {"person_code": code, "name": "", "can_login": False,
                      "user_id": None}
    codes = sorted((c for c, e in cand.items()
                    if e["can_login"] or c in with_plan),
                   key=lambda c: (cand[c]["name"] or c, c))
    # "已登记" = 该期存在 source != 'report' 的行（只自报没填计划的不算）
    submitted = {c for (c,) in db.query(StaffDatePlan.person_code)
                 .filter(StaffDatePlan.plan_date >= start,
                         StaffDatePlan.plan_date <= end,
                         StaffDatePlan.source != SOURCE_REPORT)
                 .distinct().all()}

    rows = []
    col_free = {d: 0 for d in days}
    col_none = {d: 0 for d in days}
    col_default = {d: 0 for d in days}
    for code in codes:
        states, assumed = {}, {}
        for d in days:
            av, rep = plan_map.get((code, d), (None, False))
            asm = assumed_default(d, today, deadline)
            states[d] = cell_state(av, rep, assumed=asm, past=d < today)
            assumed[d] = bool(asm and av is None)        # 这一格是"默认出勤"
        for d in days:
            if states[d] in (STATE_ON, STATE_DONE):
                col_free[d] += 1
            elif states[d] == STATE_NONE:
                col_none[d] += 1
            if assumed[d]:
                col_default[d] += 1
        a_cnt = sum(1 for d in days if assumed[d])
        rows.append({
            "person_code": code, "name": cand[code]["name"] or code,
            "short_code": short_code(code),      # 页面/导出只显示后 5 位（完整编号在 title 里）
            "states": states,
            "marks": {d: MARKS[s] for d, s in states.items()},
            "assumed_days": assumed,       # 逐日：这一格是"未登记→默认出勤"（浅色显示）
            "submitted": code in submitted,
            "assumed": a_cnt > 0,          # 该人本期真有"默认出勤"的格子
            "on_cnt": sum(1 for d in days if states[d] == STATE_ON),
            "off_cnt": sum(1 for d in days if states[d] == STATE_OFF),
            "done_cnt": sum(1 for d in days if states[d] == STATE_DONE),
            "assumed_cnt": a_cnt,
        })
    past = {d: d < today for d in days}
    # 按日小计只算"今天及以后"：已过去的日子不构成可用人力
    free_cnt = {d: (0 if past[d] else col_free[d]) for d in days}
    none_cnt = {d: col_none[d] for d in days}
    default_cnt = {d: (0 if past[d] else col_default[d]) for d in days}
    # default_all：本期还有"今天及以后"的日子，且已过登记截止日（整期已过去的历史半月不算）
    default_all = today > deadline and end >= today
    open_at, close_at = period_window(key)
    return {"key": key, "start": start, "end": end, "deadline": deadline,
            "label": period_label(key), "today": today, "overdue": today > deadline,
            "open_at": open_at, "close_at": close_at,
            "window_state": window_state(key, today),
            "default_all": default_all,
            "days": days, "past": past, "rows": rows,
            "free_cnt": free_cnt, "none_cnt": none_cnt, "default_cnt": default_cnt,
            "unsubmitted": [r["person_code"] for r in rows if not r["submitted"]],
            "assumed_people": [r["person_code"] for r in rows if r["assumed"]],
            "total": len(rows)}


def plan_xlsx(db, key: str, today=None):
    """管理端导出：**照"排班计划"参考表排**（用户 2026-10-01 提供样例）。

    版式（**没有"区域标识"那一行**）：

    ```
    说明：○=可出勤；×=不出勤；□=已出勤（已自报）；空=未登记/未提供。可出动天数=今天起可出勤（○/□）天数。
    出勤计划 2026-09-01 ~ 2026-09-15（上半月）· 填报期 2026-08-24 ~ 2026-09-03
    姓名  员工编号  可出动天数  09/01 09/02 ... 09/15
                                月    火        日      ← 七曜单字
    敬斐然 2188…      12        ○    ×    …
    可出勤人数（今天起） …
      其中默认出勤（未登记） …
    未登记人数 …
    ```

    用 `Workbook(write_only=True)` 流式写（与 report_export 同一写法）；
    首两行与前三列冻结（`freeze_panes="D3"`）。
    """
    import io

    from openpyxl import Workbook
    from openpyxl.cell import WriteOnlyCell
    from openpyxl.styles import Font

    m = admin_matrix(db, key, today)
    days = m["days"]
    ncol = 3 + len(days)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet(title=(key[:31] or "plan"))
    ws.freeze_panes = "D3"
    bold = Font(bold=True)

    def row(values, is_bold=False):
        cells = []
        for v in values:
            c = WriteOnlyCell(ws, value=v)
            if is_bold:
                c.font = bold
            cells.append(c)
        ws.append(cells)

    note = ("说明：○=可出勤；×=不出勤；□=已出勤（已自报）；空=未登记/未提供。"
            "可出动天数=今天起可出勤（○/□）的天数。")
    if m["default_all"]:
        note += "填报期已结束仍未登记的人，今天起按默认可出勤（○）计。"
    row([note])
    row(["出勤计划 %s ~ %s（%s）· 填报期 %s ~ %s"
         % (m["start"], m["end"], "上半月" if key.endswith(H1) else "下半月",
            m["open_at"], m["close_at"])])
    row(["姓名", "员工编号", "可出动天数", *[d.strftime("%m/%d") for d in days]],
        is_bold=True)
    row(["", "", "", *[WD_SHORT[d.weekday()] for d in days]], is_bold=True)

    for r in m["rows"]:
        # 未登记且未触发默认出勤 → 留空（与参考表"空白=未提供"一致）
        vals = [("" if r["states"][d] == STATE_NONE else r["marks"][d])
                for d in days]
        free = sum(1 for d in days
                   if not m["past"][d] and r["states"][d] in (STATE_ON, STATE_DONE))
        row([r["name"], r["short_code"], free, *vals])

    row([""] * ncol)
    row(["可出勤人数（今天起）", "", "",
         *[(m["free_cnt"][d] if not m["past"][d] else "") for d in days]],
        is_bold=True)
    if m["default_all"]:
        row(["　其中默认出勤（未登记）", "", "",
             *[(m["default_cnt"][d] if not m["past"][d] else "") for d in days]])
    row(["未登记人数", "", "", *[m["none_cnt"][d] for d in days]])
    bio = io.BytesIO()
    wb.save(bio)
    return bio.getvalue(), "date_plan_%s.xlsx" % key
