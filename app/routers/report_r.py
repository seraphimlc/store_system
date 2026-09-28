# -*- coding: utf-8 -*-
"""员工每日填报路由（规格 v7 §9）。

- 员工端：`/my/report`（填报 + 我的历史）
- 管理端：`/staff-reports*`（Chunk 4/5 追加）
"""
from datetime import timedelta
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import User
from app.routers.auth_r import csrf_ok, require_login
from app.templating import get_templates

from app.forms import require_form_token as _dep_form_token  # noqa: E402

router = APIRouter(dependencies=[Depends(_dep_form_token)])
templates = get_templates()


def _denied():
    return RedirectResponse("/login", status_code=302)


@router.get("/my/report", response_class=HTMLResponse)
def my_report_page(request: Request,
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db), month: str = "",
                   msg: str = "", err: str = ""):
    """员工填报页：今天还没填 → 表单；已填 → 只读展示；下方是本月历史。"""
    if user is None or user.role != "staff" or not user.person_code:
        return _denied()
    from app.services import daily_report
    today = daily_report.jst_today()
    existing = daily_report.today_report(db, user.person_code)
    months = daily_report.my_months(db, user.person_code)
    this_month = today.strftime("%Y-%m")
    if this_month not in months:
        months = [this_month] + months
    if not month or month not in months:
        month = this_month
    view = daily_report.month_days(db, user.person_code, month, today=today)
    series = daily_report.chart_series(db, user.person_code, today=today)
    chart = {"series": series,
             "geo": daily_report.chart_geometry(series) if series["show"] else {}}

    return templates.TemplateResponse("my_report.html", {
        "request": request, "current_user": user,
        "today": today, "existing": existing,
        "existing_jst": (existing.submitted_at + timedelta(hours=9)
                         ).strftime("%Y-%m-%d %H:%M") if existing else "",
        "view": view, "chart": chart, "month": month, "months": months,
        "wd_labels": ["周一", "周二", "周三", "周四", "周五", "周六", "周日"],
        "msg": msg, "err": err,
    })


@router.post("/my/report")
def my_report_submit(request: Request,
                     area: str = Form(""), p1_cnt: str = Form(""),
                     p2_cnt: str = Form(""), client_ts: str = Form(""),
                     csrf_token: str = Form(""),
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db)):
    """提交今天的填报（普通表单 + 303，页面不依赖 JS）。"""
    if user is None or user.role != "staff" or not user.person_code:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import daily_report
    try:
        daily_report.submit_report(db, user, area=area, p1_cnt=p1_cnt,
                                   p2_cnt=p2_cnt, client_ts=client_ts)
        # 成功不留 URL 参数：页面上的数据本身就是反馈（错误才用 ?err= 提示）
        return RedirectResponse("/my/report", status_code=303)
    except daily_report.AlreadySubmitted:
        return RedirectResponse("/my/report?err=" + quote("今天已经填报过了（一天一次）"),
                                status_code=303)
    except ValueError as e:  # noqa: BLE001
        return RedirectResponse("/my/report?err=" + quote(str(e)),
                                status_code=303)


# ---------------- AI 分析报告（规格 §8） ----------------

@router.post("/staff-reports/compare/analyze")
def staff_reports_analyze(request: Request, start: str = Form(""),
                          end: str = Form(""), person_code: str = Form(""),
                          sort: str = Form("acc"),
                          csrf_token: str = Form(""),
                          user: Optional[User] = Depends(require_login),
                          db: Session = Depends(get_db)):
    """生成区间分析报告（异步）：数字由程序算，模型只写评语。"""
    from urllib.parse import quote as _q
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import report_ai
    s, e = _resolved_period(db, start, end)
    _, m = report_ai.start_analysis(db, user, s, e)
    q = "start=%s&end=%s&person_code=%s&sort=%s" % (s, e, person_code, sort)
    return RedirectResponse("/staff-reports/compare?" + q + "&msg=" + _q(m),
                            status_code=303)


