# -*- coding: utf-8 -*-
"""店铺主档自动化测试：上传后增量建候选对 → exact 自动合并 → 受影响月自动重算
→ rebuild_month 内部同步找平。

覆盖 4 个"必须人工介入"缺口的修复：
1) 上传/新增实体后自动生成候选对（增量、幂等）
2) exact 候选对自动合并（fuzzy 不合并、幂等）
3) 合并后自动重算受影响月份（正式表口径 2 → 1）
4) rebuild_month 返回 synced 且找平行已刷新
"""
import pytest

import app.db as appdb
from app.auth import hash_password
from app.models import (FormalRecord, ImportFile, PayrollPeriodRow, RawRecord,
                        StoreEntity, StoreMergeLog, StorePair, User)
from app.services.importer import parse_file, upload_and_store
from app.services import flow, store_master
from tests.helpers import write_workbook

H = ["Store ID", "Store Name-Local", "Store Name-English", "Modified Time",
     "Submitter", "Record ID", "A+ POSM Visible", "Existing A+ POSM", "NEW A+ POSM"]


def _mk(db, sid, name, city="Tokyo", norm=None):
    e = StoreEntity(store_id_raw=sid, name_local=name,
                    name_norm=norm if norm is not None else store_master.norm_name(name),
                    city=city, master_id=0)
    db.add(e)
    db.flush()
    e.master_id = e.id
    return e


def _mk_pair(db, a, b, kind, status="pending", sim=None):
    lo, hi = sorted((a.id, b.id))
    p = StorePair(entity_a=lo, entity_b=hi, kind=kind, status=status,
                  sim=sim)
    db.add(p)
    db.flush()
    return p


def _seed_admin(client):
    db = appdb.SessionLocal()
    db.add(User(username="admin", password_hash=hash_password("pw123456"),
                display_name="管理员", role="admin", is_active=True))
    db.commit()
    db.close()


def _up(db, admin, name, rows, tmp_path):
    p = str(tmp_path / name)
    write_workbook(p, [("STORE_TASK_EXCEL_SHEET ", [H], rows)])
    with open(p, "rb") as f:
        imp = upload_and_store(name, f.read(), admin.id, db)
    parse_file(imp, db)
    return imp


def db_fresh():
    return appdb.SessionLocal()


# ---------------- 缺口1：上传后自动增量生成候选对 ----------------

