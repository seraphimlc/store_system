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
#: 「全部」= 不加状态过滤（**不进 tab 栏**，只给"按队汇总 → 该队全部任务"这类入口用）
TAB_ALL = "all"
#: 路由允许的 tab 值（含 all）
BOARD_TABS_ALL = BOARD_TABS + (TAB_ALL,)

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
    """业务日 = **JST**。

    ⚠️ 线上容器是 UTC（deploy 没设 TZ），旧写法 `date.today()` 会在 JST 00:00–08:59
    把进展写到**前一天**那一行（UNIQUE(task_id, progress_date) 直接覆盖），
    而派队/驳回同一时刻走的是 jst_today() → 同一天两套日期（2026-10-06 审计）。
    """
    if today:
        return today
    from app.services.date_plan import jst_today
    return jst_today()


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


def _places_without_team_sub(db: Session):
    """**已经有队伍的物理车站**（子查询：未分配 = 不在这里面的）。"""
    taken = (db.query(BdTask.place_id)
             .filter(BdTask.place_id.isnot(None), BdTask.team_id.isnot(None)))
    legacy = (db.query(BdStation.place_id)
              .join(BdTask, BdTask.station_id == BdStation.id)
              .filter(BdStation.place_id.isnot(None), BdTask.team_id.isnot(None)))
    return taken.union(legacy).subquery()


def line_options(db: Session, tab: str, stale_only: bool = False,
                 team_ids: Optional[Sequence[int]] = None) -> dict:
    """线路下拉的选项与数量（**按 tab 给不同口径**，用户 2026-10-06）。

    - `unassigned`（车站池）：只列**还有未分配车站**的线路，数量 = **未分配车站数**
    - 其它（已分配/已完成/all）：只列**有任务的线路**，数量 = **任务数**

    ⚠️ 只按 tab 算，**不叠加其它筛选**（队伍/关键词/日期）——它是"选线路"的选择器，
    数字要能跟"点进去的结果"对上（叠加筛选的话数字会一直变，看着像坏了）。
    跨线车站会同时算进它经过的每条线（与线路筛选的语义一致）。
    """
    from app.models import BdLine, BdStation, BdStationPlace
    out = []
    if tab == TAB_UNASSIGNED:
        taken = _places_without_team_sub(db)
        rows = (db.query(BdStation.line_id,
                         func.count(func.distinct(BdStation.place_id)))
                .filter(BdStation.line_id.isnot(None),
                        BdStation.place_id.isnot(None),
                        BdStation.place_id.notin_(db.query(taken.c[0])))
                .group_by(BdStation.line_id).all())
        total = list_unassigned_places(db, per=1)["total"]
    else:
        q = _base_query(db, team_id=None, line_id=None, date_from=None,
                        date_to=None, kw="")
        q = _apply_tab(q, db, tab)
        if team_ids:                     # 队长端：只列自己队的线路
            q = q.filter(BdTask.team_id.in_(list(team_ids)))
        # 任务 → 线路：物理车站口径（place→station）与历史口径（task.station_id）都算
        pl = (db.query(BdStation.place_id.label("pid"),
                       BdStation.line_id.label("lid"))
              .filter(BdStation.place_id.isnot(None),
                      BdStation.line_id.isnot(None)).subquery())
        st = (db.query(BdStation.id.label("sid"), BdStation.line_id.label("lid"))
              .filter(BdStation.line_id.isnot(None)).subquery())
        acc = {}
        for lid, n in (q.join(pl, pl.c.pid == BdTask.place_id)
                       .with_entities(pl.c.lid, func.count()).group_by(pl.c.lid).all()):
            acc[lid] = acc.get(lid, 0) + n
        for lid, n in (q.join(st, st.c.sid == BdTask.station_id)
                       .with_entities(st.c.lid, func.count()).group_by(st.c.lid).all()):
            acc[lid] = acc.get(lid, 0) + n
        rows = list(acc.items())
        total = q.count()
    names = {l.id: (l.operator_short, l.name)
             for l in db.query(BdLine).filter(BdLine.id.in_(
                 [lid for lid, _ in rows] or [0])).all()}
    for lid, n in rows:
        op, nm = names.get(lid, ("", ""))
        out.append({"id": lid, "name": nm, "operator_short": op, "n": n,
                    "label": ("%s %s" % (op, nm)).strip()})
    out.sort(key=lambda x: (-x["n"], x["label"]))
    return {"total": total, "rows": out}


def _place_kw_subquery(db: Session, kw: str):
    """关键词命中的**物理车站 id**（站名 / 经过线路文本 / 线路名 / 运营商 / 中文线路名）。

    ⚠️ 用户口径：管理员会按运营商搜（"JR"、"東武"）。旧实现只匹配站名与 lines_text
    → 搜「JR」得 0 个未分配车站，而 SQL 同口径实际有 355 个（2026-10-06 审计）。
    """
    from app.models import BdLine
    from app.services import bd_cjk
    conds, subs = [], []
    for v in bd_cjk.search_variants(kw):
        k = "%%%s%%" % v
        conds += [BdStationPlace.name.like(k), BdStationPlace.lines_text.like(k)]
        # 空格不敏感 + "运营商+线名"拼接（用户会输"京急本線"，而库里存的是 operator_short
        # ="京急" + name="本線"，显示成"京急 本線"）→ 三边都去空格再比
        v_ns = v.replace(" ", "").replace("　", "")
        if v_ns:
            kns = "%%%s%%" % v_ns
            conds.append(func.replace(BdStationPlace.lines_text, " ", "").like(kns))
            subs.append(func.replace(BdLine.name, " ", "").like(kns))
            subs.append(func.replace(
                func.coalesce(BdLine.operator_short, "") + BdLine.name,
                " ", "").like(kns))
        # ⚠️ 用户 2026-10-06："运营商的搜索我们不需要，按线路就行" → 不再匹配 operator
        subs += [BdLine.name.like(k), BdStation.line.like(k)]
        zh = bd_cjk.zh_line_ids(db, v)
        if zh:
            conds.append(BdStationPlace.id.in_(
                db.query(BdStation.place_id)
                .filter(BdStation.line_id.in_(list(zh)))))
    q1 = db.query(BdStationPlace.id).filter(or_(*conds)) if conds else None
    q2 = (db.query(BdStation.place_id)
          .join(BdLine, BdLine.id == BdStation.line_id)
          .filter(BdStation.place_id.isnot(None), or_(*subs))) if subs else None
    if q1 is not None and q2 is not None:
        return q1.union(q2)
    return q1 if q1 is not None else q2


