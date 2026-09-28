# -*- coding: utf-8 -*-
"""V3 页面路由：员工对账（我的判定确认）+ 管理员确认总览 + 入正式表。"""
from datetime import datetime
from typing import Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException,
                        Request, UploadFile)
from fastapi.responses import HTMLResponse, RedirectResponse
from app.templating import get_templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import (AppealRecord, FormalRecord, ImportFile, Person,
                        RawRecord, User)
from app.routers.auth_r import csrf_ok, require_login
from app.services import flow

router = APIRouter()
templates = get_templates()

_REASON_CN = {"master_late": "同主档已有更早有效（本店非首次）",
              "from_sub": "从档编号，已归并到主档店铺",
              "cross_file_dup": "同日同店跨文件重复（自动过滤）",
              "no_ref": "无店铺编号/无法归主档",
              "disputed": "员工申诉待处理"}


def _denied():
    return RedirectResponse("/login", status_code=302)


def _reason_cn(rr) -> str:
    return _REASON_CN.get(rr.filter_reason or "", rr.filter_reason or "")


@router.post("/files/{fid}/finalize")
def finalize_file(fid: int, request: Request,
                  csrf_token: str = Form(...),
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    res = flow.auto_finalize_pipeline(db, fid, user.id)
    if not res["ok"]:
        raise HTTPException(400, res.get("msg", "无法入正式表"))
    from urllib.parse import quote as _q2
    _msg = ("已入正式表 " + str(res["added"]) + " 条，"
            "工资/找平/看板统计/员工分析已自动刷新 "
            + ",".join(res.get("months") or []))
    return RedirectResponse(f"/files?msg={_q2(_msg)}", status_code=303)


@router.get("/my/perf", response_class=HTMLResponse)
def my_perf(request: Request,
            user: Optional[User] = Depends(require_login),
            db: Session = Depends(get_db), month: str = ""):
    """员工看自己当月的绩效：**当月全部逐日明细**（不再按日期筛选）+ 月汇总。"""
    if user is None or user.role != "staff" or not user.person_code:
        return _denied()
    from app.services import perf
    code = user.person_code
    months = perf.person_months(db, code)          # 物化表取月份（不拉正式表全表）
    # 员工可见起始月：员工端只显示该月及之后（管理员不受影响）
    svf = perf.staff_visible_from(db)
    if svf:
        months = [m for m in months if m >= svf]
    if month not in months:
        month = months[-1] if months else ""
    if not month:
        # 无可显示月份（无记录或被起始月全部隐藏）：直接空态，不读全量数据
        return templates.TemplateResponse("my_perf.html", {
            "request": request, "current_user": user, "month": "",
            "months": months, "daily": [], "summary": None,
            "daily_totals": None})
    # 当月全部逐日明细（升序）——读物化表 person_daily_stats
    daily = perf.daily_perf_person(db, month, code)
    daily_totals = None
    if daily:
        daily_totals = {
            "records": sum(int(d.get("records") or 0) for d in daily),
            "p1": sum(int(d.get("p1") or 0) for d in daily),
            "p2": sum(int(d.get("p2") or 0) for d in daily),
            "points": sum(int(d.get("points") or 0) for d in daily)}
    me = next((m for m in perf.month_perf(db, month)
               if m["code"] == code), None)
    summary = None
    if me:
        adj = perf.adjust_map(db, month).get(code, 0)
        from app.services import perf as _pf
        summary = {"records": me["records"], "p1": me["p1"], "p2": me["p2"],
                   "points": me["points"], "amount": me["amount"],
                   "adjust": adj, "payable": me["amount"] + adj,
                   "rate37": me["rate37"],
                   "dups": _pf.month_dup_map(db, month).get(code, 0)}
    return templates.TemplateResponse("my_perf.html", {
        "request": request, "current_user": user, "month": month,
        "months": months, "daily": daily, "summary": summary,
        "daily_totals": daily_totals})


# ---------------- V3 绩效 / 工资 ----------------
@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: Optional[User] = Depends(require_login),
              db: Session = Depends(get_db), staff: str = "", month: str = "",
              msg: str = "", err: str = ""):
    """管理端数据看板：逐月趋势 + 当月视图(可切换历史月份) + 员工维度 + 模型分析。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import dashboard as D
    from app.services import perf as _p
    monthly = D.monthly_series_from_db(db)
    _want = month if month and month in [m["month"] for m in monthly] else (
        monthly[-1]["month"] if monthly else "")
    if not monthly or not D.month_has_metrics(db, _want):
        # 物化缺失（首次/新数据）→ 实时回填并写表
        from app.models import MonthPerfRecord as _MPR
        for _mo in sorted({r.month for r in db.query(_MPR).all()}):
            if not D.month_has_metrics(db, _mo):
                D.sync_dash_metrics(db, _mo)
        monthly = D.monthly_series_from_db(db)
    sel = month if month in [m["month"] for m in monthly] else (
        monthly[-1]["month"] if monthly else "")
    idx = [m["month"] for m in monthly].index(sel) if sel else -1
    cur = monthly[idx] if idx >= 0 else None
    prev = monthly[idx - 1] if idx > 0 else None
    labels = [m["month"][5:] + "月" for m in monthly]
    charts = {
        "points": D.svg_line([m["points"] for m in monthly], labels,
                             color="#2f6fed"),
        "amount": D.svg_line([m["amount"] for m in monthly], labels,
                             color="#1a7f37"),
        "records": D.svg_line([m["records"] for m in monthly], labels,
                              color="#b45309"),
        "p2rate": D.svg_line([round(m["p2rate"] * 100, 1) for m in monthly],
                             labels, color="#7c3aed", fmt="{:,.1f}"),
    }
    compare = None
    if cur and prev:
        compare = {"labels": ["总点数", "工资(万円)", "有效店", "人均点数",
                              "人均工资(万円)", "2点率(%)"],
                   "cur": [cur["points"], round(cur["amount"] / 10000, 1),
                           cur["records"], round(cur["per_emp_points"], 1),
                           round(cur["per_emp_amount"] / 10000, 1),
                           round(cur["p2rate"] * 100, 1)],
                   "prev": [prev["points"], round(prev["amount"] / 10000, 1),
                            prev["records"], round(prev["per_emp_points"], 1),
                            round(prev["per_emp_amount"] / 10000, 1),
                            round(prev["p2rate"] * 100, 1)]}
    opts = D.staff_options(db)
    staff_series = D.staff_series(db, staff) if staff else []
    staff_name = dict(opts).get(staff, staff)
    if cur is None:
        return templates.TemplateResponse("dashboard.html", {
            "request": request, "current_user": user, "monthly": [],
            "cur": None, "prev": None, "compare": None, "charts": charts,
            "opts": [], "staff": "", "months": [], "sel": "",
            "staff_name": "", "staff_series": [], "top": [], "new_staff": [],
            "gone_staff": [], "quality": {}, "msg": msg, "err": err,
            "page_state": {"page": "dashboard", "months": [], "current": None,
                           "staff": "", "sel": "", "chart": {}}})
    _p.warm_config(db, sel)
    page_state = {"page": "dashboard", "months": [m["month"] for m in monthly],
                  "current": cur, "staff": staff, "sel": sel,
                  "per_point": _p.month_per_point(db, sel),
                  "bonus_group": _p.bonus_params(sel)[0],
                  "bonus_amount": _p.bonus_params(sel)[1],
                  "chart": {
                      "labels": labels,
                      "points": [m["points"] for m in monthly],
                      "amount": [m["amount"] for m in monthly],
                      "records": [m["records"] for m in monthly],
                      "p2rate": [round(m["p2rate"] * 100, 1) for m in monthly],
                      "p1": [m["p1"] for m in monthly],
                      "p2": [m["p2"] for m in monthly],
                      "cur_p1": cur["p1"], "cur_p2": cur["p2"],
                      "employees": [m["employees"] for m in monthly],
                      "hc": D.headcount_changes(db),
                      "compare": compare,
                      "staff_points": [x["points"] for x in staff_series],
                      "staff_records": [x["records"] for x in staff_series],
                      "staff_amount": [x["amount"] for x in staff_series],
                      "staff_labels": [x["month"][5:] + "月" for x in staff_series],
                  }}
    company_analysis_html = ""
    try:
        import hashlib as _hb
        from app.models import StaffAnalysis as _SA
        _facts = D.fact_text(db, None)
        _fp = _hb.md5(_facts.encode("utf-8")).hexdigest()
        _row = db.query(_SA).filter(_SA.person_code == "COMPANY",
                                    _SA.month == "ALL").first()
        if _row and _row.fingerprint == _fp and _row.content:
            from app.services.dashboard import render_analysis_html as _rh
            company_analysis_html = _rh(_row.content)
    except Exception:  # noqa: BLE001
        pass
    _pl = D.month_payloads(db, sel) if sel else {}
    top = _pl.get("top_staff") or (D.top_staff(db, sel, 8) if sel else [])
    new_staff = [(x["name"], (x["points"], x["amount"]))
                 for x in _pl.get("new_staff", [])] or (
                     D.staff_changes(db, sel)[0] if sel else [])
    gone_staff = [(x["name"], (x["points"], x["amount"]))
                  for x in _pl.get("gone_staff", [])] or (
                      D.staff_changes(db, sel)[1] if sel else [])
    quality = _pl.get("quality") or (D.quality_stats(db, sel) if sel else {})
    return templates.TemplateResponse("dashboard.html", {
        "request": request, "current_user": user, "monthly": monthly,
        "cur": cur, "prev": prev, "compare": compare, "charts": charts,
        "opts": opts, "staff": staff, "months": [m["month"] for m in monthly],
        "sel": sel, "staff_name": staff_name, "staff_series": staff_series,
        "top": top, "new_staff": new_staff, "gone_staff": gone_staff,
        "quality": quality, "company_analysis_html": company_analysis_html,
        "msg": msg, "err": err, "page_state": page_state,
    })


@router.get("/dashboard/staff", response_class=HTMLResponse)
def dashboard_staff_module(request: Request,
                           user: Optional[User] = Depends(require_login),
                           db: Session = Depends(get_db), staff: str = ""):
    """员工维度模块（htmx 局部刷新）：趋势图 + 明细 + 分析（懒生成存库）。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import dashboard as D
    ss = D.staff_series(db, staff) if staff else []
    chart = (D.svg_line([x["points"] for x in ss],
                        [x["month"][5:] + "月" for x in ss],
                        color="#2f6fed") if ss else "")
    name = dict(D.staff_options(db)).get(staff, staff)
    ok = D.staff_sample_ok(db, staff, ss[-1]["month"]) if (staff and ss) else False
    analysis = ""
    if staff and ss and ok:
        analysis = D.ensure_staff_analysis(db, staff, ss[-1]["month"])
    from app.services.dashboard import render_analysis_html
    return templates.TemplateResponse("staff_module.html", {
        "request": request, "current_user": user, "staff": staff,
        "name": name, "series": ss, "chart": chart,
        "analysis": render_analysis_html(analysis) if analysis else "",
        "sample_ok": ok,
    })


