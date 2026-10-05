# -*- coding: utf-8 -*-
"""车站任务（规格 `docs/specs-station-tasks.md`）。

口径唯一来源。核心口径（写死在测试里）：

- **一个车站 = 一个任务**（`bd_task`，UNIQUE(station_id)）
- `assign_date` = **分配日期**（管理员把任务派给团队那天）→ 管理端按区间查询
- `state` 由「担当人数 + 进度」推导：`unassigned` / `doing` / `done`
  （**缺担当 → 未分配；有担当且 pct<100 → 进行中；pct>=100 → 已完成**）
- 担当 **1~2 人**（上限应用层强制），且必须是**该队现役成员**
- 每日进展：一天一条（`bd_task_progress`，UNIQUE(task_id, progress_date)），
  **当天可改（覆盖）**，同时把 `bd_task.pct/state` 刷新为最新值
- 权限：**提交进展/分派 = 该队队长或管理员**；队员只读（`can_submit`）
"""
from datetime import date, datetime
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models import (BdStation, BdStationPlace, BdTask, BdTaskAssign,
                        BdTaskProgress, BdTeam, BdTeamMember, Person)

STATE_UNASSIGNED = "unassigned"
STATE_DOING = "doing"
STATE_DONE = "done"
STATES = (STATE_UNASSIGNED, STATE_DOING, STATE_DONE)

TAB_UNASSIGNED = "unassigned"
TAB_DOING = "doing"          # 队长页的 tab 名（历史）；管理端用 TAB_ASSIGNED
TAB_ASSIGNED = "assigned"
TAB_DONE = "done"
TABS = (TAB_UNASSIGNED, TAB_DOING, TAB_DONE)
#: 管理端任务总表的 tab（用户 2026-10-05："任务分为已完成，已分配，未分配"）
BOARD_TABS = (TAB_UNASSIGNED, TAB_ASSIGNED, TAB_DONE)

MAX_ASSIGN = 2

STATE_LABELS = {
    # 用户 2026-10-05 口径："任务分为已完成，已分配，未分配" —— 有担当/有进度但没完成
    # 就叫「已分配」（不再叫"进行中"，那个词在管理端容易和"有没有派下去"混淆）
    # ⚠️ "assigned" 是管理端 tab 的键（BOARD_TABS），必须也有标签 ——
    # 漏了就会渲染出「（515）」这种空标签（2026-10-05 实测踩到）
    "zh": {STATE_UNASSIGNED: "未分配", STATE_DOING: "已分配",
           TAB_ASSIGNED: "已分配", STATE_DONE: "已完成"},
    "ja": {STATE_UNASSIGNED: "未割当", STATE_DOING: "割当中",
           TAB_ASSIGNED: "割当中", STATE_DONE: "完了"},
}


class TaskError(Exception):
    """业务错误（路由转成页面提示，不 500）。"""


class NameExists(TaskError):
    pass


class NotOpenError(TaskError):
    pass


def norm_name(s: Optional[str]) -> str:
    """车站名归一（NFKC + 去全部空白 + **ケ/ヶ 统一**）——判重唯一入口。

    ⚠️ ケ/ヶ 必须统一：同一个站在不同资料里写法不同（`箱根ケ崎` vs `箱根ヶ崎`、
    `茅ケ崎` vs `茅ヶ崎`、`南阿佐ケ谷` vs `南阿佐ヶ谷`）—— 2026-10-05 导入 N02 实测
    有 3 个站因为这点对不上而会**重复建任务**。
    """
    import unicodedata
    t = unicodedata.normalize("NFKC", str(s or ""))
    t = "".join(t.split()).replace("ケ", "ヶ").replace("ｹ", "ヶ")
    return t


def _today(today: Optional[date] = None) -> date:
    return today or date.today()


def state_labels(lang: str = "zh") -> dict:
    return STATE_LABELS.get(lang) or STATE_LABELS["zh"]


# ---------------- 车站主数据 ----------------

def create_station(db: Session, name: str, line: str = "", note: str = "",
                   by: str = "", line_id: Optional[int] = None) -> BdStation:
    """新建车站。**唯一键 = (线路, 站名)**（一线一站，用户 2026-10-05 口径）。

    `line_id` 给了就挂到线路主档（`line` 文本自动取线路名）+ 同步建"物理车站"层；
    没给就是"无线路"的手工站（此时按站名判重，部分唯一索引兜底）。

    ⚠️ 2026-10-05 这个改动**曾经因为打补丁时中途 assert 失败而没写进去**，
    而路由已经在传 `line_id=` → 界面新建车站会 TypeError（测试抓到的）。
    """
    from app.models import BdLine
    nm = (name or "").strip()
    if not nm:
        raise TaskError("车站名不能为空")
    key = norm_name(nm)
    ln = db.get(BdLine, int(line_id)) if line_id else None
    if line_id and ln is None:
        raise TaskError("线路不存在")
    dup = (db.query(BdStation)
           .filter(BdStation.name_norm == key,
                   BdStation.line_id == (ln.id if ln else None)).first())
    if dup:
        raise NameExists("该线路下已有这个车站：%s（不覆盖）" % nm)
    st = BdStation(name=nm, name_norm=key,
                   line=((ln.name if ln else "") or (line or "").strip()),
                   line_id=(ln.id if ln else None),
                   operator=(ln.operator if ln else ""),
                   note=(note or "").strip(), status="active", source="manual")
    db.add(st)
    db.flush()
    from app.services import bd_places
    bd_places.rebuild_places(db)          # 手工站也进物理车站层（资产完整）
    return st


def update_station(db: Session, station_id: int, name: Optional[str] = None,
                   line: Optional[str] = None, note: Optional[str] = None,
                   status: Optional[str] = None, actor_user=None,
                   line_id: Optional[int] = None) -> BdStation:
    """改车站主数据（**改名/改线路/改备注/改状态都写日志**）。"""
    from app.services import bd_log
    st = db.get(BdStation, station_id)
    if st is None:
        raise TaskError("车站不存在")
    before = {"name": st.name, "line": st.line, "note": st.note,
              "status": st.status}
    if name is not None:
        nm = name.strip()
        if not nm:
            raise TaskError("车站名不能为空")
        key = norm_name(nm)
        # 一线一站：只在**同一条线路**内判重（同名跨线是允许的，用户口径先按线路来）
        dup = (db.query(BdStation)
               .filter(BdStation.name_norm == key,
                       BdStation.line_id == st.line_id,
                       BdStation.id != station_id).first())
        if dup is not None:
            raise NameExists("车站已存在：%s（不覆盖）" % nm)
        st.name, st.name_norm = nm, key
    if line is not None:
        st.line = line.strip()
    if note is not None:
        st.note = note.strip()
    if status in ("active", "closed"):
        st.status = status
    st.updated_at = datetime.utcnow()
    db.flush()
    for f, label in (("name", "车站名"), ("line", "线路"), ("note", "备注"),
                     ("status", "状态")):
        after = getattr(st, f)
        if before[f] != after:
            bd_log.log_op(db, actor_user, "station",
                          "rename" if f == "name" else "update",
                          ref_id=st.id, ref_label=st.name, field=label,
                          old=before[f], new=after)
    return st


