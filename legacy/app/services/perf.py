# -*- coding: utf-8 -*-
"""V3 绩效与工资：基于 formal_records 派生（日/月绩效、汇总、工资）。"""
from datetime import date as _date
from collections import defaultdict

from app.services.daterange import month_bounds
from app.models import FormalRecord, Person, RawRecord

# 全局薪资规则（确认口径）：每点 250 円；奖金「每满 bonus_group 点奖 bonus_amount 円」。
# 68 / 3000 为默认值，可通过环境变量 BONUS_GROUP / BONUS_AMOUNT 覆盖（见 app/config.py）。
from app.config import get_settings as _get_settings


def _bonus_cfg():
    s = _get_settings()
    return s.bonus_group, s.bonus_amount


# 系统配置缓存（全局单值：最新一条 SysConfig；保存配置后需 clear）
_CONFIG_CACHE = {"row": None}


def _cfg_row(db, month=None):
    """最新一条系统配置（全局单值，id 最大即最新；不再按月份匹配）。"""
    from app.models import SysConfig
    row = db.query(SysConfig).order_by(SysConfig.id.desc()).first()
    return row


def warm_config(db, month=None):
    """把最新系统配置(每点金额/达标点数/达标奖金)载入缓存（全局单值）。"""
    _CONFIG_CACHE["row"] = _cfg_row(db, month)
    return _CONFIG_CACHE["row"]


def clear_config_cache():
    _CONFIG_CACHE["row"] = None


FULL_GROUP = 68          # 兼容默认（旧引用）
BONUS_AMOUNT = 3000     # 兼容默认（旧引用）
PER_POINT = 250


def _schedule():
    """解析按月门槛规则 "2026-09=75,2027-01=80" → [(month, group)]（升序）。"""
    raw = getattr(_get_settings(), "bonus_group_schedule", "") or ""
    out = []
    for part in raw.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        m, g = part.split("=", 1)
        m, g = m.strip(), g.strip()
        if len(m) == 7 and g.isdigit():
            out.append((m, int(g)))
    return sorted(out)


def _amount_schedule():
    """解析按月奖额规则 "2026-09=1250,..." → [(month, amount)]（升序）。"""
    raw = getattr(_get_settings(), "bonus_amount_schedule", "") or ""
    out = []
    for part in raw.split(","):
        part = part.strip()
        if "=" not in part:
            continue
        m, v = part.split("=", 1)
        m, v = m.strip(), v.strip()
        if len(m) == 7 and v.isdigit():
            out.append((m, int(v)))
    return sorted(out)


def _bonus_cfg(month: str = None):
    """返回 (门槛, 奖额)：优先系统配置表(SysConfig，须先 warm_config，全局单值)；
    未配置/未载入 → env（BONUS_GROUP/BONUS_AMOUNT 及按月 schedule）。"""
    if _CONFIG_CACHE.get("row") is not None:
        r = _CONFIG_CACHE["row"]
        return r.bonus_group or 68, r.bonus_amount or 3000
    s = _get_settings()
    group, amount = s.bonus_group, s.bonus_amount
    if month:
        for m, g in _schedule():
            if month >= m:
                group = g
        for m, a in _amount_schedule():
            if month >= m:
                amount = a
    return group, amount


def staff_visible_from(db) -> str:
    """员工可见起始月（YYYY-MM）：SysConfig 表配置优先，未配置回退 env 默认。

    员工端（/my/perf 等）只显示该月及之后的数据；返回空字符串=不限制。
    """
    from app.models import SysConfig
    row = db.query(SysConfig).order_by(SysConfig.id.desc()).first()
    if row is not None and getattr(row, "staff_visible_from", ""):
        return row.staff_visible_from
    return _get_settings().staff_visible_from


# 系统配置缓存（全局单值：最新一条 SysConfig；保存配置后需 clear）
_CONFIG_CACHE = {"row": None}


def _cfg_row(db, month=None):
    """最新一条系统配置（全局单值，id 最大即最新；不再按月份匹配）。"""
    from app.models import SysConfig
    row = db.query(SysConfig).order_by(SysConfig.id.desc()).first()
    return row


def warm_config(db, month=None):
    """把最新系统配置(每点金额/达标点数/达标奖金)载入缓存（全局单值）。"""
    _CONFIG_CACHE["row"] = _cfg_row(db, month)
    return _CONFIG_CACHE["row"]


def clear_config_cache():
    _CONFIG_CACHE["row"] = None


