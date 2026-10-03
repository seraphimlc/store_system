# -*- coding: utf-8 -*-
"""FastAPI 应用工厂与入口。"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def create_app() -> FastAPI:
    app = FastAPI(title="巡店结算系统", version="0.2.0")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    from app.routers import (auth_r, files_r, perf_r,
                        stores_r, accounts_r, info_r, settle_r, tokens_r,
                        oauth_r, report_r, plan_r)
    app.include_router(auth_r.router)
    app.include_router(files_r.router)
    app.include_router(perf_r.router)
    app.include_router(stores_r.router)
    app.include_router(accounts_r.router)
    app.include_router(info_r.router)
    app.include_router(settle_r.router)
    app.include_router(tokens_r.router)
    app.include_router(oauth_r.router)
    app.include_router(report_r.router)
    app.include_router(plan_r.router)
    from app.routers import bd_r
    app.include_router(bd_r.router)

    # 多语言：模板全局函数已在 app/templating.get_templates() 统一注册（t/LANG_NAMES/lang_url）

    from fastapi import Depends
    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    from app.db import get_db
    from sqlalchemy.orm import Session

    STAFF_ALLOWED = ("/my/password", "/static", "/healthz",
                     "/login", "/logout", "/product", "/my/confirm",
                     "/my/appeal", "/my/perf", "/my/report", "/my/plan",
                     # 车站任务：队员看"分给我的车站"，队长在**同一页**分派 + 提交每日进展
                     # （两处写端点 `/my/tasks/assign`、`/my/tasks/progress` 由路由内做角色校验）
                     "/my/tasks",
                     # MCP OAuth：授权确认页（浏览器）+ token/register（机器端）都是公开端点
                     "/oauth/", "/.well-known/")

    # 队长端在**员工端**里（规格 §6.2），所以白名单与员工一致；
    # 单独列出是为了"以后队长若多出专属页面"有唯一落点。
    LEADER_ALLOWED = STAFF_ALLOWED

    from fastapi.responses import RedirectResponse as _RR

    from app.forms import reset_ctx as _reset_form_ctx

    @app.middleware("http")
    async def lang_middleware(request, call_next):
        """解析当前语言（URL→cookie→账号级→浏览器→默认）并注入 contextvar。

        模板全局 t() 读取该值渲染界面文案；同时回写 lang cookie 保持选择。
        """
        from app.auth import read_session_token as _read
        from app.db import SessionLocal as _SL
        from app.models import User as _User
        from app.i18n import CURRENT_LANG as _LANG
        from app.i18n import resolve_lang as _resolve
        user_lang = ""
        token = request.cookies.get("ss")
        if token:
            data = _read(token)
            if data:
                s = _SL()
                try:
                    u = s.get(_User, data["uid"])
                    if u is not None:
                        user_lang = u.lang or ""
                finally:
                    s.close()
        lang = _resolve(query=request.query_params.get("lang", ""),
                        cookie=request.cookies.get("lang", ""),
                        user_lang=user_lang,
                        accept=request.headers.get("accept-language", ""))
        tok = _LANG.set(lang)
        try:
            from app.auth import SESSION_COOKIE as _SC, read_session_token as _rst
            _reset_form_ctx((_rst(request.cookies.get(_SC)) or {}).get("uid"))
            response = await call_next(request)
            # 页面禁止缓存：保存/修改后必须看到最新数据（浏览器复用旧页面会让人以为没刷新）
            if response.headers.get("content-type", "").startswith("text/html"):
                response.headers["Cache-Control"] = "no-store, must-revalidate"
            # 仅显式 ?lang= 切换时才持久化 cookie；否则保留现有 cookie，
            # 避免登录时的默认语言覆盖账号级/浏览器级选择
            if request.query_params.get("lang", "").strip().lower() in ("zh", "ja"):
                response.set_cookie("lang", lang, httponly=False,
                                    samesite="lax", max_age=3600 * 24 * 365)
            return response
        finally:
            _LANG.reset(tok)

    @app.middleware("http")
    async def staff_isolation(request, call_next):
        from app.auth import read_session_token as _read
        from app.db import SessionLocal as _SL
        from app.models import User as _User
        path = request.url.path
        # 模板统一读 request.state.plan_pending（员工端"待填报出勤计划"弹窗），默认置空
        request.state.plan_pending = None
        token = request.cookies.get("ss")
        if token and not path.startswith("/static"):
            data = _read(token)
            if data:
                s = _SL()
                try:
                    u = s.get(_User, data["uid"])
                    if u is not None and u.role in ("staff", "leader"):
                        # 已登录员工/队长且「待改密」（首登/口令被重置）：
                        # 除改密页与登出外一律拦到改密页（队长同一规则，别漏）
                        if u.must_change_password:
                            if not (path.startswith("/my/password")
                                    or path == "/logout"):
                                return _RR("/my/password?must=1", status_code=302)
                        # 待填报出勤计划 → 员工端提示；越权访问非白名单页 → 回各角色首页
                        from app.services import date_plan as _dp
                        request.state.plan_pending = _dp.needs_plan(
                            s, u.person_code) or None
                        allowed = (LEADER_ALLOWED if u.role == "leader"
                                   else STAFF_ALLOWED)
                        if not path.startswith(allowed):
                            from app.services import home as _home
                            return _RR(_home.landing_home(s, u), status_code=302)
                finally:
                    s.close()
        return await call_next(request)

    @app.get("/healthz")
    def healthz(db: Session = Depends(get_db)):
        try:
            db.execute(text("SELECT 1"))
        except Exception:
            return JSONResponse({"ok": False}, status_code=503)
        return {"ok": True}

    return app


app = create_app()
