# -*- coding: utf-8 -*-
"""V3 文件处理流程：店铺主/从档 + 逐行判定 + 绩效申诉。

与旧版（cleaner.run_pipeline 全局重算）不兼容；本模块是 V3 唯一入口。

流程（按 docs/结算主流程设计-v3.md + 万总裁决）：
  上传 parse 落 raw → process_import：
    1) 店铺处理：对文件内 distinct store_id，店铺表缺则新建；同名店归并主/从档
    2) 去重判定（店名trim×结算月窗口）：当月最早可见行 → valid（自动生效，
       无需员工确认）；主档非最早不同日 → master_late（默认滤除、员工可申诉）；
       从档 → from_sub；同日跨文件 → cross_file_dup（均自动滤不可申诉）
    3) 员工建档（缺则建）
    4) 员工对判为 master_late 的被滤记录可「申诉」→ 管理员处理：
       accept 改判有效（下次入表计入）/ reject 维持被滤
"""
import unicodedata
from datetime import datetime

from app.db import get_db  # noqa: F401
from store_settle.rules import visible_is_candidate  # noqa: E402
from app.models import (FormalRecord, ImportFile, Person, RawRecord,
                        StoreEntity, User)


def norm_name(s) -> str:
    """与 store_master.norm_name 一致：NFKC + 去空白 + 小写。"""
    s = unicodedata.normalize("NFKC", s or "")
    return "".join(s.casefold().split())


# ---------------- 店铺处理 ----------------
def _date_of(rr: RawRecord):
    return (rr.modified_raw or "")[:10] or "9999-99-99"


def sync_store_for_import(db, imp: ImportFile):
    """文件内 distinct store_id 建实体；返回 {store_id: StoreEntity}。"""
    recs = (db.query(RawRecord)
            .filter(RawRecord.import_id == imp.id)
            .order_by(RawRecord.excel_row).all())
    # 文件内首见顺序
    seen = {}
    for rr in recs:
        sid = (rr.store_id_raw or "").strip()
        if not sid or sid in seen:
            continue
        seen[sid] = rr
    cache = {e.store_id_raw: e for e in db.query(StoreEntity).all()}
    created = 0
    # 自引用外键中间态：主档行先写 master_id=0、flush 后再自指。
    # SQLite 默认不校验；MySQL 在 INSERT 即校验；PG 由内部触发器校验。
    # 导入批次期间按 dialect 临时关闭该校验（完成即恢复），三库行为一致。
    from sqlalchemy import text as _text
    dname = db.get_bind().dialect.name
    if dname == "mysql":
        db.execute(_text("SET FOREIGN_KEY_CHECKS=0"))
    elif dname == "postgresql":
        db.execute(_text("ALTER TABLE store_entities DISABLE TRIGGER ALL"))
    try:
        for sid, rr in seen.items():
            ent = cache.get(sid)
            if ent is None:
                ent = StoreEntity(store_id_raw=sid, name_local=rr.store_name_local_raw,
                                  name_norm=norm_name(rr.store_name_local_raw),
                                  first_seen_import_id=imp.id, master_id=0)
                db.add(ent)
                db.flush()
                ent.master_id = ent.id      # 先自指（临时主档）
                ent.master_store_id = sid
                cache[sid] = ent
                created += 1
        db.commit()
    finally:
        if dname == "mysql":
            db.execute(_text("SET FOREIGN_KEY_CHECKS=1"))
        elif dname == "postgresql":
            db.execute(_text("ALTER TABLE store_entities ENABLE TRIGGER ALL"))
            db.commit()
    return cache, seen


def _lookup_master(db, name_norm: str, exclude_ids) -> StoreEntity:
    """同名主档查询（归一化相等且非自身）。"""
    cand = (db.query(StoreEntity)
            .filter(StoreEntity.name_norm == name_norm,
                    StoreEntity.master_id == StoreEntity.id)
            .all())
    for c in cand:
        if c.id not in exclude_ids:
            return c
    return None


