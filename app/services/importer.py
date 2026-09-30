# -*- coding: utf-8 -*-
"""文件上传与解析入库服务（spec §4.2/§4.3；引擎 loader 复用）。

upload_and_store: 校验 + sha256 防重 + 落盘
parse_file: loader 解析 → 单事务写 imports 诊断 + raw_records + persons 补全
"""
from typing import Optional
import hashlib
import os
import re

from app.config import get_settings
from app.models import ImportFile, Person, RawRecord, User
from app.services.login_names import unique_username
from store_settle import loader as engine_loader


class UploadError(Exception):
    pass


class DuplicateUpload(UploadError):
    pass


def _save_blob(content: bytes) -> str:
    sha = hashlib.sha256(content).hexdigest()
    d = get_settings().upload_dir
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, sha + ".xlsx")
    if not os.path.exists(path):
        with open(path, "wb") as f:
            f.write(content)
    return sha, path


def _name_of(submitter_raw: str) -> str:
    m = re.match(r"^(.+)\(\d+\)\s*$", submitter_raw.strip())
    return (m.group(1).strip() if m else submitter_raw) or "未识别"


def ensure_staff_user(db, person_code: str, display_name: str) -> User:
    """员工账号幂等创建：导入发现新人员编号时自动开户。

    登录名由姓名自动生成（模型优先：中文→拼音 / 日文→罗马字 / 英文→原名；
    模型不可用时回退规则拼音/编号）；口令为系统默认初始口令，首登须改密。
    已存在该 person_code 的账号（含早期手工账号）则不重复创建。
    """
    from app.auth import hash_password
    from app.services.login_names import login_name_for
    existing = (db.query(User)
                .filter(User.role == "staff", User.person_code == person_code)
                .first())
    if existing is not None:
        return existing
    username = login_name_for(db, display_name, person_code)
    u = User(username=username,
             password_hash=hash_password(get_settings().default_staff_password),
             display_name=display_name, role="staff", person_code=person_code,
             is_active=True, status="active", must_change_password=True)
    db.add(u)
    return u


def upload_and_store(filename: str, content: bytes, user_id: int, db) -> ImportFile:
    if not filename.lower().endswith(".xlsx"):
        raise UploadError("仅支持 .xlsx 文件")
    if len(content) > get_settings().max_upload_mb * 1024 * 1024:
        raise UploadError(f"超过 {get_settings().max_upload_mb}MB 上限")
    sha, path = _save_blob(content)
    exists = db.query(ImportFile).filter(ImportFile.file_sha256 == sha).first()
    if exists:
        raise DuplicateUpload("该文件已存在（同内容 sha256）")
    imp = ImportFile(file_name=filename, file_sha256=sha, file_size=len(content),
                     stored_path=path, uploaded_by=user_id,
                     status="uploaded", parsed_sheets=[], ignored_sheets=[],
                     warnings=[], errors=[])
    db.add(imp)
    db.commit()
    return imp


