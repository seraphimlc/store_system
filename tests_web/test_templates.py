# -*- coding: utf-8 -*-
"""模板渲染冒烟：base 可加载；登录态含标题与阶段二灰置导航；未登录仅品牌。"""
from types import SimpleNamespace
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pathlib import Path


def _env():
    return Environment(loader=FileSystemLoader(
        str(Path(__file__).parent.parent / "app" / "templates")),
        autoescape=select_autoescape(["html"]))


def _req():
    from types import SimpleNamespace as SN
    return SN(state=SN(csrf="tok"))


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


def test_base_template_anonymous():
    html = _env().get_template("base.html").render(current_user=None,
                                                  request=_req())
    assert "巡店结算系统" in html
    assert "对账（阶段二）" not in html