def promote_or_link_entities(db, cache: dict, imp: ImportFile) -> dict:
    """同名归并：同店名（trim 后原始店名）的多个 store_id 归为同一真实店铺。

    ⚠ 归并键用**原始店名 trim**（NFKC/去空白等归一化会把空格/全角差异的
    不同店误并为同一店——8 月基准证明ざくろ銀座店 vs ざくろ 銀座店是两家店）。
    主档 = 组内"最早可见记录(modified 全时间戳最小)所在 store"；其余标从档。
    返回 {store_id: (entity, is_master, master_entity)}
    """
    # 每个实体最早可见行 id（全库，仅 YES/NO）
    if cache:
        rows = db.query(RawRecord.store_id_raw, RawRecord.id,
                        RawRecord.modified_raw, RawRecord.visible_raw) \
            .filter(RawRecord.store_id_raw.in_(
                [e.store_id_raw for e in cache.values()])).all()
    else:
        rows = []
    best = {}
    for _row in rows:
        sid, rid, m = _row[0], _row[1], _row[2]
        if not visible_is_candidate(_row[3] if len(_row) > 3 else None):
            continue
        if m is None:
            continue
        cur = best.get(sid.strip())
        if cur is None or m < cur[0]:
            best[sid.strip()] = (m, rid)

    groups = {}
    for ent in cache.values():
        key = (ent.name_local or "").strip()
        if not key:
            continue
        groups.setdefault(key, []).append(ent)
    result = {}
    for name, ents in groups.items():
        if len(ents) < 2:
            e = ents[0]
            e.master_id = e.id
            e.master_store_id = e.store_id_raw
            result[e.store_id_raw] = (e, True, e)
            continue
        # 主档 = 组内最早可见记录所在 store（无可见记录者按 id 保底）
        def _rank(e):
            b = best.get(e.store_id_raw)
            return (0, b[0]) if b else (1, str(e.id))
        ents.sort(key=_rank)
        master = ents[0]
        for e in ents[1:]:
            e.master_id = master.id
            e.master_store_id = master.store_id_raw
        master.master_id = master.id
        master.master_store_id = master.store_id_raw
        for e in ents:
            result[e.store_id_raw] = (e, e.id == master.id, master)
    db.commit()
    return result


def update_earliest_anchor(db, imp: ImportFile):
    """更新每家店（store_id）的 raw_data_id = 该店全局最早出现记录。

    同一主档下从档的 master_raw_data_id 冗余主档最早记录。
    锚点只取 visible 有效行（YES/NO）：空白可见性的行不是一次有效巡店，
    不应占用"最早记录"锚位（否则该店后续有效行会被误判重复）。
    比较用 modified 完整时间戳（同日多条时取真正最早一条，而非文件序）。
    """
    # 全库该文件涉及 store 的 raw，按店分组取 modified 最早（仅 visible 行）
    sids = [sid for (sid,) in db.query(RawRecord.store_id_raw)
            .filter(RawRecord.import_id == imp.id,
                    RawRecord.store_id_raw != "")
            .distinct().all()]
    sids = [s for s in sids if s.strip()]
    if not sids:
        return
    rows = db.query(RawRecord).filter(RawRecord.store_id_raw.in_(sids)).all()
    earliest = {}
    for rr in rows:
        if not visible_is_candidate(rr.visible_raw):
            continue
        sid = rr.store_id_raw.strip()
        cur = earliest.get(sid)
        if cur is None or (rr.modified_raw or "z") < (cur.modified_raw or "z"):
            earliest[sid] = rr
    for sid, rr in earliest.items():
        ent = db.query(StoreEntity).filter(StoreEntity.store_id_raw == sid).first()
        if ent is None:
            continue
        ent.raw_data_id = rr.id
        if ent.master_id != ent.id and ent.master_store_id:
            m = db.query(StoreEntity).filter(
                StoreEntity.store_id_raw == ent.master_store_id).first()
            if m and m.raw_data_id:
                ent.master_raw_data_id = m.raw_data_id
    db.commit()


# ---------------- 去重判定 ----------------
def _norm_name(x: str) -> str:
    """店名判重归一化（2026-09 起）：NFKC(全角→半角) + 去空格(半角/全角) + 小写。
    同一店不同写法（如 WineShop Sommelier / Wine shop sommelier）合并为一家。"""
    import unicodedata as _u
    return _u.normalize("NFKC", x or "").replace(" ", "").replace("\u3000", "").lower()