#: 车站列表排序方式（车站页下拉）；文案用中文，模板 t() 翻日文
STATION_SORTS = (
    ("line", "线路 + 顺序"),    # 默认：按线路分组、组内按沿線顺序（用户 2026-10-05 要的顺序）
    ("name", "站名"),
    ("pref", "都道府県"),
    ("ekicode", "駅コード"),
)

#: 线路类型的中文标签（代码见 `bd_lines.KIND_LABELS`：jr/private/…）
KIND_LABELS_ZH = {
    "jr": "JR在来线", "shinkansen": "新干线", "private": "私营铁路",
    "public": "公营", "third": "第三部门", "monorail": "单轨",
    "agt": "新交通", "tram": "有轨电车", "cable": "钢索铁路",
}


def list_stations(db: Session, page: int = 1, per: Optional[int] = None,
                  kw: str = "", line_id: Optional[int] = None, pref: str = "",
                  operator: str = "", kind: str = "", multi_only: bool = False,
                  status: str = "", sort: str = "line",
                  all_rows: bool = False) -> dict:
    """**车站资产列表**（车站页专用）—— 只出车站信息，**不带任务**。

    用户 2026-10-06：
    - "车站归车站，任务归任务，车站这边只是维护车站信息"
      → 任务列/任务筛选/批量建任务派队都从这里移走（任务去 `/tasks`，建任务去 `/tasks/new`）
    - "你再看看车站的功能，缺失很多，我查都查不到"
      → **关键词一个框搜全部**：站名 / 駅コード / 运营商（JR東日本・都営…）/ 线路名 /
        还经过哪些线（物理车站层）/ **都道府県名**（以前只存代码，所以"千葉県"搜不到）
    - 默认排序 = **线路 + 沿線顺序**（以前按 id 排，1920 行毫无规律 → "查不到"的体感来源）
    """
    from app.models import BdLine, BdStationPlace
    from app.services import bd_lines
    from app.services import paging as _pg
    q = (db.query(BdStation)
         .outerjoin(BdLine, BdLine.id == BdStation.line_id)
         .outerjoin(BdStationPlace, BdStationPlace.id == BdStation.place_id))
    if kw:
        # ⚠️ 用户输入可能是**中文**（京叶线/涩谷/东京），数据是日文新字体 →
        #    每个变体都 OR 上去（见 app/services/bd_cjk.py；用户 2026-10-06 实测反馈）
        from app.services import bd_cjk
        conds = []
        for v in bd_cjk.search_variants(kw):
            like = "%%%s%%" % v
            conds += [BdStation.name.like(like), BdStation.line.like(like),
                      BdStation.ekicode.like(like), BdStation.operator.like(like),
                      BdLine.name.like(like), BdLine.operator_short.like(like),
                      BdStationPlace.lines_text.like(like)]
            zh_ids = bd_cjk.zh_line_ids(db, v)     # 「丸之内线」这类汉字映射管不到的
            if zh_ids:
                conds.append(BdStation.line_id.in_(zh_ids))
            pref_hits = [c for c, lbl in bd_lines.PREF_LABELS.items() if v in lbl]
            if pref_hits:                           # 搜"千葉県"→ 命中 pref 代码 12
                conds.append(BdStation.pref.in_(pref_hits))
        q = q.filter(or_(*conds)) if conds else q
    if pref:
        q = q.filter(BdStation.pref == pref)
    if operator:
        q = q.filter(BdLine.operator_short == operator)
    if kind:
        q = q.filter(BdLine.kind == kind)
    if multi_only:                          # 只看跨线站（一个物理车站被多条线经过）
        q = q.filter(BdStationPlace.n_line > 1)
    if status in ("active", "closed"):
        q = q.filter(BdStation.status == status)
    if line_id:
        q = q.filter(BdStation.line_id == line_id)
    if sort == "name":
        order = [BdStation.name.asc(), BdStation.id.asc()]
    elif sort == "pref":
        order = [BdStation.pref.asc(), BdLine.operator_short.asc(), BdLine.name.asc(),
                 BdStation.seq.is_(None), BdStation.seq.asc(), BdStation.name.asc()]
    elif sort == "ekicode":
        order = [BdStation.ekicode.is_(None), BdStation.ekicode.asc()]
    else:                                   # line（默认）
        order = [BdLine.operator_short.asc(), BdLine.name.asc(),
                 BdStation.seq.is_(None), BdStation.seq.asc(), BdStation.name.asc()]
    if all_rows:
        # ⚠️ 导出必须走这里：`paging.paginate` 把 per 夹到 PER_MAX(200) →
        #    直接传 per=100000 会**静默只导 200 行**（2026-10-06 实测：千葉县 383 条只导出 200 条）
        stations = q.order_by(*order).all()
        pg = _pg.Pager({"rows": stations, "total": len(stations), "page": 1,
                        "per": max(1, len(stations)), "pages": 1,
                        "start": 1, "end": len(stations), "has_prev": False,
                        "has_next": False, "prev_page": 1, "next_page": 1})
    else:
        pg = _pg.paginate(q.order_by(*order), page, per or _pg.PER_DEFAULT)
        stations = pg["rows"]
    lines_map = {l.id: l for l in db.query(BdLine).all()}
    places = {pl.id: pl for pl in db.query(BdStationPlace).filter(
        BdStationPlace.id.in_([x.place_id for x in stations if x.place_id] or [0])).all()}
    out = []
    for st in stations:
        ln = lines_map.get(st.line_id)
        pl = places.get(st.place_id)
        out.append({
            "station": st, "line": ln, "place": pl,
            "line_label": (("%s %s" % (ln.operator_short, ln.name)) if ln
                           else (st.line or "")),
            "operator": ((ln.operator_short if ln else "") or st.operator or ""),
            "kind": (ln.kind if ln else ""),
            "kind_label": KIND_LABELS_ZH.get(ln.kind if ln else "", ""),
            "pref_label": bd_lines.PREF_LABELS.get(st.pref or "", st.pref or ""),
            "n_line": (pl.n_line if pl else 0),
            "lines_text": (pl.lines_text if pl else ""),
            "city": (pl.city if pl else ""),
        })
    pg["rows"] = out
    return pg


def station_facets(db: Session) -> dict:
    """车站页筛选下拉的选项（都从数据现取，不写死）。"""
    from app.models import BdLine
    from app.services import bd_lines
    ops = [r[0] for r in db.query(BdLine.operator_short).distinct()
           .order_by(BdLine.operator_short.asc()).all() if r[0]]
    kinds = [r[0] for r in db.query(BdLine.kind).distinct().all() if r[0]]
    prefs = [r[0] for r in db.query(BdStation.pref).distinct().all() if r[0]]
    return {
        "operators": ops,
        "kinds": [{"code": k, "label": KIND_LABELS_ZH.get(k, k)} for k in kinds],
        "prefs": [{"code": c, "label": bd_lines.PREF_LABELS.get(c, c)}
                  for c in sorted(prefs)],
    }


