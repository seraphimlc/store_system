# -*- coding: utf-8 -*-
"""AI 批判服务测试（mock 模型响应，不触网）。"""
import pytest

import app.db as appdb
from app.models import StoreEntity, StoreMergeLog, StorePair
from app.services import ai_batch, store_master


def _mk(db, sid, name):
    e = StoreEntity(store_id_raw=sid, name_local=name,
                    name_norm=store_master.norm_name(name), city="Tokyo",
                    master_id=0)
    db.add(e)
    db.flush()
    e.master_id = e.id
    return e


@pytest.fixture
def three_pairs(client):
    db = appdb.SessionLocal()
    a1, b1 = _mk(db, "S1", "炭火焼鳥 塚田農場 赤羽店"), _mk(db, "S2", "炭火焼鳥塚田農場 赤羽")
    a2, b2 = _mk(db, "S3", "まいばすけっと 新宿５丁目"), _mk(db, "S4", "まいばすけっと新宿５丁目店")
    a3, b3 = _mk(db, "S5", "7-ELEVEN 新宿三井ビル店"), _mk(db, "S6", "7-ELEVEN 西新宿三井ビル店")
    db.flush()
    pairs = []
    for x, y in [(a1, b1), (a2, b2), (a3, b3)]:
        x.name_norm = y.name_norm = store_master.norm_name(y.name_local)
        pr = StorePair(entity_a=x.id, entity_b=y.id, kind="fuzzy", sim=95)
        db.add(pr)
        pairs.append((x, y, pr))
    db.commit()
    yield [(x.id, y.id, pr.id) for x, y, pr in pairs]
    db.close()


def test_ai_batch_applies_high_and_leaves_medium(monkeypatch, three_pairs):
    def fake_call(pairs, timeout=300.0):
        return {
            three_pairs[0][2]: {"decision": "same", "confidence": "high",
                                "reason": "仅差'店'字"},
            three_pairs[1][2]: {"decision": "diff", "confidence": "high",
                                "reason": "写法不同但同店不同名"},
            three_pairs[2][2]: {"decision": "unknown", "confidence": "low",
                                "reason": "可能不同分店"},
        }
    monkeypatch.setattr(ai_batch, "configured", lambda: True)
    monkeypatch.setattr(ai_batch, "_call", fake_call)
    db = appdb.SessionLocal()
    out = ai_batch.run_batch(db, [
        {"id": three_pairs[0][2], "name_a": "a", "name_b": "b"},
        {"id": three_pairs[1][2], "name_a": "a", "name_b": "b"},
        {"id": three_pairs[2][2], "name_a": "a", "name_b": "b"},
    ])
    assert out["configured"] and out["applied"]["same"] == 1
    assert out["applied"]["diff"] == 1 and out["left_human"] == 1
    assert db.query(StoreMergeLog).count() >= 1
    p3 = db.get(StorePair, three_pairs[2][2])
    assert p3.status == "pending" and p3.ai_decision == "unknown"
    db.close()


def test_ai_not_configured(three_pairs):
    import os
    os.environ.pop("AI_API_KEY", None)
    db = appdb.SessionLocal()
    out = ai_batch.run_batch(db, [])
    assert out == {"configured": False}
    db.close()


def test_call_chunk_retries_with_backoff_then_raises(monkeypatch):
    """重试路径必须真能跑。

    ⚠️ 回归（2026-10-17 审查抓到）：`_call_chunk` 用了 `time.sleep(6 * (i + 1))`，
    但 `app/services/ai_batch.py` **没有 `import time`** → 第一次重试就 NameError，
    被 `run_batch` 的 `except Exception` 吞成 `AI调用失败(NameError)`，
    **把真实的 API 错误盖掉**。原有测试打桩的是 `_call`，永远进不到这里。
    """
    attempts, slept = [], []

    def boom(pairs, timeout=300.0):
        attempts.append(pairs)
        raise RuntimeError("对端 502")

    monkeypatch.setattr(ai_batch, "_call", boom)
    monkeypatch.setattr(ai_batch.time, "sleep", lambda s: slept.append(s))
    with pytest.raises(RuntimeError, match="对端 502"):
        ai_batch._call_chunk([{"id": 1}])
    assert len(attempts) == ai_batch.RETRIES + 1      # 首次 + RETRIES 次重试
    assert slept == [6 * (i + 1) for i in range(ai_batch.RETRIES)]   # 退避 6s / 12s
