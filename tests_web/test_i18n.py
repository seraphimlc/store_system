# -*- coding: utf-8 -*-
"""多语言（中/日）测试：语言解析、模板翻译、切换持久化、账号级默认。"""
import re

import app.db as appdb
from app.auth import hash_password
from app.i18n import resolve_lang, translate
from app.models import User


def _seed_lang_admin(client):
    db = appdb.SessionLocal()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True))
    db.commit()
    db.close()


def _staff_with_lang(client, username, lang=""):
    db = appdb.SessionLocal()
    db.add(User(username=username, password_hash=hash_password("demo123"),
                display_name="员工甲", role="staff", person_code="P1",
                is_active=True, status="active", lang=lang))
    db.commit()
    db.close()
    client.post("/login", data={"username": username, "password": "demo123"},
                follow_redirects=False)


def test_resolve_lang_priority():
    """解析优先级：URL → cookie → 账号级 → Accept-Language → 默认。"""
    assert resolve_lang(query="ja", cookie="zh", user_lang="", accept="zh") == "ja"
    assert resolve_lang(query="", cookie="ja", user_lang="zh", accept="zh") == "ja"
    assert resolve_lang(query="", cookie="", user_lang="ja", accept="zh") == "ja"
    assert resolve_lang(query="", cookie="", user_lang="", accept="zh-CN,zh;q=0.9") == "zh"
    assert resolve_lang(query="", cookie="", user_lang="", accept="en-US") == "zh"
    assert resolve_lang(query="", cookie="", user_lang="", accept="ja-JP") == "ja"


def test_translate_ja():
    assert translate("我的绩效", "ja") == "自分の実績"
    assert translate("我的绩效", "zh") == "我的绩效"
    assert translate("未收录的词", "ja") == "未收录的词"   # 缺翻译回退原文


def test_staff_page_lang_switch_url(client):
    """员工端 /my/perf：?lang=ja 渲染日文文案；?lang=zh 渲染中文。"""
    _seed_lang_admin(client)
    _staff_with_lang(client, "emp1")
    p = client.get("/my/perf?lang=ja").text
    assert "自分の実績" in p or "実績" in p
    p = client.get("/my/perf?lang=zh").text
    assert "我的绩效" in p


def test_staff_account_lang_default(client):
    """账号级 lang=ja → 无 URL/cookie 参数时默认日文界面。"""
    _seed_lang_admin(client)
    _staff_with_lang(client, "emp2", lang="ja")
    p = client.get("/my/perf").text
    assert "自分の実績" in p or "実績" in p


def test_lang_cookie_persist(client):
    """切换后响应写 lang cookie，后续请求保持。"""
    _seed_lang_admin(client)
    _staff_with_lang(client, "emp3")
    r = client.get("/my/perf?lang=ja")
    assert r.status_code == 200
    assert any("lang=ja" in c for c in r.headers.get_list("set-cookie"))
    # 带 cookie 访问不带 lang 参数 → 仍日文
    p = client.get("/my/perf").text
    assert "自分の実績" in p or "実績" in p


def test_admin_set_staff_lang(client):
    """管理员在员工管理页可为员工指定语言。"""
    _seed_lang_admin(client)
    _staff_with_lang(client, "emp4", lang="")
    client.post("/login", data={"username": "admin", "password": "pw123456"},
                follow_redirects=False)
    page = client.get("/staff-admin").text
    assert "界面语言" in page
    db = appdb.SessionLocal()
    uid = db.query(User).filter(User.username == "emp4").one().id
    db.close()
    m = re.search(r'name="csrf_token" value="([^"]+)"', page)
    r = client.post(f"/staff-admin/{uid}/lang",
                    data={"lang": "ja", "csrf_token": m.group(1)},
                    follow_redirects=False)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(User).filter(User.username == "emp4").one().lang == "ja"
    db.close()