@router.get("/dashboard/analysis", response_class=HTMLResponse)
def dashboard_analysis(request: Request,
                       user: Optional[User] = Depends(require_login),
                       db: Session = Depends(get_db), staff: str = ""):
    """返回模型分析 HTML 片段（页面加载自动调用）。
    总体分析落盘（数据指纹缓存：数据不变读库秒开）；员工分析存 staff_analyses。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import dashboard as D
    from app.services.ai_chat import configured, chat
    if staff:
        return HTMLResponse(_render_analysis(
            D.ensure_staff_analysis(db, staff,
                                    (D.staff_series(db, staff) or [{}])[-1].get("month", ""))))
    import hashlib
    from app.models import StaffAnalysis
    facts = D.fact_text(db, None)
    fp = hashlib.md5(facts.encode("utf-8")).hexdigest()
    row = db.query(StaffAnalysis).filter(
        StaffAnalysis.person_code == "COMPANY",
        StaffAnalysis.month == "ALL").first()
    if row and row.fingerprint == fp and row.content:
        return HTMLResponse(_render_analysis(row.content, cached=True))
    if not configured():
        return HTMLResponse("<p class='hint'>未配置 AI（.env 的 AI_API_KEY）</p>")
    prompt = (
        "你是巡店结算系统的经营分析师。下面是各月经营事实数据：\n\n"
        + facts +
        "\n请用中文输出，精炼要点式（不要段落废话，不要'总体来看/综上所述'等套话，每条一句话，关键数字用**加粗**）：\n"
        "1) **一句话结论**：本期经营总体如何；\n"
        "2) **变好**（最多3条）：指标、幅度、可能原因；\n"
        "3) **变差**（最多3条）：指标、幅度、可能原因（如新增店多为1点店/重复巡店过多/2点率下滑/奖金口径等，明确数据支持哪个判断）；\n"
        "4) **建议**（最多3条）：指名到具体指标或人群，说清做什么。\n"
        "只依据数据判断，原因类表述区分'数据显示'与推断。")
    try:
        text = chat(prompt, max_tokens=2500, timeout=240)
    except Exception as e:  # noqa: BLE001
        return HTMLResponse(f"<p class='hint'>AI 分析失败：{type(e).__name__}</p>")
    if row is None:
        db.add(StaffAnalysis(person_code="COMPANY", month="ALL",
                             fingerprint=fp, content=text))
    else:
        row.fingerprint, row.content = fp, text
    db.commit()
    return HTMLResponse(_render_analysis(text, cached=False))


def _render_analysis(text: str, cached: bool = True) -> str:
    from app.services.dashboard import render_analysis_html
    return render_analysis_html(text)


@router.get("/config", response_class=HTMLResponse)
def sys_config_page(request: Request,
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """系统配置：每点金额 / 达标点数 / 达标奖金（按月生效，计算全程读取）。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import perf as _p
    from app.models import SysConfig
    rows = db.query(SysConfig).order_by(SysConfig.config_month.desc()).all()
    _p.warm_config(db, "2026-09")
    per_point = _p.month_per_point(db, "2026-09")
    g, a = _p.bonus_params("2026-09")
    svf = _p.staff_visible_from(db)
    return templates.TemplateResponse("config.html", {
        "request": request, "current_user": user,
        "rows": rows, "cur": rows[0] if rows else None,
        "per_point": per_point, "bonus_g": g, "bonus_a": a,
        "staff_visible_from": svf,
        "msg": "", "err": "",
    })