def _name_month_min(db):
    """全库可见行按 (店名trim, modified月) 归组 → {组key: (modified, raw_id, store_id)}。

    判重窗口 = 结算月 + 行原始店名(trim)。**组内保留优先级**（用户口径固化）：
      ① deploy=YES 的行（→ 2 点），多条 YES 取**第一条 YES**；
      ② S1 ∈ {OTHER, AUDIT_SUCCESS} 的行（→ 1 点）；
      ③ 其余（FAILED/NOT_REQUEST 等 → 0 点，不计成绩、不入表）。
    同级内取 modified 最早。跨月与跨月改名不互相压制。
    """
    rows = db.query(RawRecord.id, RawRecord.store_id_raw,
                    RawRecord.store_name_local_raw, RawRecord.modified_raw,
                    RawRecord.visible_raw, RawRecord.import_id,
                    RawRecord.deploy_raw).all()
    _vmx = {}
    for _i in db.query(ImportFile).all():
        _vmx[_i.id] = (((_i.layout or {}).get("value_map") or {})
                       .get("visible"))

    _ONE_PT = ("OTHER", "AUDIT_SUCCESS", "YES", "NO")  # YES/NO 为历史/8月风格兼容

    def _rank(dep, vis, mth):
        """越小越优先：
        - 2026-09 起（用户口径固化）：① deploy=YES→2 点（多条取第一条 YES）
          ② S1 ∈ {OTHER, AUDIT_SUCCESS}→1 点 ③ 其它→0 点不计、不入表；
        - 2026-09 之前（历史/封账月）：保持原口径（最早即最优，不改历史）。
        """
        if mth < "2026-09":
            return 0
        if (dep or "").strip() == "YES":
            return 0
        if (vis or "").strip() in _ONE_PT:
            return 1
        return 2

    mm = {}
    for rid, sid, nm, m, vis, impid, dep in rows:
        if not visible_is_candidate(vis, _vmx.get(impid)):
            continue
        nm = (nm or "").strip()
        if not nm or not m:
            continue
        mth = m[:7]
        key = ((_norm_name(nm) if mth >= "2026-09" else nm), mth)
        cur = mm.get(key)
        rk = _rank(dep, vis, key[1])
        if cur is None or rk < cur[3] or (rk == cur[3] and m < cur[0]):
            mm[key] = (m, rid, (sid or "").strip(), rk)
    return mm


def judge_import(db, imp: ImportFile) -> dict:
    """对文件全部 raw 逐行判定（按 店名trim×结算月 窗口）。

    有效 = 同名分组当月最早可见行——与它挂在主档还是从档编号下无关：
    主档当月未巡、从档巡了 → 从档行即该店当月首次，算有效；主档巡了而
    从档也巡（同名）→ 从档整店滤（8 月基准 99 行 from_sub 全在此类）。
    与当月最早行同 store 更早：
      同日   → cross_file_dup（数据重复，自动认可，不进员工任务）
      异日   → master_late（员工确认，可申诉）
    跨月 / 同 store 跨月改名：各月独立成组，互不压制。
    """
    # 各文件布局的可见性口径（value_map.visible：值→candidate/blank）
    _vm_by_imp = {}
    for _i in db.query(ImportFile).all():
        _vm_by_imp[_i.id] = (((_i.layout or {}).get("value_map") or {})
                             .get("visible"))

    mm = _name_month_min(db)
    recs = (db.query(RawRecord)
            .filter(RawRecord.import_id == imp.id)
            .order_by(RawRecord.excel_row).all())
    stats = {"valid": 0, "master_late": 0, "from_sub": 0,
             "cross_file_dup": 0, "no_ref": 0, "blank": 0}
    for rr in recs:
        sid = (rr.store_id_raw or "").strip()
        nm = (rr.store_name_local_raw or "").strip()
        # 空编号或无店名 → no_ref
        if not sid or not nm:
            rr.clean_status = "no_ref"
            rr.filter_reason = "no_ref"
            rr.confirm_state = "auto_ok"
            stats["no_ref"] += 1
            continue
        # Visible 非候选 → blank（口径来自该文件布局的 value_map；无则非空即候选）
        if not visible_is_candidate(rr.visible_raw, _vm_by_imp.get(rr.import_id)):
            rr.clean_status = "visible_blank"
            rr.filter_reason = None
            rr.confirm_state = "auto_ok"
            stats["blank"] += 1
            continue
        month = (rr.modified_raw or "")[:7]
        best = mm.get((_norm_name(nm) if month >= "2026-09" else nm, month))
        if best is None:
            # 理论不可达：本条自身可见且同名同月，必入 mm
            rr.clean_status = "valid"
            rr.filter_reason = None
            rr.filtered_by_raw_id = None
            rr.confirm_state = "auto_approved"   # 有效自动生效，无需员工确认
            stats["valid"] += 1
            continue
        best_mod, best_raw_id, best_store = best[0], best[1], best[2]
        if best_raw_id == rr.id:
            rr.clean_status = "valid"
            rr.filter_reason = None
            rr.filtered_by_raw_id = None
            rr.confirm_state = "auto_approved"   # 有效自动生效，无需员工确认
            stats["valid"] += 1
            continue
        rr.filtered_by_raw_id = best_raw_id
        # 被滤可见记录默认「认可滤除」(auto_ok)，不再打扰员工；
        # 员工如认为某条被滤错，可主动申诉（create_appeal）。
        if best_store == sid:
            if (best_mod or "")[:10] == (rr.modified_raw or "")[:10]:
                # 同日同店跨文件重复 = 重复导入 → 自动滤、员工端不出现
                rr.clean_status = "cross_file_dup"
                rr.filter_reason = "cross_file_dup"
                rr.confirm_state = "auto_ok"
                stats["cross_file_dup"] += 1
            else:
                rr.clean_status = "master_late"
                rr.filter_reason = "master_late"
                rr.confirm_state = "auto_ok"   # 默认认可滤除；可申诉改判
                stats["master_late"] += 1
        else:
            rr.clean_status = "from_sub"
            rr.filter_reason = "from_sub"
            rr.confirm_state = "auto_ok"       # 默认认可滤除；可申诉改判
            stats["from_sub"] += 1
    db.commit()
    return stats


