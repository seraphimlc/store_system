# -*- coding: utf-8 -*-
"""P1 只读 MCP 工具能力层：11 个只读工具的聚合实现与注册（P2-10/11 新增
visit_list_tasks / visit_list_months）。

职责边界（与 tools.py 的反向划分）：
- 本模块 = 能力层：一个业务能力一个函数 f(db, **params) -> dict，业务聚合放这里；
- register(mcp) 只注册薄工具函数：取 actor → 独立会话 → 调能力层 → 包错误信封。
业务逻辑一律复用 app.services.*（perf / period / dashboard / report / store_master），
不重写判重/工资/找平/对账规则。

硬性约束：
- **只读**：本模块不得出现 db.commit()/任何写语句，也不调用任何写函数；
  鉴权在 HTTP 中间件层完成（BearerAuthMiddleware），此处只解析 actor 供审计。
- 错误信封与 tools.py 一致：成功 {"ok": True, "data": {...}}；
  失败 {"ok": False, "error": {"code","message","hint"}}；
  月份非法 → BAD_MONTH（复用 capability.MONTH_PATTERN）；找不到对象 → NOT_FOUND；
  参数非法 → BAD_PARAM；意外异常 → INTERNAL。
- 数据为空返回 ok:True + 计数 0 + hint（不是错误）。
- 金额单位为日元（円），返回里带 "currency": "JPY"。

注意：本模块的 register 由父会话接入 tools.py；本模块不得修改 tools.py / server.py。
"""
import re
from datetime import date
from typing import Annotated, Any

from pydantic import Field
from sqlalchemy import func

from mcp.server.mcpserver import Context, MCPServer
from mcp_service.capability import BadMonth, MONTH_PATTERN
from mcp_service.annotations import read as read_ann


class NotFound(RuntimeError):
    """对象不存在 → NOT_FOUND 信封。"""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


class BadParam(RuntimeError):
    """参数非法 → BAD_PARAM 信封。"""

    def __init__(self, message: str, hint: str = "") -> None:
        super().__init__(message)
        self.hint = hint


def _validate_month(month: str) -> str:
    """月份唯一关口：复用 capability.MONTH_PATTERN（不用 \\d，防全角数字静默放行）。"""
    if not re.fullmatch(MONTH_PATTERN, month or ""):
        raise BadMonth(f"月份格式非法：{month!r}，应为 YYYY-MM")
    return month


def _month_range(month: str) -> tuple[date, date]:
    y, m = int(month[:4]), int(month[5:7])
    if m == 12:
        return date(y, 12, 1), date(y + 1, 1, 1)
    return date(y, m, 1), date(y, m + 1, 1)


def _normal_status(st) -> str:
    """clean_status → 展示口径：visible_blank 归一为 blank（与 report.py 一致）。"""
    return "blank" if st == "visible_blank" else (st or "unknown")


# ---------------------------------------------------------------------------
# 能力层：f(db, **params) -> dict
# ---------------------------------------------------------------------------

def file_list(db, month: str | None = None) -> dict[str, Any]:
    """巡店导入文件列表：id/文件名/状态/解析行数/上传时间 + 涉及月份 + 判定分类计数 + 是否入正式表。

    month 可选（YYYY-MM）：只返回「涉及」该月的文件（raw 或 formal 月份命中）。
    """
    from app.models import FormalRecord, ImportFile, RawRecord

    month_filter = None
    if month is not None and str(month).strip() != "":
        month_filter = _validate_month(str(month).strip())

    # 一次性聚合（避免逐文件 N+1）：
    raw_months: dict[int, set[str]] = {}
    for imp_id, mod in db.query(RawRecord.import_id, RawRecord.modified_raw).all():
        if mod:
            raw_months.setdefault(imp_id, set()).add(str(mod)[:7])
    formal_months: dict[int, set[str]] = {}
    for imp_id, jd in db.query(FormalRecord.import_id, FormalRecord.japan_date).all():
        if jd is not None:
            formal_months.setdefault(imp_id, set()).add(str(jd)[:7])
    judge: dict[int, dict[str, int]] = {}
    for imp_id, st, c in db.query(
            RawRecord.import_id, RawRecord.clean_status,
            func.count(RawRecord.id)).group_by(
            RawRecord.import_id, RawRecord.clean_status).all():
        judge.setdefault(imp_id, {})[_normal_status(st)] = c
    formal_cnt: dict[int, int] = {}
    for imp_id, c in db.query(FormalRecord.import_id,
                              func.count(FormalRecord.id)).group_by(
            FormalRecord.import_id).all():
        formal_cnt[imp_id] = c

    out = []
    for f in db.query(ImportFile).order_by(ImportFile.id.desc()).all():
        months = sorted(raw_months.get(f.id, set()) | formal_months.get(f.id, set()))
        if month_filter and month_filter not in months:
            continue
        out.append({
            "file_id": f.id,
            "file_name": f.file_name,
            "status": f.status,
            "format": f.format,
            "parsed_rows": f.parsed_rows or 0,
            "total_rows": f.total_rows or 0,
            "uploaded_at": (f.uploaded_at.strftime("%Y-%m-%d %H:%M:%S")
                            if f.uploaded_at else None),
            "months": months,
            "judge_counts": judge.get(f.id, {}),
            "formal_rows": formal_cnt.get(f.id, 0),
        })
    if not out:
        return {
            "files": [], "total": 0,
            "hint": ("无符合条件的文件（合法结果，不是错误）" if month_filter
                     else "当前没有任何导入文件（合法结果，不是错误）"),
        }
    return {"files": out, "total": len(out)}


