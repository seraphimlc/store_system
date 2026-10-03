# -*- coding: utf-8 -*-
"""团队 + 车站任务路由。

- 管理端：`/teams`（团队定义）、`/stations`（车站→任务）、`/tasks`（任务总表）
- 员工端：`/my/tasks`（**队长：任务分派 + 每日进展提交；队员：只读我的任务**）

规格：`docs/specs-team-management.md`、`docs/specs-station-tasks.md`。
"""
from datetime import date, datetime
from typing import List, Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from sqlalchemy.orm import Session

from app.db import get_db
from app.forms import require_form_token as _dep_form_token
from app.i18n import CURRENT_LANG
from app.models import User
from app.routers.auth_r import csrf_ok, require_login
from app.templating import get_templates

router = APIRouter(dependencies=[Depends(_dep_form_token)])
templates = get_templates()


def _denied():
    return RedirectResponse("/login", status_code=302)


def _admin_guard(user):
    """管理端守卫：非 admin → 队长按角色回员工端任务页，员工回员工首页。"""
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if user.role != "admin":
        if user.role == "leader":
            return RedirectResponse("/my/tasks", status_code=302)
        return RedirectResponse("/my/perf", status_code=302)
    return None


def _staff_guard(user):
    """员工端任务页守卫：队员(staff)与队长(leader)都能进。"""
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if user.role not in ("staff", "leader"):
        return RedirectResponse("/dashboard", status_code=302)
    return None


def _q(s: str) -> str:
    return quote(s or "")


def _parse_date(s: str) -> Optional[date]:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return datetime.strptime(s, "%Y-%m-%d").date()
    except ValueError:
        return None


def _ids(values: Optional[List[str]]) -> List[int]:
    out = []
    for v in values or []:
        try:
            out.append(int(str(v).strip()))
        except (TypeError, ValueError):
            continue
    return out


# ============================ 团队定义（管理端） ============================

@router.get("/teams", response_class=HTMLResponse)
def teams_page(request: Request, user: Optional[User] = Depends(require_login),
               db: Session = Depends(get_db), kw: str = "", status: str = "",
               msg: str = "", err: str = ""):
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_teams
    return templates.TemplateResponse("bd_teams.html", {
        "request": request, "current_user": user,
        "rows": bd_teams.list_teams(db, kw, status),
        "sum": bd_teams.summary(db),
        "kw": kw, "status": status, "msg": msg, "err": err,
    })


@router.get("/teams/{team_id}", response_class=HTMLResponse)
def team_detail(request: Request, team_id: int,
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db), msg: str = "", err: str = ""):
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_teams
    from app.models import BdTeam
    team = db.get(BdTeam, team_id)
    if team is None:
        return RedirectResponse("/teams?err=%s" % _q("团队不存在"),
                                status_code=303)
    members = bd_teams.team_members(db, team_id)
    active_codes = {m["person_code"] for m in members}
    return templates.TemplateResponse("bd_team_detail.html", {
        "request": request, "current_user": user, "team": team,
        "members": members, "active_codes": active_codes,
        "people": bd_teams.person_options(db),
        "msg": msg, "err": err,
    })


@router.post("/teams/create")
def team_create(request: Request, name: str = Form(""), code: str = Form(""),
                note: str = Form(""), csrf_token: str = Form(""),
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db)):
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_teams
    try:
        t = bd_teams.create_team(db, name, code, note, by=user.username)
        db.commit()
        return RedirectResponse("/teams/%d?msg=%s" % (t.id, _q("已建队")),
                                status_code=303)
    except bd_teams.TeamError as e:
        db.rollback()
        return RedirectResponse("/teams?err=%s" % _q(str(e)), status_code=303)


@router.post("/teams/{team_id}/edit")
def team_edit(request: Request, team_id: int, name: str = Form(""),
              code: str = Form(""), note: str = Form(""),
              status: str = Form(""), csrf_token: str = Form(""),
              user: Optional[User] = Depends(require_login),
              db: Session = Depends(get_db)):
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_teams
    try:
        bd_teams.update_team(db, team_id, name=name, code=code, note=note,
                             status=status)
        db.commit()
        return RedirectResponse("/teams/%d?msg=%s" % (team_id, _q("已保存")),
                                status_code=303)
    except bd_teams.TeamError as e:
        db.rollback()
        return RedirectResponse("/teams/%d?err=%s" % (team_id, _q(str(e))),
                                status_code=303)


