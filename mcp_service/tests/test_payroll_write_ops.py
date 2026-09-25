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

def test_register_defines_five_tools():
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
        "visit_payroll_update", "visit_config_set"}