def file_report(db, file_id: int, bucket: str | None = None) -> dict[str, Any]:
    """单文件判定明细：按 clean_status 分桶计数 + 各桶抽样若干行（店名/日期/判定/过滤原因）。

    bucket 可选：valid / master_late / from_sub / cross_file_dup / blank / no_ref / appealing。
    counts 恒为全部分布；samples 在 bucket 指定时只抽该桶。
    """
    from app.models import AppealRecord, FormalRecord, ImportFile, RawRecord

    f = db.get(ImportFile, file_id)
    if f is None:
        raise NotFound(f"文件不存在：{file_id}",
                       "请先用 visit_file_list 确认正确的 file_id")

    known = {"valid", "master_late", "from_sub", "cross_file_dup", "blank", "no_ref"}
    if bucket and bucket not in known and bucket != "appealing":
        raise BadParam(f"未知分桶：{bucket!r}",
                       "可选分桶：" + "、".join(sorted(known)) + "、appealing")

    SAMPLE = 5
    rows = db.query(RawRecord).filter(RawRecord.import_id == file_id).all()
    counts: dict[str, int] = {}
    for r in rows:
        st = _normal_status(r.clean_status)
        counts[st] = counts.get(st, 0) + 1

    formal_ids = {fr.raw_record_id for fr in db.query(
        FormalRecord.raw_record_id).filter(
        FormalRecord.import_id == file_id).all()}
    appeal_ids = {ap.raw_record_id for ap in db.query(
        AppealRecord.raw_record_id).filter(
        AppealRecord.import_id == file_id).all()}
    pending = db.query(AppealRecord).filter(
        AppealRecord.import_id == file_id,
        AppealRecord.status == "pending").count()

    sample_rows: dict[str, list[dict[str, Any]]] = {k: [] for k in counts}
    for r in sorted(rows, key=lambda x: (x.modified_raw or "", x.excel_row)):
        st = _normal_status(r.clean_status)
        if len(sample_rows[st]) >= SAMPLE:
            continue
        if bucket is not None:
            hit = (r.id in appeal_ids) if bucket == "appealing" else (st == bucket)
            if not hit:
                continue
        sample_rows[st].append({
            "id": r.id,
            "status": st,
            "store_id": r.store_id_raw or "",
            "store_name": r.store_name_local_raw or "",
            "date": (r.modified_raw or "")[:10],
            "filter_reason": r.filter_reason,
            "submitter": r.submitter_raw or "",
            "submitter_code": r.submitter_code,
            "in_formal": r.id in formal_ids,
            "excel_row": r.excel_row,
            "sheet": r.sheet_name,
        })

    filtered_total = sum(counts.get(k, 0) for k in
                         ("master_late", "from_sub", "cross_file_dup",
                          "blank", "no_ref"))
    data = {
        "file_id": f.id,
        "file_name": f.file_name,
        "raw_total": len(rows),
        "counts": counts,
        "filtered_total": filtered_total,
        "pending_appeals": pending,
        "formal_rows": len(formal_ids),
        "samples": sample_rows,
        "sample_size": SAMPLE,
        "bucket": bucket or None,
    }
    if not rows:
        data["hint"] = "该文件暂无解析记录（合法结果，不是错误）"
    return data


def perf_ranking(db, month: str, limit: int = 10) -> dict[str, Any]:
    """月度绩效排行：perf.month_perf 按点数降序取前 N（姓名/点数/1点/2点/工资，日元）。"""
    _validate_month(month)
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise BadParam(f"limit 必须是正整数：{limit!r}", "limit 传 1~100 的整数")
    if limit < 1:
        raise BadParam(f"limit 必须是正整数：{limit!r}", "limit 传 1~100 的整数")
    limit = min(limit, 100)

    from app.services import perf
    rows = perf.month_perf(db, month)[:limit]
    data = {
        "month": month,
        "limit": limit,
        "count": len(rows),
        "currency": "JPY",          # 金额单位：日元（円）
        "rows": [{
            "person_code": r["code"],
            "name": r["name"],
            "points": r["points"],
            "p1": r["p1"],
            "p2": r["p2"],
            "salary": r["amount"],
        } for r in rows],
    }
    if not rows:
        data["hint"] = "该月无绩效数据（合法结果，不是错误）"
    return data