@router.post("/config/save")
def sys_config_save(request: Request, csrf_token: str = Form(...),
                    per_point: int = Form(250),
                    bonus_group: int = Form(68),
                    bonus_amount: int = Form(3000),
                    staff_visible_from: str = Form(""),
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """保存系统配置（全局单值，最新一条生效）。"""
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    if not (0 < per_point <= 10000 and 0 < bonus_group <= 1000
            and 0 <= bonus_amount <= 1000000):
        return RedirectResponse("/config?err=配置值超出合理范围", status_code=303)
    svf = (staff_visible_from or "").strip()
    if svf and not (len(svf) == 7 and svf[:4].isdigit()
                    and svf[4] == "-" and svf[5:].isdigit()):
        return RedirectResponse("/config?err=员工可见起始月格式应为 YYYY-MM（留空=不限制）",
                                status_code=303)
    from app.models import SysConfig
    row = db.query(SysConfig).order_by(SysConfig.id.desc()).first()
    if row is None:
        db.add(SysConfig(config_month="", per_point=per_point,
                         bonus_group=bonus_group, bonus_amount=bonus_amount,
                         staff_visible_from=svf,
                         updated_by=user.id))
    else:
        row.per_point, row.bonus_group, row.bonus_amount =             per_point, bonus_group, bonus_amount
        row.staff_visible_from = svf
        row.updated_by = user.id
    db.commit()
    from app.services import perf as _p
    _p.clear_config_cache()
    from urllib.parse import quote
    return RedirectResponse(
        "/config?msg=" + quote(f"已保存：每点{per_point}円 / 满{bonus_group}点奖{bonus_amount}円 / 员工可见起始月{svf or '不限制'}"),
        status_code=303)


@router.get("/perf", response_class=HTMLResponse)
@router.get("/v3/perf", response_class=HTMLResponse)
def perf(request: Request,
            user: Optional[User] = Depends(require_login),
            db: Session = Depends(get_db), month: str = "",
            date: str = "", staff: str = "", period: str = "half1"):
    if user is None or user.role != "admin":
        return _denied()
    from app.services import perf
    months = sorted({(str(r.japan_date or ""))[:7]
                     for r in db.query(FormalRecord).all()
                     if r.japan_date})
    if month not in months:
        month = months[-1] if months else ""
    rows = perf.month_perf(db, month)
    summary = perf.company_summary(db, month)
    from app.services import dashboard as _DD
    dup_map = (_DD.month_payloads(db, month).get("dup_map")
               if month else {}) or (perf.month_dup_map(db, month)
                                     if month else {})
    dup_total = sum(dup_map.values())
    # 上月找平 = 上月未找平余量（diff−adjust，同一张表 payroll_period_rows）
    from app.services import period as _payroll
    adj = _payroll.carry_map(db, month) if month else {}
    adj_map = {k: v[0] for k, v in adj.items()}          # 余量(点)
    adj_amt_map = {k: v[1] for k, v in adj.items()}      # 余量金额(×上月单价)
    adj_total = sum(adj_map.values())
    adj_amt_total = sum(adj_amt_map.values())
    per_point = month and perf.month_per_point(db, month) or 250
    payroll_rows = _payroll.period_rows(db, month) if month else []
    payroll_total = {
        "settle_amt": sum(r["settle_amt"] for r in payroll_rows),
        "diff_amt": sum(r["diff_amt"] for r in payroll_rows),
        "diff": sum(r["diff"] for r in payroll_rows),
        "half1": sum(r["half1"] for r in payroll_rows),
        "half2": sum(r["half2"] for r in payroll_rows),
        "half1_amt": sum(r["half1_amt"] for r in payroll_rows),
        "half2_amt": sum(r["half2_amt"] for r in payroll_rows),
    }
    # 本月找平（实时读薪资找平同一张表 payroll_period_rows，保存后立即同步）
    cur_adj = {r["code"]: (r["adj"], r["adj_amt"]) for r in payroll_rows}
    # 两期发薪视图（月内发两次工资照此算）：
    # code -> (上半月点, 上半月金额(含该期奖金), 上半月奖金, 下半月点, 下半月金额, 下半月奖金)
    half_map = {}
    for r in payroll_rows:
        half_map[r["code"]] = (r["half1"], r["half1_amt"], r["half1_bonus"],
                               r["half2"], r["half2_amt"], r["half2_bonus"])
    # 按期的 有效店/1点/2点（期视图这三列只看该期，避免全月数据误导）
    half_stats = _payroll.half_stats_map(db, month) if month else {}
    # 两期应付（金额找平）：上半月应付=上半月金额+上月金额余量(正补负扣)；
    # 若上半月不够扣(为负)→上半月发0、剩余负数转到下半月继续扣。
    pay_map = {}
    for r in rows:
        hf = half_map.get(r["code"], (0, 0, 0, 0, 0, 0))
        carry = adj_amt_map.get(r["code"], 0)
        p1 = hf[1] + carry
        if p1 < 0:
            pay_map[r["code"]] = (0, hf[4] + p1)
        else:
            pay_map[r["code"]] = (p1, hf[4])
    if period not in ("half1", "half2"):
        period = "half1"
    perf.warm_config(db, month)
    bonus_g, bonus_a = perf.bonus_params(month)
    page_state = {
        "page": "perf", "month": month, "period": period,
        "employees": len(rows),
        "total_points": summary.get("total_points", 0),
        "salary_total": summary.get("total_amount", 0),
        "payroll_total": payroll_total.get("half1_amt", 0)
        + payroll_total.get("half2_amt", 0),
        "payroll_rows": len(payroll_rows),
        "per_point": per_point,
        "bonus_group": bonus_g, "bonus_amount": bonus_a,
        "dup_total": dup_total,
        "point1_stores": summary.get("p1", 0),
        "point1_points": summary.get("p1", 0),
        "point2_stores": summary.get("p2", 0),
        "point2_points": summary.get("p2", 0) * 2,
    }
    return templates.TemplateResponse("perf.html", {
        "request": request, "current_user": user, "month": month,
        "months": months, "rows": rows, "period": period,
        "summary": summary, "adj_map": adj_map, "adj_total": adj_total,
        "adj_amt_map": adj_amt_map, "adj_amt_total": adj_amt_total,
        "per_point": per_point, "half_stats": half_stats,
        "cur_adj": cur_adj, "half_map": half_map,
        "bonus_g": bonus_g, "bonus_a": bonus_a,
        "pay_map": pay_map, "dup_map": dup_map, "dup_total": dup_total,
        "payroll_rows": payroll_rows, "payroll_total": payroll_total,
        "page_state": page_state})


@router.post("/perf/per-point")
def v3_set_per_point(request: Request, month: str = Form(""),
                     per_point: int = Form(250),
                     csrf_token: str = Form(...),
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db)):
    """设置该月点数单价（円/点）：月绩效工资与薪资找平金额按新单价重算；
    已确认找平记录锁存当时单价，不受影响。"""
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import perf
    from urllib.parse import quote as _q
    if per_point <= 0 or not month:
        return RedirectResponse(
            f"/perf?month={month}&msg={_q('单价需为正整数')}",
            status_code=303)
    perf.set_month_per_point(db, month, per_point)
    return RedirectResponse(
        f"/perf?month={month}&msg="
        f"{_q(f'{month} 点数单价已设为 {per_point}円/点（工资/找平金额已重算；已确认找平按当时单价不变）')}",
        status_code=303)


