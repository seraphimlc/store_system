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

router = APIRouter()
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
        return RedirectResponse("/my/report?msg=" + quote("今日填报已提交"),
                                status_code=303)
    except daily_report.AlreadySubmitted:
        return RedirectResponse("/my/report?err=" + quote("今天已经填报过了（一天一次）"),
                                status_code=303)
    except ValueError as e:  # noqa: BLE001
        return RedirectResponse("/my/report?err=" + quote(str(e)),
                                status_code=303)


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
    return templates.TemplateResponse("staff_report_compare.html", {
        "request": request, "current_user": user, "res": res, "daily": daily,
        "start": s, "end": e, "person_code": person_code, "sort": sort, "kind": kind,
        "staff_opts": _staff_options(db), "msg": msg, "err": err,
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