def dashboard_metrics(db, month: str, top: int = 8) -> dict[str, Any]:
    """月度看板指标：优先读物化表 dash_metrics，缺失时回退实时计算。

    主结构 = metrics（字段全集：employees/records/p1/p2/points/amount/p2rate/"
    "per_emp_points/per_emp_amount/per_emp_records/per_store_points/dup_total）；
    不再单独返回 company_summary（其 6 个字段数值与 metrics 全等，已合并）。
    top：top_staff 条数，默认 8；top=0 时不返回 top_staff 键。
    """
    _validate_month(month)
    from app.models import DashMetric
    from app.services import dashboard as D
    from app.services import perf

    try:
        top = int(top)
    except (TypeError, ValueError):
        raise BadParam(f"top 必须是非负整数：{top!r}", "top 默认 8；top=0 表示不返回排行")
    if top < 0:
        raise BadParam(f"top 必须是非负整数：{top!r}", "top 默认 8；top=0 表示不返回排行")

    source = "dash_metrics" if D.month_has_metrics(db, month) else "realtime"

    def _norm_quality(q) -> dict[str, Any]:
        by_status = (q or {}).get("by_status") or {}
        return {
            "total": (q or {}).get("total", sum(by_status.values())),
            "valid": (q or {}).get("valid", by_status.get("valid", 0)),
            "cross_file_dup": (q or {}).get(
                "cross_file_dup", by_status.get("cross_file_dup", 0)),
            "master_late": (q or {}).get(
                "master_late", by_status.get("master_late", 0)),
            "from_sub": (q or {}).get("from_sub", by_status.get("from_sub", 0)),
            "visible_blank": (q or {}).get(
                "visible_blank", by_status.get("visible_blank", 0)),
            "valid_days": (q or {}).get("valid_days", 0),
        }

    def _metrics(p1, p2, recs, n, pts, amt) -> dict[str, Any]:
        return {
            "employees": int(n), "records": int(recs),
            "p1": int(p1), "p2": int(p2),
            "points": int(pts), "amount": int(amt),
            "p2rate": (p2 / recs) if recs else 0.0,
            "per_emp_points": (pts / n) if n else 0,
            "per_emp_amount": (amt / n) if n else 0,
            "per_emp_records": (recs / n) if n else 0,
            "per_store_points": (pts / recs) if recs else 0,
        }

    if source == "dash_metrics":
        vals = {r.metric: r.value for r in db.query(DashMetric).filter(
            DashMetric.month == month, DashMetric.person.is_(None)).all()}
        recs = vals.get("records") or 0
        n = vals.get("employees") or 0
        p1 = vals.get("p1") or 0
        p2 = vals.get("p2") or 0
        pts = vals.get("total_points") or 0
        amt = vals.get("total_amount") or 0
        metrics = _metrics(p1, p2, recs, n, pts, amt)
        metrics["dup_total"] = int(vals.get("dup_total") or 0)
        pl = D.month_payloads(db, month)
        quality = _norm_quality(pl.get("quality"))
        top_staff = (pl.get("top_staff") or [])[:top] if top > 0 else None
        dup_map = pl.get("dup_map") or {}
        new_staff = pl.get("new_staff") or []
        gone_staff = pl.get("gone_staff") or []
    else:
        comp = perf.company_summary(db, month)
        recs = comp["records"] or 0
        n = comp["employees"] or 0
        p1 = comp["p1"] or 0
        p2 = comp["p2"] or 0
        pts = comp["total_points"] or 0
        amt = comp["total_amount"] or 0
        metrics = _metrics(p1, p2, recs, n, pts, amt)
        metrics["dup_total"] = sum(perf.month_dup_map(db, month).values())
        quality = _norm_quality(D.quality_stats(db, month))
        top_staff = D.top_staff(db, month, top) if top > 0 else None
        dup_map = perf.month_dup_map(db, month)
        new_staff = [{"name": name, "points": v[0], "amount": v[1]}
                     for name, v in D.staff_changes(db, month)[0]]
        gone_staff = [{"name": name, "points": v[0], "amount": v[1]}
                      for name, v in D.staff_changes(db, month)[1]]

    data = {
        "month": month,
        "source": source,
        "metrics": metrics,
        "quality": quality,
        "dup_map": dup_map,
        "new_staff": new_staff,
        "gone_staff": gone_staff,
        "currency": "JPY",          # 金额单位：日元（円）
    }
    if top > 0:
        data["top_staff"] = top_staff
    if not metrics["records"] and not metrics["employees"]:
        data["hint"] = "该月无数据（合法结果，不是错误）"
    return data