def parse_file(imp: ImportFile, db, layout: Optional[dict] = None) -> None:
    """loader 解析并把行落库；失败 → status=failed + errors（单文件事务）。

    布局（表头行+列坐标+值语义）：
    - layout 显式传入（人工纠正 reparse）→ 用之；
    - 否则系统内调用模型识别（通用能力），失败/未配置回退规则。
    解析成功后把所用布局写入 imp.layout（页面可见/可纠正）。
    """
    ai_note = ""
    used = None
    if layout:
        used = layout
        ai_note = f"布局(人工纠正): 表头行{layout.get('header_row')} 列{layout.get('cols')}"
    else:
        from app.services.ai_visit import (ai_parse_visit_layout,
                                           default_layout, merge_defaults)
        try:
            used = ai_parse_visit_layout(imp.stored_path) or None
        except Exception as e:  # noqa: BLE001
            used = None
            ai_note = (f"AI 布局解析异常: {type(e).__name__}: {e}，"
                       f"回退规则解析")
        if used:
            ai_note = (f"AI 布局解析: 表头行{used['header_row']} "
                       f"列{used['cols']} 值语义{used.get('value_map') or {}} "
                       f"规则{len(used.get('point_rules') or [])}条")
        else:
            # AI 不可用 → 规则解析；用配置默认口径兜底值语义/点数规则
            used = default_layout() or None
            if used:
                ai_note = ("AI 布局解析: 无结果，回退规则解析 + 配置默认口径"
                           f"（{used.get('point_rules') and len(used['point_rules'])}条规则）")
            else:
                ai_note = "AI 布局解析: 无结果，回退规则解析"
    if used:
        used = merge_defaults(used)
    try:
        res = engine_loader.load_workbook(
            imp.stored_path, filename=imp.file_name, col_override=used)
    except Exception as e:  # 损坏/非 zip 等：绝不猜测，标 failed
        imp.status = "failed"
        imp.errors = [f"文件无法解析: {type(e).__name__}: {e}"]
        db.commit()
        return
    if res.failed:
        imp.status = "failed"
        imp.errors = res.errors
        imp.warnings = res.warnings
        db.commit()
        return
    if ai_note:
        imp.warnings = (imp.warnings or []) + [ai_note]

    persons_cache = {}  # code -> name seen this file（首见写入）
    for row in res.rows:
        code = row.submitter_code
        if code and code not in persons_cache:
            existing = db.get(Person, code)
            if existing is None:
                name = _name_of(row.submitter_raw)
                db.add(Person(code=code, display_name=name,
                              first_seen_import_id=imp.id))
            persons_cache[code] = True
        db.add(RawRecord(
            import_id=imp.id, sheet_name=row.sheet_name, excel_row=row.excel_row,
            store_id_raw=row.store_id_raw, store_name_local_raw=row.store_name_local_raw,
            store_name_en_raw=row.store_name_en_raw, modified_raw=row.modified_raw,
            submitter_raw=row.submitter_raw, submitter_code=row.submitter_code,
            record_id_raw=row.record_id_raw, visible_raw=row.visible_raw or None,
            deploy_raw=row.deploy_raw or None, original_row=row.original_row,
        ))

    imp.format = res.format
    imp.header_row = res.header_row
    imp.data_start_row = res.data_start_row
    imp.parsed_sheets = res.parsed_sheets
    imp.ignored_sheets = res.ignored_sheets
    imp.total_rows = res.total_rows
    imp.parsed_rows = res.parsed_rows
    imp.status = "parsed"
    imp.warnings = (res.warnings or []) + ([ai_note] if ai_note else [])
    imp.errors = []
    if used:
        imp.layout = dict(used)
    db.commit()



# 删文件后需要连带清理的**遗留表**（无 ORM 模型，用方言无关的文本前缀匹配）
_LEGACY_BY_DATE = ("daily_system_points", "daily_activity", "clean_records")


def purge_orphans(db, months=None, store_ids=None) -> dict:
    """清理"正式表已无记录"的派生数据（月维度 + 店铺实体）。

    背景：删文件只删正式表/原始行；月维度的派生数据（月绩效/日统计/应发工资/看板/
    员工分析）与店铺主档会留下幽灵数据（2026-09-29 线上实测：删掉测试文件后，
    11 月的派生行仍在）。判定规则统一为"该月/该店还有没有正式记录"。
    """
    from datetime import date

    from sqlalchemy import func, text as _text

    from app.models import (DashMetric, FormalRecord, MonthPerfRecord,
                            PayrollPeriodRow, PersonDailyStat, StaffAnalysis,
                            StoreEntity, StorePair)
    out = {"months": [], "stores": 0}
    real_months = {str(m)[:7] for (m,) in
                   db.query(FormalRecord.japan_date).distinct().all() if m}
    for month in sorted({str(m)[:7] for m in (months or []) if m}):
        if month in real_months:
            continue                      # 该月还有正式记录 → 不动它的派生数据
        y, mo = int(month[:4]), int(month[5:7])
        lo = date(y, mo, 1)
        hi = date(y + 1, 1, 1) if mo == 12 else date(y, mo + 1, 1)
        # ORM 表（方言无关）
        db.query(PersonDailyStat).filter(PersonDailyStat.ref_date >= lo,
                                         PersonDailyStat.ref_date < hi).delete(
            synchronize_session=False)
        for model in (MonthPerfRecord, PayrollPeriodRow, DashMetric, StaffAnalysis):
            db.query(model).filter(model.month == month).delete(
                synchronize_session=False)
        # 遗留表（可能不存在）：只回滚这一步
        for table in _LEGACY_BY_DATE:
            try:
                with db.begin_nested():
                    db.execute(_text("DELETE FROM %s WHERE CAST(japan_date AS TEXT)"
                                     " LIKE :v" % table), {"v": month + "%"})
            except Exception:  # noqa: BLE001
                pass
        out["months"].append(month)
    # 店铺实体：该文件带来的、且已无正式记录的（先删配对关系，再删实体）
    for sid in (store_ids or []):
        if not sid:
            continue
        has = (db.query(func.count(FormalRecord.id))
               .filter(FormalRecord.store_id_raw == sid).scalar() or 0)
        if has:
            continue
        for e in db.query(StoreEntity).filter(StoreEntity.store_id_raw == sid).all():
            slaves = (db.query(func.count(StoreEntity.id))
                      .filter(StoreEntity.master_id == e.id,
                              StoreEntity.id != e.id).scalar() or 0)
            if slaves:                    # 有人挂在它下面（合并主档）→ 不动
                continue
            ids = [e.id, e.master_id]
            try:
                with db.begin_nested():
                    db.query(StorePair).filter(
                        StorePair.entity_a.in_(ids) | StorePair.entity_b.in_(ids)
                    ).delete(synchronize_session=False)
            except Exception:  # noqa: BLE001
                pass
            db.delete(e)
            out["stores"] += 1
    db.commit()
    return out