@router.post("/staff-reports/analysis/{aid}/retry")
def staff_reports_analysis_retry(aid: int, request: Request,
                                 start: str = Form(""), end: str = Form(""),
                                 person_code: str = Form(""),
                                 csrf_token: str = Form(""),
                                 user: Optional[User] = Depends(require_login),
                                 db: Session = Depends(get_db)):
    from urllib.parse import quote as _q
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import report_ai
    _, m = report_ai.retry(db, aid)
    s, e = _resolved_period(db, start, end)
    q = "start=%s&end=%s&person_code=%s" % (s, e, person_code)
    return RedirectResponse("/staff-reports/compare?" + q + "&msg=" + _q(m),
                            status_code=303)


# ---------------- 员工端：我的核对结果（只看自己） ----------------

@router.post("/my/report/update")
def my_report_update(request: Request,
                     area: str = Form(""), p1_cnt: str = Form(""),
                     p2_cnt: str = Form(""), csrf_token: str = Form(""),
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db)):
    """修改今天已提交的填报（仅当天）。"""
    if user is None or user.role != "staff" or not user.person_code:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import daily_report
    try:
        daily_report.update_today(db, user, area=area, p1_cnt=p1_cnt, p2_cnt=p2_cnt)
        return RedirectResponse("/my/report", status_code=303)
    except daily_report.NoReport:
        return RedirectResponse("/my/report?err=" + quote("今天还没有填报记录"),
                                status_code=303)
    except ValueError as e:  # noqa: BLE001
        return RedirectResponse("/my/report?err=" + quote(str(e)), status_code=303)


@router.get("/my/report/feedback", response_class=HTMLResponse)
def my_report_feedback(request: Request,
                       user: Optional[User] = Depends(require_login),
                       db: Session = Depends(get_db), aid: int = 0):
    """员工端：我在某个区间报得准不准（**只给数字**：准确率 + 逐日 Δ）。

    评语只给管理员（2026-09-28 用户要求）；这里也从不把报告 payload 交给模板。
    """
    if user is None or user.role != "staff" or not user.person_code:
        return _denied()
    from app.models import StaffReportAnalysis
    from app.services import report_ai
    from app.services import perf as _perf
    svf = _perf.staff_visible_from(db)          # 员工可见起始月（默认 2026-10）
    done = [a for a in report_ai.available_periods(db, user.person_code)
            if not svf or str(a.period_start)[:7] >= svf]
    analysis = None
    if aid:
        analysis = db.get(StaffReportAnalysis, aid)
        if analysis is not None and (
                analysis.status != "done"
                or (svf and str(analysis.period_start)[:7] < svf)):
            analysis = None                     # 不在可见窗口内 → 当作没指定
    if analysis is None and done:
        analysis = done[0]
    from app.services import report_ai as _rai
    snap = _rai.employee_snapshot(db, analysis, user.person_code)
    return templates.TemplateResponse("my_report_feedback.html", {
        "request": request, "current_user": user, "analysis": analysis,
        "periods": done, "aid": aid, "mine": snap["person"],
        "days": snap["days"],
    })


# ---------------- 管理端：填报列表 + 对比 + 导出（规格 §9） ----------------

def _admin_guard(user):
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if user.role != "admin":
        return RedirectResponse("/my/perf", status_code=302)
    return None