def recompute_state(pct: int, n_assign: int) -> str:
    """状态唯一口径（优先级：已完成 > 进行中 > 未分配）：

    - `pct >= 100` → **已完成**（哪怕担当被撤掉，完成就是完成）
    - 否则 **有担当 或 有进度** → **进行中**（已经开工了就不该显示"未分配"）
    - 其他（没担当且 pct=0）→ **未分配**

    ⚠️ 「有进度也算进行中」是 2026-10-03 修的：旧口径只看担当，会出现
    "pct=30 但停在未分配"的自相矛盾状态（队长自己开工、还没分人时就会踩）。
    """
    p = int(pct or 0)
    if p >= 100:
        return STATE_DONE
    if n_assign > 0 or p > 0:
        return STATE_DOING
    return STATE_UNASSIGNED


def _assign_counts(db: Session, task_ids: Sequence[int]) -> Dict[int, int]:
    if not task_ids:
        return {}
    rows = (db.query(BdTaskAssign.task_id, func.count(BdTaskAssign.id))
            .filter(BdTaskAssign.task_id.in_(list(task_ids)))
            .group_by(BdTaskAssign.task_id).all())
    return {tid: n for tid, n in rows}


def refresh_state(db: Session, task: BdTask) -> BdTask:
    n = (db.query(func.count(BdTaskAssign.id))
         .filter(BdTaskAssign.task_id == task.id).scalar() or 0)
    task.state = recompute_state(task.pct, n)
    task.updated_at = datetime.utcnow()
    return task


def create_tasks(db: Session, station_ids: Sequence[int], by: str = "",
                 team_id: Optional[int] = None,
                 assign_date: Optional[date] = None,
                 actor_user=None) -> dict:
    """给车站**批量建任务**（缺则建，已有则跳过并回报，不覆盖、不报 500）。**写创建日志。**"""
    from app.services import bd_log
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    created = skipped = 0
    have = set()
    if station_ids:
        have = {sid for (sid,) in db.query(BdTask.station_id)
                .filter(BdTask.station_id.in_(list(station_ids))).all()}
    for sid in station_ids:
        if sid in have:
            skipped += 1
            continue
        st = db.get(BdStation, sid)
        if st is None:
            skipped += 1
            continue
        t = BdTask(station_id=sid, team_id=team_id, assign_date=assign_date,
                   state=STATE_UNASSIGNED, pct=0, created_by=(by or ""))
        db.add(t)
        db.flush()
        bd_log.log_op(db, actor_user, "task", "create", ref_id=t.id,
                      ref_label=(st.name or ""), field="",
                      new=("派给 %s" % (db.get(BdTeam, team_id).name
                                       if team_id else "（未派队）")))
        created += 1
    db.flush()
    return {"created": created, "skipped": skipped}


def _place_ids_taken(db: Session):
    """**已被任务占用的物理车站 id**（两种口径都算）。

    - 新口径：`bd_task.place_id`
    - 老口径：`bd_task.station_id` 指向的车站行 → 它的 `place_id`

    ⚠️ 少了任何一边都会出现"同一个车站两个任务"（2026-10-05 测试抓到的真 bug）。
    """
    direct = db.query(BdTask.place_id).filter(BdTask.place_id.isnot(None))
    via_station = (db.query(BdStation.place_id)
                   .join(BdTask, BdTask.station_id == BdStation.id)
                   .filter(BdStation.place_id.isnot(None)))
    return direct.union(via_station).subquery()


def place_ids_without_task(db: Session, line_id: Optional[int] = None) -> List[int]:
    """**所有**"还没有任务"的物理车站 id（批量建任务用；不受分页影响）。

    车站页的「全部建任务」从这里取全集。按 `place_id` 判定（任务单位是物理车站），
    所以跨线站不会被重复建。
    """
    taken = _place_ids_taken(db)
    q = (db.query(BdStationPlace.id)
         .filter(BdStationPlace.id.notin_(db.query(taken.c[0])),
                 BdStationPlace.status == "active"))
    if line_id:
        q = q.filter(BdStationPlace.id.in_(
            db.query(BdStation.place_id).filter(BdStation.line_id == line_id)))
    return [x for (x,) in q.all()]


def place_ids_for_stations(db: Session, station_ids: Sequence[int]) -> List[int]:
    """车站（站×线）id → **物理车站** id（去重，保持原有顺序）。"""
    ids = [int(x) for x in dict.fromkeys(station_ids or [])]
    if not ids:
        return []
    rows = (db.query(BdStation.id, BdStation.place_id)
            .filter(BdStation.id.in_(ids)).all())
    m = {sid: pid for sid, pid in rows if pid}
    out: List[int] = []
    for sid in ids:
        pid = m.get(sid)
        if pid and pid not in out:      # ⚠️ 去重：跨线站两行 → 同一个物理车站只留一个
            out.append(pid)
    return out


def list_places_for_line(db: Session, line_id: int, kw: str = "") -> List[dict]:
    """按线路列出**可以用来建任务的物理车站**（建任务页用）。

    - **顺序** = 该站在这条线上的 `seq`（OSM / 几何 / 人工三级来源）；没顺序的排最后
    - 显示：沿線里程 / 还经过哪些线（`lines_text`）/ 是否已有任务（有 → 页面禁用勾选）
    - 一个跨线站只会出现**一行**（任务单位是物理车站，不是站×线）
    """
    # ⚠️ 关联任务要**两种口径都认**（place 直连，或老任务经由 station 所属的 place）
    #    否则页面上"已建过任务的站"会显示成"未建"，一点就重复建
    taken = _place_ids_taken(db)
    legacy = (db.query(BdTask.id)
              .join(BdStation, BdStation.id == BdTask.station_id)
              .filter(BdStation.place_id == BdStationPlace.id).exists())
    q = (db.query(BdStation, BdStationPlace, BdTask, BdTeam.name)
         .join(BdStationPlace, BdStationPlace.id == BdStation.place_id)
         .outerjoin(BdTask, or_(BdTask.place_id == BdStationPlace.id,
                                BdTask.id.in_(
                                    db.query(BdTask.id)
                                    .join(BdStation, BdStation.id == BdTask.station_id)
                                    .filter(BdStation.place_id == BdStationPlace.id))))
         .outerjoin(BdTeam, BdTeam.id == BdTask.team_id)
         .filter(BdStation.line_id == line_id))
    if kw:
        from app.services import bd_cjk
        conds = []
        for v in bd_cjk.search_variants(kw):
            k = "%%%s%%" % v
            conds += [BdStationPlace.name.like(k), BdStationPlace.lines_text.like(k)]
        q = q.filter(or_(*conds)) if conds else q
    rows = (q.order_by(BdStation.seq.is_(None), BdStation.seq.asc(),
                       BdStationPlace.name.asc()).all())
    out, seen = [], set()
    for st, pl, task, team_name in rows:
        if pl.id in seen:                        # 防御：同一 place 在这条线上不该有两行
            continue
        seen.add(pl.id)
        out.append({"place_id": pl.id, "name": pl.name, "seq": st.seq,
                    "along_km": st.along_km, "lines_text": pl.lines_text,
                    "n_line": pl.n_line, "pref": pl.pref,
                    "task_id": (task.id if task is not None else None),
                    "task_state": (task.state if task is not None else ""),
                    "team_name": team_name or "",
                    "has_task": task is not None})
    return out


