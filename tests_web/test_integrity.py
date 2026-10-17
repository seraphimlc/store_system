# -*- coding: utf-8 -*-
"""数据自检（app/services/integrity.py）A1–A9 的**检测性**测试。

背景：`integrity.check_all()` 是生产在用的数据自洽自检（命令行
`scripts/verify_payroll_logic.py` 与 MCP 工具 `visit_verify` 都会跑它）。
此前全仓唯一用例 `mcp_service/tests/test_scenario_ops.py:401 test_verify_integrity`
只断言返回值里有 `checks`/`summary`/`failed` 三个键（**形状 smoke**），
不断言任何一项真的会失败 —— 把某项检查逻辑改错、或让它永远返回 pass，
测试依旧全绿。

本文件对 A1–A9 **每一项**做**双向断言**（缺一不可）：
  1) 干净底库（该项互证成立）→ 必须 `pass` 且 `count == 0`；
  2) 注入**恰好一处**坏数据 → 必须 `fail` 且 `count == 1`。
    （A6 例外：其 status 设计为 `info`，见 `test_a6_*`。）

这样"删掉/改坏某项检查的实现"必然有测试变红。

口径来源：`integrity.py` 模块 docstring 与 A1–A9 各行注释。
A9 对应 2026-10-17「该扣多少三个页面三个数」（应发表调整列/差异列双源分叉）
事故的同类型隐患。

注意：本文件只用 `app/services/integrity.py` 自身消费的表
（payroll_period_rows / payroll_adjusts / payroll_payments /
payroll_settlement_links），不跑 sync/alembic，`Base.metadata.create_all` 足够。
"""
import pytest

import app.db as appdb
from app.models import (PayrollAdjust, PayrollPayment, PayrollPeriodRow,
                        PayrollSettlementLink)
from app.services.integrity import check_all


# ------------------------- 小工具 -------------------------

def _chk(res, cid):
    """按 id 取单项结果（A1..A9），并保证 9 项齐全。"""
    ids = [c["id"] for c in res["checks"]]
    assert ids == [f"A{i}" for i in range(1, 10)], ids
    hit = [c for c in res["checks"] if c["id"] == cid]
    assert len(hit) == 1, (cid, ids)
    return hit[0]


def _fails(res):
    """status == fail 的项 id 列表（不含 info）。"""
    return sorted(c["id"] for c in res["checks"] if c["status"] == "fail")


def _seed_clean(db):
    """构造 A1–A9 **全部 pass** 的干净底库：一笔找平 = 一次发放 = 一条关联，三处自洽。

    2026-09 / P1：对账偏差 −500 円（负 = 应扣），找平表同步 −500 且已结清，
    本期发放 10000 = 应发表上半月金额，关联冲抵 −500。
    """
    row = PayrollPeriodRow(
        month="2026-09", person_code="P1",
        half1_points=40, half2_points=0,
        half1_amount=10000, half2_amount=0,
        diff_amount=-500, adjust_amount=-500,     # A9：应发表「调整列」==「差异列」
        prev_adjust_amount=-300,                  # A8：与 diff 同为负 → 递延符号一致
    )
    db.add(row)
    db.flush()
    adj = PayrollAdjust(
        source_month="2026-09", person_code="P1",
        source_row_id=row.id,
        adjust_amount=-500,                       # A1：== 应发表 diff_amount
        settled_amount=-500, remaining=0,         # A2：已找平 + 剩余 == 原始
        status="settled",                         # A3：剩余 0 ↔ settled
    )
    pay = PayrollPayment(
        month="2026-09", person_code="P1", seq=1,
        points=40, amount=10000,                  # A6：== 应发表 half1_amount
        adjust_applied=-500,                      # A4：== 关联合计
        # A7：source_row_id / source_month / source_task_id 全空 → 不产生 bad
    )
    db.add_all([adj, pay])
    db.flush()
    db.add(PayrollSettlementLink(
        adjust_id=adj.id, payment_id=pay.id,
        month="2026-09", person_code="P1",
        amount=-500,                              # A4/A5：关联合计 == −500
    ))
    db.commit()
    return {"row": row, "adj": adj, "pay": pay}


@pytest.fixture
def db(client):
    """内存库会话（client fixture 已建全表并把 SessionLocal 指过去）。"""
    s = appdb.SessionLocal()
    yield s
    s.close()


# ------------------------- 底库：9 项全 pass -------------------------

def test_clean_baseline_all_nine_pass(db):
    """干净底库必须 A1–A9 全 pass、ok=True（否则后面"注入→fail"无法归因）。"""
    _seed_clean(db)
    res = check_all(db)
    for c in res["checks"]:
        assert c["status"] == "pass", c
        assert c["count"] == 0, c
    assert res["ok"] is True
    assert res["summary"]["failed"] == 0
    assert res["summary"]["info"] == 0


