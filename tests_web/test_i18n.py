# -*- coding: utf-8 -*-
"""多语言（中/日）测试：语言解析、模板翻译、切换持久化、账号级默认。"""
import re

import app.db as appdb
from tests.helpers import form_token
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
                    data={"_ft": form_token(client), "lang": "ja", "csrf_token": m.group(1)},
                    follow_redirects=False)
    assert r.status_code == 303
    db = appdb.SessionLocal()
    assert db.query(User).filter(User.username == "emp4").one().lang == "ja"
    db.close()


def _load_prune():
    import importlib.util
    from pathlib import Path
    p = Path(__file__).parent.parent / "scripts" / "i18n_prune.py"
    spec = importlib.util.spec_from_file_location("i18n_prune", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_i18n_prune_removes_single_and_multiline_keys():
    """`i18n_prune` 必须能删**多行**词条（值在下一行）。

    ⚠️ 2026-10-04 真踩到：旧正则 `"k": "[^"]*",` 只认单行 →
    多行的死键"每次都报告已删除、实际没删"（假成功，死键一直清不掉）。
    """
    m = _load_prune()
    single = '{\n    "a": "1",\n    "keep": "y",\n}'
    multi = '{\n    "b":\n        "2",\n    "keep": "y",\n}'
    assert m.remove_keys(single, ["a"]) == ('{\n    "keep": "y",\n}', 1, [])
    assert m.remove_keys(multi, ["b"]) == ('{\n    "keep": "y",\n}', 1, [])
    # 没匹配上要**报出来**，不能假装成功
    text, n, missed = m.remove_keys(single, ["不存在"])
    assert n == 0 and missed == ["不存在"] and text == single


def test_i18n_dict_is_clean():
    """字典卫生：**0 缺日文、0 死键**（CI 兜底；有死键/漏译就直接红）。

    跑 `scripts/i18n_audit.py` 并解析两个计数。
    """
    import subprocess
    import sys
    from pathlib import Path
    out = subprocess.run(
        [sys.executable, str(Path(__file__).parent.parent
                             / "scripts" / "i18n_audit.py")],
        capture_output=True, text=True,
        cwd=str(Path(__file__).parent.parent)).stdout
    assert "模板/代码用到但字典缺失（日文界面会显示中文）: 0" in out, out
    assert "字典里有但全仓库没人用（死键）: 0" in out, out