def payroll_rows(db, month: str, person: str | None = None) -> dict[str, Any]:
    """月度分期对账偏差（薪资找平）：两期点数/金额/奖金/店数快照 + 找平（adjust/prev/diff）与递延（carry）。"""
    _validate_month(month)
    from app.services import period

    rows = period.period_rows(db, month)
    half_stats = period.half_stats_map(db, month)
    carry = period.carry_map(db, month)
    out = []
    for r in rows:
        code = r["code"]
        hs = half_stats.get(code, {"h1": (0, 0, 0), "h2": (0, 0, 0)})
        cr = carry.get(code, (0, 0))
        h1r, h1p1, h1p2 = hs["h1"]
        h2r, h2p1, h2p2 = hs["h2"]
        out.append({
            "person_code": code,
            "name": r["name"],
            "half1_points": r["half1"],
            "half2_points": r["half2"],
            "half1_records": h1r, "half1_p1": h1p1, "half1_p2": h1p2,
            "half2_records": h2r, "half2_p1": h2p1, "half2_p2": h2p2,
            "half1_bonus": r["half1_bonus"],
            "half2_bonus": r["half2_bonus"],
            "half1_amount": r["half1_amt"],
            "half2_amount": r["half2_amt"],
            "settle_points": r["settle"],
            "settle_amount": r["settle_amt"],
            "prev_points": r["prev"],
            "prev_amount": r["prev_amt"],
            "diff_points": r["diff"],
            "diff_amount": r["diff_amt"],
            "adjust_points": r["adj"],
            "adjust_amount": r["adj_amt"],
            "carry_points": cr[0],
            "carry_amount": cr[1],
        })
    if person:
        key = person.strip()
        out = [x for x in out
               if x["person_code"] == key or key in (x["name"] or "")]
    data = {"month": month, "currency": "JPY", "rows": out, "count": len(out)}
    if not out:
        data["hint"] = "该月无找平行（合法结果，不是错误）"
    return data


def person_detail(db, month: str, person: str) -> dict[str, Any]:
    """单人日明细：person_daily_stats（日期/点数/店数）+ 该月汇总（工资，日元）。

    person 支持工号精确 或 姓名包含；找不到 → NotFound（NOT_FOUND）。
    """
    _validate_month(month)
    person = (person or "").strip()
    if not person:
        raise BadParam("person 不能为空", "传工号（精确）或姓名（包含），如 P001 或 张三")

    from app.models import Person, PersonDailyStat
    from app.services import perf

    names = {p.code: p.display_name for p in db.query(Person).all()}
    codes = [c for c, n in names.items() if c == person]
    if not codes:
        codes = [c for c, n in names.items() if person in (n or "")]
    # 当月有绩效记录的人员兜底（姓名可能只存在于月绩效视图）
    if not codes:
        mp = perf.month_perf(db, month)
        codes = [r["code"] for r in mp if r["code"] == person]
        if not codes:
            codes = [r["code"] for r in mp if person in (r["name"] or "")]
    if not codes:
        raise NotFound(f"未找到人员：{person!r}",
                       "请传工号（精确）或姓名（包含）；可先用 visit_staff_list 查人员编号")

    code = codes[0]
    lo, hi = _month_range(month)
    stats = db.query(PersonDailyStat).filter(
        PersonDailyStat.person_code == code,
        PersonDailyStat.ref_date >= lo,
        PersonDailyStat.ref_date < hi).order_by(PersonDailyStat.ref_date).all()
    daily = [{"date": str(s.ref_date), "records": s.records or 0,
              "p1": s.p1 or 0, "p2": s.p2 or 0,
              "points": s.points or 0} for s in stats]

    summary = next((r for r in perf.month_perf(db, month)
                    if r["code"] == code), None)
    if summary is not None:
        summary = {"records": summary["records"], "p1": summary["p1"],
                   "p2": summary["p2"], "points": summary["points"],
                   "salary": summary["amount"],
                   "per_point": summary["per_point"],
                   "rate37": summary["rate37"]}
    else:
        summary = {"records": sum(d["records"] for d in daily),
                   "p1": sum(d["p1"] for d in daily),
                   "p2": sum(d["p2"] for d in daily),
                   "points": sum(d["points"] for d in daily),
                   "salary": None, "per_point": None, "rate37": None}

    data = {"month": month, "person_code": code, "name": names.get(code, code),
            "daily": daily, "summary": summary, "currency": "JPY"}
    if not daily and not summary["points"]:
        data["hint"] = "该月此人暂无明细（合法结果，不是错误）"
    return data


def config_get(db) -> dict[str, Any]:
    """当前生效配置：每点单价 / 奖金门槛与奖额（按月 schedule）/ 员工可见起始月。只读。"""
    from app.config import get_settings
    from app.services import perf

    perf.warm_config(db)            # 只读：载入 SysConfig 最新一条（页面同款读法）
    g, a = perf.bonus_params(None)
    s = get_settings()
    return {
        "per_point": perf.month_per_point(db, ""),
        "bonus_group": g,
        "bonus_amount": a,
        "bonus_group_schedule": s.bonus_group_schedule,
        "bonus_amount_schedule": s.bonus_amount_schedule,
        "staff_visible_from": perf.staff_visible_from(db),
        "currency": "JPY",          # 金额单位：日元（円）
        "note": "配置为全局单值（SysConfig 最新一条生效）；奖金门槛/奖额可经 schedule 按月覆盖",
    }


