# -*- coding: utf-8 -*-
"""员工登录名生成：模型优先（中文拼音/日文罗马字/英文原名），规则兜底。"""
import app.db as appdb
from app.services import ai_chat, login_names


def test_login_name_japanese_romaji_via_ai(client, monkeypatch):
    """日本人名 → 模型给出罗马字登录名（非汉语拼音）。"""
    login_names._CACHE.clear()
    monkeypatch.setattr(ai_chat, "configured", lambda: True)
    monkeypatch.setattr(ai_chat, "chat",
                        lambda prompt: '{"小川逸":"ogawa-itsuru",'
                                       '"新井圭史":"arai-keishi"}')
    db = appdb.SessionLocal()
    try:
        assert login_names.login_name_for(
            db, "小川逸", "2188240606634082") == "ogawa-itsuru"
        assert login_names.login_name_for(
            db, "新井圭史", "2188240606737868") == "arai-keishi"
    finally:
        db.close()


def test_login_name_chinese_pinyin_via_ai(client, monkeypatch):
    """中文名 → 模型给出拼音登录名。"""
    login_names._CACHE.clear()
    monkeypatch.setattr(ai_chat, "configured", lambda: True)
    monkeypatch.setattr(ai_chat, "chat",
                        lambda prompt: '{"甘子杰":"ganzijie"}')
    db = appdb.SessionLocal()
    try:
        assert login_names.login_name_for(
            db, "甘子杰", "2188240606734603") == "ganzijie"
    finally:
        db.close()


def test_login_name_fallback_rule_when_unconfigured(client, monkeypatch):
    """未配置模型 → 回退规则拼音（不崩）。"""
    login_names._CACHE.clear()
    monkeypatch.setattr(ai_chat, "configured", lambda: False)
    db = appdb.SessionLocal()
    try:
        name = login_names.login_name_for(db, "甘子杰", "2188240606734603")
        assert name == "ganzijie"
        name2 = login_names.login_name_for(db, "小川逸", "2188240606634082")
        assert name2 == "xiaochuanyix" or name2.startswith("xiaochuan")
    finally:
        db.close()
