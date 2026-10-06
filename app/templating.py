# -*- coding: utf-8 -*-
"""共享 Jinja2Templates 工厂：统一注册多语言全局函数（t / LANG_NAMES / lang_url）。

各路由从本模块取 templates，保证模板能按当前请求语言翻译界面文案。
"""
from functools import lru_cache

from fastapi.templating import Jinja2Templates

from app.i18n import LANG_NAMES, t


def register_globals(env, *, form_token=None) -> None:
    """把模板要用的全局函数注册进 `env`（**唯一来源**：生产与测试都调它）。

    ⚠️ 2026-10-06 踩到两次：
    1. 测试自己复制一份 globals → 生产加了 `html_lang()/jst()`，测试环境没有 → 用例整批红；
    2. 测试直接改 `get_templates()` 的**共享**环境（lru_cache，`overlay()` 也共享 globals）
       → 存根泄漏给其它测试（令牌页用例变红）。
    所以：注册逻辑集中在这里，测试**自己建 env + 调本函数**。
    """
    env.globals["t"] = t
    env.globals["mt"] = _mt
    # 中日字形归一（用户 2026-10-06："我输入的是中文，是不是这里有问题"）：
    # 线路下拉要挂 data-zh（中文名）给**前端 combobox** 匹配用；中文名一律现算，不落库。
    from app.services.bd_cjk import line_zh as _line_zh
    from app.services.bd_cjk import to_zh as _to_zh
    env.globals["line_zh"] = _line_zh
    env.globals["to_zh"] = _to_zh
    env.globals["LANG_NAMES"] = LANG_NAMES
    # ① 时间戳：库里存 **UTC**，页面按业务日（JST）显示 —— 用户 2026-10-06 审计点名
    env.globals["jst"] = jst_str
    # ② `<html lang>` 跟随当前语言（原来恒 zh-CN，日文界面也是 zh-CN）
    env.globals["html_lang"] = html_lang

    def _lang_url(request, lang: str) -> str:
        """构造语言切换链接：保留当前 query 参数，仅替换 lang。"""
        q = dict(request.query_params)
        q["lang"] = lang
        qs = "&".join(f"{k}={v}" for k, v in q.items())
        return f"?{qs}" if qs else ""
    env.globals["lang_url"] = _lang_url

    if form_token is None:
        from app.forms import form_token as form_token
    env.globals["form_token"] = form_token     # 模板里 {{ form_token() }}
    env.globals["static_ver"] = static_ver


@lru_cache(maxsize=1)
def get_templates() -> Jinja2Templates:
    tpl = Jinja2Templates(directory="app/templates")
    register_globals(tpl.env)
    return tpl


def jst_str(dt, fmt: str = "%Y-%m-%d %H:%M") -> str:
    """UTC 时间 → **JST** 显示串（空的给 '—'）。

    ⚠️ 库里是 `datetime.utcnow()`（UTC），直接 `strftime` 会少 9 小时 —— 生产容器本身是 UTC，
    所以"现在几点"在页面上会整体偏 9 小时（日本用户必然看出来）。
    """
    if dt is None:
        return "—"
    from datetime import timedelta, timezone
    try:
        if getattr(dt, "tzinfo", None) is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone(timedelta(hours=9))).strftime(fmt)
    except Exception:                       # noqa: BLE001  坏数据别把页面搞崩
        return "—"


def html_lang() -> str:
    """`<html lang="...">` 的值（跟随当前界面语言）。"""
    from app.i18n import CURRENT_LANG
    return {"ja": "ja", "zh": "zh-CN"}.get(CURRENT_LANG.get() or "zh", "zh-CN")


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


def _mt(key: str, *args) -> str:
    """模板里渲染**带参数的消息**（如 `{{ mt('已改派 %d 个任务', n) }}`）。"""
    from app.i18n import render_msg
    return render_msg(key, *args)
