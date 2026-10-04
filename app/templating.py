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

    from app.forms import form_token as _ft
    tpl.env.globals["form_token"] = _ft      # 模板里 {{ form_token() }}
    tpl.env.globals["static_ver"] = static_ver
    return tpl


def static_ver(name: str) -> str:
    """静态资源版本号（模板写 `/static/app.css?v={{ static_ver('app.css') }}`）。

    **为什么需要**：改完 CSS/JS 后浏览器仍用旧缓存 → 页面"没样式"。
    2026-10-03 实测踩到（管理端菜单改左侧后用户看到无样式的裸菜单）。
    版本号 = 文件 mtime + 大小，**每次渲染 stat 一次**（本地 `--reload` 改完即生效，
    不用重启、也不用人工改版本号）。
    """
    import os
    path = os.path.join("app", "static", name)
    try:
        st = os.stat(path)
        return "%x%x" % (int(st.st_mtime), st.st_size)
    except OSError:
        return "0"