def create_tasks_for_places(db: Session, place_ids: Sequence[int], by: str = "",
                            team_id: Optional[int] = None,
                            assign_date: Optional[date] = None,
                            actor_user=None) -> dict:
    """给**物理车站**批量建任务：已有的**跳过并回报**（不覆盖、不 500）。

    用户 2026-10-05 定稿："1 个车站 = 1 个任务，各自独立状态；派活是滚动的"
    （A 队那 10 个站还剩几个没做完，就可以再派新的一组给他）→ 所以：
    - 一个车站一个任务（`uq_bd_task_place` 部分唯一索引兜底）
    - **分配日期默认今天**（用户："日期不用管，有个分配日期就行"；任务会跨好几天）
    - 建的时候可以**顺便派队**（不派也行，之后再派）；**担当不在这里定**（队长分）
    - 派了队 → 该任务在"已分配" tab（口径：有队就算有人管）；没派队 → "未分配"
    """
    from app.services import bd_log
    from app.services.date_plan import jst_today
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    ids = [int(x) for x in dict.fromkeys(place_ids or [])]
    if team_id is not None and db.get(BdTeam, team_id) is None:
        raise TaskError("团队不存在")
    have = set()
    if ids:
        taken = _place_ids_taken(db)
        have = {pid for (pid,) in db.query(taken.c[0]).filter(
            taken.c[0].in_(ids)).all() if pid}
    d = assign_date or jst_today()
    created, skipped, new_ids = 0, 0, []
    team_name = (db.get(BdTeam, team_id).name if team_id else "")
    for pid in ids:
        if pid in have:
            skipped += 1
            continue
        pl = db.get(BdStationPlace, pid)
        if pl is None:
            skipped += 1
            continue
        t = BdTask(place_id=pid, station_id=None, source_type="station",
                   team_id=team_id, assign_date=(d if team_id else None),
                   state=STATE_UNASSIGNED, pct=0, created_by=by)
        db.add(t)
        db.flush()
        created += 1
        new_ids.append(t.id)
        bd_log.log_op(db, actor_user, "task", "create", ref_id=t.id,
                      ref_label=pl.name, field="任务",
                      new=("新建（已派队）" if team_id else "新建（未派队）"),
                      note="按线路选站建任务")
        if team_id:
            bd_log.log_op(db, actor_user, "task", "dispatch", ref_id=t.id,
                          ref_label=pl.name, field="队伍", new=team_name,
                          note="建任务时同时派队")
    db.commit()
    return {"created": created, "skipped": skipped, "task_ids": new_ids,
            "assign_date": d, "team_name": team_name}


def set_task_team(db: Session, task_ids: Sequence[int],
                  team_id: Optional[int], assign_date: Optional[date] = None,
                  by: str = "", actor_user=None) -> dict:
    """**派给团队**（管理员）；顺带写分配日期。

    换队 → **清空不属于新队的担当**（原担当不属于新队），并回报清掉的人数。
    **每次派活/换队/改分配日期都写日志。**
    """
    from app.services import bd_log
    if team_id is not None and db.get(BdTeam, team_id) is None:
        raise TaskError("团队不存在")
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    n_team = n_cleared = 0
    for tid in task_ids:
        t = db.get(BdTask, tid)
        if t is None:
            continue
        label = _station_name(db, t)
        old_team = db.get(BdTeam, t.team_id) if t.team_id else None
        new_team = db.get(BdTeam, team_id) if team_id else None
        if t.team_id != team_id:
            bd_log.log_op(db, actor_user, "task", "dispatch", ref_id=t.id,
                          ref_label=label, field="team",
                          old=(old_team.name if old_team else "（未派队）"),
                          new=(new_team.name if new_team else "（未派队）"))
        if assign_date is not None and t.assign_date != assign_date:
            bd_log.log_op(db, actor_user, "task", "update", ref_id=t.id,
                          ref_label=label, field="assign_date",
                          old=(t.assign_date.isoformat() if t.assign_date else ""),
                          new=assign_date.isoformat())
        if t.team_id != team_id and team_id is not None:
            keep = {c for (c,) in db.query(BdTeamMember.person_code).filter(
                BdTeamMember.team_id == team_id,
                BdTeamMember.end_date.is_(None)).all()}
            old = db.query(BdTaskAssign).filter(
                BdTaskAssign.task_id == tid).all()
            for a in old:
                if a.person_code not in keep:
                    db.delete(a)
                    n_cleared += 1
                    bd_log.log_op(db, actor_user, "task", "unassign",
                                  ref_id=t.id, ref_label=label,
                                  field="assignee", old=a.person_code,
                                  new="", note="换队清空原担当")
        t.team_id = team_id
        if assign_date is not None:
            t.assign_date = assign_date
        refresh_state(db, t)
        n_team += 1
    db.flush()
    return {"updated": n_team, "cleared": n_cleared}


