# -*- coding: utf-8 -*-
"""共享 Jinja2Templates 工厂：统一注册多语言全局函数（t / LANG_NAMES / lang_url）。

各路由从本模块取 templates，保证模板能按当前请求语言翻译界面文案。
"""
from functools import lru_cache

from fastapi.templating import Jinja2Templates

from app.i18n import LANG_NAMES, t


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    tpl = Jinja2Templates(directory="app/templates")
    tpl.env.globals["t"] = t
    tpl.env.globals["LANG_NAMES"] = LANG_NAMES

    def _lang_url(request, lang: str) -> str:
        """构造语言切换链接：保留当前 query 参数，仅替换 lang。"""
        q = dict(request.query_params)
        q["lang"] = lang
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        return f"?{qs}" if qs else ""
    tpl.env.globals["lang_url"] = _lang_url
    return tpl
