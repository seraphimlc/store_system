# -*- coding: utf-8 -*-
"""店铺主档写工具测试：visit_store_merge_pair / visit_store_skip_pair /
visit_store_split_entity / visit_store_apply_all。

临时 SQLite（Base.metadata.create_all）造数据，绝不碰真实库。
- 能力层（merge_pair / skip_pair / split_entity / apply_all）直接传临时 session
  （f(db, actor, ...) 可复用范式）：正常路径 + 权限/确认语/不存在 的拒绝；
- 工具信封层用 monkeypatch 把 tools.actor_from_ctx 与 app.db.SessionLocal
  重定向到临时库，断言 ok 信封 / UNAUTHORIZED / FORBIDDEN_TOOL / NOT_FOUND /
  CONFIRM_REQUIRED / INTERNAL_WRITE（retryable=False，禁自动重试）。
"""
import asyncio
import types

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models import Base, StoreEntity, StoreMergeLog, StorePair
from app.services import store_master as _sm
from mcp_service import guards, tokens, store_write_ops

W = tokens.Actor(uid=1, role="admin", scopes=["read", "write"], token_id=1)
R = tokens.Actor(uid=1, role="admin", scopes=["read"], token_id=1)


def fake_ctx():
    """无 Authorization 头（stdio 风格）→ actor_from_ctx 直接返回 None。"""
    return types.SimpleNamespace(headers={})


def _seed(s):
    """基础种子：实体 1/2 为 exact 候选对（同 norm 同城，可合并）；3/4 为 fuzzy 对。"""
    ents = [
        # id, store_id_raw, name_local, city, name_norm
        (1, "S001", "店A", "东京", _sm.norm_name("店A")),
        (2, "S002", "店A ", "东京", _sm.norm_name("店A ")),
        (3, "S003", "店C", "", _sm.norm_name("店C")),
        (4, "S004", "店D", "", _sm.norm_name("店D")),
    ]
    for i, sid, name, city, norm in ents:
        s.add(StoreEntity(id=i, store_id_raw=sid, name_local=name,
                          name_norm=norm, city=city, master_id=i))
    s.flush()
    s.add(StorePair(id=1, entity_a=1, entity_b=2, kind="exact", status="pending"))
    s.add(StorePair(id=2, entity_a=3, entity_b=4, kind="fuzzy", status="pending"))
    s.commit()


