# -*- coding: utf-8 -*-
"""主档合并服务测试。"""
import pytest
import app.db as appdb
from app.models import StoreEntity, StoreMergeLog
from app.services import store_master


def _mk(db, sid, name):
    e = StoreEntity(store_id_raw=sid, name_local=name,
                    name_norm=store_master.norm_name(name), city="Tokyo",
                    master_id=0)
    db.add(e)
    db.flush()
    e.master_id = e.id
    return e


def test_merge_name_group(client):
    db = appdb.SessionLocal()
    a = _mk(db, "ID1", "まいばすけっと 新宿５丁目")
    b = _mk(db, "ID2", "まいばすけっと新宿５丁目店")
    c = _mk(db, "ID3", "まいばすけっと新宿５丁目店")
    db.commit()
    # 三者的 norm 相同？ID1 norm 与 ID2 norm 因 空格/店 差异不同，需强制同 norm
    n = store_master.norm_name("まいばすけっと新宿５丁目店")
    a.name_norm = b.name_norm = c.name_norm = n
    db.commit()
    n_merged = store_master.merge_name_group(db, n, a.id, 1, note="测试")
    assert n_merged == 2
    db.expire_all()
    assert db.get(StoreEntity, b.id).master_id == a.id
    assert db.get(StoreEntity, c.id).master_id == a.id
    assert db.query(StoreMergeLog).count() == 2
    db.close()


def test_unbind_one_keeps_siblings(client):
    db = appdb.SessionLocal()
    master = _mk(db, "M1", "テスト本店")
    a = _mk(db, "M2", "テスト本店２")
    b = _mk(db, "M3", "テスト本店３")
    c = _mk(db, "M4", "テスト本店４")
    db.commit()
    # 绑定 3 家到 master
    for e in (a, b, c):
        e.master_id = master.id
    db.commit()
    # 只解绑 a
    store_master.split_entity(db, a.id, 1)
    db.expire_all()
    assert db.get(StoreEntity, a.id).master_id == a.id
    assert db.get(StoreEntity, b.id).master_id == master.id
    assert db.get(StoreEntity, c.id).master_id == master.id
    db.close()