def assign_members(db: Session, task_id: int, person_codes: Sequence[str],
                   by: str = "", actor_user=None,
                   on_date: Optional[date] = None) -> dict:
    """分派担当（**≤2 人**，必须是该队现役成员）。**谁进谁出都写日志。**

    ⚠️ 离职/停用的人**不能**被分派（他执行不了）；历史担当记录不受影响。
    """
    t = db.get(BdTask, task_id)
    if t is None:
        raise TaskError("任务不存在")
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    codes = []
    for c in person_codes:
        c = (c or "").strip()
        if c and c not in codes:
            codes.append(c)
    if len(codes) > MAX_ASSIGN:
        raise TaskError("一个车站最多分给 %d 个人" % MAX_ASSIGN)
    if codes:
        if t.team_id is None:
            raise TaskError("请先把任务派给团队，再分派队员")
        allowed = {c for (c,) in db.query(BdTeamMember.person_code).filter(
            BdTeamMember.team_id == t.team_id,
            BdTeamMember.end_date.is_(None)).all()}
        bad = [c for c in codes if c not in allowed]
        if bad:
            raise TaskError("不是该队现役成员：%s" % "、".join(bad))
        # 离职/停用的人执行不了 → 不能分派（用户 2026-10-03 口径）
        from app.models import User as _User
        gone = []
        for u in db.query(_User).filter(_User.person_code.in_(codes)).all():
            if u.status in ("resigned", "disabled"):
                gone.append(u.display_name or u.person_code)
        if gone:
            raise TaskError("这些人已离职/停用，不能派工：%s" % "、".join(gone))
    # 出勤计划 / 假期模式 / 请假 → **提醒但不阻断**（用户 2026-10-03 口径）
    warn_lines: List[str] = []
    from app.services import bd_leave
    d = on_date
    if d is None:
        # 看"任务的工作日"：分配日期与今天取**较晚**者 —— 任务还没开始就看开始那天，
        # 已经开始/分配日期已过就看今天（否则拿过期日期判断，提醒永远落不到点上）
        _base = bd_leave.today()
        d = max(t.assign_date, _base) if t.assign_date else _base
    if codes:
        av = bd_leave.availability_map(db, codes, d)
        names = {c: n for c, n in
                 db.query(BdTeamMember.person_code, Person.display_name)
                 .join(Person, Person.code == BdTeamMember.person_code)
                 .filter(BdTeamMember.person_code.in_(codes)).all()}
        for c in codes:
            txt = bd_leave.warn_text(av.get(c) or {}, names.get(c) or c)
            if txt:
                warn_lines.append(txt)
    from app.services import bd_log
    old = db.query(BdTaskAssign).filter(BdTaskAssign.task_id == task_id).all()
    old_codes = [a.person_code for a in old]
    for a in old:
        db.delete(a)
    for c in codes:
        db.add(BdTaskAssign(task_id=task_id, person_code=c,
                            assigned_by=(by or "")))
    db.flush()
    refresh_state(db, t)
    db.flush()
    label = _station_name(db, t)
    for c in old_codes:
        if c not in codes:
            bd_log.log_op(db, actor_user, "task", "unassign", ref_id=t.id,
                          ref_label=label, field="assignee", old=c, new="")
    for c in codes:
        if c not in old_codes:
            bd_log.log_op(db, actor_user, "task", "assign", ref_id=t.id,
                          ref_label=label, field="assignee", old="", new=c)
    return {"n": len(codes), "state": t.state, "warnings": warn_lines}


def save_progress(db: Session, task_id: int, pct: int, note: str = "",
                  by: str = "", on_date: Optional[date] = None,
                  actor_user=None, confirm: bool = False) -> dict:
    """**每日进展上报/调整**：写/改当天一条，并把任务刷新为最新进度。

    - `pct` 夹到 0–100
    - 当天已上报 → 覆盖（`updated_at` 变）——**员工先上报、队长做调整**都走这里
    - 刷新 `bd_task.pct/state`，开始日/完成日自动写
    - **每次上报/调整都写一条日志**（进度表会被覆盖，日志不会：
      "员工报 40% → 队长改成 60%" 能看出是谁改的）
    """
    from app.services import bd_log
    t = db.get(BdTask, task_id)
    if t is None:
        raise TaskError("任务不存在")
    try:
        p = int(pct)
    except (TypeError, ValueError):
        raise TaskError("进度必须是 0–100 的整数")
    p = max(0, min(100, p))
    d = _today(on_date)
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    old_pct, old_state = t.pct, t.state
    row = (db.query(BdTaskProgress)
           .filter(BdTaskProgress.task_id == task_id,
                   BdTaskProgress.progress_date == d).first())
    # ---- 谁在写？员工本人上报 vs 队长/管理员确认或调整（用户 2026-10-03 口径）----
    staff_report = bool(actor_user is not None
                        and is_assignee(db, actor_user, t)
                        and not can_adjust(db, actor_user, t))
    leader_review = bool(actor_user is not None
                         and can_adjust(db, actor_user, t))
    if confirm and row is not None and row.reported_pct is not None:
        p = int(row.reported_pct)             # 「确认」= 认可员工上报的原值
    if row is None:
        row = BdTaskProgress(task_id=task_id, progress_date=d, pct=p,
                             note=(note or "").strip(),
                             submitted_by=(by or ""))
        db.add(row)
        db.flush()
    else:
        row.pct = p
        row.note = (note or "").strip()
        row.submitted_by = (by or "")
        row.updated_at = datetime.utcnow()
    notified = None
    review_action = ""
    orig_reported = row.reported_pct
    if staff_report:
        # 员工上报 → 记录原值，等队长确认（重新报会再次进入待确认）
        row.reported_pct = p
        row.reported_by = (by or "")
        row.review_status = "pending"
        row.reviewed_by = ""
        row.reviewed_at = None
        row.review_note = ""
    elif leader_review:
        if row.reported_pct is None:
            row.review_status = "adjusted"     # 队长直接填（没有员工上报可比）
        elif p == int(row.reported_pct):
            row.review_status = "confirmed"    # 认可
        else:
            row.review_status = "adjusted"     # 改了值
        row.reviewed_by = (by or "")
        row.reviewed_at = datetime.utcnow()
        row.review_note = (note or "").strip()
        review_action = row.review_status
    t.pct = p
    # 开始日 / 完成日**自动写**（用户 2026-10-03 口径）
    if t.start_date is None:
        t.start_date = d                      # 首次提交 = 开始
    if p >= 100:
        if t.done_date is None:
            t.done_date = d                   # 到 100% = 完成
    else:
        t.done_date = None                    # 进度回退 → 完成日清掉，保持自洽
    refresh_state(db, t)
    db.flush()
    if p != old_pct or t.state != old_state:
        bd_log.log_op(db, actor_user, "task",
                      "progress" if p != old_pct else "state",
                      ref_id=t.id, ref_label=_station_name(db, t),
                      field="pct" if p != old_pct else "state",
                      old=("%d%%" % old_pct) if p != old_pct else old_state,
                      new=("%d%%" % p) if p != old_pct else t.state,
                      note=(note or ""))
    elif review_action:
        bd_log.log_op(db, actor_user, "task", "update", ref_id=t.id,
                      ref_label=_station_name(db, t), field="进展确认",
                      old=("%s%%" % orig_reported), new=review_action,
                      note=(note or ""))
    # **确认/调整结果通知员工**（用户明确要求：调整要发消息）
    if review_action and orig_reported is not None:
        from app.services import bd_msg
        notified = bd_msg.notify_task_progress(
            db, t, review_action, orig_reported, p, by_user=actor_user,
            note=(note or ""), station=_station_name(db, t))
    return {"pct": p, "state": t.state, "date": d,
            "start_date": t.start_date, "done_date": t.done_date,
            "review_status": row.review_status,
            "reported_pct": row.reported_pct,
            "notified": bool(notified)}