@router.post("/teams/{team_id}/members")
def team_members_save(request: Request, team_id: int,
                      person: Optional[List[str]] = Form(None),
                      leader: Optional[List[str]] = Form(None),
                      csrf_token: str = Form(""),
                      user: Optional[User] = Depends(require_login),
                      db: Session = Depends(get_db)):
    """覆盖式保存成员：勾选的人进队，没勾的移出；`leader` 里的人是队长。"""
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_teams
    leaders = set(leader or [])
    entries = [(c, "leader" if c in leaders else "member")
               for c in (person or [])]
    try:
        r = bd_teams.set_members(db, team_id, entries)
        db.commit()
        msg = "已保存：新增 %d / 移出 %d / 改角色 %d" % (
            r["added"], r["removed"], r["changed"])
        return RedirectResponse("/teams/%d?msg=%s" % (team_id, _q(msg)),
                                status_code=303)
    except bd_teams.TeamError as e:
        db.rollback()
        return RedirectResponse("/teams/%d?err=%s" % (team_id, _q(str(e))),
                                status_code=303)


# ============================ 车站（管理端） ============================

@router.get("/stations", response_class=HTMLResponse)
def stations_page(request: Request,
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db), kw: str = "",
                  status: str = "", no_task: str = "",
                  msg: str = "", err: str = ""):
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_teams, bd_tasks
    rows = bd_tasks.list_stations(db, kw, status, only_without_task=bool(no_task))
    return templates.TemplateResponse("bd_stations.html", {
        "request": request, "current_user": user, "rows": rows,
        "kw": kw, "status": status, "no_task": no_task,
        "teams": bd_teams.team_options(db),
        "sum": bd_tasks.board_summary(db),
        "msg": msg, "err": err,
    })


@router.post("/stations/create")
def station_create(request: Request, name: str = Form(""),
                   line: str = Form(""), note: str = Form(""),
                   make_task: str = Form(""),
                   csrf_token: str = Form(""),
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db)):
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    try:
        st = bd_tasks.create_station(db, name, line, note, by=user.username)
        extra = ""
        if make_task:
            r = bd_tasks.create_tasks(db, [st.id], by=user.username)
            extra = "，已建任务 %d" % r["created"]
        db.commit()
        return RedirectResponse("/stations?msg=%s" % _q("已新增车站" + extra),
                                status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/stations?err=%s" % _q(str(e)),
                                status_code=303)


@router.post("/stations/{station_id}/edit")
def station_edit(request: Request, station_id: int, name: str = Form(""),
                 line: str = Form(""), note: str = Form(""),
                 status: str = Form(""), csrf_token: str = Form(""),
                 user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db)):
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    try:
        bd_tasks.update_station(db, station_id, name=name, line=line, note=note,
                                status=status)
        db.commit()
        return RedirectResponse("/stations?msg=%s" % _q("已保存"),
                                status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/stations?err=%s" % _q(str(e)),
                                status_code=303)


@router.post("/stations/tasks")
def stations_make_tasks(request: Request,
                        station_id: Optional[List[str]] = Form(None),
                        all_without: str = Form(""),
                        csrf_token: str = Form(""),
                        user: Optional[User] = Depends(require_login),
                        db: Session = Depends(get_db)):
    """把车站定义成任务（批量；已有任务的跳过）。"""
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    ids = _ids(station_id)
    if all_without:
        ids = [r["station"].id for r in bd_tasks.list_stations(
            db, only_without_task=True)]
    if not ids:
        return RedirectResponse("/stations?err=%s" % _q("请先勾选车站"),
                                status_code=303)
    r = bd_tasks.create_tasks(db, ids, by=user.username)
    db.commit()
    return RedirectResponse(
        "/stations?msg=%s" % _q("建任务 %d 个，跳过 %d 个（已有任务）"
                                % (r["created"], r["skipped"])),
        status_code=303)