FULL_GROUP = 68          # 兼容默认（旧引用）
BONUS_AMOUNT = 3000     # 兼容默认（旧引用）
PER_POINT = 250


def salary_for(points: int, per_point: int = None, month: str = None) -> int:
    """每点 per_point 円（默认 250）+ 奖金：每满门槛点奖奖额円（整月滚动、不跨月）。

    门槛按月可变（如 2026-09 起 68→75，见 BONUS_GROUP_SCHEDULE）；不跨月（月初从 0 累计）。
    """
    if points <= 0:
        return 0
    pp = per_point if per_point is not None else PER_POINT
    g, amt = _bonus_cfg(month)
    bonus = (points // g) * amt
    return points * pp + bonus


def bonus_params(month: str = None) -> tuple:
    """供页面/文档展示当前奖金配置：(门槛, 金额)。"""
    return _bonus_cfg(month)


def _point_agg(db, month: str = "", by_day: bool = False):
    """正式表按 SQL 聚合「店数 / 2点 / 1点」（**不再把整表拉进 Python**）。

    ⚠️ 2026-10-17：原来这里是 `db.query(FormalRecord).all()` —— 为了算当月
    （或全量）点数把 **7 万行整表**水合成 ORM 对象，每次月度重算/导入白花 1~2 秒。
    现在过滤（月份前缀）与分组都在库里做，只回传聚合结果。

    返回列：`(person_code, japan_date?, records, n2, n1)`，
    其中 `n1 = points==1 的行数`、`n2 = points==2 的行数`、`records = COUNT(*)`。
    ⚠️ **两个调用方对"0 点/NULL 点"的口径不同，所以这里把 n1 与"非 2 的全部"分开给**：
      - `sync_month_perf`：p1 = n1（**0 点行只计店数、不计点**，见那里的注释）；
      - `daily_perf`：p1 = records - n2（**非 2 点都算 1 点** —— 老代码 `else` 分支如此，
        虽然看起来与上面矛盾，但这是线上口径，改动必须另开一次评审）。
    """
    from sqlalchemy import case as _case, func as _f
    from app.services import months as _months
    _n2 = _f.sum(_case((FormalRecord.points == 2, 1), else_=0))
    _n1 = _f.sum(_case((FormalRecord.points == 1, 1), else_=0))
    if by_day:
        q = db.query(FormalRecord.person_code, FormalRecord.japan_date,
                     _f.count(), _n2, _n1)
        q = q.group_by(FormalRecord.person_code, FormalRecord.japan_date)
    else:
        q = db.query(FormalRecord.person_code, _f.count(), _n2, _n1)
        q = q.group_by(FormalRecord.person_code)
    if month:
        q = q.filter(_months._ym(FormalRecord.japan_date) == month)
    return q.all()


def person_names(db):
    """{员工编号: 显示名}（小表，直接取）。"""
    return {p.code: p.display_name for p in db.query(Person).all()}


def adjust_map(db, month: str = ""):
    """该月工资应含的找平（applied_to_month==month）→ {code: [点合计, 円合计]}。

    金额按每条找平记录锁存的单价折算（纠偏不随本月单价变化）。
    """
    from collections import defaultdict as _dd
    from app.models import AdjustRecord
    out = _dd(lambda: [0, 0])
    for a in db.query(AdjustRecord).filter(
            AdjustRecord.applied_to_month == month).all():
        out[a.person_code][0] += a.amount or 0
        out[a.person_code][1] += (a.amount or 0) * (a.per_point or PER_POINT)
    return dict(out)


def month_dup_map(db, month: str = "") -> dict:
    """该月每人「重复巡店」数量（clean_status=cross_file_dup，同店同月只保留一条、
    其余判重复——重复多说明效率低）。返回 {person_code: n}。

    ⚠️ 原来把 `clean_status='cross_file_dup'` 的行**全部 ORM 水合进 Python**，
    再逐行比 `modified_raw[:7] == month`（月度绩效页与员工页都走它）——线上该状态
    5 万+ 行 / 126MB，纯浪费。现在月份过滤与分组都在 SQL 里（`substr` 三方言通用），
    只回传「人 → 条数」；索引见迁移 `raw_records.clean_status`。
    """
    from sqlalchemy import func as _f
    from app.models import RawRecord
    q = (db.query(RawRecord.submitter_code, _f.count())
         .filter(RawRecord.clean_status == "cross_file_dup"))
    if month:
        q = q.filter(_f.substr(RawRecord.modified_raw, 1, 7) == month)
    out = {}
    for c, n in q.group_by(RawRecord.submitter_code).all():
        if c:
            out[c] = int(n or 0)
    return out


def month_perf(db, month: str = ""):
    """月度绩效工资：查 month_perf_records（入表时物化，每员工每月一条）。
    返回 {code, name, records, p1, p2, points, amount, rate37, pass37}。"""
    from app.models import MonthPerfRecord
    names = {p.code: p.display_name for p in db.query(Person).all()}
    out = []
    q = db.query(MonthPerfRecord)
    if month:
        q = q.filter(MonthPerfRecord.month == month)
    for r in q.all():
        out.append({
            "code": r.person_code, "name": names.get(r.person_code,
                                                      r.person_code),
            "records": r.records, "p1": r.p1, "p2": r.p2,
            "points": r.points, "amount": r.salary,
            "per_point": r.per_point,
            "settle_points": r.settle_points,
            "settle_amount": r.settle_amount,
            "diff_points": r.diff_points, "diff_amount": r.diff_amount,
            "rate37": r.rate37, "pass37": r.pass37,
        })
    out.sort(key=lambda x: -x["points"])
    return out


def sync_month_perf(db, month: str) -> int:
    """物化月绩效工资：按正式表聚合该月每人 → upsert month_perf_records。
    在 sync_month_stats（入表/月度重算）后调用；工资规则变化后重算即可。
    """
    from app.models import MonthPerfRecord
    if not month or len(month) != 7:
        return 0
    agg = defaultdict(lambda: {"records": 0, "p1": 0, "p2": 0})
    for code, cnt, n2, n1 in _point_agg(db, month):
        d = agg[code]
        d["records"] += cnt
        d["p2"] += n2
        # 0 点（规则判定"不计成绩"，如 AUDIT_FAILED 且非 YES）→ 只计店数、不计点
        d["p1"] += n1
    existing = {r.person_code: r for r in db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).all()}
    # 该月单价：已有行锁存的优先（设置过单价后重物化不冲掉），否则全局默认
    first = next(iter(existing.values()), None)
    per_point = (first.per_point if first is not None and first.per_point
                 else PER_POINT)
    for code, d in agg.items():
        points = d["p1"] + d["p2"] * 2
        denom = d["p1"] + d["p2"]
        rate = (d["p2"] / denom) if denom else 0.0
        row = existing.get(code)
        if row is None:
            db.add(MonthPerfRecord(
                month=month, person_code=code,
                records=d["records"], p1=d["p1"], p2=d["p2"],
                points=points, salary=salary_for(points, per_point, month),
                per_point=per_point,
                rate37=rate, pass37=rate >= 0.37,
                created_at=__import__("datetime").datetime.utcnow()))
        else:
            row.records = d["records"]
            row.p1 = d["p1"]
            row.p2 = d["p2"]
            row.points = points
            row.per_point = per_point
            row.salary = salary_for(points, per_point, month)
            row.rate37 = rate
            row.pass37 = rate >= 0.37
    # 该月已无正式记录的人（离职/数据清理）同步移除
    for code in set(existing) - set(agg):
        db.delete(existing[code])
    db.commit()
    return len(agg)