# ------------------------- A1–A9 逐项 -------------------------

def test_a1_adjust_amount_equals_period_diff(db):
    """A1 互证：找平表 `payroll_adjusts.adjust_amount` == 应发表
    `payroll_period_rows.diff_amount`（同月同人）。

    注入：加一笔 2026-08/P9 的找平行（adjust_amount=100），
    但**故意不给它对应的应发（差异）行** → A1 报 fail 且计数 1。
    （该行 settled=0/remaining=100/status=in_progress，A2/A3/A5 自洽，故只打 A1。）
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A1")["status"] == "pass"          # 方向 1：干净 pass

    db.add(PayrollAdjust(source_month="2026-08", person_code="P9",
                         adjust_amount=100, settled_amount=0, remaining=100,
                         status="in_progress"))
    db.commit()
    res = check_all(db)
    c = _chk(res, "A1")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A1"], _fails(res)                     # 只打这一项


def test_a2_settled_plus_remaining_equals_adjust(db):
    """A2 互证：同一找平行内 `settled_amount + remaining == adjust_amount`。

    注入：加 2026-08/P2 一整套自洽数据，**只把找平行的 remaining 写错**成 −800
    （已找平已经 −800，剩余本应 0）→ `-800 + -800 != -800` → A2 报 fail 且计数 1。
    配套的应发行/发放/关联都补齐，使 A1/A3/A4/A5/A9 仍 pass。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A2")["status"] == "pass"

    row = PayrollPeriodRow(
        month="2026-08", person_code="P2",
        half1_amount=5000, half2_amount=0,
        diff_amount=-800, adjust_amount=-800, prev_adjust_amount=0,
    )
    db.add(row)
    db.flush()
    adj = PayrollAdjust(source_month="2026-08", person_code="P2",
                        source_row_id=row.id, adjust_amount=-800,
                        settled_amount=-800,
                        remaining=-800,               # ← 坏数据：本应 0
                        status="in_progress")
    pay = PayrollPayment(month="2026-08", person_code="P2", seq=1,
                         amount=5000, adjust_applied=-800)
    db.add_all([adj, pay])
    db.flush()
    db.add(PayrollSettlementLink(adjust_id=adj.id, payment_id=pay.id,
                                 month="2026-08", person_code="P2",
                                 amount=-800))
    db.commit()
    res = check_all(db)
    c = _chk(res, "A2")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A2"], _fails(res)


def test_a3_status_matches_remaining(db):
    """A3 互证：找平行 `(remaining == 0)` 必须等价于 `(status == "settled")`。

    注入：加 2026-07/P3 一行，`remaining=0` 但 `status="in_progress"`
    （结清了却没标 settled）→ A3 报 fail 且计数 1。
    该行 settled=600/remaining=0/adjust_amount=600 满足 A2，配套关联满足 A1/A4/A5/A9。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A3")["status"] == "pass"

    row = PayrollPeriodRow(
        month="2026-07", person_code="P3",
        half1_amount=3000, half2_amount=0,
        diff_amount=600, adjust_amount=600, prev_adjust_amount=0,
    )
    db.add(row)
    db.flush()
    adj = PayrollAdjust(source_month="2026-07", person_code="P3",
                        source_row_id=row.id, adjust_amount=600,
                        settled_amount=600,
                        remaining=0,                 # 剩余 0 …
                        status="in_progress")        # ← 坏数据：状态没跟着 settled
    pay = PayrollPayment(month="2026-07", person_code="P3", seq=1,
                         amount=3000, adjust_applied=600)
    db.add_all([adj, pay])
    db.flush()
    db.add(PayrollSettlementLink(adjust_id=adj.id, payment_id=pay.id,
                                 month="2026-07", person_code="P3",
                                 amount=600))
    db.commit()
    res = check_all(db)
    c = _chk(res, "A3")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A3"], _fails(res)


def test_a4_link_sum_by_payment_equals_adjust_applied(db):
    """A4 互证：**按发放**汇总 `payroll_settlement_links.amount`
    == 该次发放 `payroll_payments.adjust_applied`。

    注入：把干净底库那笔发放的 adjust_applied 从 −500 改成 −400
    （台账说扣了 400，关联明细合计 500）→ A4 报 fail 且计数 1。
    关联明细与找平表未动，故 A5 仍 pass。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A4")["status"] == "pass"

    pay = db.query(PayrollPayment).one()
    pay.adjust_applied = -400                     # ← 坏数据
    db.commit()
    res = check_all(db)
    c = _chk(res, "A4")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A4"], _fails(res)