# ---------------- 员工 + 确认任务 ----------------
def ensure_persons(db, imp: ImportFile, default_password="demo123"):
    """文件内 distinct submitter → Person + User 缺则建。"""
    from app.auth import hash_password
    from app.models import User
    from app.services.importer import ensure_staff_user
    codes = {r.submitter_code for r in db.query(RawRecord).filter(
        RawRecord.import_id == imp.id, RawRecord.submitter_code.isnot(None))}
    names = {}
    for c in codes:
        if db.get(Person, c) is None:
            nm = (db.query(RawRecord.submitter_raw)
                  .filter(RawRecord.import_id == imp.id,
                          RawRecord.submitter_code == c).first())
            raw = (nm[0] or "") if nm else ""
            display = raw.split("(")[0].strip() or c
            db.add(Person(code=c, display_name=display,
                          first_seen_import_id=imp.id))
            names[c] = display
        else:
            names[c] = db.get(Person, c).display_name
    db.commit()
    for c in codes:
        ensure_staff_user(db, c, names.get(c, c))
    db.commit()   # ensure_staff_user 仅 db.add，此处统一落库
    return codes


def process_import(db, import_id: int) -> dict:
    """V3 完整处理一个文件（判定即定稿：valid 自动有效、被滤可申诉）。"""
    imp = db.get(ImportFile, import_id)
    if imp is None:
        raise ValueError("文件不存在")
    cache, seen = sync_store_for_import(db, imp)
    rel = promote_or_link_entities(db, cache, imp)
    update_earliest_anchor(db, imp)
    judge = judge_import(db, imp)
    ensure_persons(db, imp)
    appealable = db.query(RawRecord).filter(
        RawRecord.import_id == imp.id,
        RawRecord.clean_status == "master_late").count()
    return {"judge": judge, "appealable": appealable, "entities": len(rel)}


# ---------------- 判定定稿 → 入正式表 ----------------
def _formal_for_raw(rr, point_rules=None):
    """按判定规则把一条 raw 转成正式表行。

    点数：有 point_rules（文件布局里的组合规则，AI 从"规则"说明提取/人工纠正）
    按 (visible, deploy) 组合算；无则默认口径（分界 2026-07-09，Deploy=YES→2点）。
    """
    from app.models import FormalRecord
    from store_settle.rules import point_for
    from datetime import date as _d
    d = rr.modified_raw[:10] if rr.modified_raw else None
    jd = None
    if d and len(d) == 10:
        try:
            jd = _d.fromisoformat(d)
        except ValueError:
            jd = None
    pts = point_for(jd, rr.deploy_raw or "", _d(2026, 7, 9),
                    visible=rr.visible_raw or "", point_rules=point_rules) if jd else 0
    return FormalRecord(import_id=rr.import_id, raw_record_id=rr.id,
                        person_code=rr.submitter_code,
                        store_id_raw=rr.store_id_raw,
                        japan_date=jd, points=pts)


