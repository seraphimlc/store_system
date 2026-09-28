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
    return env


def _req():
    from types import SimpleNamespace as SN

    class _QP(dict):
        items = dict.items
    return SN(state=SN(csrf="tok"), query_params=_QP(), cookies={})


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


def test_base_mobile_hamburger_menu():
    """H5：顶栏带汉堡菜单按钮（窄屏折叠导航），导航项仍在 DOM 中。"""
    html = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="管理员", role="admin"),
        request=_req())
    assert 'class="menu-btn"' in html and "☰" in html
    assert 'class="topnav"' in html
    assert "数据看板" in html and "绩效工资" in html  # 折叠后内容仍在 DOM
    staff = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="甲", role="staff"),
        request=_req())
    assert 'class="menu-btn"' in staff and "我的绩效" in staff


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
    assert 'data-testid="tab-perf"' in staff and 'data-testid="tab-report"' in staff
    assert 'href="/my/perf"' in staff and 'href="/my/report"' in staff
    # 顶栏不再重复这两项（只保留次要项）
    assert staff.count('>我的绩效<') == 1 and staff.count('>每日填报<') == 1
    assert "我的核对结果" in staff and "我的 Token" in staff
    admin = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="管理员", role="admin"), request=_req())
    assert 'class="tabbar"' not in admin and "has-tabbar" not in admin


def test_base_staff_tab_marks_active():
    """当前页对应的 Tab 高亮（/my/report 与 /my/report/feedback 区分开）。"""
    class _URL:
        def __init__(self, path):
            self.path = path
    for path, on_testid in (("/my/perf", "tab-perf"), ("/my/report", "tab-report")):
        req = _req()
        req.url = _URL(path)
        html = _env().get_template("base.html").render(
            current_user=SimpleNamespace(display_name="甲", role="staff"), request=req)
        i = html.index('data-testid="%s"' % on_testid)
        assert 'class="tab on"' in html[i - 40:i], path
    # 核对页属于次要项 → 填报 Tab 不高亮
    req = _req()
    req.url = _URL("/my/report/feedback")
    html = _env().get_template("base.html").render(
        current_user=SimpleNamespace(display_name="甲", role="staff"), request=req)
    assert 'class="tab on"' not in html
