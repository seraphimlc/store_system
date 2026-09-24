# -*- coding: utf-8 -*-
"""店铺主档工作台 路由（A组必同按组合并 / B组疑似+AI / 实体检索与拆分）。"""
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from app.templating import get_templates
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import StoreEntity, StorePair, User
from app.routers.auth_r import csrf_ok, require_login
from app.services import ai_batch, store_master

router = APIRouter()
templates = get_templates()


def _denied():
    return RedirectResponse("/login", status_code=302)


def _counts(db):
    from sqlalchemy import func
    E = StoreEntity
    Ma = db.query(E).filter(E.id == E.master_id).with_entities(E.id).subquery()
    Mb = db.query(E).filter(E.id == E.master_id).with_entities(E.id).subquery()
    def pend(kind):
        return db.query(func.count(StorePair.id)).join(
            Ma, Ma.c.id == StorePair.entity_a).join(
            Mb, Mb.c.id == StorePair.entity_b).filter(
            StorePair.kind == kind, StorePair.status == "pending").scalar() or 0
    return {
        "entities": db.query(StoreEntity).count(),
        "masters": db.query(StoreEntity).filter(
            StoreEntity.id == StoreEntity.master_id).count(),
        "exact": pend("exact"),
        "fuzzy": pend("fuzzy"),
    }


@router.get("/stores", response_class=HTMLResponse)
def stores_page(request: Request, user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db), view: str = "list",
                kind: str = "exact", q: str = "", page: int = 1,
                msg: str = ""):
    if user is None or user.role != "admin":
        return _denied()
    counts = _counts(db)
    tab = "fuzzy" if request.query_params.get("tab") == "fuzzy" else "list"
    if tab == "fuzzy":
        pairs = store_master._pending_pairs(db, "fuzzy")
        return templates.TemplateResponse("stores.html", {
            "request": request, "current_user": user, "counts": counts,
            "tab": "fuzzy", "pairs": pairs, "q": "", "msg": msg})
    cat = store_master.store_catalog(db, q=q, page=page)
    return templates.TemplateResponse("stores.html", {
        "request": request, "current_user": user, "counts": counts,
        "tab": "list", "cat": cat, "q": q, "pairs": [], "msg": msg})


@router.post("/stores/groups/merge")
def merge_group(request: Request, name_norm: str = Form(...),
                keep: int = Form(...), note: str = Form(""),
                csrf_token: str = Form(...),
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    n = store_master.merge_name_group(db, name_norm, keep, user.id, note=note)
    return RedirectResponse(f"/stores?kind=exact&msg=已并入 {n} 个实体", status_code=303)


@router.post("/stores/groups/skip")
def skip_group(request: Request, name_norm: str = Form(...),
               csrf_token: str = Form(...),
               user: Optional[User] = Depends(require_login),
               db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    store_master.skip_name_group(db, name_norm, user.id)
    return RedirectResponse("/stores?kind=exact&msg=已整组标记为不同店", status_code=303)


@router.post("/stores/groups/apply-all")
def apply_all(request: Request, csrf_token: str = Form(...),
              user: Optional[User] = Depends(require_login),
              db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    res = store_master.apply_all_recommended(db, user.id)
    msg = (f"整批完成：处理 {res['groups']} 组、并入 {res['merged_entities']} 个实体；"
           f"跨城市留出 {res['left_groups']} 组待人工")
    return RedirectResponse(f"/stores?kind=exact&msg={msg}", status_code=303)


@router.post("/stores/ai-run")
def ai_run(request: Request, bt: BackgroundTasks, csrf_token: str = Form(...),
           user: Optional[User] = Depends(require_login),
           db: Session = Depends(get_db)):
    from app.models import AiRun
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    if not ai_batch.configured():
        return RedirectResponse("/stores?kind=fuzzy&msg=未配置 AI_API_KEY（AI 未启用）",
                                status_code=303)
    if db.query(AiRun).filter(AiRun.status == "running").first() is not None:
        return RedirectResponse("/stores?kind=fuzzy&msg=已有 AI 批处理在运行，请稍候刷新",
                                status_code=303)
    n = db.query(StorePair).filter(StorePair.kind == "fuzzy",
                                   StorePair.status == "pending").count()
    if n == 0:
        return RedirectResponse("/stores?kind=fuzzy&msg=没有待处理候选", status_code=303)
    run = AiRun(total_pairs=n, created_by=user.id)
    db.add(run)
    db.commit()
    bt.add_task(ai_batch.run_batch_task, run.id)
    return RedirectResponse("/stores?kind=fuzzy&msg=AI 批处理已启动（几分钟），稍候刷新看结果",
                            status_code=303)


@router.get("/stores/entities", response_class=HTMLResponse)
def entities_page(request: Request, user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db), q: str = ""):
    if user is None or user.role != "admin":
        return _denied()
    query = db.query(StoreEntity)
    if q:
        like = f"%{q}%"
        query = query.filter(StoreEntity.store_id_raw.like(like)
                             | StoreEntity.name_local.like(like)
                             | StoreEntity.name_norm.like(like))
    ents = query.order_by(StoreEntity.id).limit(500).all()
    by_id = {e.id: e for e in db.query(StoreEntity).all()}
    return templates.TemplateResponse("store_entities.html", {
        "request": request, "current_user": user, "q": q,
        "counts": _counts(db), "ents": ents, "by_id": by_id})


@router.post("/stores/pairs/{pid}/merge")
def merge_pair(pid: int, request: Request, keep: int = Form(...),
               note: str = Form(""), csrf_token: str = Form(...),
               user: Optional[User] = Depends(require_login),
               db: Session = Depends(get_db), kind: str = "exact"):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    store_master.merge_pair(db, pid, keep, user.id,
                            basis="program_exact" if kind == "exact" else "manual",
                            note=note)
    return RedirectResponse(f"/stores?kind={kind}&msg=已并入，该对不再显示", status_code=303)


@router.post("/stores/pairs/{pid}/skip")
def skip_pair(pid: int, request: Request, csrf_token: str = Form(...),
              user: Optional[User] = Depends(require_login),
              db: Session = Depends(get_db), kind: str = "exact"):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    store_master.skip_pair(db, pid, user.id)
    return RedirectResponse(f"/stores?kind={kind}&msg=已跳过（视为不同店）", status_code=303)


@router.post("/stores/entities/{eid}/split")
def split_entity(eid: int, request: Request, csrf_token: str = Form(...),
                 user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db)):
    if user is None or user.role != "admin":
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    store_master.split_entity(db, eid, user.id)
    return RedirectResponse(f"/stores/entities?q={eid}", status_code=303)
