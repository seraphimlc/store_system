# -*- coding: utf-8 -*-
"""日期计划（半月出勤登记）路由：规格 `docs/specs-date-plan.md`。

- 员工端：`/my/plan`（半月表格，点选不出勤；提交/修改）
- 管理端：`/staff-plans`（员工 × 日期大表格）+ `/staff-plans/export`（xlsx）
"""
from datetime import timedelta
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.forms import require_form_token as _dep_form_token
from app.models import User
from app.routers.auth_r import csrf_ok, require_login
from app.templating import get_templates

router = APIRouter(dependencies=[Depends(_dep_form_token)])
templates = get_templates()


def _denied():
    return RedirectResponse("/login", status_code=302)


def _admin_guard(user):
    """管理端守卫：非 admin 一律拦（队长 → `/login`，再按角色落到 `/my/tasks`）。"""
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if user.role != "admin":
        if user.role == "leader":
            return RedirectResponse("/login", status_code=302)
        return RedirectResponse("/my/perf", status_code=302)
    return None


def _resolve_period(period: str, today, keys: List[str], db=None,
                    person_code: str = "") -> str:
    """未传/非法 → **正在填报的那一期**（否则本期）；下拉里只列可选半月。

    员工端多一层：**新入职的人优先落"本期"**（那是他需要补登的那一期）。
    """
    from app.services import date_plan
    p = (period or "").strip().upper()
    if p in keys:
        return p
    if db is not None and person_code:
        return date_plan.default_period_for(db, person_code, today)
    return date_plan.default_period(today)


# ---------------- 员工端 ----------------

@router.get("/my/plan", response_class=HTMLResponse)
def my_plan_page(request: Request,
                 user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db), period: str = "",
                 saved: str = "", msg: str = "", err: str = ""):
    """我的出勤计划：半月表格，默认每天都出勤，点选哪天不出勤。"""
    if user is None or user.role != "staff" or not user.person_code:
        return _denied()
    from app.services import date_plan
    today = date_plan.jst_today()
    options = date_plan.period_options(today)
    key = _resolve_period(period, today, [o["key"] for o in options],
                          db=db, person_code=user.person_code)
    view = date_plan.plan_days(db, user.person_code, key, today=today)
    return templates.TemplateResponse("my_plan.html", {
        "request": request, "current_user": user,
        "today": today, "view": view, "period": key, "periods": options,
        "wd_labels": date_plan.WD_LABELS, "marks": date_plan.MARKS,
        "next_win": date_plan.next_window(today),
        "jst_delta": timedelta(hours=9),
        "saved": saved, "msg": msg, "err": err,
    })


@router.post("/my/plan")
def my_plan_submit(request: Request,
                   period: str = Form(""),
                   unavailable: Optional[List[str]] = Form(None),
                   csrf_token: str = Form(""),
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db)):
    """提交/修改某个半月的出勤计划（只提交"不出勤"的日期）。"""
    if user is None or user.role != "staff" or not user.person_code:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import date_plan
    try:
        date_plan.save_plan(db, user, period, unavailable or ())
    except date_plan.WindowClosed:
        # 具体窗口日期由页面上的状态条给出（这里只给一句可翻译的静态文案）
        return RedirectResponse(
            "/my/plan?period=%s&err=%s" % (quote(period),
                                           quote("填报期未开放或已结束，不能提交")),
            status_code=303)
    except date_plan.AllLocked:
        return RedirectResponse(
            "/my/plan?period=%s&err=%s" % (quote(period),
                                           quote("该半月的日期都已过去或已自报，无需再登记")),
            status_code=303)
    except ValueError as e:  # noqa: BLE001
        return RedirectResponse("/my/plan?period=%s&err=%s" % (quote(period), quote(str(e))),
                                status_code=303)
    # 成功不拼串（拼出来的句子绕过多语言字典）：页面自己渲染"已保存"
    return RedirectResponse("/my/plan?period=%s&saved=1" % quote(period),
                            status_code=303)


# ---------------- 管理端 ----------------

@router.get("/staff-plans", response_class=HTMLResponse)
def staff_plans_page(request: Request,
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db), period: str = "",
                     msg: str = "", err: str = ""):
    """全员出勤计划矩阵：行=员工，列=日期（○/×/□/–）。"""
    g = _admin_guard(user)
    if g:
        return g
    from app.services import date_plan
    today = date_plan.jst_today()
    options = date_plan.period_options(today, back=3, fwd=2)
    key = _resolve_period(period, today, [o["key"] for o in options])
    m = date_plan.admin_matrix(db, key, today=today)
    return templates.TemplateResponse("staff_plans.html", {
        "request": request, "current_user": user,
        "today": today, "m": m, "period": key, "periods": options,
        "wd_labels": date_plan.WD_LABELS, "marks": date_plan.MARKS,
        "msg": msg, "err": err,
    })


@router.get("/staff-plans/export")
def staff_plans_export(user: Optional[User] = Depends(require_login),
                       db: Session = Depends(get_db), period: str = ""):
    """导出某个半月的出勤计划（员工 × 日期）。"""
    g = _admin_guard(user)
    if g:
        return g
    import io

    from fastapi.responses import StreamingResponse

    from app.services import date_plan
    today = date_plan.jst_today()
    options = date_plan.period_options(today, back=3, fwd=2)
    key = _resolve_period(period, today, [o["key"] for o in options])
    data, fname = date_plan.plan_xlsx(db, key, today=today)
    cd = ("attachment; filename=%s; filename*=UTF-8''%s" % (fname, quote(fname)))
    return StreamingResponse(
        io.BytesIO(data),
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".spreadsheetml.sheet"),
        headers={"Content-Disposition": cd})
