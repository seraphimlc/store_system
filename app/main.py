# -*- coding: utf-8 -*-
"""FastAPI 应用工厂与入口。"""
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles


def create_app() -> FastAPI:
    app = FastAPI(title="巡店结算系统", version="0.2.0")
    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    from app.routers import (auth_r, files_r, perf_r,
                        stores_r, accounts_r, info_r, settle_r)
    app.include_router(auth_r.router)
    app.include_router(files_r.router)
    app.include_router(perf_r.router)
    app.include_router(stores_r.router)
    app.include_router(accounts_r.router)
    app.include_router(info_r.router)
    app.include_router(settle_r.router)

    # 多语言：模板全局函数已在 app/templating.get_templates() 统一注册（t/LANG_NAMES/lang_url）

    from fastapi import Depends
    from fastapi.responses import JSONResponse
    from sqlalchemy import text
    from app.db import get_db
    from sqlalchemy.orm import Session

    STAFF_ALLOWED = ("/my/password", "/static", "/healthz",
                     "/login", "/logout", "/product", "/my/confirm",
                     "/my/appeal", "/my/perf")

    from fastapi.responses import RedirectResponse as _RR

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
            response = await call_next(request)
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
        # 已登录员工且「待改密」（首登/口令被重置）：除改密页与登出外一律拦到改密页
        token = request.cookies.get("ss")
        if token:
            data = _read(token)
            if data:
                s = _SL()
                try:
                    u = s.get(_User, data["uid"])
                    if (u is not None and u.role == "staff"
                            and u.must_change_password):
                        if not (path.startswith("/my/password")
                                or path == "/logout"):
                            return _RR("/my/password?must=1", status_code=302)
                finally:
                    s.close()
        if not path.startswith(STAFF_ALLOWED):
            token = request.cookies.get("ss")
            if token:
                data = _read(token)
                if data:
                    s = _SL()
                    try:
                        u = s.get(_User, data["uid"])
                        if u is not None and u.role == "staff":
                            return _RR("/my/perf", status_code=302)
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