@router.post("/stations/team")
def stations_set_team(request: Request,
                      station_id: Optional[List[str]] = Form(None),
                      team_id: str = Form(""), assign_date: str = Form(""),
                      csrf_token: str = Form(""),
                      user: Optional[User] = Depends(require_login),
                      db: Session = Depends(get_db)):
    """**批量派队**：勾选车站 → 派给一个团队（顺带写分配日期）。"""
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    ids = _ids(station_id)
    if not ids:
        return RedirectResponse("/stations?err=%s" % _q("请先勾选车站"),
                                status_code=303)
    try:
        tid = int(team_id) if str(team_id).strip() else None
    except ValueError:
        tid = None
    # 车站 → 任务（缺则建），再派队
    from app.models import BdTask
    r = bd_tasks.create_tasks(db, ids, by=user.username)
    task_ids = [t.id for t in db.query(BdTask)
                .filter(BdTask.station_id.in_(ids)).all()]
    res = bd_tasks.set_task_team(db, task_ids, tid,
                                 assign_date=_parse_date(assign_date) or date.today())
    db.commit()
    return RedirectResponse(
        "/stations?msg=%s" % _q("已派 %d 个任务（新建 %d，清空担当 %d）"
                                % (res["updated"], r["created"], res["cleared"])),
        status_code=303)


# ============================ 任务总表（管理端） ============================

@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, user: Optional[User] = Depends(require_login),
               db: Session = Depends(get_db), team: str = "", state: str = "",
               date_from: str = "", date_to: str = "", kw: str = "",
               page: int = 1, msg: str = "", err: str = ""):
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_teams, bd_tasks
    try:
        team_id = int(team) if str(team).strip() else None
    except ValueError:
        team_id = None
    limit = 200
    page = max(1, int(page or 1))
    d = bd_tasks.task_board(db, team_id=team_id, state=state,
                            date_from=_parse_date(date_from),
                            date_to=_parse_date(date_to), kw=kw,
                            limit=limit, offset=(page - 1) * limit)
    return templates.TemplateResponse("bd_tasks.html", {
        "request": request, "current_user": user,
        "rows": d["rows"], "total": d["total"], "page": page, "limit": limit,
        "team_id": team_id, "teams": bd_teams.team_options(db),
        "state": state, "date_from": date_from, "date_to": date_to, "kw": kw,
        "sum": bd_tasks.board_summary(db),
        "labels": bd_tasks.state_labels(CURRENT_LANG.get()),
        "msg": msg, "err": err,
    })