@router.get("/perf/export")
@router.get("/v3/perf/export")
def perf_export(request: Request, user: Optional[User] =
                   Depends(require_login),
                   db: Session = Depends(get_db), month: str = "",
                   period: str = "half1"):
    """导出发薪表 Excel（按期：上半月/下半月 + 日明细）。"""
    if user is None or user.role != "admin":
        return _denied()
    months = sorted({(str(r.japan_date or ""))[:7]
                     for r in db.query(FormalRecord).all()
                     if r.japan_date})
    if month not in months:
        month = months[-1] if months else ""
    from app.services import report
    data, fname = report.build_payroll_workbook(db, month, period)
    # 导出 = 发放事实：系统无发薪反馈，导出后离线按表发放 → 该期金额快照入台账
    if month:
        try:
            from app.services import period as _payroll
            half = 1 if period == "half1" else 2
            _payroll.register_exported_half(db, month, half, paid_by=user.id)
        except Exception:  # noqa: BLE001  登记失败不影响下载
            db.rollback()
    from fastapi.responses import StreamingResponse
    import io
    bio = io.BytesIO(data)
    from urllib.parse import quote
    ascii_name = "wage_%s_%s.xlsx" % (month or "all", period)
    cd = "attachment; filename=" + ascii_name + "; filename*=UTF-8''" + quote(fname)
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": cd})