def _station_name(db: Session, task: BdTask) -> str:
    """任务单位的名字：**优先物理车站**（新口径：任务挂 place），退回老的车站行。

    这个名字用在日志 ref_label / 消息文案 / 导出 —— 口径统一在这里，别处不要各写一遍。
    """
    if task is None:
        return ""
    if getattr(task, "place_id", None):
        pl = db.get(BdStationPlace, task.place_id)
        if pl is not None:
            return pl.name
    st = db.get(BdStation, task.station_id) if task.station_id else None
    return (st.name if st is not None else "") or ""


def latest_progress(db: Session, task_ids: Sequence[int]) -> Dict[int, BdTaskProgress]:
    """每个任务最新一条进展（一次查询，供列表显示"最后提交"）。"""
    if not task_ids:
        return {}
    rows = (db.query(BdTaskProgress)
            .filter(BdTaskProgress.task_id.in_(list(task_ids)))
            .order_by(BdTaskProgress.task_id.asc(),
                      BdTaskProgress.progress_date.desc()).all())
    out: Dict[int, BdTaskProgress] = {}
    for r in rows:
        out.setdefault(r.task_id, r)
    return out


# ---------------- 权限（口径见 bd_perm；数据范围见 bd_teams.is_leader_of） ----------------

def is_admin(user) -> bool:
    return user is not None and getattr(user, "role", "") == "admin"


def _is_team_leader(db: Session, user, team_id) -> bool:
    from app.services import bd_teams
    if user is None or getattr(user, "role", "") != "leader":
        return False
    return bd_teams.is_leader_of(db, getattr(user, "person_code", None), team_id)


def is_assignee(db: Session, user, task: BdTask) -> bool:
    """本人是不是这条任务的担当（1~2 人之一）。"""
    code = getattr(user, "person_code", None) if user is not None else None
    if not code:
        return False
    return db.query(BdTaskAssign).filter(
        BdTaskAssign.task_id == task.id,
        BdTaskAssign.person_code == code).first() is not None


def can_report(db: Session, user, task: BdTask) -> bool:
    """**上报进展**：管理员 / 该任务的队长 / **本人是担当**。

    ⚠️ 用户 2026-10-03 口径变更：「员工自己先上报，队长做调整」——
    旧口径只有队长能提交，已作废。
    """
    from app.services import bd_perm
    if user is None:
        return False
    if is_admin(user):
        return True
    if not bd_perm.can(db, user, "task.report"):
        return False
    return _is_team_leader(db, user, task.team_id) or is_assignee(db, user, task)


def can_assign(db: Session, user, task: BdTask) -> bool:
    """**分派担当 / 调整别人的进展**：管理员 / 该任务的队长（队员不行）。"""
    from app.services import bd_perm
    if user is None:
        return False
    if is_admin(user):
        return True
    if not bd_perm.can(db, user, "task.assign"):
        return False
    return _is_team_leader(db, user, task.team_id)


def can_adjust(db: Session, user, task: BdTask) -> bool:
    """调整（修正）进展：管理员 / 该任务的队长。"""
    from app.services import bd_perm
    if user is None:
        return False
    if is_admin(user):
        return True
    if not bd_perm.can(db, user, "task.adjust"):
        return False
    return _is_team_leader(db, user, task.team_id)


# ---------------- 视图数据 ----------------

def task_rows(db: Session, task_ids: Sequence[int]) -> List[dict]:
    """按 id 取任务行（路由/详情页用；内部走同一个 `_rows`）。"""
    if not task_ids:
        return []
    tasks = (db.query(BdTask).filter(BdTask.id.in_(list(task_ids)))
             .order_by(BdTask.id.asc()).all())
    return _rows(db, tasks)



def _rows(db: Session, tasks: Sequence[BdTask]) -> List[dict]:
    """任务行（车站/队/担当/最新进展），**批量取，避免 N+1**。"""
    if not tasks:
        return []
    tids = [t.id for t in tasks]
    sids = [t.station_id for t in tasks if t.station_id]
    pids = [t.place_id for t in tasks if getattr(t, "place_id", None)]
    stations = {s.id: s for s in db.query(BdStation)
                .filter(BdStation.id.in_(sids)).all()}
    # ⚠️ 新任务只挂 place（没有 station_id）→ 不取 place 的话页面上名字会是空的
    places = {x.id: x for x in db.query(BdStationPlace)
              .filter(BdStationPlace.id.in_(pids)).all()}
    teams = {t.id: t for t in db.query(BdTeam).all()}
    assigns: Dict[int, List[str]] = {}
    names = {}
    for a in (db.query(BdTaskAssign)
              .filter(BdTaskAssign.task_id.in_(tids))
              .order_by(BdTaskAssign.id.asc()).all()):
        assigns.setdefault(a.task_id, []).append(a.person_code)
    codes = {c for v in assigns.values() for c in v}
    if codes:
        names = {c: (d or c) for c, d in db.query(Person.code, Person.display_name)
                 .filter(Person.code.in_(list(codes))).all()}
    last = latest_progress(db, tids)
    # 每队队长名（显示用）
    team_leaders: Dict[int, List[str]] = {}
    tids_team = [t.team_id for t in tasks if t.team_id]
    if tids_team:
        lrows = (db.query(BdTeamMember.team_id, BdTeamMember.person_code,
                          Person.display_name)
                 .outerjoin(Person, Person.code == BdTeamMember.person_code)
                 .filter(BdTeamMember.team_id.in_(tids_team),
                         BdTeamMember.role == "leader",
                         BdTeamMember.end_date.is_(None)).all())
        for team_id, code, disp in lrows:
            team_leaders.setdefault(team_id, []).append(disp or code)
    out = []
    today = date.today()
    for t in tasks:
        st = stations.get(t.station_id)
        pl = places.get(getattr(t, "place_id", None))
        a_codes = assigns.get(t.id, [])
        lp = last.get(t.id)
        last_d = (lp.progress_date if lp else None)
        # "多少天没动"：从最后一次提交算；从没提交过则按分配日期算
        base = last_d or t.assign_date
        days = (today - base).days if base else None
        # 停滞 = 还没完成 且（从没提交 或 超过 2 天没更新）
        stale = (t.state != STATE_DONE
                 and (last_d is None or (days is not None and days >= 2)))
        out.append({
            "task": t, "station": st, "place": pl,
            # ⚠️ 键名保持 `station_name`/`line` 不变：模板/导出都在用，
            #    这里改成"place 优先"，视图层就**不用跟着大改**（少踩坑）
            "station_name": ((pl.name if pl else "") or (st.name if st else "")),
            "line": ((pl.lines_text if pl else "") or (st.line if st else "")),
            "team_id": t.team_id,
            "team_name": (teams[t.team_id].name if t.team_id in teams else ""),
            "team_leaders": team_leaders.get(t.team_id, []),
            "assignees": [{"code": c, "name": names.get(c, c)} for c in a_codes],
            "n_assign": len(a_codes),
            "pct": t.pct, "state": t.state,
            "assign_date": t.assign_date,
            "start_date": t.start_date, "done_date": t.done_date,
            "days_since": days, "stale": stale,
            "last_date": last_d,
            "last_pct": (lp.pct if lp else None),
            "last_note": (lp.note if lp else ""),
            # 审核（员工先报 → 队长确认/调整）；**待确认 = 有员工上报且还没处理**
            "last_reported_pct": (lp.reported_pct if lp else None),
            "last_reported_by": (lp.reported_by if lp else ""),
            "review_status": (lp.review_status if lp else ""),
            "reviewed_by": (lp.reviewed_by if lp else ""),
            "pending_review": bool(lp is not None
                                   and lp.reported_pct is not None
                                   and lp.review_status == "pending"),
        })
    return out


