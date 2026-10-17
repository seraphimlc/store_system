# -*- coding: utf-8 -*-
from app.services.store_master import norm_name

# ⚠️ 2026-10-17 测试审查：原先这里 3 条用例测的是 `store_master.earliest_nonempty_name`
#    （"建档代表名 = 时间顺序里第一个非空店名"），但那个独立函数**全仓无人调用** ——
#    规则实际是内联在 `sync_entities` 里的（`if not ent.name_local and nm: ...`，
#    注释"店名取最早的非空写法"）。"只有测试在用的函数"会永远绿，删掉函数与这 3 条用例，
#    规则由 `test_store_auto.py` 的实体归并用例从行为侧守着。


def test_norm_name_removes_wide_spaces_case():
    # ⚠️ 原样本「５０円焼きとり　どん竜」vs「50円やきとりどん竜」**本来就不相等**
    #    （焼 ≠ や，那不归归一化管），却被 `or True` 盖成了恒真断言、永不失败。
    #    换成真正等价的样本：全角→半角、全角空格剥掉、大小写折叠。
    assert norm_name("Ｗｉｎｅ　Ｓｈｏｐ") == norm_name("wine shop")
    assert norm_name("７－ＥＬＥＶＥＮ") == norm_name("7-eleven")
    assert "　" not in norm_name("a　b") and " " not in norm_name("a　b")
