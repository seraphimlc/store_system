# -*- coding: utf-8 -*-
"""店铺主档服务：从 raw 同步实体、构建候选对、合并/拆分与审计。"""
import difflib
import re
import unicodedata

from app.db import get_engine, Base
from app.models import RawRecord, ImportFile, StoreEntity, StorePair, StoreMergeLog


def norm_name(s) -> str:
    s = unicodedata.normalize("NFKC", s or "")
    return re.sub(r"[\s\u3000]+", "", s.casefold())


def sync_entities(db) -> dict:
    """从 raw_records 同步实体：每个 store_id_raw 保留最早记录的信息；不删已有。"""
    fmts = {f.id: f.format for f in db.query(ImportFile).all()}
    rows = db.query(RawRecord.import_id, RawRecord.excel_row,
                    RawRecord.store_id_raw, RawRecord.store_name_local_raw,
                    RawRecord.modified_raw, RawRecord.original_row).order_by(
        RawRecord.import_id, RawRecord.excel_row).all()
    created = updated = 0
    order = 0
    cache = {}
    for ent in db.query(StoreEntity).all():
        cache[ent.store_id_raw] = ent
    for imp_id, ex_row, sid, name, mt, orig in rows:
        sid = sid.strip()
        if not sid:
            continue
        order += 1
        ent = cache.get(sid)
        city = ""
        if fmts.get(imp_id) == "wide50" and orig and len(orig) > 4 and orig[4]:
            city = str(orig[4]).strip()
        nm = name.strip()
        addr = ""
        if fmts.get(imp_id) == "wide50" and orig and len(orig) > 21 and orig[21]:
            addr = str(orig[21]).strip()
        if ent is None:
            ent = StoreEntity(store_id_raw=sid, name_local=nm,
                              name_norm=norm_name(name), city=city,
                              address_local=addr,
                              first_seen_import_id=imp_id, master_id=0)
            db.add(ent)
            db.flush()
            ent.master_id = ent.id  # 先自指
            cache[sid] = ent
            created += 1
        else:
            # 店名取"最早的非空写法"；保证 norm/city 兜底
            if not ent.name_local and nm:
                ent.name_local = nm
                ent.name_norm = norm_name(name)
            if not ent.name_norm:
                ent.name_norm = norm_name(name)
            if not ent.city and city:
                ent.city = city
            if not ent.address_local and addr:
                ent.address_local = addr
            updated += 1
    db.commit()
    return {"entities": len(cache), "created": created}


def _pending_pairs(db, kind):
    from sqlalchemy import select
    ents = {e.id: e for e in db.query(StoreEntity).all()}
    rows = db.query(StorePair).filter(StorePair.kind == kind,
                                StorePair.status == 'pending').all()
    out = []
    for p in rows:
        a, b = ents.get(p.entity_a), ents.get(p.entity_b)
        if a is None or b is None:
            continue
        if a.master_id != a.id or b.master_id != b.id:
            continue  # 有一方已并入他档则不再提示
        if a.master_id == b.master_id:
            continue
        out.append({"pair": p, "a": a, "b": b})
    return out


def build_pairs(db, fuzzy_threshold: float = 0.90):
    """程序建候选：同 norm 且同 city 组 → exact 对；近似桶 → fuzzy 对（保留原有状态）。
    全量版：委托增量版，目标 = 全部当前主档实体。"""
    ids = [e.id for e in db.query(StoreEntity).filter(
        StoreEntity.master_id == StoreEntity.id).all()]
    return build_pairs_for_entities(db, ids, fuzzy_threshold=fuzzy_threshold)


