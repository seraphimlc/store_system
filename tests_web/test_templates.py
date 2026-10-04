# -*- coding: utf-8 -*-
"""模板渲染冒烟：base 可加载；登录态含标题与阶段二灰置导航；未登录仅品牌。"""
from types import SimpleNamespace
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path


def _env():
    env = Environment(loader=FileSystemLoader(
        str(Path(__file__).parent.parent / "app" / "templates")),
        autoescape=select_autoescape(["html"]))
    from app.i18n import t as _t, LANG_NAMES as _LANG_NAMES
    env.globals["t"] = _t
    env.globals["LANG_NAMES"] = _LANG_NAMES

    def _lang_url(request, lang):
        q = dict(request.query_params)
        q["lang"] = lang
        return "?" + "&".join(f"{k}={v}" for k, v in q.items())
    env.globals["lang_url"] = _lang_url
    env.globals["form_token"] = lambda: "ft-test"   # 一次性提交令牌存根
    # 静态资源版本号（与 app/templating.py 的 static_ver 同源，别各写一份）
    from app.templating import static_ver as _sv
    env.globals["static_ver"] = _sv
    return env


def _req():
    from types import SimpleNamespace as SN

    class _QP(dict):
        items = dict.items
    # plan_pending：中间件写入的「待填报出勤计划」（真实请求里该属性一定存在）
    return SN(state=SN(csrf="tok", plan_pending=None), query_params=_QP(),
              cookies={})


def test_base_template_renders_logged_in():
    html = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="管理员", role="admin"),
        request=_req())
    assert "巡店结算系统" in html
    assert "数据看板" in html and "绩效工资" in html and "月度对账" in html
    assert "管理员" in html


def test_base_staff_nav():
    html = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="甲", role="staff"),
        request=_req())
    assert "我的绩效" in html


def test_base_has_download_double_click_guard():
    """下载链接防连点（用户 2026-10-01："一次下载了两个文件"）。"""
    html = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="管理员", role="admin"),
        request=_req())
    assert "a[download]" in html and "busy" in html


def test_base_mobile_hamburger_menu():
    """H5：汉堡菜单按钮；导航项始终在 DOM 里（窄屏折叠，不是删掉）。

    ⚠️ 2026-10-03 用户："管理端顶部的菜单换成左边的吧，顶部内容太多了" →
    **管理端 = 左侧分组菜单（`sidenav`）**，员工/队长仍是顶部 `topnav`。
    """
    admin = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="管理员", role="admin"),
        request=_req())
    assert 'class="menu-btn"' in admin and "☰" in admin
    assert 'class="sidenav"' in admin and 'data-testid="sidenav"' in admin
    assert 'class="topnav"' not in admin, "管理端不再用顶部横排菜单"
    # 分组标题 + 全部菜单项仍在 DOM（窄屏是抽屉，不是删掉）
    for key in ("概览", "结算", "员工", "作业", "系统",
                "数据看板", "绩效工资", "月度对账", "薪资找平",
                "团队", "车站", "任务", "日志", "消息", "系统配置"):
        assert key in admin, key
    # 账号操作也在左侧（顶栏只留品牌+账号+汉堡）
    assert "修改密码" in admin and "退出" in admin
    staff = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="甲", role="staff"),
        request=_req())
    assert 'class="menu-btn"' in staff and "我的绩效" in staff
    assert 'class="topnav"' in staff and 'class="sidenav"' not in staff

def test_base_template_anonymous():
    html = _env().get_template("base.html").render(current_user=None,
                                                  request=_req())
    assert "巡店结算系统" in html
    assert "对账（阶段二）" not in html


def test_base_staff_bottom_tabs():
    """员工端主导航固定在底部（绩效/每日填报两项）；管理员不显示。"""
    staff = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="甲", role="staff"), request=_req())
    assert 'class="tabbar"' in staff and 'has-tabbar' in staff
    for tid in ("tab-perf", "tab-report", "tab-feedback"):
        assert 'data-testid="%s"' % tid in staff
    assert 'href="/my/perf"' in staff and 'href="/my/report"' in staff
    assert 'href="/my/report/feedback"' in staff
    # 顶栏不再重复这三项
    assert staff.count('>我的绩效<') == 1 and staff.count('>每日填报<') == 1
    assert staff.count('>核对结果<') == 1
    assert "我的 Token" not in staff          # 员工端 token 自助页已删除（2026-09-28）
    admin = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="管理员", role="admin"), request=_req())
    assert 'class="tabbar"' not in admin and "has-tabbar" not in admin


def test_base_staff_tab_marks_active():
    """当前页对应的 Tab 高亮（/my/report 与 /my/report/feedback 区分开）。"""
    class _URL:
        def __init__(self, path):
            self.path = path
    for path, on_testid in (("/my/perf", "tab-perf"), ("/my/report", "tab-report"),
                            ("/my/report/feedback", "tab-feedback")):
        req = _req()
        req.url = _URL(path)
        html = _env().get_template("base.html").render(
            current_user=SimpleNamespace(display_name="甲", role="staff"), request=req)
        i = html.index('data-testid="%s"' % on_testid)
        assert 'class="tab on"' in html[max(0, i - 90):i], path
    # 核对页 → 填报 Tab 不能同时高亮（两个 Tab 互斥）
    req = _req()
    req.url = _URL("/my/report/feedback")
    html = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="甲", role="staff"), request=req)
    assert html.count('class="tab on"') == 1


def test_static_assets_are_cache_busted():
    """静态资源必须带 `?v=` 版本号。

    ⚠️ 2026-10-03 实测踩到：改完 `app.css`（管理端菜单改左侧）后，服务端 CSS 完全正常
    （26492 字节、规则都在、HTTP 200），但用户浏览器仍用**旧缓存** → "菜单没有样式"。
    无 Cache-Control 的静态文件会用启发式缓存，改完不一定会重新拉。
    """
    import re
    html = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="管理员", role="admin"),
        request=_req())
    assert re.search(r'href="/static/app\.css\?v=[0-9a-f]+"', html), html[:400]
    assert re.search(r'src="/static/emp_select\.js\?v=[0-9a-f]+"', html)


def test_every_template_references_static_with_version():
    """兜底：所有模板引用 `/static/*` 都必须带 `?v=`（新增页面别漏）。"""
    import re
    from pathlib import Path
    bad = []
    for p in sorted(Path("app/templates").glob("*.html")):
        for m in re.finditer(r'(?:href|src)="/static/([^"?]+)"',
                             p.read_text(encoding="utf-8")):
            bad.append("%s → %s" % (p.name, m.group(0)))
    assert not bad, "这些静态引用没带版本号（浏览器会吃旧缓存）：%s" % bad