def test_upload_auto_generates_exact_pair(client, tmp_path):
    """上传含同 name_norm 两实体（空格差异）→ 自动生成 exact 候选对。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "p1.xlsx", [
        ["S-Z1", "ざくろ銀座店", "", "2026-08-05 10:44:46", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-Z2", "ざくろ 銀座店", "", "2026-08-05 10:49:33", "乙(222)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    # 先只建候选（不上 finalize 自动合并），验证 build_pairs_for_entities
    ids = [e.id for e in db.query(StoreEntity).all()]
    r = store_master.build_pairs_for_entities(db, ids)
    assert r["added"] >= 1
    pairs = db.query(StorePair).filter(StorePair.kind == "exact").all()
    assert len(pairs) == 1
    assert pairs[0].status == "pending"
    # 幂等：再跑一遍不重复插入
    assert store_master.build_pairs_for_entities(db, ids)["added"] == 0
    assert db.query(StorePair).count() == 1
    db.close()


def test_build_pairs_incremental_only_pairs_involving_target(client):
    """增量语义：只生成"涉及目标实体"的对；第三方之间的对不生成。"""
    db = appdb.SessionLocal()
    a = _mk(db, "ID1", "スギ薬局新宿")          # norm=スギ薬局新宿
    b = _mk(db, "ID2", "スギ薬局新宿")          # 与 a 同 norm → exact
    c = _mk(db, "ID3", "スギ薬局新宿店")        # 与 a 同桶 → fuzzy (0.923)
    d = _mk(db, "ID4", "スギ薬局新宿店")        # 只与 c 同 norm，桶内无 a
    db.commit()
    # 只以 a 为目标：应生成 (a,b) exact + (a,c) fuzzy；不生成 (b,c)/(c,d) 等
    r = store_master.build_pairs_for_entities(db, [a.id])
    kinds = {}
    for p in db.query(StorePair).all():
        kinds.setdefault((p.entity_a, p.entity_b), p.kind)
    assert (a.id, b.id) in kinds and kinds[(a.id, b.id)] == "exact"
    assert (a.id, c.id) in kinds and kinds[(a.id, c.id)] == "fuzzy"
    assert (b.id, c.id) not in kinds
    assert (c.id, d.id) not in kinds
    db.close()


# ---------------- 缺口2：exact 自动合并（fuzzy 不动、幂等） ----------------

def test_auto_merge_exact_merges_group_keeps_largest(client):
    """同 name_norm 组自动合并：保留 id 最大的实体为主档；fuzzy 不动；幂等。"""
    db = appdb.SessionLocal()
    a = _mk(db, "ID1", "まいばすけっと新宿")
    b = _mk(db, "ID2", "まいばすけっと新宿")
    c = _mk(db, "ID3", "まいばすけっと新宿")
    n = store_master.norm_name("まいばすけっと新宿")
    for e in (a, b, c):
        e.name_norm = n
    d = _mk(db, "ID4", "まいばすけっと新宿店")   # fuzzy 候选（不自动合并）
    _mk_pair(db, a, b, "exact")
    _mk_pair(db, a, c, "exact")
    _mk_pair(db, b, c, "exact")
    fpair = _mk_pair(db, a, d, "fuzzy", sim=95)
    db.commit()
    n_merged = store_master.auto_merge_exact(db, user_id=1)
    assert n_merged == 1
    db.expire_all()
    # 保留 id 最大（c）为主档
    assert db.get(StoreEntity, c.id).master_id == c.id
    assert db.get(StoreEntity, a.id).master_id == c.id
    assert db.get(StoreEntity, b.id).master_id == c.id
    # exact 候选全部 merged，留痕 program_exact
    assert db.query(StoreMergeLog).filter(
        StoreMergeLog.basis == "program_exact").count() == 2
    assert db.query(StorePair).filter(
        StorePair.kind == "exact", StorePair.status == "pending").count() == 0
    # fuzzy 不动
    db.expire_all()
    assert db.get(StorePair, fpair.id).status == "pending"
    # 幂等：再跑返回 0
    assert store_master.auto_merge_exact(db, user_id=1) == 0
    db.close()


def test_auto_merge_exact_skips_cross_city(client):
    """同 norm 但已知城市不同 → 判定不同店，不自动合并（留人工）。"""
    db = appdb.SessionLocal()
    a = _mk(db, "ID1", "セブン新宿", city="Tokyo")
    b = _mk(db, "ID2", "セブン新宿", city="Osaka")
    n = store_master.norm_name("セブン新宿")
    a.name_norm = b.name_norm = n
    _mk_pair(db, a, b, "exact")
    db.commit()
    assert store_master.auto_merge_exact(db, user_id=1) == 0
    db.expire_all()
    assert db.get(StoreEntity, a.id).master_id == a.id
    assert db.get(StoreEntity, b.id).master_id == b.id
    assert db.query(StorePair).filter(
        StorePair.kind == "exact", StorePair.status == "pending").count() == 1
    db.close()


# ---------------- 缺口3：合并后自动重算受影响月份（2 → 1） ----------------

def test_upload_auto_merge_recomputes_affected_month(client, tmp_path):
    """上传链路：建候选 → 自动合并 → 重算受影响月，正式表口径 2 → 1。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "p2.xlsx", [
        ["S-Z1", "ざくろ銀座店", "", "2026-08-05 10:44:46", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-Z2", "ざくろ 銀座店", "", "2026-08-05 10:49:33", "乙(222)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    # 合并前：两条都有效，入正式表 2 条（同店两种写法各算一次 → 多算）
    assert db.query(RawRecord).filter(
        RawRecord.clean_status == "valid").count() == 2
    res = flow.auto_finalize_pipeline(db, imp.id, admin.id)
    assert res["ok"] is True
    # 自动合并已发生：exact 对存在且 merged，留痕 program_exact
    assert db.query(StorePair).filter(
        StorePair.kind == "exact", StorePair.status == "merged").count() == 1
    assert db.query(StoreMergeLog).filter(
        StoreMergeLog.basis == "program_exact").count() == 1
    # 重算已触发：正式表该月只剩 1 条（合并口径）
    assert res.get("recomputed_months") == ["2026-08"]
    db.expire_all()
    formals = db.query(FormalRecord).all()
    assert len(formals) == 1
    # 被并掉的实体行已翻 from_sub
    sub_sid = [e.store_id_raw for e in db.query(StoreEntity).all()
               if e.master_id != e.id]
    assert len(sub_sid) == 1
    assert db.query(RawRecord).filter(
        RawRecord.store_id_raw == sub_sid[0]).one().clean_status == "from_sub"
    db.close()


def test_recompute_affected_months_fallback_to_all_formal_months(client, tmp_path):
    """month_hint 缺省 → 退化为"存在正式表的全部月份"；逐月返回处理结果。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp1 = _up(db, admin, "q1.xlsx", [
        ["S-1", "店1", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    imp2 = _up(db, admin, "q2.xlsx", [
        ["S-2", "店2", "", "2026-09-01 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp1.id)
    flow.process_import(db_fresh(), imp2.id)
    db = appdb.SessionLocal()
    flow.finalize_import(db, imp1.id)
    flow.finalize_import(db, imp2.id)
    months = sorted({str(f.japan_date)[:7] for f in db.query(FormalRecord).all()})
    assert months == ["2026-08", "2026-09"]
    out = store_master.recompute_affected_months(db, month_hint=None)
    assert out["months"] == ["2026-08", "2026-09"]
    assert set(out["results"].keys()) == {"2026-08", "2026-09"}
    # month_hint 精确收窄
    out2 = store_master.recompute_affected_months(db, month_hint="2026-08")
    assert out2["months"] == ["2026-08"]
    db.close()


def test_recompute_expands_to_merged_entities_months(client, tmp_path):
    """合并实体的 raw 所在月即使不在 month_hint 内也会被纳入重算——首次部署时
    旧有 pending exact 组被并，8 月多算不必等该月文件上传即可自动修复。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "t1.xlsx", [
        ["S-Z1", "ざくろ銀座店", "", "2026-08-05 10:44:46", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-Z2", "ざくろ 銀座店", "", "2026-08-05 10:49:33", "乙(222)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    ids = [e.id for e in db.query(StoreEntity).all()]
    store_master.build_pairs_for_entities(db, ids)
    assert store_master.auto_merge_exact(db) == 1
    # month_hint 只给 9 月（无 8 月）→ 被并实体（S-Z1）的 raw 月 8 月仍被纳入
    out = store_master.recompute_affected_months(db, month_hint="2026-09")
    assert "2026-08" in out["months"]
    db.expire_all()
    assert len(db.query(FormalRecord).all()) == 1
    db.close()


# ---------------- 缺口4：rebuild_month 同步找平 + synced ----------------

def test_rebuild_month_returns_synced_and_refreshes_period(client, tmp_path):
    """rebuild_month 成功路径末尾同步找平：返回 synced=true 且找平行已生成/刷新。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "r1.xlsx", [
        ["S-A", "店A", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-B", "店B", "", "2026-08-02 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    assert flow.finalize_import(db, imp.id)["ok"] is True
    res = flow.rebuild_month(db, "2026-08")
    assert res["ok"] is True
    assert res["synced"] is True
    rows = db.query(PayrollPeriodRow).filter(
        PayrollPeriodRow.month == "2026-08").all()
    assert len(rows) == 1          # 甲 的找平行已由 rebuild 内部同步生成
    assert rows[0].person_code == "111"
    db.close()


# ---------------- 上传主流程不被自动化异常破坏（best-effort） ----------------

def test_auto_finalize_best_effort_never_breaks_upload(client, tmp_path, monkeypatch):
    """候选对/自动合并/重算任何异常都不能让上传主流程失败。"""
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "s1.xlsx", [
        ["S-A", "店A", "", "2026-08-01 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    from app.services import store_master as _sm

    def boom(*a, **k):
        raise RuntimeError("自动候选生成炸了")

    # 后台员工分析线程会与测试收尾的 drop_all 抢共享 sqlite 连接（StaticPool）
    # 偶发段错误（既有问题，非本次改动引入）——测试里置为空操作。
    monkeypatch.setattr("app.services.dashboard.analyze_all_staff",
                        lambda d, m: None)
    monkeypatch.setattr(_sm, "build_pairs_for_entities", boom)
    monkeypatch.setattr(_sm, "auto_merge_exact", boom)
    monkeypatch.setattr(_sm, "recompute_affected_months", boom)
    res = flow.auto_finalize_pipeline(db, imp.id, admin.id)
    assert res["ok"] is True
    assert db.query(FormalRecord).count() == 1
    db.close()