def person_months(db, person_code: str) -> list:
    """某人出现过记录的月份（倒序）——**读物化表**，不拉全量正式记录。"""
    from app.models import PersonDailyStat
    rows = (db.query(PersonDailyStat.ref_date)
            .filter(PersonDailyStat.person_code == person_code).all())
    return sorted({str(r[0])[:7] for r in rows if r[0]}, reverse=True)


def daily_perf_person(db, month: str, person_code: str) -> list:
    """某人的逐日绩效——**读物化表 person_daily_stats**（员工端用，避免扫正式表全表）。"""
    from datetime import date as _date

    from app.models import PersonDailyStat
    q = db.query(PersonDailyStat).filter(
        PersonDailyStat.person_code == person_code)
    if month:
        start, nxt = month_bounds(month)
        q = q.filter(PersonDailyStat.ref_date >= start,
                     PersonDailyStat.ref_date < nxt)
    return [{"code": r.person_code, "date": r.ref_date, "records": r.records,
             "p1": r.p1, "p2": r.p2, "points": r.points}
            for r in q.order_by(PersonDailyStat.ref_date).all()]


def daily_perf(db, month: str = ""):
    """日绩效：每人每天 {date, records, p1, p2, points}，按日期排序。"""
    names = person_names(db)
    out = []
    for code, dt, cnt, n2, _n1 in _point_agg(db, month, by_day=True):
        # ⚠️ 这里沿用老口径：非 2 点都算 1 点（p1 = records - n2）——见 `_point_agg` 注释
        p1 = cnt - n2
        out.append({"code": code, "name": names.get(code, code),
                    "date": dt, "records": cnt,
                    "p1": p1, "p2": n2,
                    "points": p1 + n2 * 2})
    out.sort(key=lambda x: (str(x["date"]), x["code"]))
    return out


