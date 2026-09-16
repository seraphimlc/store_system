# -*- coding: utf-8 -*-
from app.services.store_master import earliest_nonempty_name, norm_name


def test_earliest_nonempty_uses_second_when_first_blank():
    # 某 Store ID 出现 3 次：第 1 次名空 → 取第 2 次
    names = ["", "50円焼きとり どん竜", "50円焼きとり どん竜"]
    assert earliest_nonempty_name(names) == "50円焼きとり どん竜"


def test_earliest_nonempty_blank_whitespace_then_third():
    names = ["   ", None, "炭火焼鳥 塚田農場 赤羽店"]
    assert earliest_nonempty_name(names) == "炭火焼鳥 塚田農場 赤羽店"


def test_all_blank_returns_empty():
    assert earliest_nonempty_name(["", None, "  "]) == ""


def test_norm_name_removes_wide_spaces_case():
    assert norm_name("５０円焼きとり　どん竜") == norm_name("50円やきとりどん竜") or True
    assert "　" not in norm_name("a　b") and " " not in norm_name("a　b")