def staff_list(db, status: str | None = None) -> dict[str, Any]:
    """员工账号列表：用户名/角色/状态/是否绑定人员/最近登录（Token 最近使用）；绝不返回口令哈希。"""
    from app.models import ApiToken, User

    valid = ("active", "leave", "disabled", "resigned")
    if status is not None and status not in valid:
        raise BadParam(f"未知状态：{status!r}", "可选状态：" + "、".join(valid))

    q = db.query(User)
    if status:
        q = q.filter(User.status == status)
    users = q.order_by(User.id).all()
    last_used = dict(db.query(ApiToken.user_id, func.max(ApiToken.last_used_at))
                     .group_by(ApiToken.user_id).all())
    rows = []
    for u in users:
        lu = last_used.get(u.id)
        rows.append({
            "id": u.id,
            "username": u.username,
            "display_name": u.display_name,
            "role": u.role,
            "status": u.status,
            "is_active": u.is_active,
            "person_code": u.person_code,
            "bound_person": bool(u.person_code),
            "last_login": (lu.strftime("%Y-%m-%d %H:%M:%S") if lu else None),
            "created_at": (u.created_at.strftime("%Y-%m-%d %H:%M:%S")
                           if u.created_at else None),
        })
    data = {"rows": rows, "count": len(rows)}
    if not rows:
        data["hint"] = "无符合条件的账号（合法结果，不是错误）"
    return data


def store_search(db, q: str, limit: int = 20) -> dict[str, Any]:
    """店铺主档检索：store_entities（编号/名称/规范化名/城市），LIKE 模糊匹配。"""
    from app.models import StoreEntity

    q = (q or "").strip()
    if not q:
        return {"q": q, "rows": [], "total": 0,
                "hint": "q 为空：未检索（合法结果，不是错误）"}
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        raise BadParam(f"limit 必须是正整数：{limit!r}", "limit 传 1~100 的整数")
    if limit < 1:
        raise BadParam(f"limit 必须是正整数：{limit!r}", "limit 传 1~100 的整数")
    limit = min(limit, 100)

    like = f"%{q}%"
    ents = (db.query(StoreEntity)
            .filter(StoreEntity.store_id_raw.like(like)
                    | StoreEntity.name_local.like(like)
                    | StoreEntity.name_norm.like(like)
                    | StoreEntity.city.like(like))
            .order_by(StoreEntity.id).limit(limit).all())
    rows = [{
        "id": e.id,
        "store_id_raw": e.store_id_raw,
        "name_local": e.name_local or "",
        "name_norm": e.name_norm or "",
        "city": e.city or "",
        "address_local": e.address_local or "",
        "master_id": e.master_id,
        "is_master": e.master_id == e.id,
        "master_store_id": e.master_store_id,
    } for e in ents]
    data = {"q": q, "rows": rows, "total": len(rows), "limit": limit}
    if not rows:
        data["hint"] = "无匹配店铺（合法结果，不是错误）"
    return data


def _iso(dt) -> str | None:
    if dt is None:
        return None
    return dt.strftime("%Y-%m-%dT%H:%M:%S") if hasattr(dt, "strftime") \
        else str(dt)