# ---------------- V3 月度对账 ----------------
@router.get("/recon", response_class=HTMLResponse)
@router.get("/v3/recon", response_class=HTMLResponse)
def recon_page(request: Request,
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db), msg: str = "",
                  task_id: int = 0):
    if user is None or user.role != "admin":
        return _denied()
    from app.models import ReconDayRow, ReconResult, ReconTask
    tasks = (db.query(ReconTask)
             .filter(ReconTask.kind == "monthly_v3")
             .order_by(ReconTask.id.desc()).all())
    cur = db.get(ReconTask, task_id) if task_id else None
    diffs = (db.query(ReconResult).filter(ReconResult.task_id == task_id)
             .order_by(ReconResult.submitter_code).all()) if cur else []
    day_rows = []
    if cur:
        from app.models import Person
        _names = {p.code: p.display_name for p in db.query(Person).all()}
        day_rows = [{
            "ref_date": d.ref_date, "person_code": d.person_code,
            "name": _names.get(d.person_code, ""),
            "sys_points": d.sys_points or 0, "rep_points": d.rep_points or 0,
            "diff": d.diff or 0, "side": d.side, "note": d.note or ""}
            for d in (db.query(ReconDayRow)
                      .filter(ReconDayRow.task_id == task_id)
                      .order_by(ReconDayRow.ref_date,
                                ReconDayRow.person_code).all())]
    sys_rows = []
    diff_rows = []
    next_month = ""
    if cur:
        from app.services import recon as vr
        from app.services import perf as _vp

        def _pay_diff(_db, sys_p, rep_p, mth):
            pp = _vp.month_per_point(_db, mth)
            return _vp.salary_for(rep_p, pp, mth) - _vp.salary_for(sys_p, pp, mth)  # 负=扣款/正=补款

        amap = vr.task_adjust_map(db, task_id)
        next_month = vr._next_month((cur.params or {}).get("month", ""))
        for d in diffs:
            diff_rows.append({
                "code": d.submitter_code,
                "name": (d.note or "").replace(" 点数差异", "") or
                        d.submitter_code,
                "sys": d.system_value or 0, "rep": d.report_value or 0,
                "diff": d.diff or 0,
                # 应找平金额(円,含奖金) = 系统已发金额 − 对账金额(工资规则)
                "amount": _pay_diff(db, d.system_value or 0,
                                    d.report_value or 0,
                                    (cur.params or {}).get("month", "")),
                "adjusted": amap.get(d.submitter_code)})
        if (cur.summary or {}).get("sys_only"):
            from app.models import Person
            persons = {p.code: p.display_name for p in db.query(Person).all()}
            sys_rows = [{"code": c, "name": persons.get(c, c)}
                        for c in (cur.summary or {}).get("sys_only", [])]
    kind = (cur.summary or {}).get("kind") if cur else ""
    page_state = {
        "page": "recon", "cur_task": cur.id if cur else None,
        "cur_status": cur.status if cur else None,
        "month": (cur.params or {}).get("month", "") if cur else "",
        "tasks": [{"id": t.id, "month": (t.params or {}).get("month", ""),
                   "status": t.status,
                   "replaced_by": (t.params or {}).get("replaced_by")}
                  for t in tasks],
        "compared": (cur.summary or {}).get("compared", 0) if cur else 0,
        "diff_count": (cur.summary or {}).get("diff_count", 0) if cur else 0,
        "ai_ready": bool(cur and (cur.summary or {}).get("ai_interpret")),
    }
    return templates.TemplateResponse("recon.html", {
        "request": request, "current_user": user, "msg": msg,
        "tasks": tasks, "cur": cur, "diffs": diff_rows,
        "sys_rows": sys_rows, "next_month": next_month,
        "day_rows": day_rows, "kind": kind, "page_state": page_state})