def delete_file(imp: ImportFile, db) -> None:
    """删除文件及其派生数据（PG 外键全量清理，顺序：子表→raw→引用置空→import）。

    1) 该文件的正式表行（raw_record_id/import_id）；2) 确认任务/人工判定；
    3) raw_records；4) persons/store_entities/recon_tasks 对它的外键引用置空；
    5) import 本身。删后该月绩效/工资建议用「月度重算」刷新。
    """
    _ = imp.id  # 旧 run 引用检查已下线
    from app.models import AppealRecord, FormalRecord, Person, StoreEntity
    from sqlalchemy import or_
    # 删除前记下"这个文件牵涉的月份与店铺"，删完据此清理派生数据
    _months = [m for (m,) in db.query(FormalRecord.japan_date).filter(
        FormalRecord.import_id == imp.id).distinct().all() if m]
    _sids = [s for (s,) in db.query(RawRecord.store_id_raw).filter(
        RawRecord.import_id == imp.id).distinct().all() if s]
    # 该文件 raw 的 id 集合（子表引用 raw 的外键）
    raw_ids = [x[0] for x in db.query(RawRecord.id).filter(
        RawRecord.import_id == imp.id).all()]
    if raw_ids:
        db.query(AppealRecord).filter(
            AppealRecord.raw_record_id.in_(raw_ids)).delete(
                synchronize_session=False)
        db.query(FormalRecord).filter(
            or_(FormalRecord.import_id == imp.id,
                FormalRecord.raw_record_id.in_(raw_ids))).delete(
                    synchronize_session=False)
    # 历史遗留表（模型已下线，但线上仍有 FK）：存在则清理；缺表只回滚该步(savepoint)
    from sqlalchemy import text as _text
    for _t in ("confirm_tasks", "manual_decisions"):
        try:
            with db.begin_nested():
                db.execute(_text(f"DELETE FROM {_t} WHERE import_id = :i"),
                           {"i": imp.id})
        except Exception:  # noqa: BLE001
            pass
    db.query(RawRecord).filter(
        RawRecord.import_id == imp.id).delete(synchronize_session=False)
    # 外键引用置空（保留实体/人员/对账任务本身）
    db.query(Person).filter(
        Person.first_seen_import_id == imp.id).update(
            {"first_seen_import_id": None}, synchronize_session=False)
    db.query(StoreEntity).filter(
        StoreEntity.first_seen_import_id == imp.id).update(
            {"first_seen_import_id": None}, synchronize_session=False)
    from app.models import ReconTask
    db.query(ReconTask).filter(
        ReconTask.source_import_id == imp.id).update(
            {"source_import_id": None}, synchronize_session=False)
    db.delete(imp)
    db.commit()
    # **清理派生数据**：该文件牵涉的月份若已无正式记录，月绩效/日统计/应发工资/看板/
    # 员工分析都要一并清掉；该文件带来的店铺若已无正式记录也删掉（否则留下幽灵月份/店铺）
    try:
        purge_orphans(db, months=_months, store_ids=_sids)
    except Exception:  # noqa: BLE001  best-effort：清理失败不影响删除本身
        db.rollback()
    # 物理 blob：有同名 sha 其它 import 时保留；简化：删除失败不阻断
    try:
        os.remove(imp.stored_path)
    except OSError:
        pass