def build_pairs_for_entities(db, entity_ids, fuzzy_threshold: float = 0.90) -> dict:
    """增量建候选：只为指定实体（须为当前主档且有 norm）找兄弟生成 exact/fuzzy 对。

    复用 build_pairs 的分组逻辑：
      exact = 同 name_norm 的主档两两成对（归一化全等→程序必同）；
      fuzzy = 同 city(含都空)+首字符+长度桶内 SequenceMatcher≥fuzzy_threshold。
    只生成"至少一端为目标实体"的对——每对在其任一成员首次被"涉及"时生成一次；
    已存在的对不重复插入（幂等）。避免全表重复开销。
    """
    from collections import defaultdict
    target_ids = set(entity_ids or [])
    if not target_ids:
        return {"added": 0}
    ents = db.query(StoreEntity).all()
    by_id = {e.id: e for e in ents}
    targets = [by_id[i] for i in target_ids
               if i in by_id and by_id[i].master_id == by_id[i].id
               and by_id[i].name_norm]
    if not targets:
        return {"added": 0}
    target_ids = {t.id for t in targets}
    existing = set()
    for p in db.query(StorePair.entity_a, StorePair.entity_b).all():
        existing.add(tuple(sorted((p[0], p[1]))))
    added = 0

    def _has_target(ids):
        return any(i in target_ids for i in ids)

    # exact：同 name_norm 的主档间建对（仅涉及目标）
    exact_groups = defaultdict(list)
    for e in ents:
        if e.master_id == e.id and e.name_norm:
            exact_groups[e.name_norm].append(e)
    for g in exact_groups.values():
        gs = sorted(g, key=lambda x: x.id)
        if len(gs) < 2 or not _has_target(e.id for e in gs):
            continue
        for i in range(len(gs)):
            for j in range(i + 1, len(gs)):
                if not (gs[i].id in target_ids or gs[j].id in target_ids):
                    continue
                key = (gs[i].id, gs[j].id)
                if key in existing:
                    continue
                db.add(StorePair(entity_a=gs[i].id, entity_b=gs[j].id,
                                 kind="exact"))
                existing.add(key)
                added += 1
    # fuzzy：同 city（含都空）、首字符同、长度近的桶内比（仅涉及目标）
    buckets = defaultdict(list)
    for e in ents:
        if e.master_id != e.id or not e.name_norm:
            continue
        buckets[(e.city or "", e.name_norm[:1], len(e.name_norm) // 2)].append(e)
    for lst in buckets.values():
        if not _has_target(e.id for e in lst):
            continue
        if len(lst) < 2 or len(lst) > 500:
            continue
        lst.sort(key=lambda x: x.id)
        for i in range(len(lst)):
            na = lst[i].name_norm
            for j in range(i + 1, len(lst)):
                if not (lst[i].id in target_ids or lst[j].id in target_ids):
                    continue
                nb = lst[j].name_norm
                if abs(len(na) - len(nb)) > 2 or len(na) < 3:
                    continue
                r = difflib.SequenceMatcher(None, na, nb).ratio()
                if r >= fuzzy_threshold:
                    key = (lst[i].id, lst[j].id)
                    if key in existing:
                        continue
                    db.add(StorePair(entity_a=lst[i].id, entity_b=lst[j].id,
                                     kind="fuzzy", sim=int(r * 100)))
                    existing.add(key)
                    added += 1
    db.commit()
    return {"added": added}


def auto_merge_exact(db, user_id=None) -> int:
    """exact 候选自动合并：kind=='exact' 且 status=='pending' 的对按同 name_norm
    分组，每组保留 **id 最大** 的实体为主档（与线上口径一致），调用 merge_name_group
    并入其它成员。已知城市≥2 且不同的组（跨城市）跳过——判定不同店留人工
    （与 auto_skip_cross_city / apply_all_recommended 同规则）。
    fuzzy 对不自动合并（需人工/AI）。返回合并的组数；幂等（已并对的 status=merged）。"""
    pair_ids = set()
    for p in db.query(StorePair.entity_a, StorePair.entity_b).filter(
            StorePair.kind == "exact", StorePair.status == "pending").all():
        pair_ids.add(p[0])
        pair_ids.add(p[1])
    if not pair_ids:
        return 0
    ents = {e.id: e for e in db.query(StoreEntity).all()}
    groups = {}
    for eid in pair_ids:
        e = ents.get(eid)
        if e is None or e.master_id != e.id or not e.name_norm:
            continue
        groups.setdefault(e.name_norm, []).append(e)
    merged_groups = 0
    for norm, members in groups.items():
        members = sorted(set(members), key=lambda x: x.id)
        if len(members) < 2:
            continue
        known = {m.city for m in members if m.city}
        if len(known) > 1:   # 跨城市 → 判定不同店，留人工
            continue
        keep = members[-1]   # id 最大为主档（与线上口径一致）
        merge_name_group(db, norm, keep.id, user_id,
                         note="自动合并：归一化全等(程序必同)")
        merged_groups += 1
    return merged_groups


def recompute_affected_months(db, month_hint=None, user_id=None) -> dict:
    """店铺主档变更后重算受影响月份（best-effort 逐月，互不中断）。

    受影响月份：month_hint（"YYYY-MM" 或列表）给出则用之；无法精确定位
    （缺省/非法）退化为"存在正式表的全部月份"。
    逐月依次调 flow.rebuild_month + period.sync_period_table +
    dashboard.sync_dash_metrics，各自 try/except，失败记入 warnings 不中断。
    返回处理了哪些月（同时打印）与逐月结果。"""
    import re as _re
    months = []
    if month_hint:
        hints = (month_hint if isinstance(month_hint, (list, tuple, set))
                 else [month_hint])
        for h in hints:
            h = (h or "").strip()
            if _re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", h or ""):
                months.append(h)
        months = sorted(set(months))
    if not months:
        from app.models import FormalRecord
        months = sorted({str(f.japan_date or "")[:7]
                         for f in db.query(FormalRecord).all()
                         if f.japan_date})
    # 精确扩展：凡"当前为从档且有合并留痕"的实体，其 raw 所在月一并重算——
    # 合并改变这些月的正式表口径（如首次部署时旧有 pending exact 组被并，
    # 受影响月未必在 month_hint 内；8 月多算即由此类合并修复）。
    # 历史从档（promote_or_link 同名归并、无合并日志）不在其中，不受影响。
    _merged_sids = [sid for (sid,) in db.query(StoreEntity.store_id_raw).join(
        StoreMergeLog, StoreMergeLog.entity_id == StoreEntity.id).filter(
        StoreEntity.master_id != StoreEntity.id).all() if sid]
    if _merged_sids:
        _extra = sorted({(r[0] or "")[:7] for r in db.query(
            RawRecord.modified_raw).filter(
            RawRecord.store_id_raw.in_(_merged_sids)).all() if r[0]})
        if _extra:
            months = sorted(set(months) | set(_extra))
    from app.services import flow as _flow
    from app.services import period as _period
    from app.services import dashboard as _dash
    warnings = []
    # 封账月份不重算（与 MCP 闸门同口径：sealed_months 表内月份禁止写入）；
    # 自动化尊重封账，跳过并记 warning（手动 /month/rebuild 行为不受影响）。
    try:
        from app.models import SealedMonth
        _sealed = {r[0] for r in db.query(SealedMonth.month).all()}
        if _sealed:
            keep = [m for m in months if m not in _sealed]
            for _m in set(months) - set(keep):
                warnings.append(f"{_m}: 已封账，跳过重算")
            months = keep
    except Exception:  # noqa: BLE001
        pass
    # **已发完的月份不重算**（用户口径：工资已发出去，历史口径不能被改写）。
    # 判定依据（关键）：该月**两期都已发**（台账同时有 seq1 与 seq2）才算"发完"。
    # 为什么不能只看"有台账"：一个月分两次发薪，且**下半月数据晚于上半月产出**——
    # 若上半月一发就冻结整月，下周导入下半月数据时该月重算会被跳过、新数据永远进不来
    # （实测线上 9 月只发了上半月，正处于这种在途状态）。
    # 效果：已发完的月份（如 8 月两期已发）保护不变；在途月份照常重算（新数据可落地）。
    try:
        from app.models import PayrollPayment
        _by_month = {}
        for m, sq in db.query(PayrollPayment.month,
                              PayrollPayment.seq).distinct().all():
            _by_month.setdefault(m, set()).add(sq)
        _done = {m for m, sqs in _by_month.items() if {1, 2} <= sqs}
        if _done:
            keep = [m for m in months if m not in _done]
            for _m in set(months) - set(keep):
                warnings.append(f"{_m}: 两期均已发薪，跳过重算以免改写已发口径")
            months = keep
    except Exception:  # noqa: BLE001
        pass
    results = {}
    for m in months:
        m_res = {}
        try:
            m_res["rebuild"] = _flow.rebuild_month(db, m, user_id)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{m}: 月度重算失败 {exc!r}")
        try:
            m_res["period_sync"] = _period.sync_period_table(db, m)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{m}: 找平同步失败 {exc!r}")
        try:
            m_res["dash_sync"] = _dash.sync_dash_metrics(db, m)
        except Exception as exc:  # noqa: BLE001
            warnings.append(f"{m}: 看板同步失败 {exc!r}")
        results[m] = m_res
    db.commit()
    if months:
        print(f"[store_master] recompute_affected_months: 已处理 {', '.join(months)}")
    return {"months": months, "warnings": warnings, "results": results}


def merge_pair(db, pair_id: int, keep_entity_id: int, user_id, basis="manual",
               note=""):
    """把对子里另一实体并入 keep 主档。"""
    p = db.get(StorePair, pair_id)
    if p is None:
        raise ValueError("pair not found")
    a = db.get(StoreEntity, p.entity_a)
    b = db.get(StoreEntity, p.entity_b)
    keeper = a if a.id == keep_entity_id else b
    other = b if keeper is a else a
    keeper_master = db.get(StoreEntity, keeper.master_id) if keeper.master_id != keeper.id else keeper
    if keeper_master is None:
        keeper_master = keeper
    old_master = other.master_id
    other.master_id = keeper_master.id
    db.add(StoreMergeLog(entity_id=other.id, from_master=old_master,
                         to_master=keeper_master.id, basis=basis,
                         note=note or p.note, decided_by=user_id))
    p.status = "merged"
    p.note = note or None
    p.decided_by = user_id
    db.commit()


def skip_pair(db, pair_id: int, user_id):
    p = db.get(StorePair, pair_id)
    if p is None:
        raise ValueError("pair not found")
    p.status = "skip"
    p.decided_by = user_id
    db.commit()


def split_entity(db, entity_id: int, user_id):
    """拆回自己为主档（撤销并入）。"""
    e = db.get(StoreEntity, entity_id)
    if e is None:
        raise ValueError("entity not found")
    old = e.master_id
    e.master_id = e.id
    db.add(StoreMergeLog(entity_id=e.id, from_master=old, to_master=e.id,
                         basis="manual", note="拆分回主档", decided_by=user_id))
    db.commit()


def exact_groups(db):
    """A组待处理：按 name_norm 聚类（成员均为当前主档且存在待处理 exact 对）。"""
    from sqlalchemy import func
    pair_ids = set()
    for p in db.query(StorePair.entity_a, StorePair.entity_b).filter(
            StorePair.kind == "exact", StorePair.status == "pending").all():
        pair_ids.update(p)
    ents = {e.id: e for e in db.query(StoreEntity).all()}
    groups = {}
    for eid in pair_ids:
        e = ents.get(eid)
        if e is None or e.master_id != e.id:
            continue
        groups.setdefault(e.name_norm, []).append(e)
    out = []
    for norm, members in groups.items():
        members = sorted(set(members), key=lambda x: x.id)
        if len(members) < 2:
            continue
        out.append({"name_norm": norm, "members": members,
                    "cities": sorted({m.city or "" for m in members})})
    out.sort(key=lambda g: len(g["members"]), reverse=True)
    return out


def merge_name_group(db, name_norm: str, keep_entity_id: int, user_id,
                     note: str = "") -> int:
    """把同 norm 组内其它实体并入 keep 主档；相关 exact 候选标记 merged。"""
    group = db.query(StoreEntity).filter(
        StoreEntity.name_norm == name_norm,
        StoreEntity.master_id == StoreEntity.id).all()
    keep = next((e for e in group if e.id == keep_entity_id), None)
    if keep is None:
        raise ValueError("keep entity not in group")
    ids = [e.id for e in group]
    merged = 0
    for e in group:
        if e.id == keep.id:
            continue
        old = e.master_id
        e.master_id = keep.id
        db.add(StoreMergeLog(entity_id=e.id, from_master=old, to_master=keep.id,
                             basis="program_exact", note=note or None,
                             decided_by=user_id))
        merged += 1
    if ids:
        for p in db.query(StorePair).filter(
                StorePair.kind == "exact", StorePair.status == "pending",
                StorePair.entity_a.in_(ids), StorePair.entity_b.in_(ids)).all():
            p.status = "merged"
            p.decided_by = user_id
    db.commit()
    return merged


def skip_name_group(db, name_norm: str, user_id) -> int:
    group = db.query(StoreEntity).filter(
        StoreEntity.name_norm == name_norm,
        StoreEntity.master_id == StoreEntity.id).all()
    ids = [e.id for e in group]
    skipped = 0
    if ids:
        for p in db.query(StorePair).filter(
                StorePair.kind == "exact", StorePair.status == "pending",
                StorePair.entity_a.in_(ids), StorePair.entity_b.in_(ids)).all():
            p.status = "skip"
            p.decided_by = user_id
            skipped += 1
    db.commit()
    return skipped


def apply_all_recommended(db, user_id) -> dict:
    """整批：A组每组并入最早建档实体；跨城市分组自动留出（防连锁同名误并）。
    返回处理统计；误并可凭日志在实体检索里拆回。"""
    merged_entities = 0
    processed_groups = 0
    skipped_groups = 0
    for g in exact_groups(db):
        members = sorted(g["members"], key=lambda x: x.id)
        known = {m.city for m in members if m.city}
        if len(known) > 1:  # 仅“都填了且不同城市”才算跨城市
            skipped_groups += 1
            continue
        merged_entities += merge_name_group(db, g["name_norm"], members[0].id,
                                            user_id, note="整批推荐(最早主档)")
        processed_groups += 1
    return {"groups": processed_groups, "merged_entities": merged_entities,
            "left_groups": skipped_groups}


def backfill_addresses(db) -> int:
    """给缺地址/缺城市的实体补最早含地址的 wide 行信息。"""
    from app.models import ImportFile, RawRecord
    fmts = {f.id: f.format for f in db.query(ImportFile).all()}
    need = {e.store_id_raw: e for e in db.query(StoreEntity).filter(
        (StoreEntity.address_local.is_(None)) | (StoreEntity.city.is_(None))).all()}
    if not need:
        return 0
    got = {}
    rows = db.query(RawRecord.import_id, RawRecord.store_id_raw,
                    RawRecord.original_row).order_by(
        RawRecord.import_id, RawRecord.excel_row).all()
    for imp_id, sid, orig in rows:
        sid = sid.strip()
        e = need.get(sid)
        if e is None or sid in got or fmts.get(imp_id) != "wide50" or not orig:
            continue
        city = str(orig[4]).strip() if len(orig) > 4 and orig[4] else ""
        addr = str(orig[21]).strip() if len(orig) > 21 and orig[21] else ""
        if city or addr:
            got[sid] = (city or None, addr or None)
    fixed = 0
    for sid, e in need.items():
        c, a = got.get(sid, (None, None))
        if c and not e.city:
            e.city = c
            fixed += 1
        if a and not e.address_local:
            e.address_local = a
            fixed += 1
    db.commit()
    return fixed


def auto_skip_cross_city(db) -> dict:
    """城市规则（用户确认）：同名记录若已知城市≥2 个 → 不同店，整组跳过（不合并）。
    纯同名同城 或 城市未知的组继续留人工。fuzzy 对同理（双方都有城市且不同 → 跳过）。"""
    groups = 0
    pairs_skipped = 0
    # A exact：按组处理
    for g in exact_groups(db):
        cities = {m.city for m in g["members"] if m.city}
        if len(cities) < 2:
            continue
        ids = [m.id for m in g["members"]]
        for pr in db.query(StorePair).filter(
                StorePair.kind == "exact", StorePair.status == "pending",
                StorePair.entity_a.in_(ids), StorePair.entity_b.in_(ids)).all():
            pr.status = "skip"
            pr.note = "跨城市/城市不一致 → 判定不同店"
            pairs_skipped += 1
        groups += 1
    # B fuzzy：双方城市都有且不同 → 跳过
    fz = 0
    for pr in db.query(StorePair).filter(StorePair.kind == "fuzzy",
                                         StorePair.status == "pending").all():
        a = db.get(StoreEntity, pr.entity_a)
        b = db.get(StoreEntity, pr.entity_b)
        if a and b and a.city and b.city and a.city != b.city:
            pr.status = "skip"
            pr.note = "跨城市 → 判定不同店"
            pairs_skipped += 1
            fz += 1
    db.commit()
    return {"exact_groups": groups, "fuzzy_pairs": fz,
            "pairs_skipped": pairs_skipped}


def unmerge_cross_city(db) -> int:
    """修复：非主档实体若与根主档城市均已知且不同 → 拆回自己（撤销跨城市误并）。"""
    from app.models import StoreMergeLog
    ents = {e.id: e for e in db.query(StoreEntity).all()}
    fixed = 0
    for e in db.query(StoreEntity).filter(
            StoreEntity.master_id != StoreEntity.id).all():
        if not e.city:
            continue
        root = e
        seen = set()
        while root.master_id != root.id and root.id not in seen:
            seen.add(root.id)
            root = ents.get(root.master_id) or root
        if root is e:
            continue
        if root.city and root.city != e.city:
            e.master_id = e.id
            db.add(StoreMergeLog(entity_id=e.id, from_master=root.id,
                                 to_master=e.id, basis="manual",
                                 note="跨城市修复：撤销误并"))
            fixed += 1
    db.commit()
    return fixed


_CATALOG_COLS = None      # 延迟构造（避免 import 期就抓模型属性）


def _catalog_cols():
    global _CATALOG_COLS
    if _CATALOG_COLS is None:
        _CATALOG_COLS = (StoreEntity.id, StoreEntity.master_id,
                         StoreEntity.name_local, StoreEntity.name_norm,
                         StoreEntity.city, StoreEntity.address_local,
                         StoreEntity.store_id_raw)
    return _CATALOG_COLS


def _is_root_q():
    """「自己就是主档」的条件：`master_id == id`、为空、或父行**不存在**。

    与老 Python 走路等价（`by_id.get(...) or r` 把悬挂父键兜成自根；
    本地生产副本实测 0 条悬挂、0 条环）。
    """
    from sqlalchemy import or_, select
    t = StoreEntity.__table__
    p = t.alias("p")
    return or_(t.c.master_id.is_(None), t.c.master_id == t.c.id,
               ~select(p.c.id).where(p.c.id == t.c.master_id).exists())


def _catalog_page_sql(db, page: int, per: int) -> dict:
    """无搜索词时的**纯 SQL** 分页：只把当前页那 50 组行取回 Python。

    ⚠️ 2026-10-17 第二批：老实现即使不搜索也要 `db.query(7 列).all()` 把**全表 8 万行**
    拉回来再在 Python 里解根/分组（本地生产副本实测 439ms，线上 `/stores` 剩下的
    ~1.5 秒基本都在这）。现在四步全在库里：
      ① 主档总数 = 「自根行」计数（一条 anti-join，**不递归**）；
      ② 当前页主档 = 自根行 `ORDER BY id LIMIT/OFFSET`；
      ③ 这些主档的分身 = 递归 CTE **从当前页往下走**（只碰几十行，不是全表）；
      ④ 只把这 50 组行取回 Python。
    主档 / 分身都按 id 升序 —— 与老实现同序。
    口径边界：链深上限 10（本地生产副本实测 0/1/2 层）、成环时与老 `seen` 保护不同（实测 0 环）。
    """
    from sqlalchemy import func, literal, select
    t = StoreEntity.__table__
    total = db.query(func.count(StoreEntity.id)).filter(
        _is_root_q()).scalar() or 0
    start = max(0, (max(1, int(page)) - 1) * per)
    ids = [r[0] for r in db.query(StoreEntity.id).filter(_is_root_q())
           .order_by(StoreEntity.id).limit(per).offset(start).all()]
    cols = _catalog_cols()
    rows = ({r.id: r for r in db.query(*cols).filter(
        StoreEntity.id.in_(ids)).order_by(StoreEntity.id)} if ids else {})
    kids = {}
    if ids:
        sub = select(t.c.id, t.c.id.label("root_id"),
                     literal(0).label("depth")).where(t.c.id.in_(ids))
        sub = sub.cte("sub_tree", recursive=True)
        step = select(t.c.id, sub.c.root_id,
                      (sub.c.depth + 1).label("depth")).where(
            t.c.master_id == sub.c.id, t.c.master_id != t.c.id, sub.c.depth < 10)
        sub = sub.union_all(step)
        kid_q = (db.query(*cols, sub.c.root_id)
                 .join(sub, sub.c.id == StoreEntity.id)
                 .filter(sub.c.id != sub.c.root_id)
                 .order_by(StoreEntity.id))
        for r in kid_q:
            kids.setdefault(r.root_id, []).append(r)
    return {"rows": [{"master": rows[i], "children": kids.get(i, [])}
                     for i in ids],
            "total": total, "page": max(1, int(page)),
            "pages": (total + per - 1) // per}


def _catalog_search(db, q: str, page: int, per: int) -> dict:
    """带搜索词的目录分页：**结构走 SQL / 匹配仍在 Python（口径一个字不改）**。

    ⚠️ 2026-10-17 第二批（线上实测）。搜索路径原来 2.0~2.2 秒，拆开是：
    SQL 取 7 列全表 **673ms** + by_id **55ms** + 解根 **209ms** + 分组 **479ms** + 匹配 **268ms**。
    其中「解根 / 分组」是**纯结构**、与匹配口径无关 → 可以安全下沉；这里：
      ① 只取匹配真正要用的 6 列（**去掉 `address_local`**，它只是展示列）；
      ② `root` / `children` 用 int 字典算（不再构造 8 万个 ORM `Row` 再走属性链）；
      ③ **一趟遍历**判定命中根（主档看 4 列、分身看 2 列 —— 与老 `_hit` 逐字相同）；
      ④ 只给当前页的 50 组行补取展示列。
    线上预期：2.0s → ~0.9s（剩下的都是「必须把 8 万行读进来做全角敏感的子串匹配」）。

    ⚠️ **为什么匹配不一起下沉 SQL**：老口径是 `ql in (字段 or "").lower()`。
    PG 的 `lower()/LIKE` 对**全角字符**（如 `Ａ` → `ａ`）与 Python 的 `str.lower()` 未必逐字一致，
    下沉会**悄悄少命中**（用户看不出为什么搜不到）—— 未拍板前不动，这是记在
    `docs/交接-当前状态.md` §5.11 / §5.15 的 backlog。
    """
    from sqlalchemy import select
    t = StoreEntity.__table__
    #: 匹配必需的列（`address_local` 只在展示当前页时才要）
    rows = db.execute(select(t.c.id, t.c.master_id, t.c.name_local,
                             t.c.name_norm, t.c.city, t.c.store_id_raw)
                      .order_by(t.c.id)).all()
    parent = {r[0]: r[1] for r in rows}
    root = {}
    for r in rows:
        i, seen = r[0], set()
        cur = i
        while True:                      # 与老 `while r.master_id != r.id` 同口径（含 `or r` 兜底）
            nxt = parent.get(cur)
            if nxt is None or nxt == cur or cur in seen:
                break
            seen.add(cur)
            cur = nxt
        root[i] = cur
    ql = q.lower()
    hit = set()
    children = {}
    for r in rows:                       # ③ 一趟判定（老实现是 8 万组 pairs 再逐组比）
        i, _mid, nloc, nnorm, city, sid = r
        rt = root[i]
        if rt == i:                      # 主档：4 列
            if (ql in (nloc or "").lower() or ql in (nnorm or "").lower()
                    or ql in (city or "").lower() or ql in (sid or "").lower()):
                hit.add(i)
        else:                            # 分身：2 列（命中的算到它的主档头上）
            children.setdefault(rt, []).append(i)
            if rt not in hit and (ql in (sid or "").lower()
                                  or ql in (nloc or "").lower()):
                hit.add(rt)
    picked = sorted(hit)                 # 主档按 id 升序 —— 与老实现同序
    total = len(picked)
    page = max(1, int(page))
    start = (page - 1) * per
    master_ids = picked[start:start + per]
    cols = _catalog_cols()
    want = list(master_ids)
    for m in master_ids:
        want.extend(children.get(m, ()))
    show = ({r.id: r for r in db.query(*cols).filter(
        StoreEntity.id.in_(want)).order_by(StoreEntity.id)} if want else {})
    return {"rows": [{"master": show[m],
                      "children": [show[c] for c in children.get(m, ())]}
                     for m in master_ids],
            "total": total, "page": page, "pages": (total + per - 1) // per}


def store_catalog(db, q: str = "", page: int = 1, per: int = 50) -> dict:
    """全部店铺目录：主档行 + 其下已并入的分身（可展开/解绑）。

    ⚠️ 2026-10-17 提速（用户："很多页面打开非常慢"）：线上 **82,994 行**，原来
    `db.query(StoreEntity).all()` 把每行都水合成 ORM 对象 —— 实测 **2.6 秒**，
    而 SQL 本身只 175ms（时间全花在构造 8 万个对象 + 最后再用 `ws` 无关的排序列宽上）。
    改成：
      ① 只取模板真正用到的 7 列（`Row` 照旧按属性访问，模板不用改）；
      ② 先算结构（谁是谁的主档），**只给当前页**建行 dict。
    `/stores` 实测 4.0s → ~0.5s。口径不变（主档按 id 升序、分身按 id 升序）。

    ⚠️ 2026-10-17 第二批：分成两条路（两者**口径完全一致**，已逐页逐词对拍）：
      · 不搜索 → `_catalog_page_sql`：计数/翻页/取分身**全在库里**（线上 1.7s → 0.28~0.45s）；
      · 带搜索 → `_catalog_search`：结构（解根/分组）下沉、**匹配口径原样保留在 Python**。
    为什么不把匹配也塞进 SQL：`ql in (字段 or "").lower()` 与 PG 的 `lower()/LIKE` 对
    **全角字符**未必逐字一致，下沉会**悄悄少命中**（用户看不出为什么搜不到）→ 未拍板前不动。
    """
    if not q:
        return _catalog_page_sql(db, page, per)
    return _catalog_search(db, q, page, per)


def auto_handle_exact(db) -> dict:
    """同类名直接定案（用户确认）：同城/未填城市 → 自动并入最早；已填且不同城市 → 自动跳过。
    返回统计。"""
    merged_groups = merged_entities = skipped_groups = 0
    for g in exact_groups(db):
        known = {m.city for m in g["members"] if m.city}
        ids = [m.id for m in g["members"]]
        if len(known) > 1:
            for pr in db.query(StorePair).filter(
                    StorePair.kind == "exact", StorePair.status == "pending",
                    StorePair.entity_a.in_(ids), StorePair.entity_b.in_(ids)).all():
                pr.status = "skip"
                pr.note = "同名但已知城市不同 → 自动判不同店"
            skipped_groups += 1
            db.commit()
            continue
        members = sorted(g["members"], key=lambda x: x.id)
        merged_entities += merge_name_group(db, g["name_norm"], members[0].id, None,
                                            note="导入自动：同类名并入最早")
        merged_groups += 1
    return {"merged_groups": merged_groups, "merged_entities": merged_entities,
            "skipped_groups": skipped_groups}