def finalize_import(db, import_id: int, actor_id=None) -> dict:
    """文件入正式表：valid(raw) 一律自动计入绩效。

    门槛：该文件存在「未决申诉(pending)」时不入（先处理申诉——
    申诉成立会把对应 raw 改判有效并计入，未处理先入会漏）。
    幂等：每次调用重建该文件全部 formal（先删后插），可安全重复执行。
    """
    from app.models import AppealRecord
    imp = db.get(ImportFile, import_id)
    if imp is None:
        return {"ok": False, "msg": "文件不存在"}
    pend = db.query(AppealRecord).filter(
        AppealRecord.import_id == import_id,
        AppealRecord.status == "pending").count()
    if pend:
        return {"ok": False,
                "msg": f"仍有 {pend} 条申诉未处理，处理后再入正式表"}
    db.query(FormalRecord).filter(FormalRecord.import_id == import_id).delete()
    added = 0
    _imp = db.get(ImportFile, import_id)
    _rules = ((_imp.layout or {}).get("point_rules") if _imp else None)
    for rr in db.query(RawRecord).filter(
            RawRecord.import_id == import_id,
            RawRecord.clean_status == "valid").all():
        _fr = _formal_for_raw(rr, point_rules=_rules)
        if _fr.points is None or _fr.points > 0:   # 0 点=不计成绩，不入表
            db.add(_fr)
            added += 1
    db.commit()
    from app.services import perf as _perf
    months = sorted({(r[0] or "")[:7] for r in db.query(
        RawRecord.modified_raw).filter(
            RawRecord.import_id == import_id,
            RawRecord.clean_status == "valid").all()})
    for m in months:
        if len(m) == 7:
            _perf.sync_month_stats(db, m)   # 同步 person_daily_stats
    return {"ok": True, "added": added}


