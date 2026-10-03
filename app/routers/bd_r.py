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
from app.i18n import CURRENT_LANG, translate as _tr
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
        "status_labels": bd_teams.STATUS_LABELS(CURRENT_LANG.get()),
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
    # 先算：这次会被移出、且名下还有**未完成任务**的人 → 提示（不自动改派）
    from app.models import BdTask, BdTaskAssign, BdTeamMember
    from sqlalchemy import func as _func
    keep = {c for c, _ in entries}
    old_active = {m.person_code for m in db.query(BdTeamMember).filter(
        BdTeamMember.team_id == team_id,
        BdTeamMember.end_date.is_(None)).all()}
    left_with_work = []
    for code in sorted(old_active - keep):
        n = (db.query(_func.count(BdTaskAssign.id))
             .join(BdTask, BdTask.id == BdTaskAssign.task_id)
             .filter(BdTaskAssign.person_code == code,
                     BdTask.state != "done").scalar() or 0)
        if n:
            left_with_work.append((code, n))
    try:
        r = bd_teams.set_members(db, team_id, entries)
        db.commit()
        msg = "已保存：新增 %d / 移出 %d / 改角色 %d" % (
            r["added"], r["removed"], r["changed"])
        if r.get("promoted"):
            msg += "；已设为队长账号 %d 人" % len(r["promoted"])
        if r.get("demoted"):
            msg += "；已退回队员账号 %d 人" % len(r["demoted"])
        if left_with_work:
            msg += "；⚠️ 移出的人里 %d 人还有未完成任务共 %d 条（不会自动改派）" % (
                len(left_with_work), sum(n for _, n in left_with_work))
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
               stale: str = "", page: int = 1, msg: str = "", err: str = ""):
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_leave, bd_teams, bd_tasks
    try:
        team_id = int(team) if str(team).strip() else None
    except ValueError:
        team_id = None
    limit = 200
    page = max(1, int(page or 1))
    d = bd_tasks.task_board(db, team_id=team_id, state=state,
                            date_from=_parse_date(date_from),
                            date_to=_parse_date(date_to), kw=kw,
                            stale_only=bool(stale),
                            limit=limit, offset=(page - 1) * limit)
    # 当日休假中的人（担当旁边标出来 —— 管理端一眼看到"活派给休假的人了"）
    leave_map = bd_leave.active_map(db)
    return templates.TemplateResponse("bd_tasks.html", {
        "request": request, "current_user": user,
        "rows": d["rows"], "total": d["total"], "page": page, "limit": limit,
        "team_id": team_id, "teams": bd_teams.team_options(db),
        "state": state, "date_from": date_from, "date_to": date_to, "kw": kw,
        "stale": stale, "stale_days": d["stale_days"],
        "leave_map": leave_map,
        "sum": bd_tasks.board_summary(db),
        "by_team": bd_tasks.team_board_summary(db),
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
                  db: Session = Depends(get_db), tab: str = "mine",
                  kw: str = "", msg: str = "", err: str = ""):
    """员工端任务页（**队长与队员同一页**，队长多出"任务管理"）。

    - **队员（staff）**：只看"我的任务"（自己担当的），**每行可上报进展**（滑动条）
      —— 用户 2026-10-03 口径：员工自己先上报，队长做调整
    - **队长（leader）**：同一页 + 多出 本队任务的 未分配/进行中/已完成 三个 tab
      （可分派担当、可调整进展）；他自己的任务照常上报
    """
    g = _staff_guard(user)
    if g:
        return g
    from app.services import bd_leave, bd_teams, bd_tasks
    TAB_MINE = "mine"
    TAB_PENDING = "pending"      # 待确认（队长专用 tab）
    is_leader = user.role == "leader"
    jst_today = bd_leave.today()
    teams = bd_teams.leader_teams(db, user.person_code) if is_leader else []
    team_ids = [t.id for t in teams]
    my_rows = bd_tasks.member_tasks(db, user.person_code)     # 我担当的（跨队）
    my_ids = {r["task"].id for r in my_rows}
    can_report_map, can_assign_map = {}, {}
    members = {}
    avail = {}
    if is_leader:
        all_rows = bd_tasks.team_tasks(db, team_ids, tab="", kw=kw)
        counts = {k: sum(1 for r in all_rows if r["state"] == k)
                  for k in bd_tasks.STATES}
        counts[TAB_MINE] = len(my_rows)
        # 「待确认」= 队员报过、队长还没处理的（用户 2026-10-03：确认 / 调整）
        counts[TAB_PENDING] = sum(1 for r in all_rows if r.get("pending_review"))
        if tab == TAB_MINE:
            rows = my_rows
        elif tab == TAB_PENDING:
            rows = [r for r in all_rows if r.get("pending_review")]
        elif tab in bd_tasks.TABS:
            rows = [r for r in all_rows if r["state"] == tab]
        else:
            rows = all_rows
        for tm in teams:      # ⚠️ 别用 `t` 做循环变量（会覆盖全局 t() 翻译函数）
            # 派工候选：有账号**且在职**（离职/停用的人执行不了，不列出来）
            members[tm.id] = [m for m in bd_teams.team_members(db, tm.id)
                              if m["has_account"] and m["can_work"]]
        # 出勤计划 / 假期模式 / 请假 → 候选旁边打标签（**只提醒不阻断**）
        all_codes = sorted({m["person_code"] for lst in members.values()
                            for m in lst})
        dates = sorted({(max(r["task"].assign_date, jst_today)
                         if r["task"].assign_date else jst_today)
                        for r in rows})
        for d in dates:
            for code, a in bd_leave.availability_map(db, all_codes, d).items():
                avail["%s|%s" % (d.isoformat(), code)] = a
    else:
        rows = my_rows
        counts = {k: sum(1 for r in rows if r["state"] == k)
                  for k in bd_tasks.STATES}
    for r in rows:
        tid = r["task"].id
        can_report_map[tid] = bd_tasks.can_report(db, user, r["task"])
        can_assign_map[tid] = bd_tasks.can_assign(db, user, r["task"])
    my_leave = bd_leave.current(db, user.person_code, jst_today)
    return templates.TemplateResponse("my_tasks.html", {
        "request": request, "current_user": user,
        "is_leader": is_leader, "teams": teams, "rows": rows,
        "counts": counts, "tab": tab, "kw": kw, "members": members,
        "can_report_map": can_report_map, "can_assign_map": can_assign_map,
        "tab_mine": TAB_MINE, "tab_pending": TAB_PENDING,
        "my_ids": my_ids, "n_mine": len(my_rows),
        "n_pending": counts.get(TAB_PENDING, 0),
        "avail": avail, "my_leave": my_leave,
        "avail_tags": {w: _tr(w, CURRENT_LANG.get()) for w in
                       ("休假", "计划休", "请假", "停用", "离职", "休")},
        "labels": bd_tasks.state_labels(CURRENT_LANG.get()),
        "max_assign": bd_tasks.MAX_ASSIGN,
        "today": jst_today, "msg": msg, "err": err,
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
    if not bd_tasks.can_assign(db, user, task):
        return RedirectResponse(back % "doing" + "&err="
                                + _q("只有该队队长或管理员能分派"),
                                status_code=303)
    try:
        r = bd_tasks.assign_members(db, task_id, person or (), by=user.username,
                                    actor_user=user)
        db.commit()
        msg = "已分派 %d 人" % r["n"]
        if r.get("warnings"):
            # 出勤计划 / 假期模式 → **提醒但不阻断**（用户 2026-10-03 口径）
            msg += "；⚠️ " + "；".join(r["warnings"])
        return RedirectResponse(
            "/my/tasks?tab=%s&msg=%s" % (r["state"], _q(msg)),
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
    """**每日进展上报**（员工报自己的 / 队长调整 / 管理员）：写或改今天一条。"""
    if user is None:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    task = db.get(bd_tasks.BdTask, task_id)
    if task is None:
        return RedirectResponse("/my/tasks?err=" + _q("任务不存在"),
                                status_code=303)
    if not bd_tasks.can_report(db, user, task):
        return RedirectResponse("/my/tasks?err="
                                + _q("只能上报自己担当的任务"),
                                status_code=303)
    try:
        r = bd_tasks.save_progress(db, task_id, pct, note, by=user.username,
                                   actor_user=user)
        db.commit()
        return RedirectResponse(
            "/my/tasks?tab=%s&msg=%s" % (r["state"], _q("已提交 %d%%" % r["pct"])),
            status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/my/tasks?err=%s" % _q(str(e)),
                                status_code=303)


# ============================ 任务详情（日志时间线）+ 变更日志 ============================

@router.get("/tasks/{task_id}", response_class=HTMLResponse)
def task_detail(request: Request, task_id: int,
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db), msg: str = "", err: str = ""):
    """单个任务：当前状态 + **每日进展历史** + **变更日志时间线**。

    可见范围：管理员 / 该任务的队长 / **本人是担当**（数据隔离）。
    """
    if user is None:
        return RedirectResponse("/login", status_code=302)
    from app.services import bd_log, bd_tasks
    task = db.get(bd_tasks.BdTask, task_id)
    if task is None:
        return RedirectResponse("/tasks?err=%s" % _q("任务不存在"),
                                status_code=303)
    if not bd_tasks.can_report(db, user, task):
        # 越权（别的队、也不是担当）→ 回各自首页，不泄露内容
        from app.services import home as _home
        return RedirectResponse(_home.landing_home(db, user), status_code=302)
    row = bd_tasks.task_rows(db, [task.id])
    row = row[0] if row else None
    progress = (db.query(bd_tasks.BdTaskProgress)
                .filter(bd_tasks.BdTaskProgress.task_id == task.id)
                .order_by(bd_tasks.BdTaskProgress.progress_date.desc())
                .limit(60).all())
    return templates.TemplateResponse("bd_task_detail.html", {
        "request": request, "current_user": user, "r": row, "task": task,
        "progress": progress, "timeline": bd_log.timeline(db, "task", task.id),
        "labels": bd_tasks.state_labels(CURRENT_LANG.get()),
        "action_labels": bd_log.ACTION_LABELS(CURRENT_LANG.get()),
        "can_adjust": bd_tasks.can_adjust(db, user, task),
        "msg": msg, "err": err,
    })


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, user: Optional[User] = Depends(require_login),
              db: Session = Depends(get_db), domain: str = "",
              page: int = 1, msg: str = "", err: str = ""):
    """**变更日志**（管理端）：任务/团队/成员/车站 的变化时间线。

    - 管理员：看全部
    - 队长（有 `log.view` 能力）：只看**本队任务**与**本队**的日志（数据隔离）
    """
    if user is None:
        return RedirectResponse("/login", status_code=302)
    from app.services import bd_log, bd_perm, bd_teams
    is_admin = user.role == "admin"
    if not (is_admin or bd_perm.can(db, user, "log.view")):
        from app.services import home as _home
        return RedirectResponse(_home.landing_home(db, user), status_code=302)
    limit = 200
    page = max(1, int(page or 1))
    rows = bd_log.recent(db, domain=domain, limit=limit,
                         offset=(page - 1) * limit)
    if not is_admin:
        # 队长：只保留自己队相关的（任务按 team 反查；团队/成员按队 id）
        my_teams = {t.id for t in bd_teams.leader_teams(db, user.person_code)}
        from app.models import BdTask
        task_team = {tid: team for tid, team in
                     db.query(BdTask.id, BdTask.team_id).all()}
        kept = []
        for r in rows:
            if r.domain == "task" and task_team.get(r.ref_id) in my_teams:
                kept.append(r)
            elif r.domain in ("team", "member") and r.ref_id in my_teams:
                kept.append(r)
        rows = kept
    return templates.TemplateResponse("bd_logs.html", {
        "request": request, "current_user": user, "rows": rows,
        "domain": domain, "page": page, "limit": limit,
        "domains": bd_log.DOMAINS,
        "domain_labels": bd_log.DOMAIN_LABELS(CURRENT_LANG.get()),
        "action_labels": bd_log.ACTION_LABELS(CURRENT_LANG.get()),
        "msg": msg, "err": err,
    })


@router.post("/my/tasks/confirm")
def my_tasks_confirm(request: Request, task_id: int = Form(0),
                     note: str = Form(""), csrf_token: str = Form(""),
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db)):
    """队长**确认**队员今天的上报（认可原值）→ 自动给队员发消息。"""
    if user is None:
        return _denied()
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    task = db.get(bd_tasks.BdTask, task_id)
    if task is None:
        return RedirectResponse("/my/tasks?err=%s" % _q("任务不存在"),
                                status_code=303)
    if not bd_tasks.can_adjust(db, user, task):
        return RedirectResponse("/my/tasks?err=%s"
                                % _q("只有该队队长或管理员能确认"),
                                status_code=303)
    try:
        r = bd_tasks.save_progress(db, task_id, task.pct, note, by=user.username,
                                   actor_user=user, confirm=True)
        db.commit()
        msg = "已确认 %s%%" % r["pct"]
        if r.get("reported_pct") is None:
            msg = "这条还没有队员上报，未确认"
        if r.get("notified"):
            msg += "；已通知队员"
        return RedirectResponse("/my/tasks?tab=%s&msg=%s"
                                % (r["state"], _q(msg)), status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/my/tasks?err=%s" % _q(str(e)),
                                status_code=303)