def _as_date(s, default):
    from datetime import datetime as _dt
    try:
        return _dt.strptime((s or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return default


def _resolved_period(db, start, end):
    """未传/非法 → 取「最近一次上传文件」的日期范围。"""
    from app.services import daily_report
    d1, d2 = daily_report.suggest_period(db)
    a, b = _as_date(start, d1), _as_date(end, d2)
    return (b, a) if a > b else (a, b)


def _staff_options(db):
    from app.models import Person
    rows = (db.query(Person.code, Person.display_name)
            .order_by(Person.display_name).all())
    return [{"code": c, "name": n or c} for c, n in rows]


@router.get("/staff-reports", response_class=HTMLResponse)
def staff_reports_page(request: Request,
                       user: Optional[User] = Depends(require_login),
                       db: Session = Depends(get_db), start: str = "", end: str = "",
                       person_code: str = "", page: int = 1,
                       msg: str = "", err: str = ""):
    """员工每日填报列表（可按区间/人筛选）。"""
    g = _admin_guard(user)
    if g:
        return g
    from app.services import daily_report
    s, e = _resolved_period(db, start, end)
    data = daily_report.list_reports(db, start=s, end=e, person_code=person_code,
                                     page=page, per=50)
    from app.models import Person
    names = dict(db.query(Person.code, Person.display_name).all())
    return templates.TemplateResponse("staff_reports.html", {
        "request": request, "current_user": user, "data": data,
        "start": s, "end": e, "person_code": person_code,
        "names": names, "staff_opts": _staff_options(db), "msg": msg, "err": err,
        "jst_delta": timedelta(hours=9),
    })


@router.get("/staff-reports/compare", response_class=HTMLResponse)
def staff_reports_compare(request: Request,
                          user: Optional[User] = Depends(require_login),
                          db: Session = Depends(get_db), start: str = "", end: str = "",
                          person_code: str = "", sort: str = "acc", kind: str = "",
                          msg: str = "", err: str = ""):
    """区间对比：逐人合计 + 准确率排名 + 逐日明细（程序算；模型不参与）。"""
    g = _admin_guard(user)
    if g:
        return g
    from app.services import daily_report
    s, e = _resolved_period(db, start, end)
    res = daily_report.compare(db, s, e, person_code, sort=sort)
    daily = [r for r in res["daily"] if not kind or r["kind"] == kind]
    from app.i18n import CURRENT_LANG
    from app.services import report_ai
    analysis = report_ai.latest_for(db, s, e)
    cov_start, cov_end = report_ai.file_coverage(db)
    coverage_warn = ""
    if cov_end and e > cov_end:
        coverage_warn = ("该区间的结束日（%s）超出已导入文件的覆盖范围（最后一天 %s），"
                         "多出的天数系统侧没有数据" % (e, cov_end))
    report = {}
    if analysis is not None and (analysis.payload or {}).get("by_lang"):
        by = analysis.payload["by_lang"]
        report = by.get(CURRENT_LANG.get()) or next(iter(by.values()))
    return templates.TemplateResponse("staff_report_compare.html", {
        "request": request, "current_user": user, "res": res, "daily": daily,
        "start": s, "end": e, "person_code": person_code, "sort": sort, "kind": kind,
        "staff_opts": _staff_options(db), "msg": msg, "err": err,
        "analysis": analysis, "report": report,
        "ai_enabled": report_ai.report_ai_enabled(),
        "coverage_warn": coverage_warn, "cov_end": cov_end,
    })


@router.get("/staff-reports/export")
def staff_reports_export(user: Optional[User] = Depends(require_login),
                         db: Session = Depends(get_db), kind: str = "reports",
                         start: str = "", end: str = "", person_code: str = ""):
    """导出 Excel：kind=reports（自报明细）| compare（对比结果）。"""
    g = _admin_guard(user)
    if g:
        return g
    import io as _io
    from urllib.parse import quote as _q

    from fastapi.responses import StreamingResponse

    if kind not in ("reports", "compare"):      # 白名单：kind 会进 Content-Disposition
        return HTMLResponse("未知的导出类型", status_code=400)

    from app.services import report_export
    s, e = _resolved_period(db, start, end)
    if kind == "compare":
        data, fname = report_export.compare_xlsx(db, s, e, person_code)
    else:
        data, fname = report_export.reports_xlsx(db, s, e, person_code)
    cd = ("attachment; filename=staff_%s.xlsx; filename*=UTF-8''%s"
          % (kind, _q(fname)))
    return StreamingResponse(
        _io.BytesIO(data),
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"),
        headers={"Content-Disposition": cd})
