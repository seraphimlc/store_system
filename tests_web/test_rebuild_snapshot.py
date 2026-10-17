# -*- coding: utf-8 -*-
"""`rebuild_snapshots` 只存摘要（2026-10-17 用户拍板 A）。

背景：重算前的"证据"原来是把**整月正式表**序列化成 JSON 存进 `payload_json`
（线上实测 2026-09 = 34,118 行 / **7.4MB**，全表逻辑体积 12.8MB），但**全仓没有任何
代码读它**（回灌功能从未实现，MCP 重算工具只有 preview/run）→ 只写不读，
还是一份 `person_code` / `raw_record_id` 的**个人数据副本**。
现在只留摘要（行数/点数合计/按点数分布），并顺手把"整月 ORM 全量水合"换成聚合 SQL。
"""
import json

import app.db as appdb
from app.models import FormalRecord, RebuildSnapshot, User
from app.services import flow
from tests_web.test_flow import _seed_admin, _up, db_fresh


def test_rebuild_snapshot_is_digest_not_full_dump(client, tmp_path):
    _seed_admin(client)
    db = appdb.SessionLocal()
    admin = db.query(User).first()
    imp = _up(db, admin, "snap.xlsx", [
        ["S-S1", "店S1", "", "2026-08-03 09:00:00", "甲(111)", "R1",
         "YES", "YES", "NO"],
        ["S-S2", "店S2", "", "2026-08-04 09:00:00", "甲(111)", "R2",
         "YES", "YES", "NO"],
    ], tmp_path)
    db.close()
    flow.process_import(db_fresh(), imp.id)
    db = appdb.SessionLocal()
    assert flow.finalize_import(db, imp.id)["added"] == 2
    # 重算前的事实（用于核对摘要里的合计）
    rows = db.query(FormalRecord).all()
    n_before = len(rows)
    pts_before = sum(r.points or 0 for r in rows)
    assert n_before == 2 and pts_before > 0

    res = flow.rebuild_month(db, "2026-08")
    assert res["ok"] is True, res
    assert res["formal_before"] == n_before          # 计数改成聚合 SQL 后仍要对
    assert res["points_before"] == pts_before

    snaps = db.query(RebuildSnapshot).all()
    assert len(snaps) == 1, "同月只保留最近一次"
    s = snaps[0]
    assert s.month == "2026-08" and s.row_count == n_before
    payload = json.loads(s.payload_json)
    assert isinstance(payload, dict), "不能又是整月数组（全量快照已废）"
    assert payload["kind"] == "digest"
    assert payload["count"] == n_before
    assert payload["points_sum"] == pts_before
    assert sum(int(k) * int(v) for k, v in payload["by_points"].items()) == pts_before
    # 只写不读 + 个人数据副本 → 摘要里不能有逐行个人/原始记录字段
    for bad in ("person_code", "raw_record_id", "original_row"):
        assert bad not in s.payload_json, bad
    # 体积守门：原来 7.4MB 级别，摘要必须极小
    assert len(s.payload_json) < 1024, len(s.payload_json)
    db.close()