@pytest.fixture()
def db(tmp_path):
    """能力层测试：临时 SQLite session。"""
    eng = create_engine(f"sqlite:///{tmp_path}/s.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    S = sessionmaker(bind=eng, expire_on_commit=False)
    s = S()
    _seed(s)
    yield s
    s.close()
    eng.dispose()


@pytest.fixture()
def factory(tmp_path, monkeypatch):
    """信封层测试：临时库 factory + 把 app.db.SessionLocal 重定向过去。"""
    eng = create_engine(f"sqlite:///{tmp_path}/env.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.create_all(eng)
    F = sessionmaker(bind=eng, expire_on_commit=False)
    s = F()
    _seed(s)
    s.close()
    monkeypatch.setattr("app.db.SessionLocal", F)
    yield F
    eng.dispose()


def _merge_logs(db):
    return [(r.entity_id, r.from_master, r.to_master)
            for r in db.query(StoreMergeLog).all()]


# ---------------------------------------------------------------------------
# 能力层：正常路径
# ---------------------------------------------------------------------------

def test_merge_pair_ok(db):
    res = store_write_ops.merge_pair(db, W, pair_id=1, keep=1, note="核对后合并")
    assert res["ok"] is True
    d = res["data"]
    assert d["pair_id"] == 1 and d["kind"] == "exact"
    assert d["keep_entity_id"] == 1 and d["merged_entity_id"] == 2
    assert d["affected_entities"] == 1
    # 业务落库：实体 2 并入主档 1；候选对已合并；留痕 basis=program_exact
    assert db.get(StoreEntity, 2).master_id == 1
    assert db.get(StorePair, 1).status == "merged"
    assert db.get(StorePair, 1).note == "核对后合并"
    assert _merge_logs(db) == [(2, 2, 1)]
    assert db.get(StoreMergeLog, 1).basis == "program_exact"
    assert db.get(StoreMergeLog, 1).decided_by == 1


def test_merge_pair_keep_other_side(db):
    res = store_write_ops.merge_pair(db, W, pair_id=1, keep=2)
    assert res["data"]["keep_entity_id"] == 2
    assert res["data"]["merged_entity_id"] == 1
    assert db.get(StoreEntity, 1).master_id == 2
    assert _merge_logs(db) == [(1, 1, 2)]


def test_merge_pair_fuzzy_kind_maps_to_manual(db):
    """fuzzy 对合并 → basis=manual（与网页 /stores/pairs/{pid}/merge 口径一致）。"""
    store_write_ops.merge_pair(db, W, pair_id=2, keep=3)
    assert db.get(StoreMergeLog, 1).basis == "manual"


def test_skip_pair_ok(db):
    res = store_write_ops.skip_pair(db, W, pair_id=2)
    assert res["ok"] is True
    d = res["data"]
    assert d["pair_id"] == 2 and d["kind"] == "fuzzy" and d["status"] == "skip"
    p = db.get(StorePair, 2)
    assert p.status == "skip" and p.decided_by == 1
    # 不产生合并留痕
    assert _merge_logs(db) == []


def test_split_entity_ok(db):
    store_write_ops.merge_pair(db, W, pair_id=1, keep=1)
    assert db.get(StoreEntity, 2).master_id == 1
    res = store_write_ops.split_entity(db, W, entity_id=2,
                                       confirm_text="确认拆分 2")
    assert res["ok"] is True
    d = res["data"]
    assert d["entity_id"] == 2
    assert d["from_master"] == 1 and d["to_master"] == 2
    assert d["affected_entities"] == 1
    assert db.get(StoreEntity, 2).master_id == 2   # 拆回自己为主档
    # 留痕：并入(2→1) + 拆回(1→2)
    assert _merge_logs(db) == [(2, 2, 1), (2, 1, 2)]


def test_apply_all_ok(db):
    """整批：新增同 norm 同城组（并入 1 店）与跨城市组（自动留出）。"""
    db.add(StoreEntity(id=5, store_id_raw="S005", name_local="店B",
                       name_norm=_sm.norm_name("店B"), city="横滨", master_id=5))
    db.add(StoreEntity(id=6, store_id_raw="S006", name_local="店B ",
                       name_norm=_sm.norm_name("店B "), city="横滨", master_id=6))
    db.add(StoreEntity(id=7, store_id_raw="S007", name_local="店E",
                       name_norm=_sm.norm_name("店E"), city="大阪", master_id=7))
    db.add(StoreEntity(id=8, store_id_raw="S008", name_local="店E ",
                       name_norm=_sm.norm_name("店E "), city="名古屋", master_id=8))
    db.add(StorePair(id=3, entity_a=5, entity_b=6, kind="exact", status="pending"))
    db.add(StorePair(id=4, entity_a=7, entity_b=8, kind="exact", status="pending"))
    db.commit()

    res = store_write_ops.apply_all(db, W, confirm_text="确认批量应用合并")
    assert res["ok"] is True
    d = res["data"]
    # 基础 exact 组(1,2) + 新增同城组(5,6) 各并入 1 店；跨城市组(7,8) 留出
    assert d["groups_applied"] == 2
    assert d["entities_merged"] == 2
    assert d["groups_left_manual"] == 1
    assert db.get(StoreEntity, 2).master_id == 1
    assert db.get(StoreEntity, 6).master_id == 5
    assert db.get(StoreEntity, 7).master_id == 7
    assert db.get(StoreEntity, 8).master_id == 8
    assert "网页端" in d["hint"]


# ---------------------------------------------------------------------------
# 能力层：拒绝（权限 / 确认语 / 不存在 / 参数）
# ---------------------------------------------------------------------------

def test_all_capabilities_require_write_scope(db):
    for call in (
        lambda: store_write_ops.merge_pair(db, R, pair_id=1, keep=1),
        lambda: store_write_ops.skip_pair(db, R, pair_id=1),
        lambda: store_write_ops.split_entity(db, R, entity_id=2,
                                             confirm_text="确认拆分 2"),
        lambda: store_write_ops.apply_all(db, R, confirm_text="确认批量应用合并"),
    ):
        with pytest.raises(guards.GuardError) as e:
            call()
        assert e.value.code == "FORBIDDEN_TOOL"


def test_require_write_rejects_no_actor(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.merge_pair(db, None, pair_id=1, keep=1)
    assert e.value.code == "UNAUTHORIZED"


def test_merge_pair_pair_not_found(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.merge_pair(db, W, pair_id=999, keep=1)
    assert e.value.code == "NOT_FOUND"


def test_merge_pair_keep_not_in_pair(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.merge_pair(db, W, pair_id=1, keep=99)
    assert e.value.code == "BAD_PARAM"
    assert "99" in e.value.message


def test_merge_pair_invalid_kind(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.merge_pair(db, W, pair_id=1, keep=1, kind="ai")
    assert e.value.code == "BAD_PARAM"


def test_skip_pair_not_found(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.skip_pair(db, W, pair_id=999)
    assert e.value.code == "NOT_FOUND"


def test_split_entity_confirm_required(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.split_entity(db, W, entity_id=2, confirm_text="拆吧")
    assert e.value.code == "CONFIRM_REQUIRED"


def test_split_entity_not_found(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.split_entity(db, W, entity_id=999,
                                     confirm_text="确认拆分 999")
    assert e.value.code == "NOT_FOUND"


def test_apply_all_confirm_required(db):
    with pytest.raises(guards.GuardError) as e:
        store_write_ops.apply_all(db, W, confirm_text="全并了吧")
    assert e.value.code == "CONFIRM_REQUIRED"


# ---------------------------------------------------------------------------
# 信封层（经 _write_call）：UNAUTHORIZED / FORBIDDEN_TOOL / NOT_FOUND /
# CONFIRM_REQUIRED / ok / INTERNAL_WRITE
# ---------------------------------------------------------------------------

def test_envelope_unauthorized_without_token():
    got = store_write_ops.visit_store_merge_pair(fake_ctx(), pair_id=1, keep=1)
    assert got["ok"] is False
    assert got["error"]["code"] == "UNAUTHORIZED"


def test_envelope_forbidden_for_read_only_token(factory, monkeypatch):
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: R)
    for call in (
        lambda: store_write_ops.visit_store_merge_pair(fake_ctx(), 1, 1),
        lambda: store_write_ops.visit_store_skip_pair(fake_ctx(), 1),
        lambda: store_write_ops.visit_store_split_entity(
            fake_ctx(), 2, confirm_text="确认拆分 2"),
        lambda: store_write_ops.visit_store_apply_all(
            fake_ctx(), confirm_text="确认批量应用合并"),
    ):
        got = call()
        assert got["ok"] is False
        assert got["error"]["code"] == "FORBIDDEN_TOOL"


def test_envelope_merge_pair_ok(factory, monkeypatch):
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: W)
    got = store_write_ops.visit_store_merge_pair(fake_ctx(), 1, 1, note="信封层")
    assert got["ok"] is True
    assert got["data"]["pair_id"] == 1
    s = factory()
    assert s.get(StoreEntity, 2).master_id == 1
    assert s.get(StorePair, 1).status == "merged"
    s.close()


def test_envelope_not_found(factory, monkeypatch):
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: W)
    got = store_write_ops.visit_store_merge_pair(fake_ctx(), 999, 1)
    assert got["ok"] is False
    assert got["error"]["code"] == "NOT_FOUND"
    assert "hint" in got["error"]


def test_envelope_confirm_required(factory, monkeypatch):
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: W)
    got = store_write_ops.visit_store_split_entity(fake_ctx(), 2, confirm_text="拆吧")
    assert got["ok"] is False
    assert got["error"]["code"] == "CONFIRM_REQUIRED"
    got = store_write_ops.visit_store_apply_all(fake_ctx(), confirm_text="全并了吧")
    assert got["ok"] is False
    assert got["error"]["code"] == "CONFIRM_REQUIRED"


def test_envelope_internal_write_on_unexpected(factory, monkeypatch):
    """合并非幂等 → 意外异常必须 INTERNAL_WRITE（禁自动重试），绝不 INTERNAL。"""
    monkeypatch.setattr("mcp_service.tools.actor_from_ctx", lambda ctx: W)

    def boom(*a, **k):
        raise RuntimeError("boom-db-down")

    monkeypatch.setattr(_sm, "merge_pair", boom)
    got = store_write_ops.visit_store_merge_pair(fake_ctx(), 1, 1)
    assert got["ok"] is False
    assert got["error"]["code"] == "INTERNAL_WRITE"
    assert "boom-db-down" in got["error"]["message"]
    assert "不要自动重试" in got["error"]["hint"]

    monkeypatch.setattr(_sm, "apply_all_recommended", boom)
    got = store_write_ops.visit_store_apply_all(fake_ctx(), confirm_text="确认批量应用合并")
    assert got["ok"] is False
    assert got["error"]["code"] == "INTERNAL_WRITE"


# ---------------------------------------------------------------------------
# 注册
# ---------------------------------------------------------------------------

def test_register_exposes_four_tools():
    from mcp.server.mcpserver import MCPServer
    mcp = MCPServer(name="test-store-write", version="0")
    store_write_ops.register(mcp)
    tools = asyncio.run(mcp.list_tools())
    names = {t.name for t in tools}
    assert names == {"visit_store_merge_pair", "visit_store_skip_pair",
                     "visit_store_split_entity", "visit_store_apply_all"}
    desc = {t.name: t.description for t in tools}
    for name in names:
        assert "需要写权限" in desc[name]
        assert "INTERNAL_WRITE" in desc[name] and "不要自动重试" in desc[name]
    assert "确认拆分" in desc["visit_store_split_entity"]
    assert "确认批量应用合并" in desc["visit_store_apply_all"]
    assert "网页端" in desc["visit_store_apply_all"]
    assert "NOT_FOUND" in desc["visit_store_merge_pair"]