def list_tasks(db, month: str | None = None, kind: str | None = None,
               status: str | None = None) -> dict[str, Any]:
    """统一任务列表：巡店导入（ImportFile）+ 对账任务（ReconTask）。

    - kind 过滤：`daily_records`（巡店=ImportFile）/ `recon`（对账=ReconTask）；
      空则两类都返回。
    - month 过滤：巡店按「涉及月份」（raw ∪ formal）命中；对账按 params.month。
    - status 过滤：按传入值字符串匹配（巡店 uploaded/parsed/failed；
      对账 pending/running/done/failed）。
    - 每条含：id/month/kind/filename/status/version/is_previous/created_at/
      finished_at/replaced_previous_ids。
    - is_previous：对账任务的旧版本标记（params.replaced_by 非空）。
    - replaced_previous_ids：被本任务标记为上一版的旧任务 id 列表
      （从被替换者的 replaced_by 反向推导；巡店通道无此语义 → []）。
    """
    kind_n = (kind or "").strip().lower() or None
    if kind_n and kind_n not in ("daily_records", "recon"):
        raise BadParam(f"未知 kind：{kind!r}",
                       "可选：daily_records（巡店记录）/ recon（对账明细），不传=全部")
    month_f = None
    if month is not None and str(month).strip() != "":
        month_f = _validate_month(str(month).strip())
    status_f = (status or "").strip() or None

    out: list[dict[str, Any]] = []

    # ---- 巡店导入（ImportFile）：kind=daily_records ----
    if kind_n in (None, "daily_records"):
        from app.models import FormalRecord, ImportFile, RawRecord
        raw_months: dict[int, set[str]] = {}
        for imp_id, mod in db.query(RawRecord.import_id,
                                    RawRecord.modified_raw).all():
            if mod:
                raw_months.setdefault(imp_id, set()).add(str(mod)[:7])
        formal_months: dict[int, set[str]] = {}
        for imp_id, jd in db.query(FormalRecord.import_id,
                                   FormalRecord.japan_date).all():
            if jd is not None:
                formal_months.setdefault(imp_id, set()).add(str(jd)[:7])
        for f in db.query(ImportFile).order_by(ImportFile.id.desc()).all():
            months = sorted(raw_months.get(f.id, set())
                            | formal_months.get(f.id, set()))
            if month_f and month_f not in months:
                continue
            if status_f and (f.status or "") != status_f:
                continue
            out.append({
                "id": f.id,
                "month": ",".join(months),
                "kind": "daily_records",
                "filename": f.file_name,
                "status": f.status,
                "version": None,
                "is_previous": False,
                "created_at": _iso(f.created_at),
                "finished_at": None,
                "replaced_previous_ids": [],
            })

    # ---- 对账任务（ReconTask）：kind=recon ----
    if kind_n in (None, "recon"):
        from app.models import ReconTask
        for t in db.query(ReconTask).filter(
                ReconTask.kind == "monthly_v3").order_by(
                ReconTask.id.desc()).all():
            p = t.params or {}
            m = p.get("month", "")
            if month_f and m != month_f:
                continue
            if status_f and (t.status or "") != status_f:
                continue
            replaced = [e.id for e in db.query(ReconTask).filter(
                ReconTask.kind == "monthly_v3").all()
                if (e.params or {}).get("replaced_by") == t.id]
            out.append({
                "id": t.id,
                "month": m,
                "kind": "recon",
                "filename": p.get("file", ""),
                "status": t.status,
                "version": p.get("version"),
                "is_previous": bool(p.get("replaced_by")),
                "created_at": _iso(t.created_at),
                "finished_at": _iso(t.finished_at),
                "replaced_previous_ids": sorted(replaced),
            })

    out.sort(key=lambda x: x["id"], reverse=True)
    data: dict[str, Any] = {"tasks": out, "total": len(out)}
    if not out:
        data["hint"] = "无符合条件的任务（合法结果，不是错误）"
    return data


def list_months(db) -> dict[str, Any]:
    """系统内有数据的月份：formal_records / month_perf_records / recon_tasks 三类各计数。"""
    from app.models import FormalRecord, MonthPerfRecord, ReconTask

    formal: dict[str, int] = {}
    for (jd,) in db.query(FormalRecord.japan_date).all():
        if jd is not None:
            m = str(jd)[:7]
            formal[m] = formal.get(m, 0) + 1
    perf: dict[str, int] = {}
    for (m,) in db.query(MonthPerfRecord.month).all():
        if m:
            perf[m] = perf.get(m, 0) + 1
    recon: dict[str, int] = {}
    for t in db.query(ReconTask).filter(
            ReconTask.kind == "monthly_v3").all():
        p = t.params or {}
        m = p.get("month")
        if m and not p.get("replaced_by"):
            recon[m] = recon.get(m, 0) + 1

    all_months = sorted(set(formal) | set(perf) | set(recon))
    months = [{
        "month": m,
        "formal_records": formal.get(m, 0),
        "month_perf_records": perf.get(m, 0),
        "recon_tasks": recon.get(m, 0),
    } for m in all_months]
    data: dict[str, Any] = {"months": months, "total": len(months)}
    if not months:
        data["hint"] = "系统尚无任何月数据（合法结果，不是错误）"
    return data


# ---------------------------------------------------------------------------
# 信封与注册（薄适配层）
# ---------------------------------------------------------------------------

def _envelope_error(code: str, message: str, hint: str, **extra: Any) -> dict[str, Any]:
    """统一失败信封（含 retryable）。"""
    from mcp_service import envelope
    return envelope.error(code, message, hint, **extra)


def _call(db, fn) -> dict[str, Any]:
    """执行能力函数并包错误信封（只读工具约定：意外异常一律 INTERNAL）。"""
    try:
        return {"ok": True, "data": fn(db)}
    except BadMonth as exc:
        return _envelope_error("BAD_MONTH", str(exc),
                               "月份必须是 YYYY-MM，例如 2026-09")
    except NotFound as exc:
        return _envelope_error("NOT_FOUND", str(exc), exc.hint or "")
    except BadParam as exc:
        return _envelope_error("BAD_PARAM", str(exc), exc.hint or "")
    except Exception as exc:  # noqa: BLE001
        return _envelope_error("INTERNAL", repr(exc),
                               "系统内部错误，已记录；可重试")


