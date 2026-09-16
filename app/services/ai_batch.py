# -*- coding: utf-8 -*-
"""B组 AI 批判（批处理，OpenAI 兼容接口，可插拔）。

- 未配置 AI_API_KEY → configured=False，不调用。
- 一次请求携带全部候选对，要求返回严格 JSON。
- 采纳：仅 decision ∈ {same,diff} 且 confidence == high 自动执行（same→并入更早主档，diff→跳过）。
- 其余（unknown/medium/low）：写回 ai_decision/ai_confidence/ai_reason，仍留人工。
"""
import json

import httpx

from app.config import get_settings

SYSTEM = (
    "你是店铺主数据核对助手。我会给你若干对候选店铺，每对包含店铺A、店铺B的"
    "店名(可能含日文/英文)、城市与 Store ID。请判断它们在现实中是否同一家店。"
    "输出严格 JSON（不要markdown），结构："
    '{"results":[{"id":<整数=该对编号>,"decision":"same|diff|unknown",'
    '"confidence":"high|medium|low","reason":"一句话理由"}]}'
    "decision: same=同一家店；diff=不同店（含同品牌不同分店）；"
    "unknown=仅凭现有信息无法确定。confidence 只给 high/medium/low。"
)


def configured() -> bool:
    return bool(get_settings().ai_api_key)


def _call(pairs: list, timeout: float = 300.0):
    st = get_settings()
    base = st.ai_base_url.rstrip("/")
    key = st.ai_api_key
    model = st.ai_model
    rows = []
    for p in pairs:
        rows.append(
            f"id={p['id']} | A={p['name_a']} | B={p['name_b']} | "
            f"cityA={p['city_a'] or '未知'} | cityB={p['city_b'] or '未知'}")
    user_msg = "\n".join(rows)
    resp = httpx.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "temperature": 0,
              "messages": [{"role": "system", "content": SYSTEM},
                           {"role": "user", "content": user_msg}]},
        timeout=timeout)
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"]
    # 容忍 ```json 包裹
    content = content.strip()
    if content.startswith("```"):
        content = content.split("\n", 1)[1].rsplit("```", 1)[0].strip()
    data = json.loads(content)
    return {r["id"]: r for r in data["results"]}


CHUNK = 25
RETRIES = 2


def _call_chunk(pairs):
    last = None
    for i in range(RETRIES + 1):
        try:
            return _call(pairs)
        except Exception as e:  # noqa: BLE001
            last = e
            if i < RETRIES:
                time.sleep(6 * (i + 1))
    raise last


def run_batch(db, pairs) -> dict:
    """pairs: [{id,name_a,name_b,city_a,city_b}...]；分批顺序调用，单批失败重试，最终仍败记 errors。"""
    from app.models import StoreEntity, StorePair
    from . import store_master
    if not configured():
        return {"configured": False}
    results = {}
    errors = 0
    for i in range(0, len(pairs), CHUNK):
        chunk = pairs[i:i + CHUNK]
        try:
            results.update(_call_chunk(chunk))
        except Exception as e:  # noqa: BLE001
            errors += len(chunk)
            for p in chunk:
                pair = db.get(StorePair, p["id"])
                if pair is not None:
                    pair.ai_reason = f"AI调用失败({type(e).__name__})，请重试"
            db.commit()
    applied = {"same": 0, "diff": 0, "unknown": 0}
    human = 0
    for p in pairs:
        r = results.get(p["id"])
        if not r:
            continue
        decision = r.get("decision")
        conf = r.get("confidence")
        reason = (r.get("reason") or "")[:500]
        pair = db.get(StorePair, p["id"])
        if pair is None or pair.status != "pending":
            continue
        if decision in ("same", "diff") and conf == "high":
            if decision == "same":
                # 并入“更早建档(实体id小)”的一侧为主档
                a, b = db.get(StoreEntity, pair.entity_a), db.get(StoreEntity, pair.entity_b)
                keep = a if a.id < b.id else b
                store_master.merge_pair(db, pair.id, keep.id, None,
                                        basis="ai", note=f"AI(high): {reason}")
            else:
                store_master.skip_pair(db, pair.id, None)
                pair.note = f"AI(high): {reason}"
                db.commit()
            applied["same" if decision == "same" else "diff"] += 1
        else:
            pair.ai_decision = decision
            pair.ai_confidence = conf
            pair.ai_reason = reason
            human += 1
    db.commit()
    applied["unknown"] = human
    return {"configured": True, "applied": applied,
            "left_human": human, "errors": errors, "total": len(pairs)}


def run_batch_task(run_id: int) -> None:
    """后台执行：记录 AiRun 状态，跑完后写 summary。"""
    from datetime import datetime
    import app.db as appdb
    from app.models import AiRun, StoreEntity, StorePair

    db = appdb.SessionLocal()
    run = db.get(AiRun, run_id)
    if run is None:
        db.close()
        return
    pairs = []
    for pr in db.query(StorePair).filter(StorePair.kind == "fuzzy",
                                         StorePair.status == "pending").all():
        a = db.get(StoreEntity, pr.entity_a)
        b = db.get(StoreEntity, pr.entity_b)
        if a is None or b is None:
            continue
        pairs.append({"id": pr.id,
                      "name_a": a.name_local or a.name_norm,
                      "name_b": b.name_local or b.name_norm,
                      "city_a": a.city or "", "city_b": b.city or ""})
    run.total_pairs = len(pairs)
    db.commit()
    try:
        out = run_batch(db, pairs)
        run.summary = out
    except Exception as e:  # noqa: BLE001
        run.summary = {"error": f"{type(e).__name__}: {e}"}
    run.status = "done"
    run.finished_at = datetime.utcnow()
    db.commit()
    db.close()