def rebuild_month(db, month: str, actor_id=None) -> dict:
    """按「结算月」重算并重建正式表（防同月补传双算 / 保留申诉成果）。

    背景：判重按 (店名, 结算月) 给全库当月最早一条 valid；但入表按文件粒度。
    若当月数据已入表后又补传含更早记录的文件，旧文件当月那条不会自动撤下，
    会造成同店同月双算。本函数把当月口径统一重算一次：

    1) 门槛：当月存在 pending 申诉 → 拒绝（先处理申诉再重算）。
    2) 对「当月有 raw」的全部文件重跑 judge（judge_import 以全库店名×月窗口
       重判每行；补传的更早记录会把旧行正确改判 master_late/交叉重复等）。
    3) 保护申诉成果：管理员已认可(appeals.status=approved)的 raw 重判后
       恢复 clean_status=valid（申诉改判不得被重判推翻）。
    4) 重建当月正式表：删除 japan_date 属该月的 FormalRecord，按
       clean_status=='valid' 且 modified 属该月的 raw 重新生成。
    """
    from app.models import AppealRecord
    from datetime import date as _d
    import re as _re
    # 入口守卫（唯一关口）：必须严格 YYYY-MM —— 用 [0-9] 而非 \d（\d 是 Unicode 语义，
    # 会放行全角「２０２６-08」：int() 解析成 2026 而 LIKE 匹配全角串 → 先删后填 0 行、
    # 整月正式表被清空）；用 fullmatch 而非 ^...$（拒绝尾随换行）；
    # 年份限 1..9998：m0==12 时会用 date(y+1, 1, 1)，9999 会越界。
    if (not _re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month or "")
            or not 1 <= int(month[:4]) <= 9998):
        return {"ok": False, "msg": "月份格式不正确（应为 YYYY-MM）"}
    # 根治层：只解析一次，规范月串 ym 同时驱动「日期区间」与「LIKE 匹配模式」，
    # 二者同源 → 即使上面的守卫被绕过/被删，也不可能出现「区间是 8 月、LIKE 是全角串」
    # 这种删错月的组合（ym 由 int 解析结果格式化回 ASCII）。
    y, m0 = int(month[:4]), int(month[5:7])
    ym = f"{y:04d}-{m0:02d}"
    if m0 == 12:
        ny, nm = y + 1, 1
    else:
        ny, nm = y, m0 + 1
    try:
        lo, hi = _d(y, m0, 1), _d(ny, nm, 1)
    except ValueError:      # 兜底：守卫被绕过时也不 500，视同格式错误
        return {"ok": False, "msg": "月份格式不正确（应为 YYYY-MM）"}
    pend = (db.query(AppealRecord)
            .join(RawRecord, RawRecord.id == AppealRecord.raw_record_id)
            .filter(AppealRecord.status == "pending",
                    RawRecord.modified_raw.like(ym + "%")).count())
    if pend:
        return {"ok": False,
                "msg": f"该月仍有 {pend} 条申诉未处理，处理后再重算"}
    fids = [r[0] for r in db.query(RawRecord.import_id).filter(
        RawRecord.modified_raw.like(ym + "%")).distinct().all()]
    # 申诉认可成果（先记，防 judge 重判推翻）
    approved = [ap.raw_record_id for ap in db.query(AppealRecord).filter(
        AppealRecord.status == "approved").all()]
    files = []
    for fid in fids:
        imp = db.get(ImportFile, fid)
        if imp is None:
            continue
        judge_import(db, imp)
        files.append(fid)
    for rid in approved:
        rr = db.get(RawRecord, rid)
        if rr is not None and rr.clean_status != "valid":
            rr.clean_status = "valid"
            rr.filter_reason = None
            rr.filtered_by_raw_id = None
            rr.confirm_state = "approved"
    # 重建当月正式表
    before = [f for f in db.query(FormalRecord).filter(
        FormalRecord.japan_date >= lo, FormalRecord.japan_date < hi).all()]
    pts_before = sum(f.points or 0 for f in before)
    db.query(FormalRecord).filter(
        FormalRecord.japan_date >= lo,
        FormalRecord.japan_date < hi).delete()
    db.commit()
    added = 0
    # 从档排除（精确名单）：同店异写法的店已归并从档，重建时不入正式表，
    # 保证"同一家店不同写法只算一次"（数据修复定的"最合理识别逻辑"）。
    # 仅排除明确归并的 4 组，不动历史从档（历史从档店正常有效）。
    import os
    sub_ids = set(os.environ.get(
        "DEDUP_SUB_STORES",
        "0101047092026031200555097|0202047092026032480084411|"
        "0101047092026081903348200|0101047092026060970019734").split("|"))
    if sub_ids == {""}:
        sub_ids = set()
    # 各文件布局里的点数组合规则（AI 从"规则"说明提取/人工纠正）
    _rules_by_imp = {i.id: ((i.layout or {}).get("point_rules"))
                     for i in db.query(ImportFile).all()}
    for rr in db.query(RawRecord).filter(
            RawRecord.clean_status == "valid",
            RawRecord.modified_raw.like(ym + "%")).all():
        if rr.store_id_raw in sub_ids:
            rr.clean_status = "from_sub"
            rr.filter_reason = "from_sub"
            rr.confirm_state = "auto_ok"
            continue
        _fr = _formal_for_raw(rr, point_rules=_rules_by_imp.get(rr.import_id))
        if _fr.points is None or _fr.points > 0:   # 0 点=不计成绩，不入表
            db.add(_fr)
            added += 1
    db.commit()
    after = db.query(FormalRecord).filter(
        FormalRecord.japan_date >= lo, FormalRecord.japan_date < hi).all()
    pts_after = sum(f.points or 0 for f in after)
    from app.services import perf as _perf
    _perf.sync_month_stats(db, ym)        # 同步 person_daily_stats（用规范月串）
    return {"ok": True, "files": files, "formal_before": len(before),
            "formal_after": len(after), "points_before": pts_before,
            "points_after": pts_after}


# ---------------- 员工确认 / 绩效申诉（页面辅助） ----------------
# 被滤类型中员工可申诉的：master_late(疑似重复) / from_sub(从档归并)。
# cross_file_dup = 同日同店重复导入 → 自动滤(auto_ok)，员工端不出现。
# 员工端默认全部认可（不打扰），仅当认为某条被滤错时主动申诉。
APPEALABLE = ("master_late", "from_sub")