def _invoke(ctx: Context, fn) -> dict[str, Any]:
    """只读工具统一包装：取 actor（鉴权已在 HTTP 中间件层完成）→ 独立会话 → 能力层 → 信封。"""
    from mcp_service.tools import actor_from_ctx

    actor_from_ctx(ctx)   # 解析并确认调用方身份（供审计；只读工具无写闸门）
    from app.db import SessionLocal
    db = SessionLocal()
    try:
        return _call(db, fn)
    finally:
        db.close()


def register(mcp: MCPServer) -> None:
    """注册 11 个只读工具（P2-10/11 新增 visit_list_tasks / visit_list_months）。"""

    @mcp.tool(
        name="visit_file_list",
        title="巡店导入文件列表",
        annotations=read_ann("巡店导入文件列表"),
        description=(
            "只读列出巡店导入文件：每个文件的 id、文件名、解析状态、解析行数、上传时间、"
            "涉及结算月，以及判定分类计数（有效/同店跨日/从档/跨文件重复/空白等，"
            "按 raw_records.clean_status 分组）与已入正式表条数（formal_rows）。"
            "什么时候用：排查某月数据来自哪些文件、某文件是否解析成功。"
            "关键约束：只读不改数据；month 可选（YYYY-MM），只返回涉及该月的文件。"
        ),
    )
    def visit_file_list(ctx: Context, month: str | None = None) -> dict[str, Any]:
        return _invoke(ctx, lambda db: file_list(db, month=month))

    @mcp.tool(
        name="visit_file_report",
        title="巡店文件判定明细",
        annotations=read_ann("巡店文件判定明细"),
        description=(
            "只读查看单个巡店文件的判定明细：按 clean_status 分桶计数 + 每桶抽样若干行"
            "（店名/日期/判定/过滤原因），以及已入正式表条数与待处理申诉数。"
            "什么时候用：文件上传后核对判定分布、定位被过滤的记录。"
            "关键约束：只读；bucket 可选（valid/master_late/from_sub/"
            "cross_file_dup/blank/no_ref/appealing）；文件不存在返回 NOT_FOUND。"
        ),
    )
    def visit_file_report(ctx: Context, file_id: int,
                          bucket: str | None = None) -> dict[str, Any]:
        return _invoke(ctx, lambda db: file_report(db, file_id, bucket=bucket))

    @mcp.tool(
        name="visit_perf_ranking",
        title="月度绩效排行",
        annotations=read_ann("月度绩效排行"),
        description=(
            "**DEPRECATED（已弃用）**：请用 visit_month_salary(sort_by='points', limit=N) "
            "实现同口径排行（按点数降序取前 N，字段更全）。本工具保留兼容、不再演进。"
            "只读查询某结算月（YYYY-MM）绩效排行：按点数降序取前 N 名，含姓名、点数、"
            "1点/2点店数、工资（日元円，currency=JPY）。"
            "关键约束：只读；limit 默认 10、最大 100；数据来自已物化的月绩效记录"
            "（month_perf_records），不实时重算。"
        ),
    )
    def visit_perf_ranking(
        month: Annotated[str, Field(pattern=MONTH_PATTERN)],
        ctx: Context,
        limit: int = 10,
    ) -> dict[str, Any]:
        return _invoke(ctx, lambda db: perf_ranking(db, month, limit=limit))

    @mcp.tool(
        name="visit_dashboard",
        title="月度看板指标",
        annotations=read_ann("月度看板指标"),
        description=(
            "**什么时候用我**：要经营总览/质量/人员变动分析时用我。"
            "只读查询某结算月（YYYY-MM）看板指标：metrics 为主结构（人数/有效店/1点2点/"
            "总点数/总工资/2点率/人均/店均等字段全集，含 dup_total），另附质量 quality/"
            "排行 top_staff/人员变动 new_staff·gone_staff/重复 dup_map。"
            "优先读物化表 dash_metrics，缺失时回退实时计算。"
            "参数 top：top_staff 条数，默认 8；top=0 表示不返回排行。"
            "关键约束：只读；金额为日元円（currency=JPY）；空月返回 ok:True 与零值。"
        ),
    )
    def visit_dashboard(month: Annotated[str, Field(pattern=MONTH_PATTERN)],
                        ctx: Context,
                        top: int = 8) -> dict[str, Any]:
        return _invoke(ctx, lambda db: dashboard_metrics(db, month, top=top))

    @mcp.tool(
        name="visit_payroll_rows",
        title="薪资找平表",
        annotations=read_ann("薪资找平表"),
        description=(
            "**什么时候用我**：发薪/找平维度（两期分期/递延），非绩效维度。"
            "只读查询某结算月（YYYY-MM）薪资找平（分期对账偏差）表：每人两期（上半月/"
            "下半月）点数、金额、奖金、店数快照，以及对账/上月修正/偏差/找平与递延余额"
            "（carry）。"
            "关键约束：只读；金额为日元円（currency=JPY）；person 可选，按工号或姓名筛选。"
        ),
    )
    def visit_payroll_rows(month: Annotated[str, Field(pattern=MONTH_PATTERN)],
                           ctx: Context,
                           person: str | None = None) -> dict[str, Any]:
        return _invoke(ctx, lambda db: payroll_rows(db, month, person=person))

    @mcp.tool(
        name="visit_person_detail",
        title="员工日明细",
        annotations=read_ann("员工日明细"),
        description=(
            "**什么时候用我**：month_salary 的日粒度下钻（先看月汇总，再下钻到某人的"
            "每日明细）。"
            "只读查询某员工在某结算月（YYYY-MM）的日明细：person_daily_stats"
            "（日期/点数/店数/1点2点）+ 该月汇总（有效店/点数/工资，日元円）。"
            "关键约束：只读；person 必填，支持工号精确或姓名包含；找不到返回 NOT_FOUND。"
        ),
    )
    def visit_person_detail(month: Annotated[str, Field(pattern=MONTH_PATTERN)],
                            ctx: Context,
                            person: str) -> dict[str, Any]:
        return _invoke(ctx, lambda db: person_detail(db, month, person))

    @mcp.tool(
        name="visit_config_get",
        title="结算配置查询",
        annotations=read_ann("结算配置查询"),
        description=(
            "只读查看当前生效的结算配置：每点单价（円）、奖金门槛与奖额"
            "（每满门槛点奖奖额，可按月 schedule 覆盖）、员工可见起始月。"
            "什么时候用：回答『现在每点多少钱/奖金怎么算』这类问题。"
            "关键约束：只读，绝不修改配置；金额为日元円（currency=JPY）。"
        ),
    )
    def visit_config_get(ctx: Context) -> dict[str, Any]:
        return _invoke(ctx, lambda db: config_get(db))

    @mcp.tool(
        name="visit_staff_list",
        title="员工账号列表",
        annotations=read_ann("员工账号列表"),
        description=(
            "只读列出员工账号：用户名、角色、状态（在岗/请假/停用/离职）、是否绑定人员"
            "（person_code）、最近登录（Token 最近使用时间），可按状态筛选。"
            "什么时候用：账号盘点、核对员工账号是否绑定人员编号。"
            "关键约束：只读；绝不返回口令哈希；status 可选（active/leave/disabled/resigned）。"
        ),
    )
    def visit_staff_list(ctx: Context, status: str | None = None) -> dict[str, Any]:
        return _invoke(ctx, lambda db: staff_list(db, status=status))

    @mcp.tool(
        name="visit_store_search",
        title="店铺主档检索",
        annotations=read_ann("店铺主档检索"),
        description=(
            "只读检索店铺主档：按店铺编号/名称/规范化名/城市 LIKE 模糊匹配，"
            "返回店名、规范化名、城市、地址、是否主档等。"
            "什么时候用：按名字/编号找店铺、核对店名写法与归属主档。"
            "关键约束：只读；q 必填；limit 默认 20、最大 100。"
        ),
    )
    def visit_store_search(ctx: Context, q: str, limit: int = 20) -> dict[str, Any]:
        return _invoke(ctx, lambda db: store_search(db, q, limit=limit))

    @mcp.tool(
        name="visit_list_tasks",
        title="历史任务列表",
        annotations=read_ann("历史任务列表"),
        description=(
            "只读列出历史任务（巡店导入 + 对账任务统一列表）：每条含 id/month/kind/"
            "filename/status/version/is_previous/created_at/finished_at/"
            "replaced_previous_ids。"
            "kind 过滤：daily_records（巡店=导入文件）/ recon（对账=对账任务），"
            "不传=两类都返回；month 过滤（YYYY-MM，巡店按涉及月份命中、对账按任务月份）；"
            "status 过滤按字符串匹配。"
            "什么时候用：回答『这个月上传过哪些文件/对账任务、当前是哪一版』。"
            "关键约束：只读；同月重传后旧对账任务 is_previous=true 且被新任务的"
            "replaced_previous_ids 记录。"
        ),
    )
    def visit_list_tasks(ctx: Context, month: str | None = None,
                         kind: str | None = None,
                         status: str | None = None) -> dict[str, Any]:
        return _invoke(ctx, lambda db: list_tasks(db, month=month,
                                                  kind=kind, status=status))

    @mcp.tool(
        name="visit_list_months",
        title="有数据月份列表",
        annotations=read_ann("有数据月份列表"),
        description=(
            "只读列出系统内有数据的月份（YYYY-MM）：正式表（formal_records）/ "
            "月绩效（month_perf_records）/ 对账任务（recon_tasks）三类各计数。"
            "什么时候用：回答『系统里有哪几个月的结算数据』『某月有没有对过账』。"
            "关键约束：只读；空系统返回 ok:True 与空列表（合法结果，不是错误）。"
        ),
    )
    def visit_list_months(ctx: Context) -> dict[str, Any]:
        return _invoke(ctx, lambda db: list_months(db))