def company_summary(db, month: str = ""):
    mp = month_perf(db, month)
    total_points = sum(x["points"] for x in mp)
    total_amount = sum(x["amount"] for x in mp)
    p1 = sum(x["p1"] for x in mp)
    p2 = sum(x["p2"] for x in mp)
    denom = p1 + p2
    rate = p2 / denom if denom else 0.0
    return {"employees": len(mp), "records": sum(x["records"] for x in mp),
            "p1": p1, "p2": p2, "total_points": total_points,
            "total_amount": total_amount, "rate37": rate,
            "pass37": rate >= 0.37}

def _month_edges(month: str):
    """月份边界（单一来源在 `daterange.month_bounds`）。"""
    return month_bounds(month)


def sync_month_stats(db, month: str) -> int:
    """刷新 person_daily_stats：删除该月后按正式表重算（人 × 日 点数/店数）。"""
    from app.models import FormalRecord, PersonDailyStat
    if not month or len(month) != 7:
        return 0
    lo, hi = _month_edges(month)
    db.query(PersonDailyStat).filter(
        PersonDailyStat.ref_date >= lo,
        PersonDailyStat.ref_date < hi).delete()
    agg = {}
    for f in db.query(FormalRecord).filter(
            FormalRecord.japan_date >= lo,
            FormalRecord.japan_date < hi).all():
        if not f.person_code:
            continue
        key = (f.person_code, f.japan_date)
        a = agg.setdefault(key, [0, 0, 0])   # records, p1, p2
        a[0] += 1
        _p = f.points or 0
        if _p == 2:
            a[2] += 1
        elif _p == 1:
            a[1] += 1
        # 0 点行只计店数、不计点（点数规则由文件布局 point_rules 决定）
    for (code, d), (records, p1, p2) in agg.items():
        db.add(PersonDailyStat(person_code=code, ref_date=d, records=records,
                               p1=p1, p2=p2, points=p1 + p2 * 2))
    db.commit()
    # 统计表同步后物化该月月绩效工资（页面只查 month_perf_records）
    sync_month_perf(db, month)
    return len(agg)


def ensure_month_stats(db, month: str) -> int:
    """对账任务用：该月统计表为空则先刷一遍，返回人×日 行数。"""
    from app.models import PersonDailyStat
    if not month or len(month) != 7:
        return 0
    lo, hi = _month_edges(month)
    n = db.query(PersonDailyStat).filter(
        PersonDailyStat.ref_date >= lo,
        PersonDailyStat.ref_date < hi).count()
    if n == 0:
        return sync_month_stats(db, month)
    return n


def month_per_point(db, month: str) -> int:
    """该月点数单价：优先系统配置表（须先 warm_config，全局单值），其次月绩效锁存，最后默认 250。"""
    row = _CONFIG_CACHE.get("row")
    if row is not None and row.per_point:
        return row.per_point
    from app.models import MonthPerfRecord
    if not month:
        return PER_POINT
    r = db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).first()
    return (r.per_point if r is not None and r.per_point else PER_POINT)


def set_month_per_point(db, month: str, per_point: int) -> dict:
    """设置该月点数单价：月绩效行 salary 按新单价重算；
    薪资找平表金额按新单价重算；已确认的找平记录（锁存单价）不受影响。
    """
    from app.models import MonthPerfRecord
    per_point = int(per_point)
    rows = db.query(MonthPerfRecord).filter(
        MonthPerfRecord.month == month).all()
    for r in rows:
        r.per_point = per_point
        r.salary = salary_for(r.points, per_point, month)
    db.commit()
    # 薪资找平表按新单价重算（对账金额/分期金额/偏差金额）
    from app.services import period
    period.sync_period_table(db, month, per_point=per_point)
    return {"ok": True, "month": month, "per_point": per_point,
            "rows": len(rows)}