def assignees_of(db: Session, task_id: int) -> List[dict]:
    """该任务的担当（`{code, name}`；消息通知用）。"""
    codes = [c for (c,) in db.query(BdTaskAssign.person_code)
             .filter(BdTaskAssign.task_id == task_id).all() if c]
    names = {}
    if codes:
        names = {c: (d or c) for c, d in
                 db.query(Person.code, Person.display_name)
                 .filter(Person.code.in_(codes)).all()}
    return [{"code": c, "name": names.get(c, c)} for c in codes]


def team_tasks(db: Session, team_ids: Sequence[int],
               tab: str = "", kw: str = "") -> List[dict]:
    """队长视角：本队任务（可按 tab 过滤）。"""
    if not team_ids:
        return []
    q = db.query(BdTask).filter(BdTask.team_id.in_(list(team_ids)))
    if tab in TABS:
        q = q.filter(BdTask.state == tab)
    tasks = q.order_by(BdTask.state.asc(), BdTask.id.asc()).all()
    rows = _rows(db, tasks)
    if kw:
        k = kw.strip()
        rows = [r for r in rows if k in (r["station_name"] or "")]
    return rows


def member_tasks(db: Session, person_code: Optional[str]) -> List[dict]:
    """队员视角（只读）：分给我的任务。"""
    if not person_code:
        return []
    tids = [tid for (tid,) in db.query(BdTaskAssign.task_id)
            .filter(BdTaskAssign.person_code == person_code).all()]
    if not tids:
        return []
    tasks = (db.query(BdTask).filter(BdTask.id.in_(tids))
             .order_by(BdTask.state.asc(), BdTask.id.asc()).all())
    return _rows(db, tasks)


def _has_assignee(db: Session):
    """有担当的任务 id 子查询（tab 判定用，**在 SQL 里**，不能只在当前页算）。"""
    return db.query(BdTaskAssign.task_id)


def _apply_tab(q, db: Session, tab: str):
    """三个 tab 的唯一口径（用户 2026-10-05："任务分为已完成，已分配，未分配"）。

    按**有没有人管**分（不是按 state）—— 因为"派给了团队但还没分到人"在 state 上
    仍是 `unassigned`，可对管理员来说那是**已经派下去了**（515 个历史任务全属这类）：
    - `done`       已完成（pct=100）
    - `assigned`   已分配 = 未完成 且（有队伍 或 有担当 或 有进度）
    - `unassigned` 未分配 = 未完成 且 无队伍 且 无担当 且 无进度
    """
    if tab == TAB_DONE:
        return q.filter(BdTask.state == STATE_DONE)
    if tab in (TAB_DOING, TAB_ASSIGNED):       # 已分配
        return q.filter(BdTask.state != STATE_DONE,
                        or_(BdTask.team_id.isnot(None), BdTask.pct > 0,
                            BdTask.id.in_(_has_assignee(db))))
    if tab == TAB_UNASSIGNED:
        return q.filter(BdTask.state != STATE_DONE, BdTask.team_id.is_(None),
                        BdTask.pct == 0, ~BdTask.id.in_(_has_assignee(db)))
    return q


def tab_counts(db: Session, team_id: Optional[int] = None,
               line_id: Optional[int] = None, date_from=None, date_to=None,
               kw: str = "") -> dict:
    """三个 tab 的数字（**只受其它筛选影响，不受 tab 自己影响**）。"""
    out = {}
    for tab in BOARD_TABS:
        q = _base_query(db, team_id=team_id, line_id=line_id,
                        date_from=date_from, date_to=date_to, kw=kw)
        out[tab] = _apply_tab(q, db, tab).count()
    out["all"] = sum(out.values())
    return out


def _base_query(db: Session, team_id: Optional[int] = None,
                line_id: Optional[int] = None, date_from=None, date_to=None,
                kw: str = ""):
    """任务总表的基础筛选（tab 之外的公共条件）。"""
    q = db.query(BdTask)
    if team_id:
        q = q.filter(BdTask.team_id == team_id)
    if line_id:
        # ⚠️ 任务可能挂在 place（新）或 station（老）→ 两边都要认。
        #    一个跨线站会出现在它经过的**每条线**的筛选结果里（这是对的）。
        sub = (db.query(BdStation.id)
               .filter(BdStation.line_id == line_id,
                       or_(BdStation.id == BdTask.station_id,
                           BdStation.place_id == BdTask.place_id)))
        q = q.filter(sub.exists())
    if date_from is not None:
        q = q.filter(BdTask.assign_date.isnot(None), BdTask.assign_date >= date_from)
    if date_to is not None:
        q = q.filter(BdTask.assign_date.isnot(None), BdTask.assign_date <= date_to)
    if kw:
        # ⚠️ 必须在 SQL 里过滤：任务多 + 分页，只在当前页过滤等于"搜不到"
        # ⚠️ 同样要认中文输入（京叶线/涩谷…），见 app/services/bd_cjk.py
        from app.models import BdLine
        from app.services import bd_cjk
        klike = []
        zh_ids = []
        for v in bd_cjk.search_variants(kw):
            klike.append("%%%s%%" % v)
            zh_ids += bd_cjk.zh_line_ids(db, v)
        sub = (db.query(BdTask.id)
               .outerjoin(BdStation, BdStation.id == BdTask.station_id)
               .outerjoin(BdStationPlace, BdStationPlace.id == BdTask.place_id)
               .outerjoin(BdTeam, BdTeam.id == BdTask.team_id)
               # ⚠️ BdLine 必须显式 join：只写 BdLine.name.like() 会跟 bd_team 笛卡尔积，
               # 结果是"任何一条线路命中 → 所有任务命中"（2026-10-05 实测：搜"井の頭"返回全部 1405）
               .outerjoin(BdLine, BdLine.id == BdStation.line_id)
               .filter(or_(*[c for k in klike for c in (
                           BdStation.name.like(k), BdStation.line.like(k),
                           BdStationPlace.name.like(k), BdStationPlace.lines_text.like(k),
                           BdLine.name.like(k), BdLine.operator_short.like(k),
                           BdTeam.name.like(k),
                           # ⚠️ place 任务的 `station_id` 是 NULL → 上面的 join 取不到线路/运营商，
                           #    必须再按"这个物理车站被哪些线经过"找一遍（实测：搜 JR東日本 曾经 0 命中）
                           BdTask.place_id.in_(
                               db.query(BdStation.place_id)
                               .join(BdLine, BdLine.id == BdStation.line_id)
                               .filter(BdStation.place_id.isnot(None),
                                       or_(BdLine.name.like(k),
                                           BdLine.operator_short.like(k),
                                           BdLine.operator.like(k)))))]
                           + ([BdStation.line_id.in_(zh_ids)] if zh_ids else []))))
        q = q.filter(BdTask.id.in_(sub))
    return q


