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


# ---------- 解析布局：查看/纠正/重新解析 ----------

def _vm_text(vm: Optional[dict]) -> str:
    """value_map dict → "KEY=值,KEY2=值2" 文本（KEY 空字符串显示为 ~）。"""
    if not vm:
        return ""
    return ", ".join(f"{('~' if k == '' else k)}={v}" for k, v in vm.items())


def _pr_text(pr: Optional[list]) -> str:
    """point_rules → 行式文本（每行：可见值|列表 & 投放值|列表 = 点数；~=空、*=任意）。"""
    if not pr:
        return ""
    lines = []
    for r in pr:
        if not isinstance(r, dict):
            continue
        def _fmt(k):
            v = r.get(k)
            if v is None:
                return "*"
            if isinstance(v, list):
                return "|".join(("~" if x == "" else str(x)) for x in v)
            return str(v)
        lines.append(f"{_fmt('visible')}&{_fmt('deploy')}={r.get('points', '')}")
    return "\n".join(lines)


def _parse_pr(text: str) -> Optional[list]:
    """行式文本 → point_rules；空输入 → None（使用默认口径）。"""
    text = (text or "").strip()
    if not text:
        return None
    out = []
    for line in text.splitlines():
        line = line.strip()
        if not line or "=" not in line:
            continue
        left, pts = line.rsplit("=", 1)
        pts = pts.strip()
        if not pts.isdigit():
            continue
        left = left.strip()
        vis_part = left.split("&")[0] if "&" in left else left
        dep_part = left.split("&")[1] if "&" in left else None

        def _vals(part):
            if part is None:
                return None
            vs = [x.strip() for x in part.split("|") if x.strip() != ""]
            vs = ["" if x == "~" else x for x in vs]
            if "*" in vs:
                return None
            return vs or None
        out.append({"visible": _vals(vis_part), "deploy": _vals(dep_part),
                    "points": int(pts)})
    return out or None


def _parse_vm(text: str) -> Optional[dict]:
    """"KEY=值, ~=值" → {KEY: 值/值 str}；空输入 → None（使用默认）。"""
    text = (text or "").strip()
    if not text:
        return None
    out = {}
    for part in text.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        k, v = part.split("=", 1)
        k, v = k.strip(), v.strip()
        if k == "~":
            k = ""
        out[k] = v
    return out or None


@router.get("/files/{fid}/layout", response_class=HTMLResponse)
def file_layout(fid: int, request: Request,
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db)):
    """解析布局查看/纠正页（人工可改列号与取值语义后重新解析）。"""
    if user is None or user.role != "admin":
        return _denied()
    imp = db.get(ImportFile, fid)
    if imp is None:
        return RedirectResponse("/files?msg=文件不存在", status_code=303)
    layout = imp.layout or {}
    fields = [
        ("store_id", "店铺 ID 列"), ("store_name", "店铺名列"),
        ("modified_time", "巡店时间列"), ("submitter", "提交人列"),
        ("visible", "有效性列"), ("deploy", "投放列"),
        ("record_id", "记录编号列(可选)"),
    ]
    vm = layout.get("value_map") or {}
    return templates.TemplateResponse("file_layout.html", {
        "request": request, "current_user": user, "imp": imp,
        "layout": layout, "fields": fields,
        "required": ("store_id", "store_name", "modified_time", "submitter",
                     "visible", "deploy"),
        "visible_map_text": _vm_text(vm.get("visible")),
        "deploy_map_text": _vm_text(vm.get("deploy")),
        "point_rules_text": _pr_text(layout.get("point_rules")),
        "msg": "", "err": "",
    })


@router.post("/files/{fid}/reparse", response_class=HTMLResponse)
def file_reparse(fid: int, request: Request,
                 csrf_token: str = Form(...),
                 header_row: int = Form(1),
                 store_id: int = Form(0), store_name: int = Form(0),
                 modified_time: int = Form(0), submitter: int = Form(0),
                 visible: int = Form(0), deploy: int = Form(0),
                 record_id: int = Form(0),
                 visible_map: str = Form(""), deploy_map: str = Form(""),
                 point_rules_text: str = Form(""),
                 user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db)):
    """按人工纠正的布局重新解析：删旧 raw → parse_file(layout) → 判定。"""
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    imp = db.get(ImportFile, fid)
    if imp is None:
        return RedirectResponse("/files?msg=文件不存在", status_code=303)
    cols = {"store_id": store_id, "store_name": store_name,
            "modified_time": modified_time, "submitter": submitter,
            "visible": visible, "deploy": deploy, "record_id": record_id}
    cols = {k: v for k, v in cols.items() if v and v > 0}
    if not all(cols.get(k) for k in
               ("store_id", "store_name", "modified_time", "submitter",
                "visible", "deploy")):
        return templates.TemplateResponse("file_layout.html", {
            "request": request, "current_user": user, "imp": imp,
            "layout": imp.layout or {}, "fields": [
                ("store_id", "店铺 ID 列"), ("store_name", "店铺名列"),
                ("modified_time", "巡店时间列"), ("submitter", "提交人列"),
                ("visible", "有效性列"), ("deploy", "投放列"),
                ("record_id", "记录编号列(可选)")],
            "required": ("store_id", "store_name", "modified_time",
                         "submitter", "visible", "deploy"),
            "visible_map_text": visible_map, "deploy_map_text": deploy_map,
            "point_rules_text": point_rules_text,
            "msg": "", "err": "必需列（店ID/店名/时间/提交人/有效性/投放）都要填列号",
        }, status_code=400)
    value_map = {}
    vmv = _parse_vm(visible_map)
    if vmv:
        value_map["visible"] = vmv
    vmd = _parse_vm(deploy_map)
    if vmd:
        try:
            value_map["deploy"] = {k: int(v) for k, v in vmd.items()}
        except (TypeError, ValueError):
            return HTMLResponse("投放取值点数需是整数（如 YES=2, NO=1, ~=1）",
                                status_code=400)
    point_rules = _parse_pr(point_rules_text)
    layout = {"header_row": max(1, header_row), "cols": cols,
              "value_map": value_map, "source": "manual"}
    if point_rules:
        layout["point_rules"] = point_rules
    try:
        # 清旧 raw（判定明细/申诉随之重建）
        db.query(RawRecord).filter(RawRecord.import_id == imp.id).delete()
        imp.parsed_sheets = []
        imp.total_rows = 0
        imp.parsed_rows = 0
        imp.status = "uploaded"
        imp.errors = []
        db.commit()
        from app.services import importer
        importer.parse_file(imp, db, layout=layout)
        if imp.status == "failed":
            return RedirectResponse(
                f"/files/{fid}/layout?msg=重新解析失败：{'；'.join(imp.errors[:3])}",
                status_code=303)
        from app.services import flow as _v3
        r = _v3.process_import(db, imp.id)
        j = r["judge"]
        from urllib.parse import quote
        return RedirectResponse(
            f"/files/{fid}/layout?msg=" + quote(
                f"已按新布局重新解析 {imp.parsed_rows} 行；判定：有效 "
                f"{j['valid']} / 可申诉 {j['master_late']} / 从档 "
                f"{j['from_sub']} / 跨文件同日 {j['cross_file_dup']}"),
            status_code=303)
    except Exception as e:  # noqa: BLE001
        db.rollback()
        return RedirectResponse(f"/files/{fid}/layout?msg=重新解析异常：{e}",
                                status_code=303)
