# -*- coding: utf-8 -*-
"""巡店文件 路由（页面清单 §3）：列表/上传/月份标记/删除。"""
from typing import List, Optional

from fastapi import (APIRouter, Depends, File, Form, HTTPException, Request,
                     UploadFile)
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import ImportFile, RawRecord, User
from app.routers.auth_r import csrf_ok, require_login
from app.services import importer

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MONTH_CHOICES = ["2026-04", "2026-05", "2026-06", "2026-07", "2026-08", "2026-09"]


def _denied():
    return RedirectResponse("/login", status_code=302)


@router.get("/files", response_class=HTMLResponse)
def files_page(request: Request, user: Optional[User] = Depends(require_login),
               db: Session = Depends(get_db), msg: str = ""):
    if user is None or user.role != "admin":
        return _denied()
    from sqlalchemy import func as _f
    from app.models import AppealRecord, FormalRecord, RawRecord
    files = db.query(ImportFile).order_by(ImportFile.id.desc()).all()
    # V3 每文件状态
    v3 = {}
    for f in files:
        if f.status != "parsed":
            continue
        jq = (db.query(RawRecord.clean_status, _f.count())
              .filter(RawRecord.import_id == f.id).group_by(
                  RawRecord.clean_status).all())
        j = {"valid": 0, "master_late": 0, "from_sub": 0,
             "cross_file_dup": 0, "no_ref": 0}
        for st, c in jq:
            if st in j:
                j[st] = c
            elif st == "visible_blank":
                pass
        pend = db.query(_f.count()).select_from(AppealRecord).filter(
            AppealRecord.import_id == f.id,
            AppealRecord.status == "pending").scalar() or 0
        formal = db.query(_f.count()).select_from(FormalRecord).filter(
            FormalRecord.import_id == f.id).scalar() or 0
        v3[f.id] = {"judge": j, "pend_appeal": pend, "formal": formal}
    return templates.TemplateResponse("files.html", {
        "request": request, "current_user": user, "files": files,
        "msg": msg, "v3": v3})


@router.get("/files/preview", response_class=HTMLResponse)
@router.post("/files/upload", response_class=HTMLResponse)
async def upload_files(request: Request,
                       files: List[UploadFile] = File(...),
                       csrf_token: str = Form(...),
                       user: Optional[User] = Depends(require_login),
                       db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    msgs = []
    parsed_ids = []
    for up in files:
        content = await up.read()
        try:
            imp = importer.upload_and_store(up.filename or "unknown.xlsx",
                                            content, user.id, db)
            importer.parse_file(imp, db)
            if imp.status == "failed":
                msgs.append(f"{imp.file_name}: 解析失败 "
                            f"({'；'.join(imp.errors[:3])})")
            else:
                msgs.append(f"{imp.file_name}: 导入 {imp.parsed_rows} 行"
                            f"（格式 {imp.format}）")
                parsed_ids.append(imp.id)
        except importer.DuplicateUpload as e:
            msgs.append(f"{up.filename}: {e}")
        except importer.UploadError as e:
            msgs.append(f"{up.filename}: {e}")
    # V3 流程：店铺主从档/判定/员工建档
    try:
        from app.services import flow as _v3
        for imp_id in parsed_ids:
            r = _v3.process_import(db, imp_id)
            j = r["judge"]
            msgs.append(
                f"文件#{imp_id} 判定：有效 {j['valid']} 条（自动入绩效）；过滤 "
                f"{j['master_late']+j['from_sub']+j['cross_file_dup']} 条"
                f"（可申诉 {j['master_late']}/从档 {j['from_sub']}/"
                f"跨文件同日 {j['cross_file_dup']}/空编号 {j['no_ref']}）")
    except Exception as e:  # noqa: BLE001
        msgs.append(f"V3 流程失败：{type(e).__name__}: {e}")
    from urllib.parse import quote
    return RedirectResponse(f"/files?msg={quote(' | '.join(msgs))}", status_code=303)


@router.get("/files/{fid}/report", response_class=HTMLResponse)
def file_report(fid: int, request: Request,
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db), bucket: str = "",
                q: str = "", page: int = 1, msg: str = ""):
    """按导入文件汇总 raw 判定结果（V3 口径：有效/同店跨日/从档/重复/空白 + 申诉）。"""
    if user is None or user.role != "admin":
        return _denied()
    from app.services import report
    r = report.import_report(db, fid, bucket=bucket, q=q, page=page)
    if r is None:
        return RedirectResponse("/files?msg=文件不存在", status_code=303)
    return templates.TemplateResponse("file_report.html", {
        "request": request, "current_user": user, **r, "msg": msg,
        "bucket_opts": report.BUCKET_OPTS})


@router.post("/files/{fid}/records/adjust")
@router.post("/files/{fid}/settle")
@router.post("/files/{fid}/rerun")
@router.post("/files/{fid}/delete")
def delete_file(fid: int, request: Request, csrf_token: str = Form(...),
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    imp = db.get(ImportFile, fid)
    if imp is None:
        raise HTTPException(404, "文件不存在")
    try:
        importer.delete_file(imp, db)
    except importer.UploadError as e:
        raise HTTPException(409, str(e))
    return RedirectResponse("/files", status_code=303)