def appeal_list(db, person_code):
    """该员工可申诉/已申诉的被滤记录（master_late/from_sub），按天倒序，
    返回 [(day, [raws])]，用于员工端「我的申诉」。
    已申诉的行也列出（页面显示状态徽标），未申诉的行显示申诉入口。"""
    rows = (db.query(RawRecord)
            .filter(RawRecord.submitter_code == person_code,
                    RawRecord.clean_status.in_(APPEALABLE))
            .order_by(RawRecord.modified_raw.desc()).all())
    out = {}
    for rr in rows:
        day = (rr.modified_raw or "")[:10] or "未知"
        out.setdefault(day, []).append(rr)
    return sorted(out.items(), reverse=True)


def appeal_map(db, person_code):
    """该员工全部记录 → 申诉记录 map（含已处理的）。"""
    from app.models import AppealRecord
    m = {}
    for ap in db.query(AppealRecord).filter(
            AppealRecord.submitter_code == person_code).all():
        m[ap.raw_record_id] = ap
    return m


def create_appeal(db, raw_id, person_code, reason=""):
    """员工申诉一条被滤记录（master_late/from_sub）→ appeals(pending)。"""
    from app.models import AppealRecord
    rr = db.get(RawRecord, raw_id)
    if rr is None or rr.submitter_code != person_code:
        raise ValueError("记录不属于你")
    if rr.clean_status not in APPEALABLE:
        raise ValueError("该条不可申诉（仅疑似重复/从档归并可申诉）")
    old = db.query(AppealRecord).filter(
        AppealRecord.raw_record_id == raw_id).first()
    if old is not None:
        raise ValueError("该条已申诉过")
    db.add(AppealRecord(raw_record_id=raw_id, import_id=rr.import_id,
                        submitter_code=person_code, reason=reason,
                        status="pending"))
    rr.confirm_state = "disputed"     # 申诉中标记
    db.commit()


def resolve_appeal(db, appeal_id, decision, actor_id, file_id=None) -> dict:
    """管理员处理申诉：
    accept → 该 raw 改判有效(下次入表计入)；reject → 维持被滤(auto_ok)。

    file_id 传入时校验归属：申诉对应的 raw 不属于该文件则拒绝（防串文件处理）；
    file_id=None 时跳过校验（兼容其它调用方）。
    """
    from app.models import AppealRecord
    ap = db.get(AppealRecord, appeal_id)
    if ap is None or ap.status != "pending":
        return {"ok": False, "msg": "申诉不存在或已处理"}
    rr = db.get(RawRecord, ap.raw_record_id)
    if file_id is not None:
        owner = rr.import_id if rr is not None else ap.import_id
        if owner != file_id:
            return {"ok": False, "msg": "申诉不属于该文件"}
    if decision == "accept":
        rr.clean_status = "valid"
        rr.filter_reason = None
        rr.confirm_state = "approved"
        ap.status = "approved"
    else:
        rr.confirm_state = "auto_ok"   # 员工已申诉过 → 维持被滤并定案
        ap.status = "rejected"
    ap.handled_by = actor_id
    ap.handled_at = datetime.utcnow()
    db.commit()
    return {"ok": True, "decision": decision}


def auto_finalize_pipeline(db, fid: int, user_id: int = None) -> dict:
    """入正式表 + 全自动后续：工资/找平 + 看板统计 + 员工分析预生成(后台)。
    供「入正式表」按钮与「上传自动入表」共用。返回 finalize 结果。"""
    res = finalize_import(db, fid, user_id)
    if not res["ok"]:
        return res
    from app.models import RawRecord as _RR
    _months = sorted({(r.modified_raw or "")[:7] for r in
                      db.query(_RR).filter(_RR.import_id == fid).all()
                      if r.modified_raw})
    from app.services import period as _pay
    from app.services import dashboard as _D
    for _mo in _months:
        try:
            _pay.sync_period_table(db, _mo)
        except Exception:  # noqa: BLE001
            pass
        try:
            _D.sync_dash_metrics(db, _mo)
        except Exception:  # noqa: BLE001
            pass
    db.commit()
    import threading as _th
    from app.db import SessionLocal as _SL

    def _bg():
        _db = _SL()
        try:
            for _mo in _months:
                _D.analyze_all_staff(_db, _mo)
        except Exception:  # noqa: BLE001
            pass
        finally:
            _db.close()

    _th.Thread(target=_bg, daemon=True).start()
    res["months"] = _months
    return res