def task_board(db: Session, team_id: Optional[int] = None, state: str = "",
               date_from: Optional[date] = None,
               date_to: Optional[date] = None, kw: str = "",
               only_assigned: bool = False, stale_only: bool = False,
               stale_days: int = 2, tab: str = "",
               line_id: Optional[int] = None,
               limit: int = 500, offset: int = 0) -> dict:
    """**管理端任务总表**（tab = 未分配 / 已分配 / 已完成，可按线路过滤）。

    - `date_from/date_to` 按**分配日期**区间过滤（用户要求）
    - `stale_only` = 只看"停滞"（未完成 且 ≥`stale_days` 天没更新，或从没提交）
    """
    q = _base_query(db, team_id=team_id, line_id=line_id,
                    date_from=date_from, date_to=date_to, kw=kw)
    if only_assigned:
        q = q.filter(BdTask.team_id.isnot(None))
    if state in STATES:
        q = q.filter(BdTask.state == state)
    if tab in BOARD_TABS:
        q = _apply_tab(q, db, tab)
    if stale_only:
        from datetime import timedelta as _td
        cutoff = date.today() - _td(days=max(0, stale_days))
        recent = (db.query(BdTaskProgress.task_id)
                  .filter(BdTaskProgress.progress_date >= cutoff))
        q = q.filter(BdTask.state != STATE_DONE, BdTask.id.notin_(recent))
    total = q.count()
    tasks = (q.order_by(BdTask.assign_date.desc().nullslast(),
                        BdTask.id.asc())
             .limit(limit).offset(offset).all())
    rows = _rows(db, tasks)
    if stale_only:
        rows = [r for r in rows if r["stale"]]
    return {"rows": rows, "total": total, "offset": offset, "limit": limit,
            "stale_days": stale_days, "tab": tab, "line_id": line_id}


def team_board_summary(db: Session, stale_days: int = 2) -> List[dict]:
    """**按队汇总**（一次查询后在 Python 聚合）：每队 总数/未分配/进行中/已完成/停滞。

    "停滞" = 未完成 且（从没提交 或 超过 `stale_days` 天没更新）——
    管理端一眼看出"哪个队没动"（没有分母时这是最有用的抓手）。
    """
    tasks = db.query(BdTask.id, BdTask.team_id, BdTask.state,
                     BdTask.assign_date).all()
    last = latest_progress(db, [t[0] for t in tasks])
    teams = {t.id: t.name for t in db.query(BdTeam).all()}
    today = date.today()
    agg: Dict[int, dict] = {}
    for tid, team_id, state, assign_date in tasks:
        if team_id is None:
            continue
        a = agg.setdefault(team_id, {"team_id": team_id,
                                     "team_name": teams.get(team_id, ""),
                                     "total": 0, "unassigned": 0, "doing": 0,
                                     "done": 0, "stale": 0})
        a["total"] += 1
        a[state if state in ("unassigned", "doing", "done") else "unassigned"] += 1
        lp = last.get(tid)
        base = (lp.progress_date if lp else assign_date)
        days = (today - base).days if base else None
        if state != STATE_DONE and (lp is None or (days is not None
                                                  and days >= stale_days)):
            a["stale"] += 1
    return sorted(agg.values(), key=lambda x: (-x["stale"], x["team_name"]))


def board_summary(db: Session) -> dict:
    """汇总（一次聚合）：任务总数、各状态数、未派队的任务数、队数。"""
    rows = (db.query(BdTask.state, func.count(BdTask.id))
            .group_by(BdTask.state).all())
    by_state = {s: n for s, n in rows}
    n_task = sum(by_state.values())
    n_no_team = db.query(func.count(BdTask.id)).filter(
        BdTask.team_id.is_(None)).scalar() or 0
    n_station = db.query(func.count(BdStation.id)).scalar() or 0
    return {"n_station": n_station, "n_task": n_task,
            "unassigned": by_state.get(STATE_UNASSIGNED, 0),
            "doing": by_state.get(STATE_DOING, 0),
            "done": by_state.get(STATE_DONE, 0),
            "no_team": n_no_team}


def tasks_xlsx(db: Session, team_id: Optional[int] = None, state: str = "",
               date_from: Optional[date] = None,
               date_to: Optional[date] = None, lang: str = "zh"):
    """导出（**流式写**，不在内存里留整份工作簿）。"""
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    labels = state_labels(lang)
    data = task_board(db, team_id=team_id, state=state, date_from=date_from,
                      date_to=date_to, limit=100000)
    wb = Workbook(write_only=True)
    ws = wb.create_sheet("车站任务")
    head = ["车站", "线路", "团队", "担当", "状态", "进度%",
            "分配日期", "开始日", "完成日", "最后提交", "多少天没动", "备注"]
    ws.append(head)
    for c in range(1, len(head) + 1):
        ws.column_dimensions[get_column_letter(c)].width = 18

    def _d(v):
        return v.strftime("%Y-%m-%d") if v else ""

    for r in data["rows"]:
        ws.append([r["station_name"], r["line"], r["team_name"],
                   "、".join(a["name"] for a in r["assignees"]),
                   labels.get(r["state"], r["state"]), r["pct"],
                   _d(r["assign_date"]), _d(r["start_date"]), _d(r["done_date"]),
                   _d(r["last_date"]),
                   ("" if r["days_since"] is None else r["days_since"]),
                   r["last_note"]])
    buf = BytesIO()
    wb.save(buf)
    buf.seek(0)
    return buf


def station_ids_without_task(db: Session, kw: str = "",
                            status: str = "",
                            line_id: Optional[int] = None) -> List[int]:
    """**所有**「还没有任务」的车站 id（不分页；批量建任务用）。

    分页只影响列表展示，批量操作要作用于**全集** —— 不能只拿当前页。
    """
    sub = db.query(BdTask.id).filter(BdTask.station_id == BdStation.id)
    q = db.query(BdStation.id).filter(~sub.exists())
    if kw:
        like = "%%%s%%" % kw.strip()
        q = q.filter(or_(BdStation.name.like(like), BdStation.line.like(like)))
    if status in ("active", "closed"):
        q = q.filter(BdStation.status == status)
    if line_id:
        q = q.filter(BdStation.line_id == line_id)
    return [i for (i,) in q.order_by(BdStation.id.asc()).all()]
