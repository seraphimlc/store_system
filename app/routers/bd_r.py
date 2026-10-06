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
from app.i18n import CURRENT_LANG, render_msg as _m, translate as _tr
from app.models import User
from app.routers.auth_r import csrf_ok, require_login
from app.templating import get_templates

def _safe_back(back: str, default: str) -> str:
    """操作后回跳目标（只允许本站任务页，防开放重定向）。"""
    b = (back or "").strip()
    if b.startswith(("/tasks", "/my/tasks")) and "//" not in b and "\n" not in b:
        return b
    return default


def _with_msg(url: str, key: str, text: str, default: str = "/tasks") -> str:
    """把 msg=/err= 挂到回跳 URL 上（自动判断 ? 还是 &）。"""
    u = _safe_back(url, default)
    return "%s%s%s=%s" % (u, "&" if "?" in u else "?", key, _q(text))


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
        return RedirectResponse("/teams?err=%s" % _q(_m("团队不存在")),
                                status_code=303)
    members = bd_teams.team_members(db, team_id)
    active_codes = {m["person_code"] for m in members}
    # 候选 = 本队现役成员 + 自由人；**已被别队圈走的人不列**（用户 2026-10-03 口径）
    people = bd_teams.person_options(db, team_id=team_id)
    elsewhere = bd_teams.active_team_of(db, exclude_team_id=team_id)
    return templates.TemplateResponse("bd_team_detail.html", {
        "request": request, "current_user": user, "team": team,
        "members": members, "active_codes": active_codes,
        "people": people,
        "n_elsewhere": len(elsewhere),
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
        return RedirectResponse("/teams/%d?msg=%s" % (t.id, _q(_m("已建队"))),
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
        return RedirectResponse("/teams/%d?msg=%s" % (team_id, _q(_m("已保存"))),
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
    # ⚠️ 被指定为队长的人**一定也是队员**：前端点「队长」会自动勾上「进队」，
    # 但 JS 没跑（CDN 挂了/禁用 JS）时 `person` 里没有他 → 以前会**静默丢掉**，
    # 用户看到"指定了队长却没生效"（2026-10-03 用户问"如何指定队长？"）。
    # 这里兜底并进来，语义：指定他当队长 = 他当然在队里。
    codes = list(dict.fromkeys(list(person or []) + list(leaders)))
    entries = [(c, "leader" if c in leaders else "member") for c in codes]
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
                  status: str = "", line: str = "", pref: str = "",
                  operator: str = "", kind: str = "", multi: str = "",
                  sort: str = "line", page: int = 1, per: int = 0,
                  msg: str = "", err: str = ""):
    """**车站资产**页（车站归车站：只维护车站信息，任务不在这里）。

    用户 2026-10-06：
    - "车站归车站，任务归任务，车站这边只是维护车站信息"
      → 任务列/任务筛选/批量建任务·派队 全部移走（去 `/tasks` 与 `/tasks/new`）
    - "你再看看车站的功能，缺失很多，我查都查不到"
      → 关键词一个框搜全部（站名 / 駅コード / 运营商 / 线路名 / 还经过 / **都道府県名**）+
        都道府県・运营公司・线路类型・只看跨线站 四个筛选 + 默认按**线路+沿線顺序**排
    """
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_tasks, bd_lines, bd_places, paging
    line_id = int(line) if str(line).strip().isdigit() else None
    sort = sort if sort in dict(bd_tasks.STATION_SORTS) else "line"
    filters = {"kw": kw, "status": status, "line_id": line_id, "pref": pref,
               "operator": operator, "kind": kind, "multi_only": bool(multi),
               "sort": sort}
    pager = bd_tasks.list_stations(db, page=page, per=per or paging.PER_DEFAULT,
                                   **filters)
    return templates.TemplateResponse("bd_stations.html", {
        "request": request, "current_user": user, "rows": pager["rows"],
        "pager": pager, "page_qs": paging.qs(request.query_params),
        "kw": kw, "status": status, "pref": pref, "operator": operator,
        "kind": kind, "multi": multi, "sort": sort,
        "sorts": bd_tasks.STATION_SORTS,
        "facets": bd_tasks.station_facets(db),
        "lines": bd_lines.line_options(db), "line_id": line_id,
        "asset": bd_places.asset_stats(db),   # 车站数据资产（三层规模）
        "has_filter": any([kw, status, pref, operator, kind, multi, line_id]),
        "msg": msg, "err": err,
    })


@router.get("/stations/export")
def stations_export(request: Request,
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db), kw: str = "",
                    status: str = "", line: str = "", pref: str = "",
                    operator: str = "", kind: str = "", multi: str = "",
                    sort: str = "line"):
    """把**当前筛选结果**导成 CSV（车站资产；UTF-8 BOM，Excel 直接打开）。"""
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_tasks
    line_id = int(line) if str(line).strip().isdigit() else None
    d = bd_tasks.list_stations(db, kw=kw, status=status, line_id=line_id, pref=pref,
                               operator=operator, kind=kind, multi_only=bool(multi),
                               sort=sort if sort in dict(bd_tasks.STATION_SORTS) else "line",
                               all_rows=True)      # ⚠️ 全量（分页会把 per 夹到 200 → 会截断）
    import csv as _csv
    import io as _io
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(["都道府県", "线路", "顺序", "里程km", "駅名", "駅コード",
                "物理车站ID", "还经过", "緯度", "経度", "状態", "備考"])
    for r in d["rows"]:
        st = r["station"]
        w.writerow([r["pref_label"], r["line_label"], st.seq or "",
                    ("%.1f" % st.along_km) if st.along_km is not None else "",
                    st.name, st.ekicode or "", st.place_id or "",
                    r["lines_text"], st.lat or "", st.lon or "",
                    ("启用" if st.status == "active" else "停用"), st.note or ""])
    data = buf.getvalue().encode("utf-8-sig")
    from urllib.parse import quote as _quote
    fn = "stations.csv"
    return StreamingResponse(
        _io.BytesIO(data), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition":
                 "attachment; filename=%s; filename*=UTF-8''%s"
                 % (fn, _quote("车站资产.csv"))})


@router.post("/stations/{station_id}/edit")
def station_edit(request: Request, station_id: int, name: str = Form(""),
                 line: str = Form(""), note: str = Form(""),
                 line_id: str = Form(""), status: str = Form(""),
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
        bd_tasks.update_station(db, station_id, name=name, line=line, note=note,
                                status=status,
                                line_id=int(line_id) if line_id.strip().isdigit() else None)
        db.commit()
        return RedirectResponse("/stations?msg=%s" % _q(_m("已保存")),
                                status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/stations?err=%s" % _q(str(e)),
                                status_code=303)


@router.get("/tasks", response_class=HTMLResponse)
def tasks_page(request: Request, user: Optional[User] = Depends(require_login),
               db: Session = Depends(get_db), team: str = "", state: str = "",
               tab: str = "", line: str = "",
               date_from: str = "", date_to: str = "", kw: str = "",
               stale: str = "", page: int = 1, msg: str = "", err: str = ""):
    """任务总表：三个 tab（未分配 / 已分配 / 已完成）+ 按线路/队/日期/关键词筛选。

    用户 2026-10-05："任务分为已完成，已分配，未分配几个tab页"、"可以根据线路做查询"、
    "车站即任务"（所以这一页就是任务列表，不再强调车站/任务的区别）。
    """
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_leave, bd_teams, bd_tasks, bd_lines, paging
    try:
        team_id = int(team) if str(team).strip() else None
    except ValueError:
        team_id = None
    line_id = int(line) if str(line).strip().isdigit() else None
    # ⚠️ 允许 `all`（"该队全部任务"入口用）；其它非法值一律回「未分配」
    tab = tab if tab in bd_tasks.BOARD_TABS_ALL else bd_tasks.TAB_UNASSIGNED
    limit = paging.PER_DEFAULT
    page = max(1, int(page or 1))
    if tab == bd_tasks.TAB_UNASSIGNED:
        # ⚠️ 「未分配」= **车站池**（还没队伍的物理车站，含"没建过任务"的）——
        #    在这一栏勾站 + 选队 + 分配 = "建任务 + 派队"一步完成（用户 2026-10-06）
        d = bd_tasks.list_unassigned_places(db, page=page, per=limit, kw=kw,
                                           line_id=line_id)
    else:
        d = bd_tasks.task_board(db, team_id=team_id, state=state, tab=tab,
                                line_id=line_id,
                                date_from=_parse_date(date_from),
                                date_to=_parse_date(date_to), kw=kw,
                                stale_only=bool(stale),
                                limit=limit, offset=(page - 1) * limit)
    pager = paging.info_from(d["total"], d["page"] if "page" in d else page, limit)
    pager["rows"] = d["rows"]
    # 当日休假中的人（担当旁边标出来 —— 管理端一眼看到"活派给休假的人了"）
    leave_map = bd_leave.active_map(db)
    # 「修正进展」能不能点：管理员**只能改队长确认/处理过的**（用户 2026-10-06）
    # ⚠️ 一次算完（批量），别在模板里逐条判
    # ⚠️ 未分配 tab 的行是**车站池**（没有 task 键）→ 不参与
    adjust_map = ({tid: v.get("adjust", False) for tid, v in
                   bd_tasks.can_reject_maps(db, user,
                                            [r["task"] for r in d["rows"]],
                                            rows=d["rows"]).items()}
                  if tab != bd_tasks.TAB_UNASSIGNED else {})
    # 三个 tab 的数字（**只受其它筛选影响**）——统计卡与 tab 共用同一份，避免两套口径
    counts = bd_tasks.tab_counts(
        db, team_id=team_id, line_id=line_id,
        date_from=_parse_date(date_from), date_to=_parse_date(date_to), kw=kw)
    return templates.TemplateResponse("bd_tasks.html", {
        "request": request, "current_user": user,
        "rows": d["rows"], "total": d["total"], "page": pager["page"],
        # 未分配 tab 用的是"车站池"（不同的行结构：place/seq/along_km/has_task）
        "pool_rows": (d["rows"] if tab == bd_tasks.TAB_UNASSIGNED else []),
        # "当前这一栏没结果"（未分配看车站池，其余看任务行）—— 跨 tab 提示用它
        "empty_current": (not d["rows"]),
        "limit": limit, "pager": pager,
        "page_qs": paging.qs(request.query_params),
        # tab 链接专用：**去掉 tab 自己**（否则出现 ?tab=assigned&tab=done 这种重复参数）
        "tab_qs": paging.qs([(k, v) for k, v in request.query_params.multi_items()
                             if k not in ("page", "tab")]),
        "team_id": team_id, "teams": bd_teams.team_options(db),
        "tab": tab, "tabs": bd_tasks.BOARD_TABS,
        "tab_counts": counts,
        "lines": bd_lines.line_options(db), "line_id": line_id,
        # 线路下拉：**按 tab 给口径**（未分配=未分配车站数；已分配/已完成=任务数）——
        # 只列有内容的线路（用户 2026-10-06："没有任务的就不要显示，数量只显示任务的数量"）
        "line_opts": bd_tasks.line_options(db, tab),
        "state": state, "date_from": date_from, "date_to": date_to, "kw": kw,
        "stale": stale, "stale_days": d.get("stale_days", 2),
        "adjust_map": adjust_map,
        "leave_map": leave_map,
        "sum": bd_tasks.board_summary(db),
        # ⚠️ 统计卡与 tab **同一口径**（2026-10-06 审计：卡片按 state 数 → "已分配 0"，
        #    同屏 tab 却是 515，管理员第一眼拿到两个矛盾的数）
        "cards": {
            "pool": counts["unassigned"],       # 车站池（还没派队的物理车站）
            "assigned": counts["assigned"],     # 已派队/有人管（未完成）
            "done": counts["done"],
            "stale": bd_tasks.stale_count(
                db, team_id=team_id, line_id=line_id,
                date_from=_parse_date(date_from), date_to=_parse_date(date_to),
                kw=kw, stale_days=d.get("stale_days", 2)),
            "total": counts["assigned"] + counts["done"],
        },
        "by_team": bd_tasks.team_board_summary(db),
        "labels": bd_tasks.state_labels(CURRENT_LANG.get()),
        "msg": msg, "err": err,
    })


@router.get("/tasks/new", response_class=HTMLResponse)
def task_new_page(request: Request, user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db), line: str = "", kw: str = "",
                  msg: str = "", err: str = ""):
    """**建任务**：选线路（快速过滤）→ 该线的物理车站按沿線顺序列出 → 勾选 → 可选派队 → 建。

    用户 2026-10-05 定稿：
    - **1 个物理车站 = 1 个任务**（跨线站只 1 个；同名异地本来就 2 个）
    - **派活是滚动的**（"我分给 A 队的 10 个站，并不是这 10 个站作为一个整体任务跑完我再分新的，
      而是在剩下几个站的时候，我就可以再派发新的一组给他"）→ 建完的站变"已有任务"禁用，
      剩下的可以继续勾、换个队、再建一次
    - 建任务**只到队伍**（担当由队长分）；**分配日期自动今天**（不做日期输入）
    """
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_teams, bd_tasks, bd_lines
    line_id = int(line) if str(line).strip().isdigit() else None
    rows = bd_tasks.list_places_for_line(db, line_id, kw=kw) if line_id else []
    return templates.TemplateResponse("bd_task_new.html", {
        "request": request, "current_user": user,
        "lines": bd_lines.line_options(db), "line_id": line_id, "kw": kw,
        "rows": rows, "n_all": len(rows),
        "n_has": sum(1 for r in rows if r["has_task"]),
        "n_free": sum(1 for r in rows if not r["has_task"]),
        "teams": bd_teams.team_options(db),
        "msg": msg, "err": err,
    })


@router.post("/tasks/new")
def task_new_create(request: Request,
                    place_id: Optional[List[str]] = Form(None),
                    team: str = Form(""), line: str = Form(""),
                    csrf_token: str = Form(""),
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """按勾选的物理车站**批量建任务**（已有的跳过；可选同时派队）。"""
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    ids = [int(x) for x in (place_id or []) if str(x).strip().isdigit()]
    back = "/tasks/new?line=%s" % _q(line)
    if not ids:
        return RedirectResponse(back + "&err=%s" % _q(_m("请先勾选要建任务的站")),
                                status_code=303)
    team_id = int(team) if str(team).strip().isdigit() else None
    try:
        r = bd_tasks.create_tasks_for_places(db, ids, by=user.username,
                                             team_id=team_id, actor_user=user)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse(back + "&err=%s" % _q(str(e)), status_code=303)
    m = "已新建 %d 个任务" % r["created"]
    if r["team_name"]:
        m += "（已派给「%s」，分配日期 %s）" % (r["team_name"], r["assign_date"])
    else:
        m += "（未派队，可在任务页派）"
    if r["skipped"]:
        m += "；跳过 %d 个（已有任务）" % r["skipped"]
    return RedirectResponse(back + "&msg=%s" % _q(m), status_code=303)


@router.post("/tasks/{task_id}/progress")
def task_progress_admin(request: Request, task_id: int, pct: str = Form("0"),
                        note: str = Form(""), csrf_token: str = Form(""),
                        back: str = Form(""),
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
        # ⚠️ 必须传 actor_user：否则 save_progress 两个审核分支都不进
        #    （不写 review_status、不发消息，队长一点确认还会用员工原值覆盖管理员刚改的值）
        r = bd_tasks.save_progress(db, task_id, pct, note, by=user.username,
                                   actor_user=user)
        db.commit()
        # ⚠️ 回跳**原视图**：原来固定 /tasks（=车站池），管理员在"已分配"里每改一条就被弹走
        return RedirectResponse(
            _with_msg(back, "msg", _m("已修正：%d%%", r["pct"])), status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse(_with_msg(back, "err", str(e)), status_code=303)


@router.post("/tasks/ai-suggest", response_class=HTMLResponse)
def tasks_ai_suggest(request: Request, line: str = Form(""), kw: str = Form(""),
                     csrf_token: str = Form(""),
                     user: Optional[User] = Depends(require_login),
                     db: Session = Depends(get_db)):
    """**AI 派工建议**（htmx 局部刷新）：给"未分配的车站池"出"哪几站派给哪个队 + 理由"。

    ⚠️ 只**建议**、绝不写库（不建任务、不派队）：管理员看完勾选 → 走 `/tasks/assign` 才生效。
    数字由程序算，模型只分组 + 写理由；编造的站名/队伍会被校验层丢弃（见 bd_assign_ai）。
    """
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_assign_ai
    line_id = int(line) if str(line).strip().isdigit() else None
    try:
        sug = bd_assign_ai.suggest(db, kw=kw, line_id=line_id)
    except bd_assign_ai.SuggestError as e:
        return templates.TemplateResponse("_ai_suggest.html", {
            "request": request, "current_user": user, "err": str(e)})
    return templates.TemplateResponse("_ai_suggest.html", {
        "request": request, "current_user": user, "err": "",
        "sug": sug, "kw": kw, "line_id": line_id})


@router.post("/tasks/assign")
def tasks_assign(request: Request,
                 place_id: Optional[List[str]] = Form(None),
                 team: str = Form(""), line: str = Form(""),
                 csrf_token: str = Form(""),
                 user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db)):
    """**未分配 → 分配**：勾选的车站直接派给队伍（没任务的顺便建任务，一步完成）。

    用户 2026-10-06："对于未分配，我可以选择一些车站，直接做分配" +
    "不要单独的'建任务按钮'"。
    """
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    ids = [int(x) for x in (place_id or []) if str(x).strip().isdigit()]
    back = "/tasks?tab=%s&line=%s" % (bd_tasks.TAB_UNASSIGNED, _q(line))
    if not ids:
        return RedirectResponse(back + "&err=%s" % _q(_m("请先勾选要分配的车站")),
                                status_code=303)
    tid = int(team) if str(team).strip().isdigit() else None
    try:
        r = bd_tasks.assign_team_to_places(db, ids, tid, by=user.username,
                                          actor_user=user)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse(back + "&err=%s" % _q(str(e)), status_code=303)
    return RedirectResponse(
        back + "&msg=%s" % _q(_m("已派给「%s」：新建任务 %d 个，派队 %d 个", r["team_name"], r["created"], r["assigned"])),
        status_code=303)


@router.get("/tasks/export")
def tasks_export(user: Optional[User] = Depends(require_login),
                 db: Session = Depends(get_db), team: str = "",
                 state: str = "", date_from: str = "", date_to: str = "",
                 kw: str = "", line: str = "", stale: str = ""):
    g = _admin_guard(user)
    if g:
        return g
    from app.services import bd_tasks
    try:
        team_id = int(team) if str(team).strip() else None
    except ValueError:
        team_id = None
    line_id = int(line) if str(line).strip().isdigit() else None
    d2 = 2
    buf = bd_tasks.tasks_xlsx(db, team_id=team_id, state=state,
                              date_from=_parse_date(date_from),
                              date_to=_parse_date(date_to),
                              lang=CURRENT_LANG.get(),
                              kw=kw, line_id=line_id,
                              # 页面勾了「只看停滞」→ 导出也要一致（审计：以前导出是全量）
                              stale_only=bool(stale), stale_days=d2)
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

@router.post("/tasks/reassign")
def tasks_reassign(request: Request, task_id: Optional[List[str]] = Form(None),
                   team_id: str = Form(""), csrf_token: str = Form(""),
                   back: str = Form(""),
                   user: Optional[User] = Depends(require_login),
                   db: Session = Depends(get_db)):
    """**改派队伍**（用户 2026-10-06 审计：派队原本单向不可逆，选错只能改库）。

    沿用 `set_task_team` 既有口径：**换队会清空不属于新队的担当**（页面提交前已提示）。
    """
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    ids = [int(x) for x in (task_id or []) if str(x).strip().isdigit()]
    if not ids or not str(team_id).strip().isdigit():
        return RedirectResponse(_with_msg(back, "err", "请选择要改派的队伍"),
                                status_code=303)
    try:
        r = bd_tasks.set_task_team(db, ids, int(team_id), by=user.username,
                                   actor_user=user)
        db.commit()
        msg = _m("已改派 %d 个任务", r.get("n_team", len(ids)))
        if r.get("n_cleared"):
            msg += "；清空了 %d 名不属新队的担当" % r["n_cleared"]
        return RedirectResponse(_with_msg(back, "msg", msg), status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse(_with_msg(back, "err", str(e)), status_code=303)


@router.post("/tasks/return-pool")
def tasks_return_pool(request: Request, task_id: Optional[List[str]] = Form(None),
                      csrf_token: str = Form(""), back: str = Form(""),
                      user: Optional[User] = Depends(require_login),
                      db: Session = Depends(get_db)):
    """**退回车站池**（解除队伍 + 清担当 + 清分配日期）——给"派错队"一条回收路径。"""
    g = _admin_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    ids = [int(x) for x in (task_id or []) if str(x).strip().isdigit()]
    if not ids:
        return RedirectResponse(_with_msg(back, "err", "请先勾选任务"), status_code=303)
    r = bd_tasks.return_to_pool(db, ids, by=user.username, actor_user=user)
    db.commit()
    return RedirectResponse(_with_msg(back, "msg", _m("已退回车站池：%d 个", r["n"])),
                            status_code=303)


@router.get("/my/tasks", response_class=HTMLResponse)
def my_tasks_page(request: Request,
                  user: Optional[User] = Depends(require_login),
                  db: Session = Depends(get_db), tab: str = "",
                  kw: str = "", line: str = "", msg: str = "", err: str = ""):
    """员工端任务页（**队长与队员同一页**，队长多出"任务管理"）。

    - **队员（staff）**：只看"我的任务"（自己担当的），**每行可上报进展**（滑动条）
      —— 用户 2026-10-03 口径：员工自己先上报，队长做调整
    - **队长（leader）**：同一页 + 多出 本队任务的 未分配/进行中/已完成 三个 tab
      （可分派担当、可调整进展）；他自己的任务照常上报
    """
    g = _staff_guard(user)
    if g:
        return g
    from app.services import bd_leave, bd_teams, bd_tasks, self_report
    TAB_MINE = "mine"
    TAB_PENDING = "pending"      # 待确认（队长专用 tab）
    is_leader = user.role == "leader"
    jst_today = bd_leave.today()
    teams = bd_teams.leader_teams(db, user.person_code) if is_leader else []
    team_ids = [t.id for t in teams]
    my_rows = bd_tasks.member_tasks(db, user.person_code)     # 我担当的（跨队）
    my_ids = {r["task"].id for r in my_rows}
    can_report_map, can_assign_map, can_reject_map = {}, {}, {}
    members = {}
    avail = {}
    bulk_avail = {}
    line_id = int(line) if str(line).strip().isdigit() else None
    if is_leader:
        all_rows = bd_tasks.team_tasks(db, team_ids, tab="", kw=kw,
                                       line_id=line_id)
        # 队长 tab 口径 = **按有没有分人**（与 team_tasks 一致，见其注释）
        counts = {
            "unassigned": sum(1 for r in all_rows if not r["assignees"]),
            "doing": sum(1 for r in all_rows if r["assignees"]),
            "done": sum(1 for r in all_rows if r["state"] == "done"),
        }
        counts[TAB_MINE] = len(my_rows)
        # 「待确认」= 队员报过、队长还没处理的（用户 2026-10-03：确认 / 调整）
        counts[TAB_PENDING] = sum(1 for r in all_rows if r.get("pending_review"))
        # ⚠️ 队长默认落点（2026-10-06 审计：默认「我的」→ 队长第一眼是空列表，
        #    看不到还有 82 个站没分人）→ 有待确认先看待确认，否则看未分配
        if tab not in (TAB_MINE, TAB_PENDING, "unassigned", "doing", "done"):
            tab = (TAB_PENDING if counts[TAB_PENDING]
                   else ("unassigned" if counts["unassigned"] else TAB_MINE))
        if tab == TAB_MINE:
            rows = my_rows
        elif tab == TAB_PENDING:
            rows = [r for r in all_rows if r.get("pending_review")]
        elif tab == "unassigned":        # 队内**待派** = 未完成且没分人（含"有进展没人"）
            rows = [r for r in all_rows if not r["assignees"]]
        elif tab == "doing":             # 进行中 = 未完成且已分人
            rows = [r for r in all_rows if r["assignees"]]
        elif tab == "done":
            rows = [r for r in all_rows if r["state"] == "done"]
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
        # ⚠️ 每个日期只算一次可用性；`bulk_avail`（未分配 tab 的下拉）直接复用今天那一档
        for d in dates:
            m = bd_leave.availability_map(db, all_codes, d)
            for code, a in m.items():
                avail["%s|%s" % (d.isoformat(), code)] = a
            if d == jst_today:
                bulk_avail = m
        if not bulk_avail and (is_leader and tab == "unassigned") and all_codes:
            bulk_avail = bd_leave.availability_map(db, all_codes, jst_today)
    else:
        # 用户 2026-10-06："员工端只有'我的'和'已完成'。其它的员工端不需要。"
        open_rows = [r for r in my_rows if r["state"] != "done"]
        done_rows = [r for r in my_rows if r["state"] == "done"]
        if tab not in ("done", TAB_MINE):
            tab = TAB_MINE
        counts = {TAB_MINE: len(open_rows), "done": len(done_rows)}
        rows = done_rows if tab == "done" else open_rows
    # ⚠️ 权限**一次算完**（逐条 can_* 会让 76 行变成 180+ 条 SQL → 页面慢）
    perms = bd_tasks.can_reject_maps(db, user, [r["task"] for r in rows],
                                    lead_team_ids=team_ids, rows=rows)
    locked_map = {}
    for tid, d in perms.items():
        can_report_map[tid] = d["report"]
        can_assign_map[tid] = d["assign"]
        can_reject_map[tid] = d["reject"]
        locked_map[tid] = d.get("locked", False)     # 队长已确认 → 队员今天不能再改
    my_leave = bd_leave.current(db, user.person_code, jst_today)
    return templates.TemplateResponse("my_tasks.html", {
        "request": request, "current_user": user,
        "is_leader": is_leader, "teams": teams, "rows": rows,
        "counts": counts, "tab": tab, "kw": kw, "members": members,
        "can_report_map": can_report_map, "can_assign_map": can_assign_map,
        "can_reject_map": can_reject_map, "locked_map": locked_map,
        "tab_mine": TAB_MINE, "tab_pending": TAB_PENDING,
        # 队长端线路下拉：只列**本队有内容的线路**（与管理端同口径，2026-10-06 审计）
        "line_opts": (bd_tasks.line_options(db, tab or "assigned",
                                            team_ids=team_ids)
                      if is_leader else {}),
        "line_id": line_id, "line": line,
        "my_ids": my_ids, "n_mine": len(my_rows),
        # 员工「一页一次提交」自报（点数 + 当天任务进度）：见 docs/任务域-全流程.md §8-7
        "self_report": (self_report.view(db, user) if not is_leader
                        and tab == TAB_MINE else None),
        "n_pending": counts.get(TAB_PENDING, 0),
        "avail": avail, "my_leave": my_leave,
        # 批量分派下拉的休假/请假标签（未分配 tab 用"今天"这一档，算一次就够）
        "bulk_avail": bulk_avail,
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
        return RedirectResponse(back % "doing" + "&err=" + _q(_m("任务不存在")),
                                status_code=303)
    if not bd_tasks.can_assign(db, user, task):
        return RedirectResponse(back % "doing" + "&err="
                                + _q(_m("只有该队队长或管理员能分派")),
                                status_code=303)
    try:
        r = bd_tasks.assign_members(db, task_id, person or (), by=user.username,
                                    actor_user=user)
        db.commit()
        msg = _m("已分派 %d 人", r["n"])
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


@router.post("/my/tasks/assign-bulk")
def my_tasks_assign_bulk(request: Request,
                         task_id: Optional[List[str]] = Form(None),
                         person: Optional[List[str]] = Form(None),
                         csrf_token: str = Form(""),
                         user: Optional[User] = Depends(require_login),
                         db: Session = Depends(get_db)):
    """**未分配 → 批量分派到人**（用户 2026-10-06："未分配任务，可以多选，然后点一下分配，
    可以分配到人"）。

    逐条走 `bd_tasks.assign_members`（判权/在职校验/日志都在那里），
    一条失败不影响其它条，最后回报成功几条 + 警告。
    """
    g = _staff_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks, bd_teams
    ids = [int(x) for x in (task_id or []) if str(x).strip().isdigit()]
    people = [p for p in (person or []) if str(p).strip()]
    back = "/my/tasks?tab=unassigned"
    # ⚠️ **判权**：管理员 / 该任务的队长（队员不能分派）—— 与单条 /my/tasks/assign 的
    #    can_assign 同一口径。漏了它会变成「任何队员都能改本队分工」（2026-10-06 审计 P0）
    _is_admin = bd_tasks.is_admin(user)
    _lead = set() if _is_admin else {
        t.id for t in bd_teams.leader_teams(db, user.person_code)}
    if not ids:
        return RedirectResponse(back + "&err=%s" % _q(_m("请先勾选任务")), status_code=303)
    if not people:
        return RedirectResponse(back + "&err=%s" % _q(_m("请先选队员")), status_code=303)
    ok, errs, warns, denied = 0, [], [], 0
    for tid in ids:
        _t = db.get(bd_tasks.BdTask, tid)
        if _t is None:
            errs.append("任务 %d 不存在" % tid)
            continue
        if not (_is_admin or _t.team_id in _lead):
            denied += 1
            continue
        try:
            r = bd_tasks.assign_members(db, tid, people, by=user.username,
                                        actor_user=user)
            db.commit()          # ⚠️ assign_members 自己不 commit（由调用方提交）
            ok += 1
            warns += (r or {}).get("warnings") or []
        except Exception as e:                                  # noqa: BLE001
            db.rollback()
            errs.append(str(e))
    msg = _m("已分派 %d 个任务", ok)
    if warns:
        msg += "；⚠️ " + "；".join(warns[:3])
    if denied:
        msg += "；%d 条不在你的队里，已跳过" % denied
    if errs:
        msg += "；%d 条失败：%s" % (len(errs), errs[0])
    q = ("msg=" + _q(msg)) if not errs else ("err=" + _q(msg))
    return RedirectResponse("%s&%s" % (back, q), status_code=303)


@router.post("/my/tasks/reject")
def task_reject(request: Request, task_id: int = Form(0), pct: str = Form("0"),
                note: str = Form(""), csrf_token: str = Form(""),
                user: Optional[User] = Depends(require_login),
                db: Session = Depends(get_db)):
    """**驳回**：把已完成的 100% 退回成不到 100%。

    用户 2026-10-06："队员报了100%，队长可以驳回，队长确认了以后，管理员可以驳回。
    驳回就是把100%的进度改成不到100%"。判权唯一入口 `bd_tasks.can_reject`。

    ⚠️ 路径挂在 `/my/` 下：**队长被中间件挡在 `/tasks/*` 外**（他只能在 `/my/*` 操作），
    与既有的 `/my/tasks/confirm`、`/my/tasks/progress` 保持一致；管理端页面也提交到这里。
    """
    if user is None:
        return RedirectResponse("/login", status_code=302)
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    t = db.get(bd_tasks.BdTask, task_id)
    if t is None or not bd_tasks.can_reject(db, user, t):
        return RedirectResponse("/login", status_code=302)
    try:
        r = bd_tasks.reject_progress(db, task_id, int(pct or 0), note,
                                     by=user.username, actor_user=user)
    except bd_tasks.TaskError as e:
        db.rollback()
        back = "/my/tasks" if user.role != "admin" else "/tasks"
        return RedirectResponse("%s?err=%s" % (back, _q(str(e))), status_code=303)
    back = "/my/tasks" if user.role != "admin" else "/tasks?tab=done"
    return RedirectResponse(
        "%s?msg=%s" % (back, _q(_m("已驳回：进度改为 %d%%", r["pct"]))),
        status_code=303)


@router.post("/my/self-report")
async def my_self_report(request: Request,
                         user: Optional[User] = Depends(require_login),
                         db: Session = Depends(get_db)):
    """**员工每日自报（一页一次提交）**：点数 + 当天 N 个任务进度，同一事务。

    用户 2026-10-06："一是报点数，二是报任务完成的进度情况，一起提交自报"。
    ⚠️ 字段名是动态的（`pct_<task_id>` / `note_<task_id>`）→ 必须读原始表单，
    所以这个端点是 `async def`（同步路由拿不到 `await request.form()`）。
    """
    g = _staff_guard(user)
    if g:
        return g
    form = await request.form()
    if not csrf_ok(request, form.get("csrf_token", "")):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import self_report
    items = []
    for key, val in form.items():
        if not key.startswith("pct_"):
            continue
        try:
            tid = int(key[4:])
        except ValueError:
            continue
        items.append({"task_id": tid, "pct": val,
                      "note": str(form.get("note_%d" % tid) or "")})
    back = "/my/tasks?tab=mine"
    try:
        r = self_report.submit(db, user, area=str(form.get("area") or ""),
                               p1_cnt=form.get("p1_cnt", ""),
                               p2_cnt=form.get("p2_cnt", ""), items=items)
        return RedirectResponse(
            back + "&msg=" + _q(_m("已提交自报：1点 %d / 2点 %d，%d 个任务进度",
                                   r["p1"], r["p2"], r["tasks"])), status_code=303)
    except Exception as e:                       # noqa: BLE001
        db.rollback()                            # 一起提交 → 失败就整体回滚
        return RedirectResponse(back + "&err=" + _q(_m(str(e))), status_code=303)


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
        return RedirectResponse("/my/tasks?err=" + _q(_m("任务不存在")),
                                status_code=303)
    if not bd_tasks.can_report(db, user, task):
        # ⚠️ 分两种说清楚：不是我的活 vs 我的活但**队长今天已确认**（锁住）
        #   2026-10-06 浏览器实测：原来一律说"只能上报自己担当的任务"，
        #   队员明明是担当、只是被锁了，看到这句话会完全摸不着头脑
        _mine = bd_tasks.is_assignee(db, user, task)
        _row = bd_tasks.day_progress_map(db, [task.id]).get(task.id)
        _msg = (_m("队长已确认，今天这条不能再改（如需修改请联系队长或管理员）")
                if _mine and bd_tasks.is_locked_for_staff(_row)
                else _m("只能上报自己担当的任务"))
        return RedirectResponse("/my/tasks?err=" + _q(_msg), status_code=303)
    try:
        r = bd_tasks.save_progress(db, task_id, pct, note, by=user.username,
                                   actor_user=user)
        db.commit()
        return RedirectResponse(
            "/my/tasks?tab=%s&msg=%s" % (r["state"], _q(_m("已提交 %d%%", r["pct"]))),
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
    from app.services import bd_log, bd_tasks, bd_teams
    task = db.get(bd_tasks.BdTask, task_id)
    if task is None:
        return RedirectResponse("/tasks?err=%s" % _q(_m("任务不存在")),
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
        "teams": bd_teams.team_options(db),      # 改派下拉（管理员）
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


@router.post("/my/tasks/confirm-bulk")
def my_tasks_confirm_bulk(request: Request,
                          task_id: Optional[List[str]] = Form(None),
                          person: Optional[List[str]] = Form(None),
                          csrf_token: str = Form(""), back: str = Form(""),
                          user: Optional[User] = Depends(require_login),
                          db: Session = Depends(get_db)):
    """**待确认批量确认**（用户 2026-10-06 审计：30 条要 30 次往返 + 每处理完被弹走）。"""
    g = _staff_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.models import BdTaskProgress as _P
    from app.services import bd_tasks
    ids = [int(x) for x in (task_id or []) if str(x).strip().isdigit()]
    back = _safe_back(back, "/my/tasks?tab=pending")
    if not ids:
        return RedirectResponse(_with_msg(back, "err", "请先勾选任务", "/my/tasks"),
                                status_code=303)
    ok, errs = 0, []
    for tid in ids:
        t = db.get(bd_tasks.BdTask, tid)
        if t is None:
            errs.append("任务不存在"); continue
        if not bd_tasks.can_adjust(db, user, t):
            errs.append("没有权限：%s" % (t.id,)); continue
        pend = (db.query(_P).filter(_P.task_id == tid,
                                    _P.review_status == "pending")
                .order_by(_P.progress_date.desc()).first())
        if pend is None or pend.reported_pct is None:
            errs.append("无待确认上报：%s" % (t.id,)); continue
        try:
            bd_tasks.save_progress(db, tid, pend.reported_pct, pend.note or "",
                                   by=user.username, actor_user=user,
                                   confirm=True, on_date=pend.progress_date)
            db.commit()
            ok += 1
        except bd_tasks.TaskError as e:
            db.rollback()
            errs.append(str(e))
    msg = _m("已确认 %d 条", ok)
    if errs:
        msg += "；%d 条跳过（%s）" % (len(errs), errs[0])
    return RedirectResponse(_with_msg(back, "msg" if ok else "err", msg, "/my/tasks"),
                            status_code=303)


@router.post("/my/tasks/return")
def my_tasks_return(request: Request, task_id: int = Form(0),
                    back_tab: str = Form(""), csrf_token: str = Form(""),
                    user: Optional[User] = Depends(require_login),
                    db: Session = Depends(get_db)):
    """**队长回收 / 空置**：把任务收回本队待派（清掉担当，**进展保留**）。

    用户 2026-10-06："对于分配出去的任务，队长也可以回收回来，或者分给别人，或者空置。
    **对于已经有进展的任务，也可以这样做**。"
    """
    g = _staff_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_tasks
    t = db.get(bd_tasks.BdTask, task_id)
    back = "/my/tasks?tab=%s" % (back_tab or "doing")
    if t is None:
        return RedirectResponse(back + "&err=" + _q(_m("任务不存在")),
                                status_code=303)
    if not bd_tasks.can_assign(db, user, t):
        return RedirectResponse(back + "&err=" + _q(_m("只有该队队长或管理员能回收")),
                                status_code=303)
    try:
        bd_tasks.assign_members(db, task_id, [], by=user.username,
                                actor_user=user)
        db.commit()
    except Exception as e:                        # noqa: BLE001
        db.rollback()
        return RedirectResponse(back + "&err=" + _q(_m(str(e))), status_code=303)
    return RedirectResponse(back + "&msg="
                            + _q(_m("已回收：回到本队待派（进展保留）")),
                            status_code=303)


@router.post("/my/tasks/confirm-day")
def my_tasks_confirm_day(request: Request, csrf_token: str = Form(""),
                         user: Optional[User] = Depends(require_login),
                         db: Session = Depends(get_db)):
    """**队长一键确认当天全部**（用户 2026-10-06："队长确认和自动确认"→ 一键全确认）。"""
    g = _staff_guard(user)
    if g:
        return g
    if not csrf_ok(request, csrf_token):
        return HTMLResponse("CSRF 校验失败", status_code=400)
    from app.services import bd_teams, bd_tasks
    teams = bd_teams.leader_teams(db, user.person_code)
    if not teams and user.role != "admin":
        return RedirectResponse("/my/tasks?err=" + _q(_m("只有队长能确认")),
                                status_code=303)
    team_ids = None if user.role == "admin" else [t.id for t in teams]
    try:
        n = bd_tasks.confirm_day(db, user, team_ids=team_ids)
        db.commit()
    except Exception as e:                        # noqa: BLE001
        db.rollback()
        return RedirectResponse("/my/tasks?tab=pending&err=" + _q(_m(str(e))),
                                status_code=303)
    if n == 0:
        return RedirectResponse("/my/tasks?tab=pending&msg="
                                + _q(_m("没有待确认的上报")), status_code=303)
    return RedirectResponse("/my/tasks?tab=pending&msg="
                            + _q(_m("已一键确认 %d 条", n)), status_code=303)


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
        return RedirectResponse("/my/tasks?err=%s" % _q(_m("任务不存在")),
                                status_code=303)
    if not bd_tasks.can_adjust(db, user, task):
        return RedirectResponse("/my/tasks?err=%s"
                                % _q(_m("只有该队队长或管理员能确认")),
                                status_code=303)
    # ⚠️ 跨天确认：找**还挂着 pending 的那条上报行**，把它的日期一起传下去。
    #    旧写法锚定"今天"→ 员工昨天下班报、队长第二天点确认会另造一条 adjusted 行，
    #    昨天那条永远停在 pending，待确认 tab 里也消失了（2026-10-06 审计）
    from app.models import BdTaskProgress as _P
    pend = (db.query(_P).filter(_P.task_id == task_id,
                                _P.review_status == "pending")
            .order_by(_P.progress_date.desc()).first())
    if pend is None or pend.reported_pct is None:
        return RedirectResponse("/my/tasks?tab=pending&err=%s"
                                % _q(_m("这条还没有队员上报，未确认")), status_code=303)
    try:
        r = bd_tasks.save_progress(db, task_id, pend.reported_pct,
                                   pend.note or note, by=user.username,
                                   actor_user=user, confirm=True,
                                   on_date=pend.progress_date)
        db.commit()
        msg = "已确认 %s%%" % r["pct"]
        if r.get("notified"):
            msg += "；已通知队员"
        # 处理完**留在待确认队列**（队长通常要连着处理一串）
        return RedirectResponse("/my/tasks?tab=pending&msg=%s" % _q(msg),
                                status_code=303)
    except bd_tasks.TaskError as e:
        db.rollback()
        return RedirectResponse("/my/tasks?err=%s" % _q(str(e)),
                                status_code=303)
