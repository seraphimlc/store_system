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