def test_a5_link_sum_by_adjust_equals_settled(db):
    """A5 互证：**按找平**汇总 `payroll_settlement_links.amount`
    == 该找平行 `payroll_adjusts.settled_amount`。

    注入：加 2026-06/P4 一行，找平表记 `settled_amount=-700` 却**没有任何关联明细**
    （宣称已找平 700 但查不到是哪笔发放冲的）→ A5 报 fail 且计数 1。
    该行 remaining=0/status=settled 满足 A2/A3，A1/A9 的应发行也补齐。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A5")["status"] == "pass"

    row = PayrollPeriodRow(
        month="2026-06", person_code="P4",
        half1_amount=0, half2_amount=0,
        diff_amount=-700, adjust_amount=-700, prev_adjust_amount=0,
    )
    db.add(row)
    db.flush()
    db.add(PayrollAdjust(source_month="2026-06", person_code="P4",
                         source_row_id=row.id, adjust_amount=-700,
                         settled_amount=-700,           # 宣称已找平 …
                         remaining=0, status="settled"))  # … 但一条 link 都没有
    db.commit()
    res = check_all(db)
    c = _chk(res, "A5")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A5"], _fails(res)


def test_a6_payment_amount_vs_current_period_row(db):
    """A6 互证：发放台账**快照** `payroll_payments.amount` == 当前应发表
    `payroll_period_rows.half1_amount`（seq=1；seq≠1 用 half2_amount）。

    **A6 的 status 设计为 `info` 而非 `fail`**（计划可变、已发的钱不可改，
    只作分歧提示，不计入 `ok`）—— 故这里断言 `status=="info"`、`count==1`
    且 `res["ok"] is True`，其余 8 项仍全 pass。

    注入：把那笔发放的 amount 从 10000 改成 9000（重算后应发变了，台账快照是旧的）
    → A6 报 info 且计数 1。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A6")["status"] == "pass"

    pay = db.query(PayrollPayment).one()
    pay.amount = 9000                             # ← 坏数据：快照 ≠ 当前应发
    db.commit()
    res = check_all(db)
    c = _chk(res, "A6")
    assert c["status"] == "info" and c["count"] == 1, c
    assert res["ok"] is True                      # 分歧提示不算失败
    assert _fails(res) == [], _fails(res)


def test_a7_adjust_source_must_be_resolvable(db):
    """A7 互证：发放台账的抵扣来源字段必须**能反查**到真实行
    （`adjust_source_row_id` → payroll_period_rows；`adjust_source_month` → 该人该月应发行；
    `adjust_source_task_id` → recon_tasks）。

    注入：把那笔发放的 `adjust_source_row_id` 指到不存在的 999999
    （"这钱按哪个月的差异扣的"查无此据）→ A7 报 fail 且计数 1。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A7")["status"] == "pass"

    pay = db.query(PayrollPayment).one()
    pay.adjust_source_row_id = 999999             # ← 坏数据：悬空外键
    db.commit()
    res = check_all(db)
    c = _chk(res, "A7")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A7"], _fails(res)


def test_a8_defer_chain_sign_consistent(db):
    """A8 互证：应发表的 `diff_amount`（本月差异）与 `prev_adjust_amount`（结转）
    同时非 0 时**符号必须一致**（都是"补"或都是"扣"，不能一正一负）。

    注入：把底库应发行的 prev_adjust_amount 从 −300 翻成 +300
    （本月差 −500 = 要扣，结转却写 +300 = 要补）→ A8 报 fail 且计数 1。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A8")["status"] == "pass"

    row = db.query(PayrollPeriodRow).one()
    row.prev_adjust_amount = 300                  # ← 坏数据：符号与 diff 相反
    db.commit()
    res = check_all(db)
    c = _chk(res, "A8")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A8"], _fails(res)


def test_a9_period_adjust_column_equals_diff_column(db):
    """A9 互证：应发表**同一行内**「调整列」`adjust_amount` == 「差异列」`diff_amount`
    （历史遗留列防双源；2026-10-17「该扣多少三个页面三个数」就是这类分叉）。

    两列都在 `payroll_period_rows` 上、夹具可直接写，**双向断言完全覆盖**。

    注入：把底库应发行的 adjust_amount 从 −500 改成 −400（diff 仍 −500，
    即页面/报表读的调整列与重算出的差异列分叉）→ A9 报 fail 且计数 1。
    注意 A1 用的是「找平表 adjust_amount vs 应发表 diff_amount」，故 A1 不受影响。
    """
    _seed_clean(db)
    assert _chk(check_all(db), "A9")["status"] == "pass"

    row = db.query(PayrollPeriodRow).one()
    row.adjust_amount = -400                      # ← 坏数据：与 diff_amount 分叉
    db.commit()
    res = check_all(db)
    c = _chk(res, "A9")
    assert c["status"] == "fail" and c["count"] == 1, c
    assert _fails(res) == ["A9"], _fails(res)
