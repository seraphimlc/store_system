# -*- coding: utf-8 -*-
"""对账/找平/配置写工具的闸门与信封（spec §7）。

A9 交付了模块但未带测试（子代理上下文耗尽），此文件补齐关键闸门覆盖。
"""
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, ReconTask, SealedMonth
from mcp_service import guards, payroll_write_ops, tokens

W = tokens.Actor(uid=1, role="admin", scopes=["read", "write"], token_id=1)
R = tokens.Actor(uid=1, role="admin", scopes=["read"], token_id=1)


@pytest.fixture()
def db(tmp_path):
    eng = create_engine(f"sqlite:///{tmp_path}/p.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    s.add(SealedMonth(month="2026-08", note="8月封账"))
    s.add(ReconTask(id=1, kind="monthly_v3", status="done", created_by=1,
                    params={"month": "2026-09", "kind": "daily_records",
                            "version": 1, "current": True},
                    summary={}, created_at=datetime.utcnow()))
    s.commit()
    yield s
    s.close()
    eng.dispose()


# ---------- 权限：5 个工具全部要求写权限 ----------

def test_all_require_write_scope(db):
    for call in (
        lambda: payroll_write_ops.recon_interpret(db, R, task_id=1),
        lambda: payroll_write_ops.recon_adjust(db, R, task_id=1,
                                               person_code="P001",
                                               action="add"),
        lambda: payroll_write_ops.payroll_generate(db, R, month="2026-09"),
        lambda: payroll_write_ops.payroll_update(db, R, month="2026-09",
                                                 person_code="P001",
                                                 half1_amount=100),
        lambda: payroll_write_ops.config_set(db, R, per_point=250),
    ):
        with pytest.raises(guards.GuardError) as e:
            call()
        assert e.value.code == "FORBIDDEN_TOOL"


# ---------- 对账：AI 解读 / 找平执行 ----------

def test_recon_interpret_not_found(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.recon_interpret(db, W, task_id=999)
    assert e.value.code == "NOT_FOUND"


def test_recon_adjust_rejects_bad_action(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.recon_adjust(db, W, task_id=1, person_code="P001",
                                       action="bogus")
    assert e.value.code == "BAD_PARAM"


def test_recon_adjust_requires_confirm_with_month(db):
    """确认语含任务月份与人员与动作（涉钱）。"""
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.recon_adjust(db, W, task_id=1, person_code="P001",
                                       action="add", confirm_text="随便")
    assert e.value.code == "CONFIRM_REQUIRED"
    assert "2026-09" in e.value.hint and "P001" in e.value.hint


# ---------- 找平表：生成 / 人工修正 ----------

def test_payroll_generate_validates_month(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.payroll_generate(db, W, month="2026-9")
    assert e.value.code == "BAD_MONTH"


def test_payroll_generate_refuses_sealed_month(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.payroll_generate(db, W, month="2026-08")
    assert e.value.code == "MONTH_SEALED"


def test_payroll_update_requires_confirm(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.payroll_update(db, W, month="2026-09",
                                         person_code="P001",
                                         half1_amount=100, confirm_text="改吧")
    assert e.value.code == "CONFIRM_REQUIRED"


def test_payroll_update_refuses_sealed_month(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.payroll_update(db, W, month="2026-08",
                                         person_code="P001", half1_amount=100,
                                         confirm_text="确认修正 2026-08 P001")
    assert e.value.code == "MONTH_SEALED"


# ---------- 配置：全局单值，影响所有月份 ----------

def test_config_set_requires_confirm(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.config_set(db, W, per_point=260, confirm_text="改")
    assert e.value.code == "CONFIRM_REQUIRED"


def test_config_set_rejects_bad_values(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.config_set(db, W, per_point=0,
                                     confirm_text="确认修改配置")
    assert e.value.code == "BAD_PARAM"


# ---------- 注册清单 ----------

def test_register_defines_six_tools():
    class _MCP:
        def __init__(self):
            self.names = []

        def tool(self, **kw):
            self.names.append(kw.get("name"))

            def deco(fn):
                return fn
            return deco

    m = _MCP()
    payroll_write_ops.register(m)
    assert set(m.names) == {
        "visit_recon_interpret", "visit_recon_adjust", "visit_payroll_generate",
        "visit_payroll_update", "visit_config_set", "visit_payroll_mark_paid"}


# ---------- 发薪标记（找平吸收额度只算未发薪的期）----------

def test_mark_paid_requires_write(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.payroll_mark_paid(db, R, month="2026-09", half=1)
    assert e.value.code == "FORBIDDEN_TOOL"


def test_mark_paid_rejects_bad_half(db):
    with pytest.raises(guards.GuardError) as e:
        payroll_write_ops.payroll_mark_paid(db, W, month="2026-09", half=3)
    assert e.value.code == "BAD_PARAM"


def test_mark_paid_then_unmark(db):
    res = payroll_write_ops.payroll_mark_paid(db, W, month="2026-09", half=1)
    assert res["ok"] is True and res["data"]["paid_halves"] == [1]
    res2 = payroll_write_ops.payroll_mark_paid(db, W, month="2026-09", half=1,
                                               unmark=True)
    assert res2["data"]["paid_halves"] == []


def test_paid_mark_reduces_absorption_capacity(db):
    """已发薪的期不计入可吸收额度 → 上月结转未被吸收的部分递延下月。"""
    from datetime import date
    from app.models import (FormalRecord, ImportFile, PayrollPeriodRow,
                            PersonDailyStat, Person)
    from app.services import period

    db.add(Person(code="P001", display_name="甲"))
    db.add(ImportFile(id=9, file_name="f.xlsx", file_sha256="y", file_size=1,
                      stored_path="/tmp/y.xlsx", uploaded_by=1, status="parsed",
                      parsed_sheets=[], ignored_sheets=[], warnings=[], errors=[]))
    # 上月(2026-08)结转：-59000 円
    db.add(PayrollPeriodRow(month="2026-08", person_code="P001",
                            diff_amount=-59000, prev_adjust_amount=-59000,
                            adjust_amount=0, adjust_points=0, diff_points=-200))
    # 本月(2026-09)：上半月 375000 已发、下半月 0
    db.add(FormalRecord(import_id=9, raw_record_id=1, person_code="P001",
                        store_id_raw="0101", japan_date=date(2026, 9, 3),
                        points=1))
    db.add(PersonDailyStat(person_code="P001", ref_date=date(2026, 9, 3),
                           records=1, p1=1, p2=0, points=1))
    db.commit()

    period.mark_paid(db, "2026-09", 1)          # 上半月已发
    period.sync_period_table(db, "2026-09")
    db.commit()
    row = db.query(PayrollPeriodRow).filter_by(month="2026-09",
                                               person_code="P001").first()
    # 上半月已发 → 可吸收额度=下半月(0) → 结转 -59000 未被吸收 → 递延进下月结转
    assert row.prev_adjust_amount == row.diff_amount - 59000


# ---------- 发放台账（方案 C 第一步：计算与实发分离）----------

def test_record_payment_snapshots_amount(db):
    """台账记录的是**发放时快照**，后续重算不改写它。"""
    from datetime import date
    from app.models import (FormalRecord, ImportFile, PayrollPayment,
                            PayrollPeriodRow, Person, PersonDailyStat)
    from app.services import period

    db.add(Person(code="P001", display_name="甲"))
    db.add(ImportFile(id=9, file_name="f.xlsx", file_sha256="y", file_size=1,
                      stored_path="/tmp/y.xlsx", uploaded_by=1, status="parsed",
                      parsed_sheets=[], ignored_sheets=[], warnings=[], errors=[]))
    db.add(FormalRecord(import_id=9, raw_record_id=1, person_code="P001",
                        store_id_raw="0101", japan_date=date(2026, 9, 3), points=1))
    db.add(PersonDailyStat(person_code="P001", ref_date=date(2026, 9, 3),
                           records=1, p1=1, p2=0, points=1))
    db.commit()
    period.sync_period_table(db, "2026-09")
    db.commit()

    assert period.record_payment(db, "2026-09", "P001", 1, paid_by=1) is True
    row = db.query(PayrollPayment).filter_by(month="2026-09",
                                             person_code="P001").first()
    snapshot = row.amount
    assert snapshot == 250          # 1 点 × 250 円（发放时快照）
    # 再登记同一期 → 不覆盖（事实不可重写）
    assert period.record_payment(db, "2026-09", "P001", 1) is False
    assert db.query(PayrollPayment).filter_by(month="2026-09",
                                              person_code="P001").count() == 1
    # 重算后台账金额不变
    period.sync_period_table(db, "2026-09")
    db.commit()
    assert db.query(PayrollPayment).filter_by(
        month="2026-09", person_code="P001").first().amount == snapshot


def test_paid_seqs_reads_ledger(db):
    from app.services import period
    from app.models import PayrollPayment
    assert period.paid_seqs(db, "2026-09") == set()
    db.add(PayrollPayment(month="2026-09", person_code="P001", seq=1,
                          points=10, amount=2500))
    db.commit()
    assert period.paid_seqs(db, "2026-09") == {1}


def test_unmark_removes_ledger_rows(db):
    from app.services import period
    from app.models import PayrollPayment, PayrollPeriodRow
    db.add(PayrollPeriodRow(month="2026-09", person_code="P001",
                            half1_amount=1000, half1_points=4))
    db.commit()
    n = period.mark_paid(db, "2026-09", 1, marked_by=1)
    assert n == 1
    assert db.query(PayrollPayment).count() == 1
    removed = period.mark_paid(db, "2026-09", 1, unmark=True)
    assert removed == 1 and db.query(PayrollPayment).count() == 0