@router.post("/recon/upload2")
@router.post("/v3/recon/upload2")
async def recon_upload2(request: Request, month: str = Form(""),
                           csrf_token: str = Form(...),
                           file=File(...),
                           user: Optional[User] = Depends(require_login),
                           db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    content = await file.read()
    import os as _os
    try:
        from app.services import recon
        t, older = recon.submit_task(
            db, month, file.filename, content, user.id,
            sync=_os.environ.get("RECON_SYNC") == "1")
    except Exception as e:  # noqa: BLE001
        raise HTTPException(400, f"对账文件无法登记任务: {type(e).__name__}: {e}")
    msg = f"对账任务 # {t.id} 已提交，正在离线处理（完成后可下载结果）"
    if older:
        msg = (f"检测到 {month} 已有对账版本（{'、'.join('#'+str(x) for x in older)}），"
               f"已标记为「上一版」保留可查，本次为新对账；" + msg)
    if _os.environ.get("RECON_SYNC") == "1":
        msg = msg.replace("正在离线处理（完成后可下载结果）",
                          "已同步处理完成")
    from urllib.parse import quote as _quote
    return RedirectResponse(f"/v3/recon?msg={_quote(msg)}&task_id={t.id}",
                            status_code=303)



@router.post("/recon/{task_id}/interpret")
@router.post("/v3/recon/{task_id}/interpret")
def recon_interpret(task_id: int, request: Request,
                    csrf_token: str = Form(...),
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """AI 对账解读（可选；未配置模型时提示，不影响对账）。"""
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import recon
    res = recon.interpret_task(db, task_id)
    msg = res.get("msg", "") or ("已生成 AI 解读" if res.get("ok") else "失败")
    return RedirectResponse(f"/v3/recon?task_id={task_id}&msg={msg}",
                            status_code=303)


@router.post("/recon/{task_id}/adjust/{code}")
@router.post("/v3/recon/{task_id}/adjust/{code}")
def recon_adjust(task_id: int, code: str, request: Request,
                 action: str = Form("add"),
                 csrf_token: str = Form(...),
                 user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db)):
    """找平：对账差异确认 → 记入下月工资（加/减）；action=remove 取消。"""
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import recon as vr
    if action == "add":
        res = vr.confirm_adjust(db, task_id, code, user.id)
        msg = (f"已确认找平：{res['amount']:+d} 円记入下月工资"
               if res["ok"] else f"无法确认：{res.get('msg', '?')}")
    elif action == "remove":
        vr.cancel_adjust(db, task_id, code)
        msg = "已取消该员工的找平确认"
    else:
        raise HTTPException(400, f"未知动作: {action}")
    return RedirectResponse(f"/v3/recon?task_id={task_id}&msg={msg}",
                            status_code=303)


@router.get("/recon/export")
@router.get("/v3/recon/export")
def recon_export(request: Request, task_id: int = 0,
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """导出对账差异 Excel：差异明细 + 反向名单（系统有而对账文件无）。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import report
    res = report.build_recon_diff_workbook(db, task_id)
    if res is None:
        raise HTTPException(404, "对账任务不存在")
    data, fname = res
    from fastapi.responses import StreamingResponse
    import io
    bio = io.BytesIO(data)
    from urllib.parse import quote
    ascii_name = f"recon_diff_{task_id}.xlsx"
    cd = "attachment; filename=" + ascii_name + "; filename*=UTF-8''" + quote(fname)
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": cd})


@router.get("/recon/report")
@router.get("/v3/recon/report")
def recon_report(request: Request, task_id: int = 0,
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """一键生成《月度对账报告.xlsx》（摘要+差异+反向名单+找平留痕）。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import report
    res = report.build_recon_report_workbook(db, task_id, user.display_name)
    if res is None:
        raise HTTPException(404, "对账任务不存在")
    data, fname = res
    from fastapi.responses import StreamingResponse
    import io
    bio = io.BytesIO(data)
    from urllib.parse import quote
    ascii_name = f"recon_report_{task_id}.xlsx"
    cd = "attachment; filename=" + ascii_name + "; filename*=UTF-8''" + quote(fname)
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": cd})


@router.get("/recon/result")
@router.get("/v3/recon/result")
def recon_result(request: Request, task_id: int = 0,
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """下载对账任务产物 Excel（离线任务完成后落盘；历史任务兜底现算）。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.models import ReconTask
    from fastapi.responses import StreamingResponse
    import io, os
    t = db.get(ReconTask, task_id) if task_id else None
    if t is None:
        raise HTTPException(404, "对账任务不存在")
    if t.status not in ("done", "parsed"):
        return RedirectResponse(f"/v3/recon?task_id={task_id}"
                                "&msg=任务尚未完成，请稍后刷新再下载",
                                status_code=303)
    bio = io.BytesIO()
    fp = (t.params or {}).get("result_path", "")
    if fp and os.path.exists(fp):
        with open(fp, "rb") as f:
            bio.write(f.read())
    else:
        from app.services import recon
        wb = recon.build_report(db, task_id, user.display_name)
        if wb is None:
            raise HTTPException(404, "对账任务不存在")
        wb.save(bio)
    bio.seek(0)
    from urllib.parse import quote
    ascii_name = f"recon_result_{task_id}.xlsx"
    fname = f"对账结果_任务{task_id}.xlsx"
    cd = "attachment; filename=" + ascii_name + "; filename*=UTF-8''" + quote(fname)
    return StreamingResponse(
        bio,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": cd})


# ---------------- 管理端：月度重算（防同月补传双算） ----------------
def _month_of_modified(rr):
    return (rr.modified_raw or "")[:7]


@router.post("/month/rebuild")
@router.post("/v3/month/rebuild")
def month_rebuild(request: Request, month: str = Form(""),
                     csrf_token: str = Form(...),
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db)):
    """按结算月重算并重建正式表（幂等；pending 申诉存在时拒绝）。"""
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from urllib.parse import quote as _q
    # 月份格式（含年份范围）校验的唯一关口在 flow.rebuild_month，此处只消费结果
    res = flow.rebuild_month(db, month, user.id)
    if not res["ok"]:
        return RedirectResponse(f"/dashboard?err={_q(res.get('msg', '无法重算'))}",
                                status_code=303)
    return RedirectResponse(
        f"/dashboard?msg=已重算 {len(res['files'])} 个文件并重建正式表："
        f"条数 {res['formal_before']} → {res['formal_after']}，"
        f"点数 {res['points_before']} → {res['points_after']}",
        status_code=303)


# ---------------- 月度分期对账偏差表（薪资找平，独立功能页） ----------------
@router.get("/payroll-settle", response_class=HTMLResponse)
def payroll_settle_page(request: Request, user: Optional[User] =
                        Depends(require_login),
                        db: Session = Depends(get_db), month: str = "",
                        staff: str = ""):
    """薪资找平：月 × 员工 分期对账偏差表（独立页面，可按员工定位）。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import period as _payroll
    from app.models import PayrollPeriodRow, PersonDailyStat
    months = sorted(
        {(str(r.ref_date or ""))[:7] for r in db.query(PersonDailyStat).all()
         if r.ref_date and (str(r.ref_date))[:7] >= "2026-07"}
        | {r.month for r in db.query(PayrollPeriodRow).all()
           if r.month >= "2026-07"})
    if month not in months:
        month = months[-1] if months else ""
    rows = _payroll.period_rows(db, month) if month else []
    # 员工定位：从绩效行「找平」按钮带着 staff 参数进来 → 该员工排最前（不隐藏其他人）
    if staff and rows:
        rows = sorted(rows, key=lambda r: (r["code"] != staff, r["code"]))
    total = {
        "half1": sum(r["half1"] for r in rows),
        "half2": sum(r["half2"] for r in rows),
        "bonus": sum(r["half1_bonus"] + r["half2_bonus"] for r in rows),
        "half_amt": sum(r["half1_amt"] + r["half2_amt"] for r in rows),
        "settle": sum(r["settle"] for r in rows),
        "settle_amt": sum(r["settle_amt"] for r in rows),
        "prev": sum(r["prev"] for r in rows),
        "prev_amt": sum(r["prev_amt"] for r in rows),
        "diff": sum(r["diff"] for r in rows),
        "diff_amt": sum(r["diff_amt"] for r in rows),
    }
    page_state = {"page": "payroll_settle", "month": month,
                  "rows": len(rows), "total": total,
                  "adjusted": sum(1 for r in rows if r["adj"])}
    from app.services import perf as _vp
    _vp.warm_config(db, month)
    bonus_g, bonus_a = _vp.bonus_params(month)
    return templates.TemplateResponse("payroll_settle.html", {
        "request": request, "current_user": user, "month": month,
        "months": months, "rows": rows, "total": total, "staff": staff,
        "bonus_g": bonus_g, "bonus_a": bonus_a,
        "recon_pending": bool(month) and not _payroll.month_has_recon(db, month),
        "page_state": page_state})


@router.post("/payroll-settle/generate")
def payroll_settle_generate(request: Request, month: str = Form(""),
                            csrf_token: str = Form(...),
                            user: Optional[User] = Depends(require_login),
                            db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import period as _payroll
    from urllib.parse import quote as _q
    try:
        res = _payroll.sync_period_table(db, month)
        from app.services import dashboard as _D
        try:
            _D.sync_dash_metrics(db, month)   # 算完工资 → 物化看板统计
        except Exception:  # noqa: BLE001
            pass
        try:
            # 算完工资 → 后台自动为全部员工预生成分析（落盘，样本不足跳过）
            import threading as _th
            from app.db import SessionLocal as _SL

            def _bg():
                _db = _SL()
                try:
                    _D.analyze_all_staff(_db, month)
                except Exception:  # noqa: BLE001
                    pass
                finally:
                    _db.close()
            _th.Thread(target=_bg, daemon=True).start()
        except Exception:  # noqa: BLE001
            pass
        return RedirectResponse(
            f"/payroll-settle?month={month}&msg={_q('已生成/更新 ' + str(res.get('rows', 0)) + ' 人，看板统计已刷新')}",
            status_code=303)
    except Exception as e:  # noqa: BLE001
        return RedirectResponse(
            f"/payroll-settle?month={month}&msg={_q('生成失败: ' + str(e)[:80])}",
            status_code=303)


@router.post("/payroll-settle/{month}/{person_code}/update")
def payroll_settle_update(month: str, person_code: str, request: Request,
                          half1_amount: int = Form(0),
                          half2_amount: int = Form(0),
                          adjust_delta: int = Form(0),
                          csrf_token: str = Form(...),
                          user: Optional[User] = Depends(require_login),
                          db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import period as _payroll
    from urllib.parse import quote as _q
    res = _payroll.set_period_values(
        db, month, person_code, half1_amount, half2_amount,
        user_id=user.id, adjust_delta=adjust_delta)
    return RedirectResponse(
        f"/payroll-settle?month={month}&msg={_q(res.get('msg', '已保存'))}",
        status_code=303)


@router.get("/payroll-settle/export")
def payroll_settle_export(request: Request,
                          user: Optional[User] = Depends(require_login),
                          db: Session = Depends(get_db), month: str = ""):
    if user is None or user.role != "admin":
        return _denied()
    from app.services import report
    data, fname = report.build_payroll_settle_workbook(db, month)
    from fastapi.responses import StreamingResponse
    import io as _io
    buf = _io.BytesIO(data)
    return StreamingResponse(
        buf, media_type=("application/vnd.openxmlformats-officedocument"
                         ".spreadsheetml.sheet"),
        headers={"Content-Disposition": f'attachment; filename="{fname}"'})