@router.post("/tasks/{task_id}/progress")
def task_progress_admin(request: Request, task_id: int, pct: str = Form("0"),
                        note: str = Form(""), csrf_token: str = Form(""),
                        user: Optional[User] = Depends(require_login),
                        db: Session = Depends(get_db)):
    """管理员修正某任务的今日进展。"""
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    try:
        r = bd_tasks.save_progress(db, task_id, pct, note, by=user.username)
        db.commit()
        return RedirectResponse(
            "/tasks?msg=%s" % _q("已修正：%d%%" % r["pct"]), status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/tasks?err=%s" % _q(str(e)), status_code=303)


@router.get("/tasks/export")
def tasks_export(user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db), team: str = "",
                 state: str = "", date_from: str = "", date_to: str = ""):
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_tasks
    try:
        team_id = int(team) if str(team).strip() else None
    except ValueError:
        team_id = None
    buf = bd_tasks.tasks_xlsx(db, team_id=team_id, state=state,
                              date_from=_parse_date(date_from),
                              date_to=_parse_date(date_to),
                              lang=CURRENT_LANG.get())
    fname = "station_tasks_%s.xlsx" % datetime.now().strftime("%Y%m%d")
    nice = "车站任务_%s.xlsx" % datetime.now().strftime("%Y%m%d")
    # 响应头只能 latin-1：ASCII 名给 filename=，中文名走 RFC 5987 的 filename*
    cd = ("attachment; filename=%s; filename*=UTF-8''%s"
          % (fname, quote(nice)))
    return StreamingResponse(
        buf, media_type=(
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        headers={"Content-Disposition": cd})


# ============================ 员工端：我的任务 / 队长端 ============================

@router.get("/my/tasks", response_class=HTMLResponse)
def my_tasks_page(request: Request,
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db), tab: str = "unassigned",
                  kw: str = "", msg: str = "", err: str = ""):
    """员工端任务页。

    - **队长**：本队任务按 tab（未分配/进行中/已完成）分组，可分派 + 提交每日进展
    - **队员**：只读"分给我的车站"
    """
    g = _staff_guard(user)
    if g:
        return g
    from app.services import bd_teams, bd_tasks
    is_leader = user.role == "leader"
    teams = bd_teams.leader_teams(db, user.person_code) if is_leader else []
    team_ids = [t.id for t in teams]
    if is_leader:
        all_rows = bd_tasks.team_tasks(db, team_ids, tab="", kw=kw)
        counts = {k: sum(1 for r in all_rows if r["state"] == k)
                  for k in bd_tasks.STATES}
        rows = [r for r in all_rows if r["state"] == tab] \
            if tab in bd_tasks.TABS else all_rows
        members = {}
        for t in teams:
            members[t.id] = [m for m in bd_teams.team_members(db, t.id)
                             if m["has_account"]]
    else:
        rows = bd_tasks.member_tasks(db, user.person_code)
        counts = {k: sum(1 for r in rows if r["state"] == k)
                  for k in bd_tasks.STATES}
        members = {}
    return templates.TemplateResponse("my_tasks.html", {
        "request": request, "current_user": user,
        "is_leader": is_leader, "teams": teams, "rows": rows,
        "counts": counts, "tab": tab, "kw": kw, "members": members,
        "labels": bd_tasks.state_labels(CURRENT_LANG.get()),
        "max_assign": bd_tasks.MAX_ASSIGN,
        "today": date.today(), "msg": msg, "err": err,
    })


@router.post("/my/tasks/assign")
def my_tasks_assign(request: Request, task_id: int = Form(0),
                    person: Optional[List[str]] = Form(None),
                    csrf_token: str = Form(""),
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """分派/改派担当（**队长或管理员**；≤2 人，必须是本队现役成员）。"""
    if user is None:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    task = db.get(bd_tasks.BdTask, task_id)
    back = "/my/tasks?tab=%s"
    if task is None:
        return RedirectResponse(back % "doing" + "&err=" + _q("任务不存在"),
                                status_code=303)
    if not bd_tasks.can_submit(db, user, task):
        return RedirectResponse(back % "doing" + "&err="
                                + _q("只有该队队长或管理员能分派"),
                                status_code=303)
    try:
        r = bd_tasks.assign_members(db, task_id, person or (), by=user.username)
        db.commit()
        return RedirectResponse(
            "/my/tasks?tab=%s&msg=%s" % (r["state"],
                                         _q("已分派 %d 人" % r["n"])),
            status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/my/tasks?err=%s" % _q(str(e)),
                                status_code=303)


@router.post("/my/tasks/progress")
def my_tasks_progress(request: Request, task_id: int = Form(0),
                      pct: str = Form("0"), note: str = Form(""),
                      csrf_token: str = Form(""),
                      user: Optional[User] = Depends(require_login),
                      db: Session = Depends(get_db)):
    """**每日进展提交**（队长或管理员）：写/改今天一条，刷新任务进度与状态。"""
    if user is None:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    task = db.get(bd_tasks.BdTask, task_id)
    if task is None:
        return RedirectResponse("/my/tasks?err=" + _q("任务不存在"),
                                status_code=303)
    if not bd_tasks.can_submit(db, user, task):
        return RedirectResponse("/my/tasks?err="
                                + _q("只有该队队长或管理员能提交进展"),
                                status_code=303)
    try:
        r = bd_tasks.save_progress(db, task_id, pct, note, by=user.username)
        db.commit()
        return RedirectResponse(
            "/my/tasks?tab=%s&msg=%s" % (r["state"], _q("已提交 %d%%" % r["pct"])),
            status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/my/tasks?err=%s" % _q(str(e)),
                                status_code=303)