def list_unassigned_places(db: Session, page: int = 1, per: Optional[int] = None,
                           kw: str = "", line_id: Optional[int] = None,
                           all_rows: bool = False) -> dict:
    """**未分配的车站池**（建任务页的正式形态，用户 2026-10-06 口径）。

    "未分配" = 该**物理车站还没有队伍**（没建过任务 / 建了任务但没派队都算）——
    所以在这一栏勾站 + 选队 + 按「分配」，就等于"建任务 + 派队"一步完成。
    """
    from app.models import BdLine, BdStationPlace
    from app.services import bd_cjk, paging as _pg
    taken = _places_without_team_sub(db)
    q = db.query(BdStationPlace).filter(
        BdStationPlace.id.notin_(db.query(taken.c[0])),
        BdStationPlace.status == "active")
    if line_id:
        q = q.filter(BdStationPlace.id.in_(
            db.query(BdStation.place_id).filter(BdStation.line_id == line_id)))
    if kw:
        sub = _place_kw_subquery(db, kw)
        if sub is not None:
            q = q.filter(BdStationPlace.id.in_(sub))
    if all_rows:
        places = q.order_by(BdStationPlace.name.asc()).all()
        pg = _pg.Pager({"rows": places, "total": len(places), "page": 1,
                        "per": max(1, len(places)), "pages": 1, "start": 1,
                        "end": len(places), "has_prev": False, "has_next": False,
                        "prev_page": 1, "next_page": 1})
    else:
        # 选了线路 → 按该线的沿線顺序排；否则按站名
        if line_id:
            seq_map = {pid: (sq, km) for pid, sq, km in db.query(
                BdStation.place_id, BdStation.seq, BdStation.along_km)
                .filter(BdStation.line_id == line_id,
                        BdStation.place_id.isnot(None)).all()}
            places = q.all()
            places.sort(key=lambda x: ((seq_map.get(x.id, (None, None))[0] is None),
                                       seq_map.get(x.id, (10 ** 9, 0))[0],
                                       x.name))
            total = len(places)
            per_n = max(1, min(int(per or _pg.PER_DEFAULT), _pg.PER_MAX))
            pages = max(1, (total + per_n - 1) // per_n)
            p = max(1, min(int(page or 1), pages))
            pg = _pg.Pager({"rows": places[(p - 1) * per_n:p * per_n], "total": total,
                            "page": p, "per": per_n, "pages": pages,
                            "start": (p - 1) * per_n + 1,
                            "end": min(p * per_n, total), "has_prev": p > 1,
                            "has_next": p < pages, "prev_page": max(1, p - 1),
                            "next_page": min(pages, p + 1)})
        else:
            pg = _pg.paginate(q.order_by(BdStationPlace.name.asc()), page,
                              per or _pg.PER_DEFAULT)
    rows = []
    line_names = {l.id: ("%s %s" % (l.operator_short, l.name))
                  for l in db.query(BdLine).all()}
    for pl in pg["rows"]:
        st = (db.query(BdStation)
              .filter(BdStation.place_id == pl.id,
                      BdStation.line_id == line_id if line_id else True)
              .order_by(BdStation.seq.is_(None), BdStation.seq.asc()).first())
        rows.append({"place": pl, "name": pl.name,
                     "lines_text": pl.lines_text, "n_line": pl.n_line,
                     "line_label": (line_names.get(st.line_id, st.line or "")
                                    if st else ""),
                     "seq": (st.seq if st else None),
                     "along_km": (st.along_km if st else None),
                     "has_task": bool(db.query(BdTask.id)
                                      .filter(BdTask.place_id == pl.id).first())})
    pg["rows"] = rows
    return pg


def assign_team_to_places(db: Session, place_ids: Sequence[int],
                          team_id: Optional[int], by: str = "",
                          actor_user=None) -> dict:
    """**给车站派队**（未分配 tab 的动作）：没任务的**建任务**，然后把队伍写上。

    用户 2026-10-06："未分配…我可以选择一些车站，直接做分配" —— 这就是"建任务 + 派队"合并的一步。
    """
    from app.services import bd_log
    ids = [int(x) for x in dict.fromkeys(place_ids or [])]
    if not ids:
        raise TaskError("请先勾选车站")
    if team_id is None:
        raise TaskError("请选择要派给的队伍")
    if db.get(BdTeam, team_id) is None:
        raise TaskError("团队不存在")
    team_name = db.get(BdTeam, team_id).name
    # ① 没任务的先建（跳过判定用两种口径，避免同一车站两个任务）
    created = create_tasks_for_places(db, ids, by=by, team_id=None,
                                      actor_user=actor_user)["created"]
    # ② 把队伍写上（含本次新建的与原先"建了任务没派队"的）
    task_ids = []
    for t in (db.query(BdTask)
              .filter(or_(BdTask.place_id.in_(ids),
                          BdTask.station_id.in_(
                              db.query(BdStation.id).filter(
                                  BdStation.place_id.in_(ids))))).all()):
        task_ids.append(t.id)
    # ⚠️ 分配日期默认今天（用户："日期不用管，有个分配日期就行"）——
    #    不传的话"先建任务、后派队"的车站会留下空分配日期（测试实测抓到）
    from app.services.date_plan import jst_today
    res = set_task_team(db, task_ids, team_id, assign_date=jst_today(),
                        by=by, actor_user=actor_user)
    db.commit()
    return {"created": created, "assigned": len(task_ids),
            "team_name": team_name, "cleared": res.get("cleared", 0)}


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
                  by: str = "", actor_user=None, action: str = "dispatch") -> dict:
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
            bd_log.log_op(db, actor_user, "task", action, ref_id=t.id,
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


def transfer_team_task(db: Session, user, task_ids: Sequence[int],
                       to_team_id: int, by: str = "",
                       actor_user=None) -> dict:
    """**队长把本队任务转给别的队**（用户 2026-10-06："队长之间可以私下交换任务"）。

    口径（用户当场选定）：
    - **不用对方确认，直接过去**；转完给对方队长发站内消息
    - **未分配 + 进行中都能转**；**已完成不能转**（那是该队已经干出来的业绩）
    - 转出**移出原担当**（沿用 `set_task_team` 的换队口径），**已上报进度保留**
      → 原担当也发消息（不然他手上的活凭空没了）
    - 只能转**自己是队长**的那个队的任务；目标队必须存在且不是本队

    想"互换"就各自转一条（单向两次 = 互换）。
    """
    from app.services import bd_msg, bd_teams
    to_team = db.get(BdTeam, int(to_team_id or 0))
    if to_team is None:
        raise TaskError("目标队伍不存在")
    pc = getattr(user, "person_code", None)
    picked, removed, done_skipped = [], [], 0
    for raw in task_ids or []:
        try:
            t = db.get(BdTask, int(raw))
        except (TypeError, ValueError):
            continue
        if t is None or not t.team_id or t.team_id == to_team.id:
            continue
        if t.state == STATE_DONE:
            done_skipped += 1                 # 已完成不转（用户口径）
            continue
        if not bd_teams.is_leader_of(db, pc, t.team_id):
            raise TaskError("只能转自己担任队长的那个队的任务")
        picked.append(t)
        for a in (db.query(BdTaskAssign)
                  .filter(BdTaskAssign.task_id == t.id).all()):
            removed.append(a.person_code)
    if not picked:
        raise TaskError("已完成的任务不能转" if done_skipped else "请先勾选要转的任务")
    labels = [_station_name(db, t) for t in picked]
    old_team = db.get(BdTeam, picked[0].team_id)
    old_name = (old_team.name if old_team else "（未派队）")
    r = set_task_team(db, [t.id for t in picked], to_team.id, by=by,
                      actor_user=actor_user, action="transfer")
    head = "、".join(labels[:3]) + ("…" if len(labels) > 3 else "")

    def _to_leaders(lg):
        if lg == "ja":
            return ("%d件のタスクが%sから回ってきました：%s"
                    % (len(labels), old_name, head),
                    "未割当・進行中のタスクです。担当を割り当ててください。")
        return ("从%s转来 %d 个任务：%s" % (old_name, len(labels), head),
                "都是未分配/进行中的任务，派工即可。")

    def _to_removed(lg):
        if lg == "ja":
            return ("担当していたタスクが他チームへ移りました",
                    "%s（%sへ）" % (head, to_team.name))
        return ("你担当的任务被转给别的队了",
                "%s（转给 %s）" % (head, to_team.name))

    bd_msg.send_localized(db, actor_user, bd_teams.leader_codes(db, to_team.id),
                          _to_leaders, url="/my/tasks?tab=unassigned",
                          scope="task_review")
    if removed:
        bd_msg.send_localized(db, actor_user, sorted(set(removed)),
                              _to_removed, url="/my/tasks",
                              scope="task_review")
    return {"transferred": len(picked), "cleared": r.get("cleared", 0),
            "removed_people": sorted(set(removed)), "labels": labels,
            "to_team": to_team.name, "from_team": old_name}


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
    # 当天派工日期（用户 2026-10-06："队长每天给队员派当天的任务"；
    # 昨天没做完的**自动延续**，不新增行 —— 靠查询口径）
    from app.services.date_plan import jst_today as _jst
    _d = on_date or _jst()
    for c in codes:
        db.add(BdTaskAssign(task_id=task_id, person_code=c,
                            assigned_by=(by or ""), dispatch_date=_d))
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
                  actor_user=None, confirm: bool = False,
                  store_count: Optional[int] = None,
                  require_store_count: bool = False) -> dict:
    """**每日进展上报/调整**：写/改当天一条，并把任务刷新为最新进度。

    - `pct` 夹到 0–100
    - **店铺数**（用户 2026-10-06）：填到 100% 时必填（**允许 0**，空着不行）
      `require_store_count=True` 时强制；队长批量补录传 False（允许留空）
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
    # ---- 两条新规则（用户 2026-10-06）：写入时强校验，别只靠界面藏按钮 ----
    from app.services import bd_perm as _perm
    _is_lead = _is_team_leader(db, actor_user, t.team_id) if actor_user else False
    if actor_user is not None and staff_report and not _is_lead \
            and is_locked_for_staff(row):
        raise TaskError("这条今天的进展队长已确认，不能再改（如需修改请联系队长或管理员）")
    if actor_user is not None and not staff_report and not _is_lead \
            and is_admin(actor_user) and not admin_may_adjust(row):
        raise TaskError("这条还没有队长确认，管理员暂不能修改")
    if confirm and row is not None and row.reported_pct is not None:
        p = int(row.reported_pct)             # 「确认」= 认可员工上报的原值
    # ---- 店铺数（用户 2026-10-06）：报 100% 时必填，**允许 0**，空着不行 ----
    sc = None
    if store_count is not None and str(store_count).strip() != "":
        try:
            sc = int(store_count)
        except (TypeError, ValueError):
            raise TaskError("店铺数要填 0 或正整数")
        if sc < 0:
            raise TaskError("店铺数不能是负数")
    # ⚠️ 判定按**身份**而不是按入口：`staff_report` = 本人是担当且在自报（不是队长/管理员在调整）
    #    → 队员从任何入口（一页自报 / 单条滑块）报到 100% 都必填；
    #      队长批量补录、管理员调整仍可选（用户 2026-10-06 口径）
    if (require_store_count or staff_report) and p >= 100 and sc is None:
        raise TaskError("任务做到 100% 时要填这家车站的店铺数量（可以填 0）")
    _prev_pct = None                  # 旧值快照（判"同值"用；row 为空时保持 None）
    if row is None:
        row = BdTaskProgress(task_id=task_id, progress_date=d, pct=p,
                             note=(note or "").strip(),
                             submitted_by=(by or ""))
        db.add(row)
        db.flush()
    else:
        _prev_pct = row.pct               # ⚠️ 先快照旧值：下面 row.pct 会被覆盖
        row.pct = p
        if (note or "").strip():
            row.note = note.strip()      # 只有真填了才覆盖（别把已有备注清掉）
        # ⚠️ `submitted_by` 只在**员工上报**时更新；队长确认/调整只写 `reviewed_by`，
        #    否则「谁上报的」会被操作人覆盖（2026-10-06 浏览器试跑实测：确认后变成队长）
        if staff_report:
            row.submitted_by = (by or "")
        row.updated_at = datetime.utcnow()
    notified = None
    review_action = ""
    orig_reported = row.reported_pct
    if staff_report:
        # 员工上报 → 记录原值，等队长确认。
        # ⚠️ **同值重复提交不重开审核**：队长确认/调整（或驳回）之后，滑块的预置值就是当前值，
        #    员工手滑再点一次会把队长的处理打回 pending、备注也被清空（2026-10-06 审计）
        # "同值" = 与**员工原值**相同，或与**当前生效值**相同
        # （队长调整后滑块预置的是当前值，员工手滑再点一次不能把调整打回 pending）
        # 什么算"同值不用重开审核"（避免手滑把队长的结论打回）：
        #   队长**已经定了值**（confirmed/adjusted）且员工再报的就是这个值。
        # ⚠️ 不拿 `reported_pct` 比：驳回后员工重报**原值**时会永远停在 rejected
        #   → 队长再也确认不了（2026-10-06 端到端实测踩到）。
        # ⚠️ 必须用**旧值**快照：`row.pct` 上面已被赋成 p，拿它比恒为真。
        _same = bool(row.review_status in ("confirmed", "adjusted")
                     and _prev_pct is not None and int(_prev_pct) == p)
        if not _same:
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
    if sc is not None and t.store_count != sc:
        bd_log.log_op(db, actor_user, "task", "update", ref_id=t.id,
                      ref_label=_station_name(db, t), field="store_count",
                      old=("" if t.store_count is None else str(t.store_count)),
                      new=str(sc))
        t.store_count = sc
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
            note=(note or ""), station=_station_name(db, t),
            # 报给"真正上报的人"（回收/改派后他可能已不是担当）
            reporter=((row.reported_by or row.submitted_by or "") if row is not None else ""))
    return {"pct": p, "state": t.state, "date": d,
            "start_date": t.start_date, "done_date": t.done_date,
            "review_status": row.review_status,
            "reported_pct": row.reported_pct,
            # 店铺数（完成时填；已在库里就回显，2026-10-06）
            "store_count": t.store_count,
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


def day_progress_map(db: Session, task_ids: Sequence[int],
                     on_date: Optional[date] = None) -> Dict[int, "BdTaskProgress"]:
    """**那一天**的进展行（task_id → row）。用于"确认后锁定"与管理员前置校验。"""
    ids = [i for i in (task_ids or [])]
    if not ids:
        return {}
    d = on_date or _today()
    rows = (db.query(BdTaskProgress)
            .filter(BdTaskProgress.task_id.in_(ids),
                    BdTaskProgress.progress_date == d).all())
    return {r.task_id: r for r in rows}


def is_locked_for_staff(row) -> bool:
    """队员能不能改这一天：**队长已确认** → 锁（用户 2026-10-06 口径）。"""
    return bool(row is not None and row.review_status == "confirmed")


def admin_may_adjust(row) -> bool:
    """管理员能不能改这一天：**该天已被队长处理过**（确认/调整/驳回）才能改。"""
    return bool(row is not None
                and row.review_status in ("confirmed", "adjusted", "rejected"))


def can_report(db: Session, user, task: BdTask,
               on_date: Optional[date] = None) -> bool:
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
    mine = is_assignee(db, user, task)
    if mine:
        # ⚠️ 队长的**确认**只锁那一天（进度+备注）；第二天任务没完可以继续报
        row = day_progress_map(db, [task.id], on_date).get(task.id)
        if is_locked_for_staff(row):
            return False
    return _is_team_leader(db, user, task.team_id) or mine


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


def can_reject(db: Session, user, task: BdTask) -> bool:
    """**驳回**权限（用户 2026-10-06 口径）：

    - 队员报了 100% → **该任务的队长**可以驳回（此时员工上报还没被处理，`pending`）
    - 队长确认之后 → **管理员**可以驳回（队长自己不能再改回，避免"自己确认自己驳回"）
    - 员工不能驳回
    """
    if user is None or task is None:
        return False
    if is_admin(user):
        return True
    if task.pct != 100:
        return False                      # 只有"已完成"才谈得上驳回
    if not _is_team_leader(db, user, task.team_id):
        return False
    row = latest_progress(db, [task.id]).get(task.id)
    return bool(row is not None and row.reported_pct == 100
                and row.review_status == "pending")


def reject_progress(db: Session, task_id: int, pct: int, note: str = "",
                    by: str = "", actor_user=None) -> dict:
    """**驳回**：把已完成的 100% 退回成不到 100%（用户 2026-10-06 口径）。

    - 只对 `pct == 100` 的任务有效；新进度**必须 < 100**
    - 写**今天**这条进展（当天已有则覆盖），`review_status='rejected'`，
      **员工上报的原值 `reported_pct` 保留**（能展示"队员报 100% → 被驳回改 80%"）
    - **回退清完成日**（与"进度回退"的既有口径一致），状态回到进行中
    - 写日志 + **给担当发消息**（含原值 → 新值与操作人）
    """
    from app.services import bd_log, bd_msg
    from app.services.date_plan import jst_today
    t = db.get(BdTask, task_id)
    if t is None:
        raise TaskError("任务不存在")
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    if t.pct != 100:
        raise TaskError("只能驳回已完成的（100%）任务")
    pct = int(pct)
    if not (0 <= pct < 100):
        raise TaskError("驳回后的进度必须在 0–99% 之间（不能还是 100%）")
    d = jst_today()
    row = (db.query(BdTaskProgress)
           .filter(BdTaskProgress.task_id == task_id,
                   BdTaskProgress.progress_date == d).first())
    if row is None:
        row = BdTaskProgress(task_id=task_id, progress_date=d)
        db.add(row)
    # ⚠️ 驳回**不是**员工重新上报：原值/原上报人必须留住，否则记录会挂到操作人名下
    #    （旧代码写 `row.by_user` —— BdTaskProgress 根本没这个列，静默丢失；
    #     还把驳回理由塞进 `note`，覆盖掉员工自己写的备注 —— 2026-10-06 审计）
    if row.reported_pct is None:
        row.reported_pct = t.pct
    if not (row.reported_by or "").strip():
        row.reported_by = row.submitted_by or ""
    orig = row.reported_pct if row.reported_pct is not None else t.pct
    row.pct = pct
    row.review_status = "rejected"
    row.reviewed_by = (by or "")
    row.reviewed_at = datetime.utcnow()
    row.review_note = (note or "").strip()
    row.updated_at = datetime.utcnow()
    t.pct = pct
    t.done_date = None                    # ⚠️ 回退清完成日
    refresh_state(db, t)
    bd_log.log_op(db, actor_user, "task", "reject", ref_id=t.id,
                  ref_label=_station_name(db, t), field="进度",
                  old="100%%", new="%d%%" % pct, note=(note or ""))
    notified = None
    if orig is not None and orig != pct:
        notified = bd_msg.notify_task_progress(
            db, t, "rejected", orig, pct, by_user=actor_user,
            note=(note or ""), station=_station_name(db, t),
            reporter=(row.reported_by or row.submitted_by or ""))
    db.commit()
    return {"pct": pct, "state": t.state, "done_date": t.done_date,
            "reported_pct": orig, "notified": bool(notified)}



#: 「队长已处理过」的两种结论（确认 / 调整）—— 管理员批量驳回针对它们（用户 2026-10-07）
REVIEW_SETTLED = ("confirmed", "adjusted")

#: 审核状态的中文说法（日志/跳过原因/页面上统一用这一份）
REVIEW_LABELS = {"pending": "待确认", "confirmed": "已确认",
                 "adjusted": "已调整", "rejected": "已驳回", "": "未上报"}


def review_label(status: Optional[str]) -> str:
    s = (status or "").strip()
    return REVIEW_LABELS.get(s, s or "—")


def settled_reviews(db: Session, *, date_from: Optional[date] = None,
                    date_to: Optional[date] = None,
                    team_id: Optional[int] = None,
                    limit: int = 500) -> dict:
    """**队长已确认/调整过的进展**清单（管理员批量驳回用，用户 2026-10-07）。

    行 = 一条 `bd_task_progress`（队长处理过的），**不是任务** —— 管理员要驳回的对象是
    "这个队长确认了这条进展"，不是"这个任务"。

    列表 SQL **常数条**（铁律 5）：任务/队/站点名/人名各一次批量查询，循环里不查库。
    """
    q = (db.query(BdTaskProgress, BdTask)
         .join(BdTask, BdTask.id == BdTaskProgress.task_id)
         .filter(BdTaskProgress.review_status.in_(REVIEW_SETTLED)))
    if date_from is not None:
        q = q.filter(BdTaskProgress.progress_date >= date_from)
    if date_to is not None:
        q = q.filter(BdTaskProgress.progress_date <= date_to)
    if team_id:
        q = q.filter(BdTask.team_id == int(team_id))
    rows = (q.order_by(BdTaskProgress.progress_date.desc(),
                       BdTaskProgress.id.desc()).limit(limit + 1).all())
    more = len(rows) > limit
    rows = rows[:limit]
    if not rows:
        return {"rows": [], "more": False, "limit": limit,
                "n_confirmed": 0, "n_adjusted": 0, "n_no_origin": 0}

    pids = [t.place_id for _pr, t in rows if t.place_id]
    sids = [t.station_id for _pr, t in rows if t.station_id]
    from app.models import BdLine, User
    team_names = {t.id: t.name for t in db.query(BdTeam).all()}
    place_names = ({p.id: p.name for p in db.query(BdStationPlace)
                    .filter(BdStationPlace.id.in_(pids)).all()} if pids else {})
    st_rows = (db.query(BdStation).filter(BdStation.id.in_(sids)).all()
               if sids else [])
    st_names = {s.id: s.name for s in st_rows}
    line_names = {l.id: l.name for l in db.query(BdLine).all()}
    st_lines = {s.id: line_names.get(s.line_id, "") for s in st_rows}
    unames = {x for _pr, _t in rows for x in
              ((_pr.reported_by or "").strip(), (_pr.reviewed_by or "").strip()) if x}
    disp = ({u.username: (u.display_name or u.username)
             for u in db.query(User).filter(User.username.in_(unames)).all()}
            if unames else {})

    out = []
    n_conf = n_adj = n_no = 0
    for pr, t in rows:
        can_back = pr.reported_pct is not None
        if pr.review_status == "confirmed":
            n_conf += 1
        else:
            n_adj += 1
        if not can_back:
            n_no += 1
        out.append({
            "progress_id": pr.id, "task_id": t.id, "date": pr.progress_date,
            "station": place_names.get(t.place_id) or st_names.get(t.station_id, ""),
            "line": st_lines.get(t.station_id, ""),
            "team_id": t.team_id, "team_name": team_names.get(t.team_id, ""),
            "status": pr.review_status, "status_label": review_label(pr.review_status),
            "reported_pct": pr.reported_pct, "pct": pr.pct, "can_back": can_back,
            "reported_by": disp.get((pr.reported_by or "").strip(),
                                    pr.reported_by or ""),
            "reviewer": disp.get((pr.reviewed_by or "").strip(), pr.reviewed_by or ""),
            "task_pct": t.pct, "task_state": t.state,
        })
    return {"rows": out, "more": more, "limit": limit,
            "n_confirmed": n_conf, "n_adjusted": n_adj, "n_no_origin": n_no}


def reject_reviews_many(db: Session, progress_ids: Sequence[int], *, by: str = "",
                        actor_user=None, force_pct=None, note: str = "") -> dict:
    """**批量驳回队长已确认/调整的进展**（管理员；用户 2026-10-07 口径）。

    - 目标行：`review_status in ('confirmed','adjusted')`，其余**跳过并给原因**
    - 目标值：**退回员工上报原值 `reported_pct`**；`force_pct` 给了就统一用它
      （⚠️ 专门用来把"已完成 100%"打回进行中 —— 员工原值也是 100% 时靠它才退得回来）
    - 目标状态：`pending`（回到"待确认"，队长重新看到它；**不再锁员工**）
    - 任务侧：**只有当这条是该任务最新一天的那条**时才改 `bd_task.pct/state`
      （更早那几天不是"当前生效值"，动它会污染今天的实际进度）
    - 每条一个 savepoint（一条坏不影响其它）+ 每条写日志 + 通知
    """
    from app.services import bd_log, bd_msg, bd_teams
    if actor_user is not None:
        by = getattr(actor_user, "username", "") or by
    if force_pct is not None and str(force_pct).strip() != "":
        try:
            force_pct = int(force_pct)
        except (TypeError, ValueError):
            raise TaskError("统一退回的进度要是 0–100 的整数")
        if not (0 <= force_pct <= 100):
            raise TaskError("统一退回的进度要在 0–100 之间")
    else:
        force_pct = None

    ids = [int(x) for x in dict.fromkeys(progress_ids or [])]
    # 「每个任务最新一天」预取（一条 SQL；避免逐行查库 —— 铁律 5）
    latest_date: Dict[int, date] = {}
    if ids:
        tids = [tid for (tid,) in db.query(BdTaskProgress.task_id)
                .filter(BdTaskProgress.id.in_(ids)).all() if tid]
        if tids:
            for tid, d in (db.query(BdTaskProgress.task_id,
                                    func.max(BdTaskProgress.progress_date))
                           .filter(BdTaskProgress.task_id.in_(tids))
                           .group_by(BdTaskProgress.task_id).all()):
                latest_date[tid] = d

    updated, skipped = 0, []
    done = []                                     # [(pr, t, old_status, old_pct, target)]
    by_team: Dict[int, int] = {}
    for pid in ids:
        pr = db.get(BdTaskProgress, pid)
        if pr is None:
            skipped.append({"id": pid, "why": "进展记录不存在"})
            continue
        if pr.review_status not in REVIEW_SETTLED:
            skipped.append({"id": pid, "why": "还没被队长处理过（当前：%s）"
                            % review_label(pr.review_status)})
            continue
        target = force_pct if force_pct is not None else pr.reported_pct
        if target is None:
            skipped.append({"id": pid,
                            "why": "队长直接填的、没有员工上报原值；"
                                   "要退请填「统一退回到」的百分比"})
            continue
        t = db.get(BdTask, pr.task_id)
        if t is None:
            skipped.append({"id": pid, "why": "任务不存在"})
            continue
        try:
            with db.begin_nested():               # savepoint：这条失败只回滚这条
                old_status, old_pct = pr.review_status, pr.pct
                pr.pct = int(target)
                pr.review_status = "pending"      # 回到待确认（队长重新看）
                pr.reviewed_by = ""
                pr.reviewed_at = None
                pr.review_note = ""
                pr.updated_at = datetime.utcnow()
                if latest_date.get(t.id) == pr.progress_date:
                    t.pct = int(target)
                    if int(target) < 100:
                        t.done_date = None        # 回退清完成日（与既有口径一致）
                    refresh_state(db, t)
                    db.flush()
                bd_log.log_op(db, actor_user, "task", "reject", ref_id=t.id,
                              ref_label=_station_name(db, t), field="进展确认",
                              old="%s %s%%" % (review_label(old_status),
                                               "—" if old_pct is None else old_pct),
                              new="退回待确认 %d%%" % int(target),
                              note=(note or ""))
            updated += 1
            done.append((pr, t, old_status, old_pct, int(target)))
            if t.team_id:
                by_team[t.team_id] = by_team.get(t.team_id, 0) + 1
        except Exception as e:                    # noqa: BLE001
            skipped.append({"id": pid, "why": str(e)[:80]})
    db.flush()

    # ---- 通知：队员各自一条（参照"驳回"的通知口径：回值 + 说明）----
    notified = 0
    for pr, t, _old_status, old_pct, target in done:
        try:
            r = bd_msg.notify_task_progress(
                db, t, "unconfirmed", old_pct, target, by_user=actor_user,
                note=(note or ""), station=_station_name(db, t),
                reporter=(pr.reported_by or pr.submitted_by or ""))
            notified += 1 if r is not None else 0
        except Exception:                         # noqa: BLE001
            pass                                  # 通知失败不能把驳回回滚掉
    # ---- 涉及的队长各一条汇总（不逐条轰炸）----
    summaries = 0
    for tid, n in sorted(by_team.items()):
        try:
            codes = bd_teams.leader_codes(db, tid)
            if not codes:
                continue
            bd_msg.send(db, actor_user, codes,
                        _m_summary_title(n),
                        _m_summary_body(db, tid, n, actor_user, note),
                        url="/my/tasks?tab=pending", scope="task_review")
            summaries += 1
        except Exception:                         # noqa: BLE001
            pass
    db.flush()
    return {"updated": updated, "skipped": skipped, "notified": notified,
            "summaries": summaries, "force_pct": force_pct}


def _m_summary_title(n: int) -> str:
    from app.i18n import render_msg as R
    return R("你队的 %d 条进展确认被管理员驳回", n)


def _m_summary_body(db: Session, team_id: int, n: int, actor_user, note: str) -> str:
    from app.i18n import render_msg as R
    team = db.get(BdTeam, team_id)
    who = ((getattr(actor_user, "display_name", "") or
            getattr(actor_user, "username", "")) if actor_user else "") or "管理员"
    tpl = "%s 把 %s 的 %d 条进展确认退回了「待确认」：进度按队员上报的原值计，请你重新确认。%s"
    return R(tpl, who, (team.name if team else "你队"), n, note or "")

def stale_count(db: Session, team_id: Optional[int] = None,
                line_id: Optional[int] = None, date_from=None, date_to=None,
                kw: str = "", stale_days: int = 2) -> int:
    """停滞任务数（**已分到人** + 未完成 + ≥N 天没动）——与列表 `stale` 标记同一口径。"""
    q = _base_query(db, team_id=team_id, line_id=line_id, date_from=date_from,
                    date_to=date_to, kw=kw)
    tasks = q.filter(BdTask.state != STATE_DONE,
                     BdTask.id.in_(_has_assignee(db))).all()
    if not tasks:
        return 0
    last = latest_progress(db, [t.id for t in tasks])
    today = _today()
    n = 0
    for t in tasks:
        lp = last.get(t.id)
        base = (lp.progress_date if lp else t.assign_date)
        days = (today - base).days if base else None
        if lp is None or (days is not None and days >= stale_days):
            n += 1
    return n


def _team_label(db: Session, team_id) -> str:
    """队名（日志里给人看的；队被删了也不报错）。"""
    if not team_id:
        return ""
    t = db.get(BdTeam, team_id)
    return t.name if t else ""


def return_to_pool(db: Session, task_ids: Sequence[int], by: str = "",
                   actor_user=None, force: bool = False) -> dict:
    """把任务**退回车站池**（解除队伍 + 清担当 + 清分配日期）。

    用户 2026-10-06 审计："派队是单向不可逆的"——选错队只能改库。
    ⚠️ 与 `set_task_team` 的"换队清空原担当"同一口径；全部写日志（可追溯）。
    """
    from app.services import bd_log
    n = 0
    skipped: List[dict] = []
    for tid in list(task_ids or []):
        t = db.get(BdTask, tid)
        if t is None:
            continue
        if t.team_id is None:
            skipped.append({"task_id": tid, "why": "还没派给队伍"})
            continue
        n_asg = (db.query(func.count(BdTaskAssign.id))
                 .filter(BdTaskAssign.task_id == tid).scalar() or 0)
        if n_asg and not force:
            # 用户 2026-10-06 口径：管理员撤回的是"**已派队但还没分到人**"（含有进展没分人）
            # 有担当的任务要**队长先回收/改派**，不然等于把队员手上的活静默抽走
            skipped.append({"task_id": tid, "why": "还有担当，请让队长先回收或改派"})
            continue
        old_team = t.team_id
        bd_log.log_op(db, actor_user, "task", "return_pool", ref_id=t.id,
                      ref_label=_station_name(db, t), field="team",
                      old=(_team_label(db, old_team) if old_team else ""),
                      new="（车站池）", note="")
        db.query(BdTaskAssign).filter(BdTaskAssign.task_id == tid).delete()
        t.team_id = None
        t.assign_date = None
        refresh_state(db, t)
        n += 1
    db.flush()
    return {"n": n, "skipped": skipped}


def can_return_pool(db: Session, user, task: BdTask) -> bool:
    """**管理员撤回**（回车站池）能不能点：只针对"已派队但**没分到人**"的任务。

    用户 2026-10-06："对于已经分配到团队，但还没有分配到队员的任务，管理员可以撤回"、
    "对于有进展但还是没有分配队员的任务，管理员也可以撤回并重新分配"。
    有担当的走队长回收/改派（避免把队员手上的活静默抽走）。
    """
    if user is None or task is None or not is_admin(user):
        return False
    if task.team_id is None:
        return False
    n = (db.query(func.count(BdTaskAssign.id))
         .filter(BdTaskAssign.task_id == task.id).scalar() or 0)
    return n == 0


def can_reject_maps(db: Session, user, tasks: Sequence[BdTask],
                    lead_team_ids: Optional[Sequence[int]] = None,
                    rows: Optional[Sequence[dict]] = None) -> Dict[int, dict]:
    """**一页任务的权限一次算完**（`can_report/can_assign/can_adjust/can_reject` 的批量版）。

    ⚠️ 逐条调用那几个 `can_*` 会**每条任务查好几次库** —— 实测队长端"团队任务"tab
    **76 行 = 180+ 条 SQL、70ms+**（用户 2026-10-06："员工端/队长端加载页面太慢"）。
    这里改成：**担当一次查 + 我带的队一次查 + 最新进展一次查 + 能力表按角色缓存**。
    """
    from app.services import bd_perm, bd_teams
    ids = [t.id for t in tasks]
    out = {tid: {"report": False, "assign": False, "adjust": False,
                 "reject": False} for tid in ids}
    if user is None or not ids:
        return out
    # 当天的进展行（"管理员只能改队长处理过的"要按天判）——必须在管理员提前返回**之前**算好
    day = day_progress_map(db, ids)
    if is_admin(user):
        for tid in ids:
            out[tid] = {"report": True, "assign": True,
                        # ⚠️ 管理员也要遵守"队长确认后才能改"（2026-10-06 口径）
                        "adjust": admin_may_adjust(day.get(tid)),
                        "reject": True}
        return out
    cap_report = bd_perm.can(db, user, "task.report")
    cap_assign = bd_perm.can(db, user, "task.assign")
    cap_adjust = bd_perm.can(db, user, "task.adjust")
    code = getattr(user, "person_code", None)
    mine = set()
    if code:
        mine = {tid for (tid,) in db.query(BdTaskAssign.task_id)
                .filter(BdTaskAssign.task_id.in_(ids),
                        BdTaskAssign.person_code == code).all()}
    # 调用方给了就不重复查（同一请求里这两份数据页面已经查过了）
    lead_teams = (set(lead_team_ids) if lead_team_ids is not None
                  else {t.id for t in bd_teams.leader_teams(db, code)})
    by_row = {r["task"].id: r for r in (rows or [])}
    latest = {} if by_row else latest_progress(db, ids)
    # （day 已在上面算好）
    for t in tasks:
        is_lead = (t.team_id in lead_teams)
        d = out[t.id]
        d["locked"] = bool(t.id in mine and is_locked_for_staff(day.get(t.id)))
        d["report"] = bool(cap_report and (is_lead or (t.id in mine and not d["locked"])))
        d["assign"] = bool(cap_assign and is_lead)
        d["adjust"] = bool(is_lead or (cap_adjust and admin_may_adjust(day.get(t.id))))
        if by_row:                      # 用页面已经装好的字段（last_reported_pct/review_status）
            r0 = by_row.get(t.id) or {}
            d["reject"] = bool(t.pct == 100 and is_lead
                               and r0.get("last_reported_pct") == 100
                               and r0.get("review_status") == "pending")
        else:
            row = latest.get(t.id)
            d["reject"] = bool(t.pct == 100 and is_lead and row is not None
                               and row.reported_pct == 100
                               and row.review_status == "pending")
    return out


def set_progress_many(db: Session, task_ids: Sequence[int], pct, note: str = "",
                      by: str = "", actor_user=None) -> dict:
    """**批量设进展 / 批量标记完成**（用户 2026-10-06：513 条存量任务要队长补录）。

    - 逐条走 `save_progress`（权限、锁定、值域、日志、通知全都一致，不另写一套）
    - 每条一个 **savepoint**：某条失败只跳过它，不影响已成功的（批量补录不能"一条坏全废"）
    - 返回 `{"updated": n, "skipped": [{"task_id":…, "why":…}]}`（界面要如实报被跳过的）
    """
    updated = 0
    skipped: List[dict] = []
    for tid in list(task_ids or []):
        t = db.get(BdTask, tid)
        if t is None:
            skipped.append({"task_id": tid, "why": "任务不存在"})
            continue
        if actor_user is not None and not can_report(db, actor_user, t):
            skipped.append({"task_id": tid, "why": "没有权限（不是你队的任务，或已被锁定）"})
            continue
        try:
            with db.begin_nested():               # savepoint：这条失败只回滚这条
                save_progress(db, tid, pct, note, by=by, actor_user=actor_user)
            updated += 1
        except Exception as e:                    # noqa: BLE001
            skipped.append({"task_id": tid, "why": str(e)[:80]})
    db.flush()
    return {"updated": updated, "skipped": skipped}


def pending_review_rows(db: Session, team_ids: Optional[Sequence[int]] = None,
                        on_date: Optional[date] = None) -> List[Tuple[int, date]]:
    """还挂着 `pending` 的进展行 → `[(task_id, progress_date)]`（队长的待确认队列口径）。"""
    q = (db.query(BdTaskProgress.task_id, BdTaskProgress.progress_date)
         .join(BdTask, BdTask.id == BdTaskProgress.task_id)
         .filter(BdTaskProgress.review_status == "pending"))
    if team_ids is not None:
        ids = [int(i) for i in team_ids]
        if not ids:
            return []
        q = q.filter(BdTask.team_id.in_(ids))
    if on_date is not None:
        q = q.filter(BdTaskProgress.progress_date == on_date)
    return [(int(t), d) for (t, d) in q.all()]


def confirm_day(db: Session, user, team_ids: Optional[Sequence[int]] = None,
                on_date: Optional[date] = None) -> int:
    """**一键确认（当天全部）** —— 用户 2026-10-06 口径："队长一键全确认"。

    逐条走 `save_progress(confirm=True)`：审核字段、日志、给队员发消息全都一致，
    不另写一套（避免两条路径口径漂移）。返回真正确认的条数。
    """
    rows = pending_review_rows(db, team_ids, on_date=on_date)
    by = getattr(user, "username", "") or ""
    n = 0
    for tid, d in rows:
        t = db.get(BdTask, tid)
        if t is None or not can_adjust(db, user, t, on_date=d):
            continue                     # 不是他的队 / 没权限 → 跳过，不报错
        save_progress(db, tid, (t.pct or 0), "", by=by, actor_user=user,
                      confirm=True, on_date=d)
        n += 1
    db.flush()
    return n


def can_adjust(db: Session, user, task: BdTask,
               on_date: Optional[date] = None) -> bool:
    """调整（修正）进展：管理员 / 该任务的队长。

    ⚠️ 用户 2026-10-06 口径：**管理员只能改"队长已确认过"的**（未处理的不能抢先改）；
    队长对自己队的任务不受此限。
    """
    from app.services import bd_perm
    if user is None:
        return False
    if _is_team_leader(db, user, task.team_id):
        return True
    if is_admin(user):
        row = day_progress_map(db, [task.id], on_date).get(task.id)
        return admin_may_adjust(row)
    if not bd_perm.can(db, user, "task.adjust"):
        return False
    return False


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
    #: (task_id, person_code) → 派工日（JST）。用来在**队长的队列表**上标"今天派的"
    disp_of: Dict[tuple, object] = {}
    for a in (db.query(BdTaskAssign)
              .filter(BdTaskAssign.task_id.in_(tids))
              .order_by(BdTaskAssign.id.asc()).all()):
        assigns.setdefault(a.task_id, []).append(a.person_code)
        disp_of[(a.task_id, a.person_code)] = a.dispatch_date
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
    today = _today()
    for t in tasks:
        st = stations.get(t.station_id)
        pl = places.get(getattr(t, "place_id", None))
        a_codes = assigns.get(t.id, [])
        lp = last.get(t.id)
        last_d = (lp.progress_date if lp else None)
        # "多少天没动"：从最后一次提交算；从没提交过则按分配日期算
        base = last_d or t.assign_date
        days = (today - base).days if base else None
        # 停滞 = **已分到人** + 还没完成 +（从没提交 或 超过 2 天没更新）
        # ⚠️ 审计：旧口径把"还没分担当"也算停滞 → 6 个队全部等于各自的未分担当数，
        #    停滞筛选 515/515 条全中，等于没有筛选（2026-10-06）
        stale = (t.state != STATE_DONE and bool(a_codes)
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
            "assignees": [{"code": c, "name": names.get(c, c),
                           "dispatch_date": disp_of.get((t.id, c)),
                           "dispatched_today": (disp_of.get((t.id, c)) == today)}
                          for c in a_codes],
            "n_assign": len(a_codes),
            "pct": t.pct, "state": t.state,
            "store_count": t.store_count,      # 店铺数（完成时填，2026-10-06）
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


def team_tasks(db: Session, team_ids: Sequence[int], tab: str = "",
               kw: str = "", line_id: Optional[int] = None) -> List[dict]:
    """队长视角：本队任务（可按 tab / 线路 / 关键词过滤）。

    ⚠️ 关键词与线路筛选**复用管理端同一条 SQL 口径**（`_base_query`）：
    旧实现只在 Python 里按站名匹配 → 队长搜「南武線」「京急」全军覆没（2026-10-06 审计）。
    """
    if not team_ids:
        return []
    q = _base_query(db, team_id=None, line_id=line_id, kw=kw).filter(
        BdTask.team_id.in_(list(team_ids)))
    # ⚠️ 队长视角的 tab **按"有没有分到人"分**，不按全局 state：
    #    - 未分配（待派）= 本队未完成 且 **无担当**（含"有进展但没人"→ 回收/空置后落这里）
    #    - 进行中       = 本队未完成 且 **有担当**
    #    - 已完成       = pct=100
    #    用户 2026-10-06："队长可以收回来的、分给别人、或者空置；**已经有进展的也能这样**"
    #    旧写法用 state → "有进展没人"会被算成进行中，回收后回不到"待派"（实测踩到）
    if tab == TAB_UNASSIGNED:
        q = q.filter(BdTask.state != STATE_DONE,
                     ~BdTask.id.in_(_has_assignee(db)))
    elif tab == TAB_DOING:
        q = q.filter(BdTask.state != STATE_DONE,
                     BdTask.id.in_(_has_assignee(db)))
    elif tab == TAB_DONE:
        q = q.filter(BdTask.state == STATE_DONE)
    tasks = q.order_by(BdTask.state.asc(), BdTask.id.asc()).all()
    return _rows(db, tasks)


def member_tasks(db: Session, person_code: Optional[str],
                 today: Optional[date] = None) -> List[dict]:
    """队员视角：**今天要做的活** = 今天派给我的 ∪ 之前派给我但**还没完成**的。

    用户 2026-10-06 口径："队长每天给队员派当天的任务；昨天没做完的**自动延续**"
    → 不新增派工行，靠这个查询口径实现；每行带 `dispatch_date` / `carried` 供界面区分。
    """
    if not person_code:
        return []
    d = today or _today()
    rows = (db.query(BdTaskAssign.task_id, BdTaskAssign.dispatch_date)
            .filter(BdTaskAssign.person_code == person_code).all())
    if not rows:
        return []
    disp = {tid: dd for tid, dd in rows}
    tasks = (db.query(BdTask).filter(BdTask.id.in_(list(disp)))
             .order_by(BdTask.state.asc(), BdTask.id.asc()).all())
    # 今天派的 ∪ 未完成的（历史 dispatch_date 为 NULL 的老行：未完成即延续）
    keep = [t for t in tasks
            if t.state != STATE_DONE or disp.get(t.id) == d]
    out = _rows(db, keep)
    for r in out:
        dd = disp.get(r["task"].id)
        r["dispatch_date"] = dd
        r["carried"] = bool(dd and dd < d and r["task"].state != STATE_DONE)
        r["dispatched_today"] = (dd == d)
        #: 延续了几天（0 = 今天派的）—— 界面显示"延续 3 天"
        r["carried_days"] = ((d - dd).days if (dd and d > dd) else 0)
    return out


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
    """三个 tab 的数字（**只受其它筛选影响，不受 tab 自己影响**）。

    ⚠️ 「未分配」= **车站池**（还没有队伍的物理车站，含"没建过任务"的），
    与另两个 tab（任务口径）不是一回事 —— 用户 2026-10-06："未分配的查询的是车站"。
    """
    out = {}
    for tab in BOARD_TABS:
        if tab == TAB_UNASSIGNED:
            # 车站池的数字：只认线路/关键词筛选（队伍筛选对"没队伍的站"无意义）
            out[tab] = list_unassigned_places(
                db, page=1, per=1, kw=kw, line_id=line_id)["total"]
            continue
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
                           BdLine.name.like(k),
                           BdTeam.name.like(k),
                           # ⚠️ place 任务的 `station_id` 是 NULL → 上面的 join 取不到线路/运营商，
                           #    必须再按"这个物理车站被哪些线经过"找一遍（实测：搜 JR東日本 曾经 0 命中）
                           BdTask.place_id.in_(
                               db.query(BdStation.place_id)
                               .join(BdLine, BdLine.id == BdStation.line_id)
                               .filter(BdStation.place_id.isnot(None),
                                       or_(BdLine.name.like(k)))))]
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
        cutoff = _today() - _td(days=max(0, stale_days))
        recent = (db.query(BdTaskProgress.task_id)
                  .filter(BdTaskProgress.progress_date >= cutoff))
        # ⚠️ 这里必须与「行里的 stale 标记」「统计卡的 stale_count」**同一口径**：
        #    **已分到人** + 未完成 + 最近 N 天没进展。
        #    2026-10-06 浏览器试跑实测：SQL 用宽松口径 → total=511，而列表只有 1 行，
        #    页面出现「共 511 条」却几乎空白的怪象。
        q = q.filter(BdTask.state != STATE_DONE,
                     BdTask.id.in_(_has_assignee(db)),
                     BdTask.id.notin_(recent))
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
    today = _today()
    agg: Dict[int, dict] = {}
    # 停滞只看**已分到人**的任务（没分人的还没开始，谈不上"没动"）
    with_asg = {r[0] for r in db.query(BdTaskAssign.task_id)
                .filter(BdTaskAssign.task_id.in_(
                    [t[0] for t in tasks] or [0])).all()}
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
        if (state != STATE_DONE and tid in with_asg
                and (lp is None or (days is not None and days >= stale_days))):
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
               date_to: Optional[date] = None, lang: str = "zh",
               kw: str = "", line_id: Optional[int] = None,
               stale_only: bool = False, stale_days: int = 2):
    """导出（**三个 sheet**，流式写）。

    用户 2026-10-06："我需要'未分配'，进行中，已完成的都导出来。进行中的要显示进展。
    已完成的显示完成日期。未分配的只显示线路/站点。"
    → 一个文件三个 sheet，各自列不同，**跟着当前筛选**（线路/队伍/关键词/日期区间）。
    """
    from io import BytesIO
    from openpyxl import Workbook
    from openpyxl.utils import get_column_letter
    labels = state_labels(lang)
    wb = Workbook(write_only=True)

    def _d(v):
        return v.strftime("%Y-%m-%d") if v else ""

    def _sheet(title, head, rows, widths=None):
        ws = wb.create_sheet(title)
        ws.append(head)
        for c in range(1, len(head) + 1):
            ws.column_dimensions[get_column_letter(c)].width = (
                widths[c - 1] if widths else 18)
        for r in rows:
            ws.append(r)
        return ws

    # ① 未分配 = **车站池**（只给 线路 / 站点）
    pool = list_unassigned_places(db, kw=kw, line_id=line_id, all_rows=True)
    _sheet("未分配", ["线路", "站点"],
           [[r["lines_text"] or r["line_label"], r["name"]] for r in pool["rows"]],
           widths=[34, 20])

    # ② 进行中 = 任务（带**进展**）
    doing = task_board(db, team_id=team_id, line_id=line_id, tab=TAB_ASSIGNED,
                       date_from=date_from, date_to=date_to, kw=kw,
                       limit=100000, stale_only=stale_only,
                       stale_days=stale_days)
    # ⚠️ `state` 以前收了不用（按队汇总点「未分配」进来，导出却是全量）→ 真正生效：
    #    unassigned = **已派队但还没分到人**（按队汇总那一列的口径）
    if state == STATE_UNASSIGNED:
        doing = dict(doing, rows=[r for r in doing["rows"]
                                 if not r.get("assignees")])
    elif state == STATE_DONE:
        doing = dict(doing, rows=[])
    _sheet("进行中",
           ["线路", "站点", "团队", "担当", "进展%", "店铺数", "分配日期", "开始日", "最后提交"],
           [[r["line"], r["station_name"], r["team_name"],
             "、".join(a["name"] for a in r["assignees"]), r["pct"],
             ("" if r.get("store_count") is None else r["store_count"]),
             _d(r["assign_date"]), _d(r["start_date"]), _d(r["last_date"])]
            for r in doing["rows"]],
           widths=[26, 18, 12, 14, 8, 8, 12, 12, 12])

    # ③ 已完成 = 任务（带**完成日期**与用时）
    done = task_board(db, team_id=team_id, line_id=line_id, tab=TAB_DONE,
                      date_from=date_from, date_to=date_to, kw=kw,
                      limit=100000)
    rows = []
    for r in done["rows"]:
        days = ""
        if r["start_date"] and r["done_date"]:
            days = (r["done_date"] - r["start_date"]).days
        rows.append([r["line"], r["station_name"], r["team_name"],
                     "、".join(a["name"] for a in r["assignees"]),
                     ("" if r.get("store_count") is None else r["store_count"]),
                     _d(r["done_date"]), _d(r["start_date"]),
                     _d(r["assign_date"]), days])
    _sheet("已完成",
           ["线路", "站点", "团队", "担当", "店铺数", "完成日期", "开始日", "分配日期", "用时(天)"],
           rows, widths=[26, 18, 12, 14, 8, 12, 12, 12, 10])

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
