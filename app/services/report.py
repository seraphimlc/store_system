# -*- coding: utf-8 -*-
"""V3 文件判定明细（/files/{id}/report）：基于 raw_records.clean_status + 申诉状态。

替代旧版 analytics.import_report（旧 CleanRecord 桶模型，V3 流程已不再写入）。
"""
from typing import Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import AppealRecord, FormalRecord, ImportFile, Person, RawRecord

PAGE = 50

STATUS_META = {
    "valid": ("有效（自动计入绩效）", "ok"),
    "master_late": ("同店跨日：该店当月已有更早记录", "warn"),
    "from_sub": ("从档归并：同店不同编号，主档保留", "warn"),
    "cross_file_dup": ("同日跨文件重复导入（同一条记录）", "err"),
    "blank": ("Visible 空白：不计有效、不判重", ""),
    "no_ref": ("无对应", ""),
}

BUCKET_OPTS = [
    ("", "全部"),
    ("valid", "有效"),
    ("master_late", "同店跨日"),
    ("from_sub", "从档"),
    ("cross_file_dup", "重复导入"),
    ("blank", "Visible 空白"),
    ("appealing", "有申诉"),
]


def import_months(db: Session, import_id: int):
    """该文件数据涉及的自然月（按 Modified 前 7 位去重，升序）。"""
    vals = db.query(RawRecord.modified_raw).filter(
        RawRecord.import_id == import_id).all()
    months = {((m or "")[:7]) for (m,) in vals if m and len(m) >= 7}
    return sorted(months)


def import_report(db: Session, import_id: int, bucket: str = "",
                  q: str = "", page: int = 1):
    """按导入文件汇总 raw 判定结果（V3 口径）。

    bucket: "" 全部 / valid / master_late / from_sub / cross_file_dup /
            blank / appealing（有申诉的行）。
    返回 dict：file/raw_total/各状态计数/rows/total/page/pages/bucket/q/months。
    """
    f = db.get(ImportFile, import_id)
    if f is None:
        return None
    qry = db.query(RawRecord).filter(RawRecord.import_id == import_id)
    if bucket == "appealing":
        qry = qry.join(AppealRecord, AppealRecord.raw_record_id == RawRecord.id)
    elif bucket == "blank":
        qry = qry.filter(RawRecord.clean_status.in_(("blank", "visible_blank")))
    elif bucket:
        qry = qry.filter(RawRecord.clean_status == bucket)
    if q:
        like = f"%{q}%"
        hit = [p.code for p in db.query(Person).filter(
            Person.display_name.like(like)).all()]
        cond = ((RawRecord.store_id_raw.like(like))
                | (RawRecord.store_name_local_raw.like(like))
                | (RawRecord.submitter_raw.like(like)))
        if hit:
            cond = cond | RawRecord.submitter_code.in_(hit)
        qry = qry.filter(cond)
    total = qry.count()
    rows_raw = (qry.order_by(RawRecord.modified_raw, RawRecord.excel_row)
                .offset((page - 1) * PAGE).limit(PAGE).all())
    names = {p.code: p.display_name for p in db.query(Person).all()}
    appeals = {ap.raw_record_id: ap for ap in db.query(AppealRecord).all()}
    formal_ids = {fr.raw_record_id for fr in db.query(FormalRecord).filter(
        FormalRecord.import_id == import_id).all()}
    out = []
    for rr in rows_raw:
        st = rr.clean_status or ""
        if st == "visible_blank":
            st = "blank"   # 旧枚举归一
        label, _pill = STATUS_META.get(st, (st, ""))
        ap = appeals.get(rr.id)
        out.append({
            "id": rr.id,
            "code": rr.submitter_code,
            "name": names.get(rr.submitter_code, rr.submitter_raw or "未识别"),
            "store_id": rr.store_id_raw, "store_name": rr.store_name_local_raw,
            "date": (rr.modified_raw or "")[:10],
            "visible": rr.visible_raw or "", "deploy": rr.deploy_raw or "",
            "status": st, "label": label,
            "excel_row": rr.excel_row, "sheet": rr.sheet_name,
            "in_formal": rr.id in formal_ids,
            "appeal": ({"status": ap.status, "reason": ap.reason or ""}
                       if ap else None),
        })
    cnt = dict(db.query(RawRecord.clean_status, func.count()).filter(
        RawRecord.import_id == import_id).group_by(
        RawRecord.clean_status).all())
    if "visible_blank" in cnt:
        cnt["blank"] = cnt.get("blank", 0) + cnt.pop("visible_blank")
    raw_total = db.query(RawRecord).filter(
        RawRecord.import_id == import_id).count()
    pages = (total + PAGE - 1) // PAGE if total else 1
    return {"file": f, "raw_total": raw_total,
            "valid": cnt.get("valid", 0),
            "master_late": cnt.get("master_late", 0),
            "from_sub": cnt.get("from_sub", 0),
            "cross_file_dup": cnt.get("cross_file_dup", 0),
            "blank": cnt.get("blank", 0),
            "no_ref": cnt.get("no_ref", 0),
            "appealing": db.query(AppealRecord).filter(
                AppealRecord.import_id == import_id,
                AppealRecord.status == "pending").count(),
            "filtered_total": sum(cnt.get(k, 0) for k in
                                  ("master_late", "from_sub",
                                   "cross_file_dup", "blank", "no_ref")),
            "rows": out, "total": total, "page": page, "pages": pages,
            "bucket": bucket, "q": q, "months": import_months(db, import_id)}
